"""A Store user reaches the Billing Counter and Reports, and nothing else.

    python test_store_access.py

The warehouse's Users & Access can narrow an account's Store to billing and
reports. Inside the Essa app the /pos mount says so with a header on each
request (backend/app/main.py); this checks the shop's half — app/modules.py and
the gate in create_app:

  * with the header, billing works end to end — the counter, its lookups, the
    drawer, the bill it prints — and so do Reports; every other screen is
    refused, as a page for a screen and as JSON for an API call;
  * the shop's own menu only offers the two;
  * WITHOUT the header nothing changes, because that is every request from a
    shop opened on its own;
  * and it only narrows: a cashier's login still cannot open Reports.
"""
import os
import sys
import tempfile
from pathlib import Path

SHOP_DIR = Path(__file__).resolve().parent

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 'store_access_test.db'}"
os.environ["ESSA_WAREHOUSE_DB"] = str(Path(tempfile.mkdtemp()) / "no-warehouse.db")
sys.path.insert(0, str(SHOP_DIR))

from app import create_app, db                                       # noqa: E402
from app.models import (Category, Company, Counter, Customer, Floor,  # noqa: E402
                        Invoice, InvoiceItem, Location, Product, User)

failures = []


def ok(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if not cond and detail else ""))
    if not cond:
        failures.append(name)


app = create_app()
with app.app_context():
    db.create_all()
    admin = User(username="admin", full_name="A Rahman", role="admin")
    admin.set_password("x")
    cashier = User(username="ravi", full_name="Ravi Kumar", role="cashier")
    cashier.set_password("x")
    cat = Category(name="SAREE")
    cust = Customer(name="R Kumar", phone="9876500001")
    co = Company(name="TAQUA SILKS", is_default=True, active=True)
    db.session.add_all([admin, cashier, cat, cust, co])
    db.session.flush()
    loc = Location(name="TIRUPUR", company_id=co.id, active=True)
    db.session.add(loc)
    db.session.flush()
    storey = Floor(location_id=loc.id, name="Ground Floor", prefix="TG", active=True)
    db.session.add(storey)
    db.session.flush()
    till = Counter(name="Counter 1", location_id=loc.id, floor_id=storey.id)
    prod = Product(sku="ESSA-00001", name="SILK SAREE", category_id=cat.id,
                   selling_price=2000.0, stock_qty=10, gst_rate=5.0)
    db.session.add_all([till, prod])
    db.session.flush()
    inv = Invoice(invoice_number="TG26-001", cashier_id=admin.id, staff_id=admin.id,
                  customer_id=cust.id, subtotal=2000.0, total=2100.0,
                  company_id=co.id, location_id=loc.id, floor_id=storey.id,
                  counter_id=till.id, bill_prefix="TG", fin_year="26", bill_seq=1)
    db.session.add(inv)
    db.session.flush()
    db.session.add(InvoiceItem(invoice_id=inv.id, product_id=prod.id, quantity=1,
                               unit_price=2000.0, gst_rate=5.0,
                               line_total=2000.0, tax_amount=100.0))
    db.session.commit()
    ids = {"loc": loc.id, "till": till.id, "inv": inv.id}

USER = {"X-Essa-Store-Access": "user"}


def signed_in(username):
    c = app.test_client()
    c.post("/login", data={"username": username, "password": "x"})
    c.post("/pos/place", json={"location_id": ids["loc"], "counter_id": ids["till"]})
    return c


# ---- billing and reports open ------------------------------------------------
print("-- a Store user, signed in to the shop as its admin --")
client = signed_in("admin")
for path in ["/pos/", "/pos/api/next-bill", "/pos/api/staff?code=admin",
             "/pos/api/product?code=ESSA-00001", "/pos/api/coupon?code=NOPE&amount=100",
             "/pos/api/credit-note?code=CN-NOPE", f"/pos/invoice/{ids['inv']}",
             f"/pos/invoice/{ids['inv']}/print", "/drawer/",
             "/floor/api/customer-lookup?q=kumar",
             "/reports/", "/reports/?view=all", "/reports/r/sales_report",
             "/reports/r/sales_report?export=csv", "/reports/catalogue"]:
    r = client.get(path, headers=USER)
    ok(f"GET {path} opens", r.status_code == 200, f"HTTP {r.status_code}")

r = client.post("/pos/place", headers=USER,
                json={"location_id": ids["loc"], "counter_id": ids["till"]})
ok("choosing the till is billing", r.status_code == 200, f"HTTP {r.status_code}")

r = client.get("/", headers=USER)
ok("the dashboard forwards to the counter rather than refusing",
   r.status_code == 302 and r.headers["Location"].rstrip("/").endswith("/pos"),
   f"HTTP {r.status_code} → {r.headers.get('Location')}")

# ---- everything else refused -------------------------------------------------
print("\n-- …and nothing else --")
for path in ["/pos/invoices", "/inventory/", "/customers/", "/floor/", "/delivery/",
             "/returns/", "/alterations/", "/stock-check/", "/audits/", "/stores/",
             "/promotions/", "/coupons/", "/staff/"]:
    r = client.get(path, headers=USER)
    ok(f"GET {path} is refused", r.status_code == 403, f"HTTP {r.status_code}")
r = client.post(f"/pos/invoice/{ids['inv']}/cancel", headers=USER, data={})
ok("cancelling a bill is not billing", r.status_code == 403, f"HTTP {r.status_code}")
r = client.get("/inventory/", headers=USER)
ok("a refused screen says why, and offers the way back",
   b"Billing Counter and Reports only" in r.data and b'href="/pos/"' in r.data)
r = client.get("/promotions/api/products?q=saree", headers=USER)
ok("a refused API call answers in JSON", r.is_json and r.status_code == 403,
   f"HTTP {r.status_code} {r.content_type}")

page = client.get("/pos/", headers=USER).data
ok("the shop's own menu offers Reports", b'href="/reports/"' in page)
ok("…and not the screens that are refused", b'href="/inventory/"' not in page
   and b'href="/staff/"' not in page and b'href="/pos/invoices"' not in page)

# ---- no header: exactly as before --------------------------------------------
print("\n-- without the header, nothing changes --")
for path in ["/", "/inventory/", "/stores/", "/staff/", "/pos/invoices"]:
    r = client.get(path)
    ok(f"GET {path} opens", r.status_code == 200, f"HTTP {r.status_code}")
ok("and the menu still lists every screen", b'href="/inventory/"' in client.get("/pos/").data)
r = client.get("/inventory/", headers={"X-Essa-Store-Access": "admin"})
ok("a header saying anything but user narrows nothing", r.status_code == 200,
   f"HTTP {r.status_code}")

fresh = app.test_client()
r = fresh.get("/login", headers=USER)
ok("the login form is reachable for a Store user", r.status_code == 200,
   f"HTTP {r.status_code}")

# ---- it only narrows ---------------------------------------------------------
print("\n-- it never opens what the shop's login would not --")
till_login = signed_in("ravi")
r = till_login.get("/pos/", headers=USER)
ok("a cashier login still bills", r.status_code == 200, f"HTTP {r.status_code}")
r = till_login.get("/reports/", headers=USER)
ok("but Reports stays a manager's screen here", r.status_code == 403,
   f"HTTP {r.status_code}")

print("\n" + ("=" * 60))
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("A Store user gets billing and reports, and nothing else.")
