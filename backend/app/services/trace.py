"""Follow one thing end to end: Supplier → LR → Invoice → GRN → Warehouse →
Store → POS → Sold → Returned → what is left.

A code arrives with no label on it. It might be a product QR, a piece code off
one garment, a GRN number, a supplier's invoice number, a transfer code, an LR
number or a store bill — and the person typing it rarely knows which. So every
kind is tried, exact matches on document numbers first (they are cheap and they
are unambiguous when they hit), then the Item Locator's own resolver for
anything printed on a tag.

When one code is two things — a supplier invoice numbered INV-000123 and a store
bill numbered INV-000123 both exist — both are offered rather than one chosen:
guessing is how somebody reads a supplier's bill as their own sale.

The chain is a list of steps, each {stage, title, detail, when, ref, qty, state},
drawn by the screen as a vertical line of cards. `state` is done / pending / warn
/ none, so a GRN still in draft or a transfer still in transit reads as such.
"""
from sqlalchemy import func

from .. import models
from . import business_day, pos_insights
from .figures import inr, qty as fmt_qty


def _when(value):
    """A step's time as the business reads it.

    A TIMESTAMP (stored UTC — posted_at, created_at, or its ISO string) becomes
    the business's local wall-clock time; a plain DATE (an invoice date the
    supplier printed) is already local and is left as it is."""
    import datetime as dt
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return business_day.local(value).isoformat(timespec="minutes")
    if isinstance(value, dt.date):
        return value.isoformat()
    s = str(value)
    if len(s) > 10 and s[4] == "-" and s[10] in "T ":
        local = business_day.local(s)
        return local.isoformat(timespec="minutes") if local else s[:16]
    return s


def _step(stage, title, detail="", when=None, ref=None, qty=None, state="done", open_=None):
    return {"stage": stage, "title": title or "—", "detail": detail or "",
            "when": _when(when), "ref": ref, "qty": qty, "state": state, "open": open_}


def _eq(col, code):
    return func.upper(col) == code.upper()


def candidates(db, code):
    """Every document this code names exactly: [{kind, id, label}]."""
    code = (code or "").strip()
    if not code:
        return []
    out = []
    for p in db.query(models.Purchase).filter(_eq(models.Purchase.grn_no, code)).limit(3):
        out.append({"kind": "grn", "id": p.id, "label": f"GRN {p.grn_no}"})
    if not out:
        for p in db.query(models.Purchase).filter(_eq(models.Purchase.invoice_number, code)).limit(5):
            sup = p.supplier.name if p.supplier else "supplier"
            out.append({"kind": "grn", "id": p.id,
                        "label": f"Supplier invoice {p.invoice_number} ({sup}) → GRN {p.grn_no or '#' + str(p.id)}"})
    for o in db.query(models.StockOutward).filter(_eq(models.StockOutward.code, code)).limit(3):
        out.append({"kind": "transfer", "id": o.id, "label": f"Transfer {o.code}"})
    for r in db.query(models.PurchaseReturn).filter(_eq(models.PurchaseReturn.code, code)).limit(3):
        out.append({"kind": "debit_note", "id": r.id, "label": f"Debit note {r.code}"})
    for e in (db.query(models.LREntry)
                .filter(_eq(models.LREntry.lr_entry_no, code) | _eq(models.LREntry.lr_no, code))
                .limit(3)):
        out.append({"kind": "lr", "id": e.id, "label": f"LR {e.lr_no or '—'} ({e.lr_entry_no or '#' + str(e.id)})"})
    bill = pos_insights.find_bill(code)
    if bill:
        out.append({"kind": "bill", "id": bill["bill_no"], "label": f"Store bill {bill['bill_no']}"})
    return out


def find(db, code, kind=None, ref_id=None, allowed=None):
    """The trace for a code, or {"ok": False, "choices": [...]} when it is ambiguous,
    or {"ok": False, "line": why} when it names nothing."""
    code = (code or "").strip()
    if not code and not ref_id:
        return {"ok": False, "line": "Give a code to track — a QR, SKU, GRN, invoice, transfer or bill number."}

    if kind and ref_id is not None:
        return _by_kind(db, kind, ref_id, allowed, code)

    found = candidates(db, code)
    kinds = {c["kind"] for c in found}
    if len(found) == 1 or (len(kinds) == 1 and len(found) >= 1 and found[0]["kind"] != "grn"):
        c = found[0]
        return _by_kind(db, c["kind"], c["id"], allowed, code)
    if len(found) > 1:
        return {"ok": False, "intent": "trace", "code": code, "choices": found,
                "line": f"“{code}” matches {len(found)} records — pick the one you mean."}
    return product(db, code, allowed)


def _by_kind(db, kind, ref_id, allowed, code):
    if kind == "grn":
        p = db.get(models.Purchase, int(ref_id))
        return grn(db, p, allowed) if p else _missing(code)
    if kind == "transfer":
        o = db.get(models.StockOutward, int(ref_id))
        return transfer(db, o, allowed) if o else _missing(code)
    if kind == "debit_note":
        r = db.get(models.PurchaseReturn, int(ref_id))
        return grn(db, r.purchase, allowed, focus_return=r) if (r and r.purchase) else _missing(code)
    if kind == "lr":
        e = db.get(models.LREntry, int(ref_id))
        return lr(db, e, allowed) if e else _missing(code)
    if kind == "bill":
        return bill(db, str(ref_id), allowed)
    if kind == "product":
        return product(db, code or str(ref_id), allowed, product_id=int(ref_id) if str(ref_id).isdigit() else 0)
    return _missing(code)


def _missing(code):
    return {"ok": False, "intent": "trace", "code": code,
            "line": f"Nothing matches “{code}” — not a QR, SKU, piece code, GRN, "
                    "supplier invoice, transfer, LR or bill number."}


def _refused(what):
    return {"ok": False, "intent": "trace",
            "line": f"{what} belongs to a warehouse you are not allotted."}


# ---------------------------------------------------------------------------
#  A GRN, with the supplier, lorry and invoice behind it and the money after it
# ---------------------------------------------------------------------------
def grn(db, p, allowed=None, focus_return=None):
    from . import locator, payments as pay_svc
    if allowed and p.warehouse_id and p.warehouse_id not in allowed:
        return _refused(f"GRN {p.grn_no or p.id}")
    supplier = p.supplier.name if p.supplier else None
    lorry = locator.consignment_of(db, p)
    units = sum(float(l.qty or 0) for l in p.lines)
    moved = sum(float(m.qty_delta or 0) for m in db.query(models.StockMovement).filter(
        models.StockMovement.ref_type == "purchase", models.StockMovement.ref_id == p.id,
        models.StockMovement.kind == "inward"))
    returns = db.query(models.PurchaseReturn).filter(models.PurchaseReturn.purchase_id == p.id).all()
    settled = pay_svc.invoice_settled(db, p.id)
    owed = pay_svc.invoice_outstanding(db, p) if p.status == "posted" else None

    gstin = getattr(p.supplier, "gstin", None) if p.supplier else None
    steps = [
        _step("Supplier", supplier or "Supplier not recorded",
              f"GSTIN {gstin}" if gstin else "", state="done" if supplier else "warn"),
        _step("LR Entry", f"LR {lorry['lr_no']}" if lorry else "No consignment linked",
              (f"{lorry.get('transport') or ''} · received {lorry.get('recv_date') or '—'}"
               if lorry else "The transport register has no row tied to this invoice"),
              when=lorry.get("lr_date") if lorry else None,
              ref=lorry.get("lr_entry_no") if lorry else None,
              qty=lorry.get("qty") if lorry else None, state="done" if lorry else "none"),
        _step("Invoice", f"Invoice {p.invoice_number or '—'}", inr(p.grand_total, paise=True),
              when=p.invoice_date, ref=p.invoice_number),
        _step("GRN", f"GRN {p.grn_no or '#' + str(p.id)}",
              f"{len(p.lines)} line(s) · {fmt_qty(units)} units · {p.status}",
              when=p.posted_at, ref=p.grn_no, qty=units,
              state="done" if p.status == "posted" else "pending", open_="purchases"),
        _step("Stock Inward", p.warehouse.name if p.warehouse else "Warehouse not set",
              f"{fmt_qty(moved)} units became stock" if moved else "Nothing has entered stock yet",
              when=p.posted_at, qty=moved, state="done" if moved else "pending", open_="inventory"),
    ]
    if returns:
        total = sum(float(r.total or 0) for r in returns)
        steps.append(_step("Returned", f"{len(returns)} debit note(s)",
                           f"{inr(total)} back to the supplier"
                           + (f" — {focus_return.code}" if focus_return else ""),
                           when=max((r.posted_at for r in returns if r.posted_at), default=None),
                           ref=returns[-1].code, state="warn", open_="returns"))
    if p.status == "posted":
        steps.append(_step("Payment", "Paid in full" if (owed or 0) <= 0.01 else f"{inr(owed)} still owed",
                           f"{inr(settled)} settled so far", ref=None,
                           state="done" if (owed or 0) <= 0.01 else "pending", open_="payments"))

    rows = [{"SKU / barcode": l.barcode or "", "Description": l.description or "",
             "Qty": l.qty, "Rate": l.rate, "Amount": l.amount} for l in p.lines]
    line = (f"GRN {p.grn_no or '#' + str(p.id)}: {fmt_qty(units)} units from {supplier or 'an unrecorded supplier'}"
            f" worth {inr(p.grand_total)} — {p.status}"
            + (f", {inr(owed)} still owed" if owed and owed > 0.01 else ", paid" if p.status == "posted" else ""))
    return {"ok": True, "intent": "trace", "kind": "grn", "title": f"GRN {p.grn_no or p.id}",
            "line": line, "speak": line.replace("₹", "rupees "), "chain": steps,
            "columns": list(rows[0].keys()) if rows else [], "rows": rows,
            "open": {"tab": "purchases"}}


# ---------------------------------------------------------------------------
#  A transfer
# ---------------------------------------------------------------------------
def transfer(db, o, allowed=None):
    if allowed and not ({o.from_warehouse_id, o.to_warehouse_id} & set(allowed)):
        return _refused(f"Transfer {o.code or o.id}")
    src = o.from_warehouse.name if o.from_warehouse else (o.from_location or "Warehouse")
    steps = [
        _step("Warehouse", src, "where the goods left from", state="done"),
        _step("Packed", f"{fmt_qty(o.total_qty)} units on {len(o.lines)} line(s)",
              f"packed by {o.packed_by}" if o.packed_by else "", when=o.date, ref=o.code,
              qty=o.total_qty, open_="outward"),
        _step("Dispatched", o.to_destination or "—", "stock left the warehouse" if o.posted_at else "still a draft",
              when=o.posted_at, state="done" if o.posted_at else "pending"),
        _step("Received", o.to_destination or "—",
              (f"{fmt_qty(o.total_accepted)} accepted by {o.received_by or '—'}"
               + (f" · {fmt_qty(o.shortfall)} short" if o.shortfall else ""))
              if o.status == "received" else "not yet accepted — in transit" if o.posted_at else "",
              when=o.received_at, qty=o.total_accepted if o.status == "received" else None,
              state="done" if o.status == "received" and not o.shortfall
              else "warn" if o.status == "received" else "pending" if o.posted_at else "none",
              open_="inward"),
    ]
    rows = [{"SKU / barcode": l.barcode or "", "Description": l.description or "",
             "Sent": l.qty, "Accepted": l.accepted_qty} for l in o.lines]
    line = (f"Transfer {o.code or '#' + str(o.id)}: {fmt_qty(o.total_qty)} units from {src} to "
            f"{o.to_destination or '—'} — "
            + ("received" + (f", {fmt_qty(o.shortfall)} short" if o.shortfall else "") if o.status == "received"
               else "in transit" if o.status == "posted" else "draft"))
    return {"ok": True, "intent": "trace", "kind": "transfer", "title": f"Transfer {o.code or o.id}",
            "line": line, "speak": line, "chain": steps,
            "columns": list(rows[0].keys()) if rows else [], "rows": rows,
            "open": {"tab": "outward"}}


# ---------------------------------------------------------------------------
#  An LR entry — the lorry, and what it became
# ---------------------------------------------------------------------------
def lr(db, e, allowed=None):
    purchase = None
    if getattr(e, "invoice_document_id", None):
        purchase = (db.query(models.Purchase)
                      .filter(models.Purchase.document_id == e.invoice_document_id)
                      .order_by(models.Purchase.id.desc()).first())
    if purchase is not None:
        out = grn(db, purchase, allowed)
        if out.get("ok"):
            out["title"] = f"LR {e.lr_no or e.lr_entry_no}"
        return out
    steps = [
        _step("Supplier", e.supplier_name or "—"),
        _step("LR Entry", f"LR {e.lr_no or '—'}", f"{e.transport or ''}", when=e.lr_date,
              ref=e.lr_entry_no, qty=e.qty, open_="lr"),
        _step("Invoice", f"Invoice {e.inv_no or '—'}", "", when=e.inv_date, ref=e.inv_no,
              state="done" if e.inv_no else "pending"),
        _step("GRN", "No GRN yet", "the invoice has not been received against", state="pending"),
    ]
    line = f"LR {e.lr_no or e.lr_entry_no}: {fmt_qty(e.qty or 0)} pieces from {e.supplier_name or '—'} — no GRN posted yet"
    return {"ok": True, "intent": "trace", "kind": "lr", "title": f"LR {e.lr_no or e.lr_entry_no}",
            "line": line, "speak": line, "chain": steps, "columns": [], "rows": [],
            "open": {"tab": "lr"}}


# ---------------------------------------------------------------------------
#  A store bill
# ---------------------------------------------------------------------------
def bill(db, number, allowed=None):
    b = pos_insights.find_bill(number)
    if not b:
        return _missing(number)
    if allowed:
        ids = pos_insights.location_ids_for_warehouses(db, allowed) or []
        if b["location_id"] not in ids:
            return _refused(f"Bill {b['bill_no']}")
    units = sum(l["qty"] for l in b["lines"])
    returned = sum(r["total"] for r in b["returns"])
    steps = [
        _step("Store", b["branch"] or "Branch not recorded", " · ".join(x for x in (b["floor"], b["counter"]) if x)),
        _step("POS", b["counter"] or "Counter not recorded",
              f"billed by {b['cashier'] or '—'}" + (f", served by {b['staff']}" if b["staff"] else ""),
              when=b["at"], ref=b["bill_no"]),
        _step("Sale", f"{fmt_qty(units)} item(s) — {inr(b['total'], paise=True)}",
              ", ".join(f"{t['method']} {inr(t['amount'])}" for t in b["tenders"]) or (b["method"] or ""),
              qty=units, state="warn" if b["status"] == "cancelled" else "done"),
        _step("Customer", b["customer"] or "Walk-in", b["phone"] or ""),
    ]
    if b["returns"]:
        steps.append(_step("Returned", f"{len(b['returns'])} credit note(s)", inr(returned),
                           ref=b["returns"][-1]["number"], state="warn"))
    if b["status"] == "cancelled":
        steps.append(_step("Cancelled", "Bill cancelled", b["cancel_reason"] or "", state="warn"))
    rows = [{"SKU": l["sku"], "Product": l["product"], "Qty": l["qty"], "Rate": l["rate"],
             "Amount": l["amount"]} for l in b["lines"]]
    line = (f"Bill {b['bill_no']}: {inr(b['total'])} for {fmt_qty(units)} item(s) at "
            f"{b['counter'] or 'the counter'}{', ' + b['branch'] if b['branch'] else ''}"
            + (" — cancelled" if b["status"] == "cancelled" else "")
            + (f" — {inr(returned)} returned" if returned else ""))
    return {"ok": True, "intent": "trace", "kind": "bill", "title": f"Bill {b['bill_no']}",
            "line": line, "speak": line, "chain": steps,
            "columns": list(rows[0].keys()) if rows else [], "rows": rows, "open": None}


# ---------------------------------------------------------------------------
#  A product, piece or carton — through the Item Locator
# ---------------------------------------------------------------------------
def product(db, code, allowed=None, product_id=0):
    from fastapi import HTTPException
    from ..routers.inventory import locate

    try:
        data = locate(code=code, product_id=product_id, db=db)
    except HTTPException:
        return _missing(code)
    if data.get("kind") == "bundle":
        bd = data["bundle"]
        steps = [_step("GRN", f"GRN {bd.get('grn_no') or '—'}", f"invoice {bd.get('invoice_number') or '—'}"),
                 _step("Carton", bd.get("code") or code, f"{bd.get('location') or 'no rack recorded'} · {bd.get('status') or ''}",
                       qty=bd.get("qty"))]
        line = f"Carton {bd.get('code') or code}: {fmt_qty(bd.get('qty') or 0)} pieces at {bd.get('location') or 'no recorded rack'}"
        return {"ok": True, "intent": "trace", "kind": "bundle", "title": f"Carton {bd.get('code') or code}",
                "line": line, "speak": line, "chain": steps, "columns": [], "rows": [],
                "open": {"tab": "locator"}}

    p = data["product"]
    name = p.get("name") or p.get("description") or p.get("sku")
    receipts = data.get("receipts") or []
    posted = [r for r in receipts if r.get("status") == "posted"]
    first = receipts[-1] if receipts else None
    newest = receipts[0] if receipts else None
    lorry = data.get("consignment")
    received = sum(float(r.get("qty") or 0) for r in posted)
    ws = data.get("warehouse_stock") or {}
    sales = data.get("sales") or {}
    sold = sum(float(r["qty"]) for r in sales.get("rows", []) if r["kind"] == "sale")
    cust_ret = -sum(float(r["qty"]) for r in sales.get("rows", []) if r["kind"] == "return")
    transferred = sum(float(t.get("packed_qty") or 0) for t in (data.get("transfers") or [])
                      if t.get("status") != "draft")
    destinations = sorted({t.get("to") for t in (data.get("transfers") or []) if t.get("to")})
    from . import stock_locations as stock_loc
    balances = [b for b in stock_loc.balances_for(db, p["product_id"]) if b["qty"]]
    in_stores = pos_insights.store_stock_of(p["product_id"]) if pos_insights.available() else None
    unit = data.get("unit")

    steps = [
        _step("Supplier", (newest or {}).get("supplier") or p.get("supplier") or "Supplier not recorded"),
    ]
    if lorry:
        steps.append(_step("LR Entry", f"LR {lorry.get('lr_no') or '—'}", lorry.get("transport") or "",
                           when=lorry.get("lr_date"), ref=lorry.get("lr_entry_no"), qty=lorry.get("qty")))
    if newest:
        steps.append(_step("Invoice", f"Invoice {newest.get('invoice_number') or '—'}", "",
                           when=newest.get("invoice_date"), ref=newest.get("invoice_number")))
        steps.append(_step("GRN", f"GRN {newest.get('grn_no') or '—'}"
                           + (f" (+{len(receipts) - 1} more)" if len(receipts) > 1 else ""),
                           f"{fmt_qty(received)} received on posted GRN(s)",
                           when=newest.get("posted_at"), ref=newest.get("grn_no"), qty=received,
                           state="done" if posted else "pending", open_="purchases"))
    else:
        steps.append(_step("GRN", "No receipt found", "this item has no GRN line", state="warn"))
    steps.append(_step("Warehouse",
                       ", ".join(f"{b['warehouse_name']}: {fmt_qty(b['qty'])}" for b in balances) or "None in any warehouse",
                       f"{fmt_qty(ws.get('stock', p.get('stock_qty') or 0))} units on hand",
                       qty=ws.get("stock"), open_="inventory"))
    if transferred or destinations:
        steps.append(_step("Transferred", f"{fmt_qty(transferred)} units", " → ".join(destinations[:3]),
                           qty=transferred, open_="outward"))
    if sales.get("available"):
        steps.append(_step("Sold", f"{fmt_qty(sold)} units", f"on {sales.get('bills', 0)} bill(s)"
                           + (f", last {sales['last_sold']}" if sales.get("last_sold") else ""),
                           qty=sold, state="done" if sold else "none"))
        if cust_ret:
            steps.append(_step("Returned", f"{fmt_qty(cust_ret)} units", "brought back by customers",
                               qty=cust_ret, state="warn"))
    if ws.get("returned"):
        steps.append(_step("Returned to supplier", f"{fmt_qty(ws['returned'])} units", "on debit notes",
                           qty=ws["returned"], state="warn"))
    current = float(ws.get("stock") or 0) + float(in_stores or 0)
    steps.append(_step("Current Stock", f"{fmt_qty(current)} units",
                       f"{fmt_qty(ws.get('stock') or 0)} in warehouses"
                       + (f" + {fmt_qty(in_stores)} in stores" if in_stores is not None else ""),
                       qty=current))

    label = (f"Piece {unit['code']}" if unit else f"Product {p.get('sku') or code}")
    line = (f"{name} ({p.get('sku')}): received {fmt_qty(received)}"
            + (f", transferred {fmt_qty(transferred)}" if transferred else "")
            + (f", sold {fmt_qty(sold)}" if sales.get("available") else "")
            + (f", returned {fmt_qty(cust_ret)}" if cust_ret else "")
            + f" — current stock {fmt_qty(current)}")
    rows = [{"When": (m.get("at") or "")[:16].replace("T", " "), "Movement": m.get("kind"),
             "Qty": m.get("qty_delta"), "Balance": m.get("balance_after"), "Note": m.get("note") or ""}
            for m in (data.get("movements") or [])]
    return {"ok": True, "intent": "trace", "kind": "product", "title": f"{label} — {name}",
            "line": line, "speak": line, "chain": steps,
            "columns": list(rows[0].keys()) if rows else [], "rows": rows,
            "open": {"tab": "locator", "code": code}, "first_received": first and first.get("posted_at")}
