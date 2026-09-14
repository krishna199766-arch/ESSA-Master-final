"""Checks the Sales / Retail Reports catalogue — every report, and the figures in the ones that count money.

    python test_retail_reports.py

No pytest — the shop has no test dependency and this needs none. Runs against a
throwaway database with no warehouse attached, so it never touches
textile_shop.db.

Seventy reports is too many to eyeball, so the checks are in two layers. Every
report must RUN on a shop with ordinary data in it and hand back a table whose
rows fit its columns, and every report page and export must open. Then the
reports people reconcile against — sales, returns, tax, collections, what is
still owed — are held to figures worked out by hand from the bills below, because
a report that runs and adds up wrong is worse than one that does not run.
"""
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

SHOP_DIR = Path(__file__).resolve().parent
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 'retail_reports_test.db'}"
os.environ["ESSA_WAREHOUSE_DB"] = str(Path(tempfile.mkdtemp()) / "no-warehouse.db")
sys.path.insert(0, str(SHOP_DIR))

from app import create_app, db                                    # noqa: E402
from app.models import (Attendance, Category, Company, Counter,    # noqa: E402
                        CreditNote, CreditNoteItem, Customer, Invoice,
                        InvoiceItem, InvoicePayment, Location, LocationStock,
                        LoyaltyTxn, Product, SaleSession, SaleSessionItem,
                        StockMovement, TransferReceipt, User)

app = create_app()
failures = []


def check(name, got, want):
    ok_ = got == want
    print(f"{'PASS' if ok_ else 'FAIL'}  {name}")
    if not ok_:
        print(f"        got : {got!r}")
        print(f"        want: {want!r}")
        failures.append(name)


def ok(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if not cond and detail else ""))
    if not cond:
        failures.append(name)


now = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
yesterday = now - timedelta(days=1)

# ---- a shop with a day and a half of ordinary trade -------------------------
with app.app_context():
    db.create_all()
    boss = User(username="boss", full_name="M Anand", role="manager")
    boss.set_password("x")
    till = User(username="till", full_name="Till User", role="cashier")
    till.set_password("x")
    priya = User(username="priya", full_name="Priya S", role="cashier", commission_pct=2.0,
                 salary=15000)
    priya.set_password("x")
    mens = Category(name="MENS-SHIRT", section="MENS")
    ladies = Category(name="LADIES-SAREE", section="LADIES")
    kumar = Customer(name="R Kumar", phone="9876500001")
    mehta = Customer(name="Mehta Traders", phone="9876500002", gstin="33ABCDE1234F1Z5")
    idle = Customer(name="Old Friend", phone="9876500003")
    co = Company(name="Taqua Silks", gstin="33AAAAA0000A1Z5")
    db.session.add_all([boss, till, priya, mens, ladies, kumar, mehta, idle, co])
    db.session.flush()
    branch = Location(name="TAQUA TIRUPUR", company_id=co.id)
    db.session.add(branch)
    db.session.flush()
    counter = Counter(name="G-BILL1", location_id=branch.id)
    db.session.add(counter)
    shirt = Product(sku="ESSA-00002", barcode="8901000000021", name="MENS COTTON SHIRT",
                    category_id=mens.id, hsn_code="6205", cost_price=500.0, selling_price=1000.0,
                    gst_rate=5.0, stock_qty=20, size="L", color="Blue")
    saree = Product(sku="ESSA-00009", name="SILK SAREE", category_id=ladies.id, hsn_code="5007",
                    cost_price=2000.0, selling_price=3000.0, gst_rate=12.0, stock_qty=5)
    db.session.add_all([shirt, saree])
    db.session.flush()

    def bill(number, when, lines, customer=None, staff=None, discount=0.0, placed=True,
             payments=None, method="cash"):
        subtotal = sum(q * p for _, q, p, _ in lines)
        tax = sum(round(q * p * r / 100.0, 2) for _, q, p, r in lines)
        inv = Invoice(invoice_number=number, invoice_date=when, customer_id=customer,
                      cashier_id=till.id, staff_id=staff, subtotal=subtotal, discount=discount,
                      cgst=round(tax / 2, 2), sgst=round(tax / 2, 2),
                      total=round(subtotal - discount + tax, 2), payment_method=method,
                      company_id=co.id if placed else None,
                      location_id=branch.id if placed else None,
                      counter_id=counter.id if placed else None)
        db.session.add(inv)
        db.session.flush()
        for prod, q, p, r in lines:
            db.session.add(InvoiceItem(invoice_id=inv.id, product_id=prod.id, quantity=q,
                                       unit_price=p, gst_rate=r, line_total=q * p,
                                       tax_amount=round(q * p * r / 100.0, 2)))
        for m, amount, tendered in payments or []:
            db.session.add(InvoicePayment(invoice_id=inv.id, method=m, amount=amount,
                                          tendered=tendered, created_at=when))
        return inv

    # A: two shirts, 100 off, half cash (2,000 handed over) and half card.
    a = bill("TG26-001", now, [(shirt, 2, 1000.0, 5.0)], kumar.id, priya.id, discount=100.0,
             payments=[("cash", 1500.0, 2000.0), ("card", 500.0, 500.0)], method="mixed")
    # B: a saree to a GST-registered trader, paid by UPI.
    b = bill("TG26-002", now, [(saree, 1, 3000.0, 12.0)], mehta.id, priya.id,
             payments=[("upi", 3360.0, 3360.0)], method="upi")
    # C: yesterday, a walk-in, from before tenders or branches were recorded.
    bill("INV-000001", yesterday, [(shirt, 1, 1000.0, 5.0)], placed=False)
    # D: a bill whose recorded payment falls 50 short.
    d = bill("TG26-003", now, [(shirt, 1, 1000.0, 5.0)], kumar.id, priya.id, placed=False,
             payments=[("cash", 1000.0, 1000.0)])
    db.session.flush()

    note = CreditNote(number="CN-0001", invoice_id=a.id, cashier_id=till.id, created_at=now,
                      subtotal=1000.0, cgst=25.0, sgst=25.0, total=1050.0, refund_method="cash",
                      reason="size")
    db.session.add(note)
    db.session.flush()
    db.session.add(CreditNoteItem(credit_note_id=note.id, invoice_item_id=a.items[0].id,
                                  product_id=shirt.id, quantity=1, unit_price=1000.0, gst_rate=5.0,
                                  line_total=1000.0, tax_amount=50.0))
    db.session.add(LoyaltyTxn(customer_id=kumar.id, points=-100, reason="redeem",
                              invoice_id=a.id, created_at=now))
    db.session.add(StockMovement(product_id=shirt.id, change=20, reason="opening",
                                 reference="OPEN", created_at=now))
    db.session.add(TransferReceipt(wh_line_id=1, code="TRF-1", location_id=branch.id,
                                   product_id=saree.id, qty=5,
                                   applied_at=now - timedelta(days=40)))
    db.session.add(LocationStock(location_id=branch.id, product_id=shirt.id, qty=12))
    db.session.add(Attendance(user_id=priya.id, check_in=now))
    approved = SaleSession(code="AP0001", salesperson_id=priya.id, customer_id=kumar.id,
                           status="completed", invoice_id=a.id, created_at=now)
    estimate = SaleSession(code="ES0001", salesperson_id=priya.id, status="open", created_at=now)
    db.session.add_all([approved, estimate])
    db.session.flush()
    for s in (approved, estimate):
        item = SaleSessionItem(session_id=s.id, product_id=shirt.id, quantity=1,
                               unit_price=1000.0, gst_rate=5.0)
        item.recompute()
        db.session.add(item)
    db.session.commit()
    BRANCH = branch.id

from app import retail_reports as rr                              # noqa: E402

today, week_ago = date.today(), date.today() - timedelta(days=7)

# ---- the menu --------------------------------------------------------------
print("\n-- the catalogue mirrors the reference menu")
with app.app_context():
    cat = rr.catalogue()
    check("eleven groups", len(cat), 11)
    check("seventy reports", sum(len(g["reports"]) for g in cat), 70)
    check("group sizes, in menu order",
          [len(g["reports"]) for g in cat], [17, 4, 8, 2, 9, 6, 4, 4, 14, 1, 1])
    unavailable = [r["key"] for g in cat for r in g["reports"] if r["unavailable"]]
    check("thirteen are honest about having nothing recorded", len(unavailable), 13)
    ok("and each says why", all(rr.REPORTS[k]["unavailable"].strip() for k in unavailable))
    ok("a 'see instead' always points at a real, runnable report",
       all(rr.REPORTS[r["see"]]["run"] for g in cat for r in g["reports"] if r.get("see")))

# ---- every report runs -----------------------------------------------------
print("\n-- every available report runs and fits its own columns")
results = {}
with app.test_request_context():
    for key, spec in rr.REPORTS.items():
        if spec["unavailable"]:
            continue
        try:
            out = rr.run(key, week_ago, today)
        except Exception as exc:                                   # noqa: BLE001
            ok(f"{key} runs", False, f"{type(exc).__name__}: {exc}")
            continue
        results[key] = out
        bad = [r for r in out["rows"] if len(r) != len(out["columns"])]
        ok(f"{key}: {len(out['rows'])} row(s), all {len(out['columns'])} wide", not bad,
           f"{len(bad)} row(s) the wrong width")


def total(key, name):
    return results[key]["totals"].get(name)


# ---- the figures -----------------------------------------------------------
print("\n-- the money adds up")
# bills: A 2,000 · B 3,360 · C 1,050 · D 1,050
check("sales report: four bills", total("sales_report", "Bills"), 4)
check("sales report: net of every bill", total("sales_report", "Net"), 7460.0)
check("day summary: the return comes off", total("day_summary", "Returns"), 1050.0)
check("day summary: net sales", total("day_summary", "Net sales"), 6410.0)
check("unsettled: only the bill 50 short", [r[0] for r in results["unsettled_bills"]["rows"]],
      ["TG26-003"])
check("unsettled: its balance", total("unsettled_bills", "Balance"), 50.0)
check("reconciliation: the same 50", total("settlement_reconciliation", "Difference"), 50.0)
# collected: A 1,500+500 · B 3,360 · C 1,050 (single tender) · D 1,000
check("day-end settlement: collected", total("settlement_day_end", "Collected"), 7410.0)
check("day-end settlement: refunds", total("settlement_day_end", "Refunds"), 1050.0)
check("cash in drawer: cash taken less cash refunded",
      total("settlement_day_end", "Cash in drawer"), 1500.0 + 1050.0 + 1000.0 - 1050.0)
check("settlement detail: the split bill is two tenders plus the rest",
      total("settlement_detail", "Tenders"), 5)
check("change handed back on the cash tender", total("settlement_detail", "Change given"), 500.0)

print("\n-- tax, rate by rate")
rates = {r[0]: r for r in results["tax_summary"]["rows"]}
check("5% taxable (shirts: 2,000 + 1,000 + 1,000)", rates["5%"][3], 4000.0)
check("12% taxable (the saree)", rates["12%"][3], 3000.0)
check("total tax", total("tax_summary", "Tax"), 560.0)
check("CGST is half of intra-state tax", total("tax_summary", "CGST"), 280.0)
check("HSN report: one row per HSN and rate", len(results["hsn"]["rows"]), 2)
check("column-wise tax: one row per bill", total("tax_column", "Bills"), 4)
check("row-wise tax: one row per bill per rate", len(results["tax_row"]["rows"]), 4)
gstr = {r[0] for r in results["gstr"]["rows"]}
ok("GSTR summary separates B2B, B2C and credit notes",
   {"B2B — registered customers", "B2C — intra-state",
    "Credit notes — unregistered (CDNUR)"} <= gstr, str(gstr))

print("\n-- the B2B vertical is only registered customers")
check("B2B sales: the trader's bill alone", [r[0] for r in results["b2b_sales"]["rows"]],
      ["TG26-002"])
check("B2B HSN: the saree alone", [r[0] for r in results["b2b_hsn"]["rows"]], ["5007"])

print("\n-- people, discount and margin")
check("salesman wise: returns come off the seller",
      [(r[0], r[5]) for r in results["sales_salesman"]["rows"] if r[0] == "Priya S"],
      [("Priya S", 1050.0)])
check("biller-wise discount", total("discount_biller", "Discount"), 100.0)
margin = {r[0]: r for r in results["sales_margin"]["rows"]}
check("shirt margin (4,000 sold at 500 cost each)", (margin["ESSA-00002"][6], margin["ESSA-00002"][7]),
      (2000.0, 50.0))
check("employee incentive is net × 2%",
      [r[7] for r in results["incentive_employees"]["rows"] if r[0] == "Priya S"],
      [round((2000 + 3360 + 1050 - 1050) * 0.02, 2)])
check("employee detail counts the day present",
      [r[10] for r in results["employee_detail"]["rows"] if r[1] == "Priya S"], [1])

print("\n-- customers, carts and stock")
check("loyalty consumption", total("loyalty_consumption", "Points used"), 100)
check("approved bills: the completed cart", [r[0] for r in results["approved_bills"]["rows"]],
      ["AP0001"])
check("estimates: the cart never billed", [r[0] for r in results["estimate_bills"]["rows"]],
      ["ES0001"])
check("credit notes by customer", total("cn_customer", "Amount"), 1050.0)
check("direct stock: the opening entry", total("direct_stock", "Qty"), 20)
ok("stock split names the branch", "TAQUA TIRUPUR" in results["stock_split"]["columns"])
ok("text day summary carries a text block", "Bills:" in results["text_day_summary"].get("text", ""))

with app.test_request_context():
    inactive = rr.run("inactive_customers", week_ago, today, params={"days": 90})
    check("inactive customers: only the one who never bought",
          [r[0] for r in inactive["rows"]], ["Old Friend"])
    placed = rr.run("sales_report", week_ago, today, location=BRANCH)
    check("a branch filter narrows the bills", placed["totals"]["Bills"], 2)
    whole_shop = rr.run("employee_detail", week_ago, today, location=BRANCH)
    check("a whole-shop report ignores the branch", len(whole_shop["rows"]), 3)

# ---- the pages -------------------------------------------------------------
print("\n-- every page opens, and every export downloads")
client = app.test_client()
with app.app_context():
    boss_id = User.query.filter_by(username="boss").first().id
with client.session_transaction() as s:
    s["_user_id"] = str(boss_id)
    s["_fresh"] = True

page = client.get("/reports/")
check("the reports page opens", page.status_code, 200)
html = page.get_data(as_text=True)
ok("it leads with the catalogue", "Report catalogue" in html and "Stock Aging Detail Report" in html)
ok("and still carries the sales overview", 'Total sales</div><div class="value">' in html)
ok("the ask bar's catalogue still answers", client.get("/reports/catalogue").status_code == 200)

pages_bad, csv_bad = [], []
for key, spec in rr.REPORTS.items():
    r = client.get(f"/reports/r/{key}")
    if r.status_code != 200 or spec["label"] not in r.get_data(as_text=True):
        pages_bad.append((key, r.status_code))
    if not spec["unavailable"]:
        e = client.get(f"/reports/r/{key}?export=csv")
        if (e.status_code != 200 or "text/csv" not in e.headers.get("Content-Type", "")
                or spec["label"] not in e.get_data(as_text=True)):
            csv_bad.append((key, e.status_code))
check("all 70 report pages open", pages_bad, [])
check("all 57 exports download", csv_bad, [])
ok("an unavailable report says why",
   "no date of birth" in client.get("/reports/r/birthday").get_data(as_text=True))
check("an unknown report is a 404", client.get("/reports/r/nonsense").status_code, 404)

print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("All retail report checks passing.")
