"""Purchase orders — what we asked for, before anything arrived.

WHY THIS EXISTS AT ALL. Intake began at the supplier's invoice, so the system
knew what turned up and what it cost but never what was ordered. That gap is why
"Invoice Vs Purchase Order" is one of the six reports the catalogue lists as
absent rather than empty, and why receiving could never be three-way matched.

WHAT IS DELIBERATELY NOT HERE. No stock moves, no valuation, no tax arithmetic.
An order is a statement of intent: it is raised by us, it names a price we were
quoted, and until goods arrive against it nothing in the ledger has happened.
Everything that touches stock stays where it already is — services/inventory.

THE LIFECYCLE IS THE FEATURE:

    draft ──▶ pending ──▶ confirmed
      │          │            │
      └──────────┴────────────┴──▶ cancelled

`draft` is being written. `pending` has gone to the supplier and is awaiting
their word. `confirmed` is agreed by both sides — and is the ONLY state goods
may be received against, which is what makes "no LR without a confirmed PO"
expressible instead of being a convention someone has to remember.

EDITING STOPS AT CONFIRMED. A confirmed order is what the supplier agreed to; if
it could still be edited then the LR guard would be checking a document that no
longer says what it said when it was confirmed. Amending one means cancelling it
and raising its replacement, which is also what leaves an audit trail.
"""
import datetime as dt

from sqlalchemy.orm import Session

from .. import models
from ..config import COMPANY_NAME
from . import dates, numbering

#: The header fields a caller may set, in the order the form shows them. Used by
#: both the manual route and the extraction path, so a field added here reaches
#: both without a second list to keep in step.
HEADER_FIELDS = ["po_date", "supplier_name", "company", "brand", "item", "place",
                 "transport", "agent", "purchaser", "discount_pct", "notes"]

#: What one line may carry. `particulars` is the description column as the buying
#: office writes it — the reference screen's own word for it.
LINE_FIELDS = ["particulars", "size", "qty", "uom", "rate", "amount", "brand",
               "design_no", "hsn", "notes"]

#: Every state an order can be in, and what it means on screen. Order matters —
#: it is the sequence the status filter draws.
STATUSES = ["draft", "pending", "confirmed", "cancelled"]

#: Which states may still be edited. A confirmed order is a commitment; see the
#: module note.
EDITABLE = {"draft", "pending"}

#: Which states may be deleted outright: never sent, or already called off.
#: Pending and confirmed orders are cancelled first — see the delete route.
DELETABLE = {"draft", "cancelled"}

#: Where each state may go next. Cancelled is terminal: an order that was called
#: off and then quietly revived is how somebody receives goods against a PO the
#: supplier was told to ignore.
TRANSITIONS = {
    "draft":     {"pending", "confirmed", "cancelled"},
    "pending":   {"draft", "confirmed", "cancelled"},
    "confirmed": {"cancelled"},
    "cancelled": set(),
}

#: Fields that must be filled before an order may be CONFIRMED — not before it
#: may be saved. A draft is somewhere to put what you know so far, and holding a
#: half-written order to the finished article's rules is how people keep them on
#: paper until the end. The check bites at the moment the document becomes a
#: commitment, which is the moment it starts to matter.
REQUIRED_TO_CONFIRM = [("supplier_name", "supplier"), ("po_date", "PO date")]


def _num(v):
    """A number, or None. Accepts what a form and an OCR pass actually send —
    "1,250", "₹1250", "" — rather than only what json.loads produces."""
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return None


def _text(v):
    s = str(v).strip() if v is not None else ""
    return s or None


# ---------------------------------------------------------------------------
#  numbering
# ---------------------------------------------------------------------------
def next_po_no(db: Session, warehouse_id=None, taken=()):
    """The next order number, stepping over anything already issued.

    Read from the numbers on the table rather than from the counter alone, for
    the reason services/numbering states at length: a counter drifts out of step
    with deleted rows, and the first time it does it hands out a number that
    already exists — on the field two people quote at each other on the phone.
    """
    used = set(taken)
    for (v,) in db.query(models.PurchaseOrder.po_no).filter(
            models.PurchaseOrder.po_no.isnot(None)).all():
        used.add(v)
    return numbering.next_number(db, "purchase_order", warehouse_id=warehouse_id,
                                 is_taken=lambda code: code in used)


# ---------------------------------------------------------------------------
#  arithmetic
# ---------------------------------------------------------------------------
def line_amount(qty, rate, given=None):
    """What a line comes to. An explicitly given amount wins.

    Same rule the extraction validator uses on an invoice: derive what is missing,
    never overwrite what the document actually states. A buyer who types an agreed
    line total that is not exactly qty x rate has agreed that total.
    """
    amount = _num(given)
    if amount is not None:
        return round(amount, 2)
    q, r = _num(qty), _num(rate)
    if q is None or r is None:
        return None
    return round(q * r, 2)


def recompute(po: models.PurchaseOrder):
    """Refresh the header totals from the lines. Called on every write.

    Stored rather than derived on read because an order is quoted, sent and
    argued over on its total, and a figure that is recomputed differently by a
    later version of this code would change a document somebody already signed.
    """
    subtotal = round(sum((l.amount or 0) for l in po.lines), 2)
    pct = _num(po.discount_pct) or 0.0
    discount = round(subtotal * pct / 100.0, 2) if pct else 0.0
    po.subtotal = subtotal
    po.discount_amount = discount
    po.total = round(subtotal - discount, 2)
    return po


# ---------------------------------------------------------------------------
#  writing
# ---------------------------------------------------------------------------
def _apply_lines(db: Session, po: models.PurchaseOrder, rows):
    """Replace the order's lines with `rows`.

    Replace rather than merge: the form sends the grid as it now stands, and
    diffing it against what is stored would need a row identity the grid does not
    have. Blank rows are dropped — a grid always carries a spare one at the foot,
    and saving it would put an empty line on every order in the system.
    """
    if rows is None:
        return
    po.lines.clear()
    db.flush()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        vals = {f: raw.get(f) for f in LINE_FIELDS}
        if not any(str(v).strip() for v in vals.values() if v is not None):
            continue                      # the grid's spare row
        line = models.PurchaseOrderLine(
            particulars=_text(vals["particulars"]),
            size=_text(vals["size"]),
            qty=_num(vals["qty"]),
            uom=_text(vals["uom"]),
            rate=_num(vals["rate"]),
            amount=line_amount(vals["qty"], vals["rate"], vals["amount"]),
            brand=_text(vals["brand"]),
            design_no=_text(vals["design_no"]),
            hsn=_text(vals["hsn"]),
            notes=_text(vals["notes"]),
        )
        po.lines.append(line)


def apply_header(po: models.PurchaseOrder, payload: dict):
    """Set the header fields present in `payload`, leaving the rest alone.

    Absent means "not mentioned", not "clear it" — a PATCH from a screen showing
    half the form must not wipe the half it isn't showing.
    """
    for field in HEADER_FIELDS:
        if field not in payload:
            continue
        value = payload[field]
        if field == "discount_pct":
            po.discount_pct = _num(value)
        elif field == "po_date":
            po.po_date = dates.normalise(value)
        else:
            setattr(po, field, _text(value))
    return po


def create(db: Session, payload: dict, warehouse_id=None) -> models.PurchaseOrder:
    """Raise a new order. Starts as a draft unless told otherwise."""
    po = models.PurchaseOrder(
        warehouse_id=warehouse_id,
        status="draft",
        entry_source=_text(payload.get("entry_source")) or "manual",
        document_id=payload.get("document_id") or None,
        supplier_id=payload.get("supplier_id") or None,
        # The configured company, unless the order names one. One deployment has
        # a single billing entity, so this is almost always the default — it is a
        # column rather than a constant because naming who is billed is the first
        # thing a second entity would need.
        company=_text(payload.get("company")) or COMPANY_NAME,
        po_date=dates.normalise(payload.get("po_date")) or dates.today(),
    )
    apply_header(po, payload)
    po.po_no = _text(payload.get("po_no")) or next_po_no(db, warehouse_id)
    db.add(po)
    db.flush()
    _apply_lines(db, po, payload.get("lines"))
    recompute(po)
    return po


def update(db: Session, po: models.PurchaseOrder, payload: dict) -> models.PurchaseOrder:
    """Amend an order that has not been committed yet.

    Refuses once confirmed — see the module note. The refusal names the way
    forward rather than only the rule, because "cancel it and raise another" is
    not obvious and is the only thing the person can actually do.
    """
    if po.status not in EDITABLE:
        raise ValueError(
            f"a {po.status} order cannot be edited — cancel it and raise a "
            f"replacement, so the change leaves a trail")
    apply_header(po, payload)
    if payload.get("supplier_id") is not None:
        po.supplier_id = payload["supplier_id"] or None
    _apply_lines(db, po, payload.get("lines"))
    recompute(po)
    return po


# ---------------------------------------------------------------------------
#  the lifecycle
# ---------------------------------------------------------------------------
def missing_to_confirm(po: models.PurchaseOrder):
    """What still has to be filled in before this order can be committed."""
    gaps = [label for field, label in REQUIRED_TO_CONFIRM
            if not str(getattr(po, field, "") or "").strip()]
    if not po.lines:
        gaps.append("at least one line")
    return gaps


def set_status(db: Session, po: models.PurchaseOrder, status: str,
               by: str = None, reason: str = None) -> models.PurchaseOrder:
    """Move an order along its lifecycle, or refuse and say why."""
    status = (status or "").strip().lower()
    if status not in STATUSES:
        raise ValueError(f"'{status}' is not a purchase order status")
    if status == po.status:
        return po
    if status not in TRANSITIONS.get(po.status, set()):
        raise ValueError(f"a {po.status} order cannot become {status}")

    if status == "confirmed":
        gaps = missing_to_confirm(po)
        if gaps:
            raise ValueError("cannot confirm without " + ", ".join(gaps))
        po.confirmed_at = dt.datetime.utcnow()
        po.confirmed_by = _text(by)
    if status == "cancelled":
        # An order goods have already been booked in against is not cancellable:
        # the consignment on the transport register points at it, and cancelling
        # it would leave that row citing a document that says it never happened.
        linked = db.query(models.LREntry).filter(
            models.LREntry.purchase_order_id == po.id).count()
        if linked:
            raise ValueError(
                f"{linked} transport entr{'y' if linked == 1 else 'ies'} already "
                f"reference this order — it cannot be cancelled")
        po.cancelled_at = dt.datetime.utcnow()
        po.cancel_reason = _text(reason)
    po.status = status
    return po


def open_orders(db: Session, warehouse_id=None, supplier_name=None):
    """Confirmed orders, which are the only ones goods may arrive against.

    This is what the LR Entry form's PO picker offers. Narrowed by supplier when
    one is known, because a transport desk booking in a Matoshree consignment
    should not be scrolling past every other supplier's open orders.
    """
    from . import scope
    q = db.query(models.PurchaseOrder).filter(
        models.PurchaseOrder.status == "confirmed")
    q = scope.purchase_orders(q, warehouse_id)
    name = (supplier_name or "").strip().lower()
    if name:
        q = q.filter(models.PurchaseOrder.supplier_name.isnot(None))
    rows = q.order_by(models.PurchaseOrder.id.desc()).all()
    if name:
        # Matched in Python rather than SQL: supplier names on an order and on a
        # register page differ by punctuation and suffixes far more often than
        # they differ by spelling, and LIKE would miss "AMS Garments" against
        # "A.M.S. GARMENTS". Substring either way is the same forgiving test the
        # rest of the app searches with.
        rows = [r for r in rows
                if name in (r.supplier_name or "").lower()
                or (r.supplier_name or "").lower() in name] or rows
    return rows


# ---------------------------------------------------------------------------
#  serialisation
# ---------------------------------------------------------------------------
def line_out(l: models.PurchaseOrderLine) -> dict:
    return {"id": l.id, "particulars": l.particulars, "size": l.size,
            "qty": l.qty, "uom": l.uom, "rate": l.rate, "amount": l.amount,
            "brand": l.brand, "design_no": l.design_no, "hsn": l.hsn,
            "notes": l.notes}


def out(po: models.PurchaseOrder, db: Session = None, with_lines=True) -> dict:
    d = {
        "id": po.id, "po_no": po.po_no,
        "po_date": po.po_date, "po_date_display": dates.to_display(po.po_date),
        "warehouse_id": po.warehouse_id,
        "supplier_id": po.supplier_id, "supplier_name": po.supplier_name,
        "company": po.company, "brand": po.brand, "item": po.item,
        "place": po.place, "transport": po.transport, "agent": po.agent,
        "purchaser": po.purchaser, "discount_pct": po.discount_pct,
        "subtotal": po.subtotal, "discount_amount": po.discount_amount,
        "total": po.total,
        "status": po.status, "entry_source": po.entry_source,
        "document_id": po.document_id, "notes": po.notes,
        "editable": po.status in EDITABLE,
        "can_receive": po.status == "confirmed",
        "confirmed_at": po.confirmed_at.isoformat() if po.confirmed_at else None,
        "confirmed_by": po.confirmed_by,
        "cancelled_at": po.cancelled_at.isoformat() if po.cancelled_at else None,
        "cancel_reason": po.cancel_reason,
        "created_at": po.created_at.isoformat() if po.created_at else None,
        "line_count": len(po.lines),
        # What is stopping this order being confirmed, so the button can explain
        # itself instead of failing on a press — the same courtesy /api/voice/status
        # pays the Tamil mic.
        "blockers": missing_to_confirm(po),
    }
    if with_lines:
        d["lines"] = [line_out(l) for l in po.lines]
    if db is not None:
        d["linked_lr_count"] = db.query(models.LREntry).filter(
            models.LREntry.purchase_order_id == po.id).count()
    return d
