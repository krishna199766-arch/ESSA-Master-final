"""Stock check — where is this item, and have we got it.

Two questions, one screen. With the garment in your hand: scan it and get the
whole picture — what the shop holds, what the warehouse still holds, the piece
codes behind it, and every bill it has gone out on. With the customer in front of
you asking for a medium in black: filter by attribute and price and see what
comes back.

The shop is the only side that can answer both. It keeps its own stock and its
own invoices, and it reads the warehouse database read-only through
warehouse_items — the backend has no route the other way.

One honest limit, worth knowing before trusting a screen like this: per-piece
codes are only ever created `in_stock` and nothing marks one sold, because a sale
takes a SKU and a quantity, not a piece. So the pieces listed are the ones the
warehouse minted, not proof that each is still on a shelf, and sales history is
answered per product rather than per garment.
"""
from flask import Blueprint, render_template, request
from flask_login import login_required

from app import db
from app import warehouse_items
from app.models import Category, Invoice, InvoiceItem, Product

checker_bp = Blueprint("checker", __name__)

# The attribute columns worth filtering on, in the order a person narrows down.
FILTERS = [
    ("category", "Category"), ("size", "Size"), ("color", "Colour"),
    ("fabric", "Material"), ("product_type", "Type"), ("pattern", "Pattern"),
    ("fit", "Fit"),
]


def _values(column):
    """Every value actually present in the catalogue, for a dropdown.

    Built from the stock rather than from a fixed list, so it can only ever offer
    a filter that has something behind it.
    """
    rows = db.session.query(column).filter(column.isnot(None), column != "") \
        .distinct().order_by(column).all()
    return [r[0] for r in rows]


@checker_bp.route("/")
@login_required
def index():
    code = (request.args.get("code") or "").strip()
    selected = {name: (request.args.get(name) or "").strip() for name, _ in FILTERS}
    price_min = request.args.get("price_min", type=float)
    price_max = request.args.get("price_max", type=float)
    text = (request.args.get("q") or "").strip()

    product, results, sales, units, warehouse_row = None, [], [], [], None

    if code:
        # The fast path: a scanned tag or a typed SKU is one specific item.
        product = warehouse_items.resolve_scan(code)
    else:
        query = Product.query.filter_by(active=True)
        if text:
            like = f"%{text}%"
            query = query.filter((Product.name.ilike(like)) | (Product.sku.ilike(like)))
        if selected["category"]:
            query = query.join(Category).filter(Category.name == selected["category"])
        for name, _ in FILTERS:
            if name != "category" and selected[name]:
                query = query.filter(getattr(Product, name) == selected[name])
        if price_min is not None:
            query = query.filter(Product.selling_price >= price_min)
        if price_max is not None:
            query = query.filter(Product.selling_price <= price_max)

        asked = bool(text or price_min is not None or price_max is not None
                     or any(selected.values()))
        if asked:
            results = query.order_by(Product.name).limit(200).all()

    if product:
        # Everything known about this one item, from both sides.
        warehouse_row = warehouse_items.fetch_item(sku=product.sku)
        if product.warehouse_id:
            units = warehouse_items.fetch_units(product.warehouse_id)
        sales = (db.session.query(InvoiceItem, Invoice)
                 .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
                 .filter(InvoiceItem.product_id == product.id, Invoice.live())
                 .order_by(Invoice.invoice_date.desc()).limit(50).all())

    options = {"category": [c.name for c in
                            Category.query.join(Product).distinct().order_by(Category.name).all()]}
    for name, _ in FILTERS:
        if name != "category":
            options[name] = _values(getattr(Product, name))

    return render_template(
        "checker/index.html", code=code, text=text, product=product,
        results=results, sales=sales, units=units, warehouse_row=warehouse_row,
        filters=FILTERS, options=options, selected=selected,
        price_min=price_min, price_max=price_max,
        warehouse_available=warehouse_items.available())
