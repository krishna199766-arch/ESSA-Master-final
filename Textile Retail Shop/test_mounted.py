"""Checks every screen still works when the name `app` is no longer ours.

    python test_mounted.py

The shop runs two ways. Standalone, its package owns the name `app` for the life
of the process. Mounted inside the Essa warehouse — which is how it is actually
deployed — it is imported with the warehouse's `app` lifted out of the way and
put back immediately afterwards (see backend/app/pos_mount.py). From then on,
`app` in sys.modules is the WAREHOUSE's package.

So an `import` that runs at REQUEST time reaches into the wrong package:

    def invoice_list():
        from app.models import Floor      # ImportError, mounted. Fine, standalone.

That line shipped. Every other test suite passed, because they all run the shop
standalone where the name is still its own, and the screen 500s only for the
people actually using it. This file exists so that cannot happen twice: it
swaps `app` away exactly as the mount does, then asks for every screen.

It is a structural check, not a behavioural one. It does not care what a page
says — only that it can be built at all once the package underneath it has moved.
"""
import os
import sys
import tempfile
import types
from pathlib import Path

SHOP_DIR = Path(__file__).resolve().parent

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 'mount_test.db'}"
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


# ---- a shop with enough in it that every screen has something to draw --------
app = create_app()
with app.app_context():
    db.create_all()
    admin = User(username="admin", full_name="A Rahman", role="admin")
    admin.set_password("x")
    cat = Category(name="SAREE")
    cust = Customer(name="R Kumar", phone="9876500001")
    co = Company(name="TAQUA SILKS", is_default=True, active=True)
    db.session.add_all([admin, cat, cust, co])
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
    # A count in progress, so the audit screens have something to draw and the
    # scan endpoint has somewhere to put what it finds.
    from app import audits as audit_svc
    prod.floor_id = storey.id
    db.session.commit()
    audit = audit_svc.open_audit(storey, user_id=admin.id)

    ids = {"loc": loc.id, "till": till.id, "inv": inv.id, "prod": prod.id,
           "audit": audit.id}

client = app.test_client()
client.post("/login", data={"username": "admin", "password": "x"},
            follow_redirects=True)
client.post("/pos/place", json={"location_id": ids["loc"], "counter_id": ids["till"]})


# ---- take the name away, exactly as the mount does --------------------------
def _is_ours(name):
    return name == "app" or name.startswith("app.")


saved = {k: v for k, v in sys.modules.items() if _is_ours(k)}
for name in list(saved):
    del sys.modules[name]
# A stand-in with no submodules, so any `from app.something import …` at request
# time fails the way it does under the real mount. The warehouse's package would
# resolve and then not have the name; this one does not resolve at all. Either
# way the request raises, which is the thing being tested.
sys.modules["app"] = types.ModuleType("app")

print("-- every screen, with `app` belonging to somebody else --")
CHECKS = [
    ("GET", "/", None),
    ("GET", "/pos/", None),
    ("GET", "/pos/invoices", None),
    ("GET", "/pos/invoices?series=TG", None),
    ("GET", f"/pos/invoice/{ids['inv']}", None),
    ("GET", f"/pos/invoice/{ids['inv']}/print", None),
    ("GET", "/pos/api/next-bill", None),
    ("GET", "/pos/api/staff?code=admin", None),
    ("GET", "/pos/api/product?code=ESSA-00001", None),
    ("POST", "/pos/place", {"location_id": ids["loc"], "counter_id": ids["till"]}),
    ("POST", "/pos/api/promotions", {"items": [{"product_id": ids["prod"],
                                                "quantity": 3}]}),
    ("GET", "/stores/", None),
    ("GET", "/stores/series", None),
    ("GET", "/audits/", None),
    ("GET", f"/audits/{ids['audit']}", None),
    ("GET", f"/audits/{ids['audit']}?status=shortage&q=SAREE", None),
    ("GET", f"/audits/{ids['audit']}/print", None),
    ("GET", f"/audits/{ids['audit']}/export", None),
    ("GET", "/audits/api/products?q=saree", None),
    ("POST", f"/audits/{ids['audit']}/scan", {"code": "ESSA-00001"}),
    ("GET", "/promotions/", None),
    ("GET", "/promotions/new", None),
    ("GET", "/promotions/api/products?q=saree", None),
    ("GET", "/inventory/", None),
    ("GET", "/inventory/categories", None),
    ("GET", "/inventory/movements", None),
    ("GET", f"/inventory/{ids['prod']}/edit", None),
    ("GET", "/customers/", None),
    ("GET", "/returns/", None),
    ("GET", "/returns/?q=TG26-001", None),
    ("POST", "/returns/api/review", {"invoice_id": ids["inv"], "taking": {}}),
    ("GET", "/returns/list", None),
    ("GET", "/alterations/", None),
    ("GET", "/delivery/", None),
    ("GET", "/delivery/pending", None),
    ("GET", "/delivery/list", None),
    ("GET", "/floor/", None),
    ("GET", "/stock-check/", None),
    ("GET", "/staff/", None),
    ("GET", "/staff/attendance", None),
    ("GET", "/staff/commissions", None),
    ("GET", "/reports/", None),
    ("GET", "/reports/catalogue", None),
    ("GET", "/reports/?view=all", None),
    ("GET", "/reports/r/sales_report", None),
    ("GET", "/reports/r/sales_report?export=csv", None),
    ("GET", "/reports/r/gstr", None),
    ("GET", "/reports/r/stock_split", None),
    ("GET", "/reports/r/price_changer", None),
    ("GET", "/reports/r/text_day_summary", None),
    ("GET", "/reports/r/birthday", None),
    ("GET", "/reports/low-stock", None),
    ("POST", "/reports/ask", {"q": "what did we sell last month"}),
]

try:
    for method, path, payload in CHECKS:
        try:
            resp = (client.get(path) if method == "GET"
                    else client.post(path, json=payload))
            # Under 500 is the bar. A 404 for a missing row or a 400 for a
            # deliberately empty lookup is the screen working.
            ok(f"{method} {path}", resp.status_code < 500,
               f"HTTP {resp.status_code}")
        except Exception as exc:                     # noqa: BLE001
            ok(f"{method} {path}", False, f"{type(exc).__name__}: {exc}")
finally:
    for name in [k for k in list(sys.modules) if _is_ours(k)]:
        del sys.modules[name]
    sys.modules.update(saved)


print("\n" + ("=" * 60))
if failures:
    print(f"{len(failures)} FAILED — these import at request time, and will 500 "
          f"for anyone using the shop inside the warehouse:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("Every screen builds with the package name taken away.")
