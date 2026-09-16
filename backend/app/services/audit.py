"""The audit trail: every change anybody made, as one plain sentence each.

    10:42 — Kumar (Admin) changed MRP of ESSA-00293 from ₹1,299 to ₹1,399 — Erode
    10:48 — Ravi (User) posted GRN GRN-2026-00045 — 50 units, ₹42,000 — Erode

WHERE IT IS WRITTEN
-------------------
Once, in the auth middleware (security.auth_middleware), for every write that
reaches a route — not by each route remembering to. The middleware already knows
who is asking, which screen the path belongs to and what the method does to it
(security.POLICY), so a route added next month is on the trail the day it ships.

A generic line ("added a supplier") is always possible from that alone. The
sentences worth reading need the document: which GRN, how many units, from whom.
Those are looked up AFTER the route has done its work, from the row it left
behind — see DESCRIBERS — so the route files need not change to be described.
A route can still say something only it knows by setting `request.state.audit`
(the user-management routes do: nothing after the fact remembers the old role).

WHAT IS NOT WRITTEN
-------------------
Reads. Opening a screen is not an act, and a trail of every page view buries the
dozen lines a day somebody will actually look for. Nor the writes that change
nothing: previewing a price change, asking a question, marking the bell read.

A REFUSED change IS written, with outcome "refused". Somebody repeatedly trying
to post returns they are not allowed to is exactly the exception this is for.

Never raises. A trail that could fail a GRN post would be worse than no trail.
"""
import datetime as dt
import re

from .. import models
from . import business_day
from .figures import inr, qty as fmt_qty

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

#: Writes that are not acts worth a line.
SKIP = [re.compile(p) for p in (
    r"^/api/auth/",                  # sign-ins are written by the auth router, with the outcome
    r"^/api/notifications/(read|read-all|mute)",
    r"^/api/voice/",
    r"^/api/command/ask",
    r"^/api/reports/ask",
    r"^/api/pricing/preview",
    r"^/api/physical-audit/preview",
    r"^/api/purchase-orders/extract",
    r"^/api/lr/extract",
    r"^/api/admin/boot",
)]

VERB = {"create": "added", "modify": "changed", "delete": "deleted",
        "print": "printed", "view": "used"}

#: What a screen's records are called, for the generic sentence.
NOUN = {
    "purchases": "a GRN", "documents": "a supplier invoice", "lr": "an LR entry",
    "purchase_orders": "a purchase order", "outward": "a stock transfer",
    "inward": "a stock receipt", "payments": "a supplier payment",
    "returns": "a debit note", "masters": "a master list", "suppliers": "a supplier",
    "catalogues": "a catalogue", "locations": "a location",
    "labels": "a label template", "labelprint": "labels", "pricing": "prices",
    "deadstock": "a clearance campaign", "stock_audit": "a warehouse stock audit",
    "physical_audit": "a physical stock audit", "inventory": "stock",
    "users": "an account", "dashboard": "the dashboard", "locator": "an item",
    "central": "the central dashboard", "command": "the command center",
    "audit": "the audit trail", "ask": "a question",
}

SCREEN_LABEL = {
    "purchases": "GRN", "documents": "Invoice Entry", "lr": "LR Entry",
    "purchase_orders": "Purchase Orders", "outward": "Stock Outward",
    "inward": "Stock Inward", "payments": "Payments", "returns": "Returns",
    "masters": "Masters", "suppliers": "Suppliers", "catalogues": "Catalogues",
    "locations": "Locations", "labels": "Label Designer",
    "labelprint": "Label Printing", "pricing": "Price Changer",
    "deadstock": "Dead Stock", "stock_audit": "Warehouse Stock Audit",
    "physical_audit": "Physical Stock Audit", "inventory": "Inventory",
    "users": "Users & Access", "dashboard": "Dashboard", "locator": "Item Locator",
    "central": "Central Dashboard", "command": "Command Center",
    "audit": "Audit Trail", "ask": "Ask Anything", "session": "Sign-in",
    "settings": "Server Settings",
}


def skipped(path: str) -> bool:
    return any(rx.match(path) for rx in SKIP)


# ---------------------------------------------------------------------------
#  Sentences from the row a route left behind
# ---------------------------------------------------------------------------
def _latest(db, model, **eq):
    q = db.query(model)
    for k, v in eq.items():
        q = q.filter(getattr(model, k) == v)
    return q.order_by(model.id.desc()).first()


def _wh(db, wid):
    if not wid:
        return None
    w = db.get(models.Warehouse, int(wid))
    return w.name if w else None


def _grn_post(db, m, before, who):
    p = db.get(models.Purchase, int(m.group(1)))
    if not p:
        return None
    units = sum(float(l.qty or 0) for l in p.lines)
    supplier = p.supplier.name if p.supplier else None
    return {"summary": f"posted GRN {p.grn_no or '#' + str(p.id)} — "
                       f"{fmt_qty(units)} units, {inr(p.grand_total)}"
                       + (f" from {supplier}" if supplier else ""),
            "ref": p.grn_no, "warehouse_id": p.warehouse_id}


def _grn_unpost(db, m, before, who):
    p = db.get(models.Purchase, int(m.group(1)))
    if not p:
        return None
    return {"summary": f"unposted GRN {p.grn_no or '#' + str(p.id)} — its stock was taken back out",
            "ref": p.grn_no, "warehouse_id": p.warehouse_id}


def _grn_before(db, m):
    p = db.get(models.Purchase, int(m.group(1)))
    return {"grn": p.grn_no, "invoice": p.invoice_number, "wid": p.warehouse_id} if p else {}


def _grn_delete(db, m, before, who):
    before = before or {}
    return {"summary": f"deleted draft GRN {before.get('grn') or '#' + m.group(1)}"
                       + (f" (invoice {before['invoice']})" if before.get("invoice") else ""),
            "ref": before.get("grn"), "warehouse_id": before.get("wid")}


def _grn_from_doc(db, m, before, who):
    p = _latest(db, models.Purchase, document_id=int(m.group(1)))
    if not p:
        return None
    return {"summary": f"raised GRN {p.grn_no or '#' + str(p.id)} against invoice "
                       f"{p.invoice_number or '—'}", "ref": p.grn_no,
            "warehouse_id": p.warehouse_id}


def _outward_post(db, m, before, who):
    o = db.get(models.StockOutward, int(m.group(1)))
    if not o:
        return None
    src = o.from_warehouse.name if o.from_warehouse else (o.from_location or "the warehouse")
    return {"summary": f"dispatched {o.code or 'transfer #' + str(o.id)} — "
                       f"{fmt_qty(o.total_qty)} units from {src} to {o.to_destination or '—'}",
            "ref": o.code, "warehouse_id": o.from_warehouse_id}


def _outward_receive(db, m, before, who):
    o = db.get(models.StockOutward, int(m.group(1)))
    if not o:
        return None
    short = o.shortfall
    return {"summary": f"received {o.code or 'transfer #' + str(o.id)} at "
                       f"{o.to_destination or '—'} — {fmt_qty(o.total_accepted)} of "
                       f"{fmt_qty(o.total_qty)} units"
                       + (f", {fmt_qty(short)} short" if short and short > 0 else ""),
            "ref": o.code, "warehouse_id": o.to_warehouse_id or o.from_warehouse_id}


def _outward_new(db, m, before, who):
    o = _latest(db, models.StockOutward)
    if not o:
        return None
    return {"summary": f"packed transfer {o.code or '#' + str(o.id)} to "
                       f"{o.to_destination or '—'} — {fmt_qty(o.total_qty)} units (draft)",
            "ref": o.code, "warehouse_id": o.from_warehouse_id}


def _payment(db, m, before, who):
    p = _latest(db, models.Payment)
    if not p:
        return None
    supplier = p.supplier.name if p.supplier else "a supplier"
    return {"summary": f"paid {inr(p.paid_amount)} to {supplier} by {p.mode or 'payment'}"
                       f" — receipt {p.receipt_no}", "ref": p.receipt_no}


def _return_post(db, m, before, who):
    r = db.get(models.PurchaseReturn, int(m.group(1)))
    if not r:
        return None
    supplier = r.supplier.name if r.supplier else "the supplier"
    wid = r.purchase.warehouse_id if r.purchase else None
    return {"summary": f"posted debit note {r.code or '#' + str(r.id)} — "
                       f"{inr(r.total)} back to {supplier}", "ref": r.code, "warehouse_id": wid}


def _return_new(db, m, before, who):
    p = db.get(models.Purchase, int(m.group(1)))
    return {"summary": "raised a debit note against GRN "
                       f"{(p.grn_no if p else None) or '#' + m.group(1)} (draft)",
            "ref": p.grn_no if p else None, "warehouse_id": p.warehouse_id if p else None}


_FIELD_WORD = {"mrp": "MRP", "sale_price": "selling price",
               "sale_discount_pct": "sale discount"}


def _price_apply(db, m, before, who):
    q = db.query(models.PriceRevision)
    if who:
        q = q.filter(models.PriceRevision.created_by == who)
    r = q.order_by(models.PriceRevision.id.desc()).first()
    if not r:
        return None
    field = _FIELD_WORD.get(r.field, r.field or "price")
    if r.product_count == 1 and r.changes:
        c = r.changes[0]
        money = r.field != "sale_discount_pct"
        was = inr(c.old_value) if money else f"{c.old_value or 0:g}%"
        now_ = inr(c.new_value) if money else f"{c.new_value or 0:g}%"
        return {"summary": f"changed {field} of {c.sku or c.description} from {was} to {now_}"
                           f" — {r.number}", "ref": r.number}
    how = {"percent": f"{r.value:+g}%", "amount": f"{inr(r.value)} each",
           "set": f"to {inr(r.value)}", "discount_off_mrp": f"to MRP less {r.value:g}%"}
    return {"summary": f"changed {field} of {r.product_count} products "
                       f"{how.get(r.operation, '')} — {r.number}".replace("  ", " "),
            "ref": r.number}


def _price_revert(db, m, before, who):
    r = db.get(models.PriceRevision, int(m.group(1)))
    if not r:
        return None
    return {"summary": f"put back price change {r.number} on {r.product_count} product(s)",
            "ref": r.number}


def _physical_apply(db, m, before, who):
    a = db.get(models.PhysicalAudit, int(m.group(1)))
    if not a:
        return None
    return {"summary": f"corrected the books from physical stock audit {a.code or '#' + str(a.id)}",
            "ref": a.code, "warehouse_id": getattr(a, "warehouse_id", None)}


def _doc_confirm(db, m, before, who):
    d = db.get(models.Document, int(m.group(1)))
    if not d:
        return None
    supplier = d.supplier.name if d.supplier else None
    return {"summary": "confirmed a supplier invoice"
                       + (f" from {supplier}" if supplier else "") + f" ({d.filename})",
            "warehouse_id": d.warehouse_id}


def _lr_new(db, m, before, who):
    e = _latest(db, models.LREntry)
    if not e:
        return None
    return {"summary": f"entered LR {e.lr_no or '—'} ({e.lr_entry_no or '#' + str(e.id)})"
                       + (f" from {e.supplier_name}" if e.supplier_name else ""),
            "ref": e.lr_entry_no or e.lr_no, "warehouse_id": getattr(e, "warehouse_id", None)}


def _lr_receive(db, m, before, who):
    e = db.get(models.LREntry, int(m.group(1)))
    if not e:
        return None
    return {"summary": f"received consignment LR {e.lr_no or '—'} ({e.lr_entry_no or '#' + str(e.id)})",
            "ref": e.lr_entry_no or e.lr_no, "warehouse_id": getattr(e, "warehouse_id", None)}


def _po_new(db, m, before, who):
    p = _latest(db, models.PurchaseOrder)
    if not p:
        return None
    return {"summary": f"raised purchase order {p.po_no or '#' + str(p.id)}"
                       + (f" to {p.supplier_name}" if p.supplier_name else ""),
            "ref": p.po_no, "warehouse_id": p.warehouse_id}


def _po_status(db, m, before, who):
    p = db.get(models.PurchaseOrder, int(m.group(1)))
    if not p:
        return None
    return {"summary": f"marked purchase order {p.po_no or '#' + str(p.id)} {p.status}",
            "ref": p.po_no, "warehouse_id": p.warehouse_id}


def _place(kind, model):
    def before(db, m):
        row = db.get(model, int(m.group(1)))
        return {"name": row.name} if row else {}

    def after_change(db, m, before, who):
        row = db.get(model, int(m.group(1)))
        name = (row.name if row else None) or (before or {}).get("name") or f"#{m.group(1)}"
        return {"summary": f"changed {kind} {name}"}

    def after_delete(db, m, before, who):
        name = (before or {}).get("name") or f"#{m.group(1)}"
        row = db.get(model, int(m.group(1)))
        closed = row is not None and getattr(row, "active", True) is False
        return {"summary": f"{'closed' if closed else 'removed'} {kind} {name}"}

    def after_new(db, m, before, who):
        row = _latest(db, model)
        return {"summary": f"added {kind} {row.name}" if row else f"added a {kind}"}
    return before, after_change, after_delete, after_new


def _settings(what):
    def fn(db, m, before, who):
        return {"summary": what}
    return fn


def _clear_all(db, m, before, who):
    return {"summary": "CLEARED ALL transaction data — documents, GRNs, stock, "
                       "transfers, returns and payments"}


def _adjust(db, m, before, who):
    p = db.get(models.Product, int(m.group(1)))
    if not p:
        return None
    return {"summary": f"adjusted the stock of {p.sku or p.description} by hand "
                       f"(now {fmt_qty(p.stock_qty)})", "ref": p.sku}


_W_BEFORE, _W_CHANGE, _W_DELETE, _W_NEW = _place("warehouse", models.Warehouse)
_S_BEFORE, _S_CHANGE, _S_DELETE, _S_NEW = _place("store", models.Store)
_T_BEFORE, _T_CHANGE, _T_DELETE, _T_NEW = _place("POS counter", models.PosTerminal)

#: (method, path pattern, before-hook or None, describer). First match wins.
DESCRIBERS = [
    ("POST", r"^/api/purchases/(\d+)/post$", None, _grn_post),
    ("POST", r"^/api/purchases/(\d+)/unpost$", None, _grn_unpost),
    ("DELETE", r"^/api/purchases/(\d+)$", _grn_before, _grn_delete),
    ("POST", r"^/api/purchases/from-document/(\d+)$", None, _grn_from_doc),
    ("POST", r"^/api/outward/(\d+)/post$", None, _outward_post),
    ("POST", r"^/api/outward/(\d+)/receive$", None, _outward_receive),
    ("POST", r"^/api/outward$", None, _outward_new),
    ("POST", r"^/api/payments$", None, _payment),
    ("POST", r"^/api/returns/(\d+)/post$", None, _return_post),
    ("POST", r"^/api/returns/from-purchase/(\d+)$", None, _return_new),
    ("POST", r"^/api/pricing/apply$", None, _price_apply),
    ("POST", r"^/api/pricing/revisions/(\d+)/revert$", None, _price_revert),
    ("POST", r"^/api/physical-audit/(\d+)/apply$", None, _physical_apply),
    ("POST", r"^/api/documents/(\d+)/confirm$", None, _doc_confirm),
    ("DELETE", r"^/api/documents/clear-all$", None, _clear_all),
    ("POST", r"^/api/lr(/save)?$", None, _lr_new),
    ("POST", r"^/api/lr/(\d+)/receive$", None, _lr_receive),
    ("POST", r"^/api/purchase-orders$", None, _po_new),
    ("POST", r"^/api/purchase-orders/(\d+)/status$", None, _po_status),
    ("POST", r"^/api/locations/warehouses$", None, _W_NEW),
    ("PATCH", r"^/api/locations/warehouses/(\d+)$", _W_BEFORE, _W_CHANGE),
    ("DELETE", r"^/api/locations/warehouses/(\d+)$", _W_BEFORE, _W_DELETE),
    ("POST", r"^/api/locations/stores$", None, _S_NEW),
    ("PATCH", r"^/api/locations/stores/(\d+)$", _S_BEFORE, _S_CHANGE),
    ("DELETE", r"^/api/locations/stores/(\d+)$", _S_BEFORE, _S_DELETE),
    ("POST", r"^/api/locations/terminals$", None, _T_NEW),
    ("PATCH", r"^/api/locations/terminals/(\d+)$", _T_BEFORE, _T_CHANGE),
    ("DELETE", r"^/api/locations/terminals/(\d+)$", _T_BEFORE, _T_DELETE),
    ("POST", r"^/api/settings/vision$", None, _settings("turned on the AI key for reading invoices and questions")),
    ("POST", r"^/api/settings/vision/off$", None, _settings("turned off the AI key")),
    ("POST", r"^/api/settings/model$", None, _settings("changed the AI model")),
    ("POST", r"^/api/inventory/products/(\d+)/adjust-stock$", None, _adjust),
    ("POST", r"^/api/documents/upload$", None, _settings("uploaded a supplier invoice to be read")),
]
DESCRIBERS_RE = [(meth, re.compile(p), b, a) for meth, p, b, a in DESCRIBERS]


def match(method: str, path: str):
    """(before-hook, describer, match) for a request, or (None, None, None)."""
    for meth, rx, before, after in DESCRIBERS_RE:
        if meth == method:
            m = rx.match(path)
            if m:
                return before, after, m
    return None, None, None


def generic(screen, action, path):
    """The sentence when nothing more specific is known."""
    noun = NOUN.get(screen or "", None)
    verb = VERB.get(action, action or "changed")
    if noun:
        return f"{verb} {noun}"
    return f"{verb} {path}"


# ---------------------------------------------------------------------------
#  Writing
# ---------------------------------------------------------------------------
def record(db, *, user=None, username=None, full_name=None, role=None,
           method=None, path=None, screen=None, action=None, outcome="ok",
           status=None, warehouse_id=None, summary=None, ref=None):
    """Write one event. Commits its own row; never raises."""
    try:
        if user:
            username = username or user.get("username")
            full_name = full_name or user.get("full_name")
            role = role or user.get("role")
        wid = int(warehouse_id) if str(warehouse_id or "").isdigit() else None
        db.add(models.AuditEvent(
            username=username, full_name=full_name or None, role=role,
            method=method, path=(path or "")[:300], screen=screen, action=action,
            outcome=outcome, status=status, warehouse_id=wid,
            warehouse_name=_wh(db, wid), summary=(summary or "")[:500],
            ref=(ref or None) and str(ref)[:80]))
        db.commit()
    except Exception:                                  # noqa: BLE001
        try:
            db.rollback()
        except Exception:                              # noqa: BLE001
            pass


def note(request, summary, ref=None, warehouse_id=None, action=None):
    """What a route knows that nothing after the fact can: set the sentence."""
    try:
        request.state.audit = {"summary": summary, "ref": ref,
                               "warehouse_id": warehouse_id, "action": action}
    except Exception:                                  # noqa: BLE001
        pass


def describe(db, method, path, screen, action, before, who, noted=None):
    """The sentence, ref and warehouse for a write that has just happened."""
    if noted and noted.get("summary"):
        return noted
    _b, after, m = match(method, path)
    if after is not None:
        try:
            out = after(db, m, before, who)
            if out and out.get("summary"):
                return out
        except Exception:                              # noqa: BLE001
            pass
    return {"summary": generic(screen, action, path)}


# ---------------------------------------------------------------------------
#  Reading
# ---------------------------------------------------------------------------
def event_out(e):
    from .users import ROLE_LABEL
    at_local = business_day.local(e.at)
    return {
        "id": e.id,
        "at": e.at.isoformat() if e.at else None,
        "local": at_local.isoformat(timespec="minutes") if at_local else None,
        "username": e.username, "full_name": e.full_name or "",
        "who": e.full_name or e.username or "someone",
        "role": e.role, "role_label": ROLE_LABEL.get(e.role or "", e.role or ""),
        "screen": e.screen, "module": SCREEN_LABEL.get(e.screen or "", e.screen or ""),
        "action": e.action, "outcome": e.outcome, "status": e.status,
        "warehouse_id": e.warehouse_id, "warehouse": e.warehouse_name,
        "summary": e.summary, "ref": e.ref,
    }


def feed(db, *, limit=50, before_id=None, username=None, screen=None,
         warehouse_id=None, date_from=None, date_to=None, q=None, outcome=None,
         allowed_warehouses=None, hide_screens=()):
    """Newest first. `allowed_warehouses` confines a restricted account to its
    buildings — events that name no warehouse (a supplier added, a sign-in) are
    shown only to an unrestricted reader, because they are company-wide."""
    E = models.AuditEvent
    qry = db.query(E)
    if before_id:
        qry = qry.filter(E.id < int(before_id))
    if username:
        qry = qry.filter(E.username == username)
    if screen:
        qry = qry.filter(E.screen == screen)
    if outcome:
        qry = qry.filter(E.outcome == outcome)
    if warehouse_id:
        qry = qry.filter(E.warehouse_id == int(warehouse_id))
    if allowed_warehouses:
        qry = qry.filter(E.warehouse_id.in_(list(allowed_warehouses)))
    if hide_screens:
        qry = qry.filter((E.screen.is_(None)) | (~E.screen.in_(list(hide_screens))))
    d0, d1 = business_day.parse_day(date_from), business_day.parse_day(date_to)
    if d0 or d1:
        start, end = business_day.bounds(d0 or d1, d1 or d0)
        qry = qry.filter(E.at >= start, E.at < end)
    if q:
        like = f"%{q.strip()}%"
        qry = qry.filter((E.summary.ilike(like)) | (E.username.ilike(like))
                         | (E.full_name.ilike(like)) | (E.ref.ilike(like)))
    limit = max(1, min(int(limit or 50), 500))
    rows = qry.order_by(E.id.desc()).limit(limit + 1).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return {"events": [event_out(e) for e in rows],
            "more": more, "next_before": rows[-1].id if (more and rows) else None}


def counts_since(db, since, allowed_warehouses=None):
    """{"changes": n, "refused": n, "people": n} since a UTC instant."""
    E = models.AuditEvent
    qry = db.query(E).filter(E.at >= since)
    if allowed_warehouses:
        qry = qry.filter(E.warehouse_id.in_(list(allowed_warehouses)))
    rows = qry.all()
    return {"changes": sum(1 for r in rows if r.outcome == "ok" and r.action not in ("signin", "signout")),
            "refused": sum(1 for r in rows if r.outcome == "refused"),
            "failed_signins": sum(1 for r in rows if r.action == "signin" and r.outcome == "failed"),
            "people": len({r.username for r in rows if r.username and r.outcome == "ok"})}
