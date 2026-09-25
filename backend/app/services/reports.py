"""
Reporting service — read-only views over the structured data, mirroring the
report catalogue in the reference app's Warehouse Reports screen.

Each report returns {"columns", "rows", "totals", "note"} so the UI and the CSV
export can render any of them generically. `note` is where a report says what it
is counting when that is not obvious from its name — several of these look like
their counterpart in the reference app and are computed from what this system
actually records, which is not always the same thing. Saying so on the report is
better than letting someone infer it from a total that doesn't match.

WHAT IS NOT HERE, AND WHY
-------------------------
The reference catalogue carries a handful of reports this system has no data to
produce. They are deliberately absent rather than present-and-empty, because an
empty report reads as "nothing happened" when the truth is "nothing is recorded":

  * **Stock - Depreciation** — nothing depreciates stock here. There is no rate,
    method or asset register, and garments are held at weighted-average cost.
  * **Job Work Outward / Inward** — no job-work concept exists: goods sent to a
    processor and returned are not modelled at all.
  * **Invoice Vs Purchase Order** — purchase orders now exist (services/
    purchase_orders.py), so this is no longer absent for want of a data model.
    What it still lacks is the MATCHING: an order is linked to the consignment
    that arrives against it (LREntry.purchase_order_id), not yet to the invoice
    or the receipt, so there is no line-by-line ordered-vs-billed comparison to
    report. Kept on this list until there is.
  * **Retail Stock Analysis** — stock is held at one warehouse. A dispatch reduces
    it; what the receiving store then holds is not tracked here.
  * **Purchase Return (Cancelled)** — a return is draft or posted. Nothing is
    cancelled, so the report would always be empty.
  * **Transport Payment Report** — freight owed is recorded on the consignment,
    but transport *payments* are not: there is no transport ledger to report.

Adding any of them means adding the data first; see ARCHITECTURE.md §9.
"""
import datetime as dt
import inspect
from collections import defaultdict
from .. import models
from . import payments as pay
from . import shortages as short
from . import dates as date_svc
from . import stock_locations as stock_loc


def _rep(columns, rows, totals=None, note=None):
    return {"columns": columns, "rows": rows, "totals": totals or {},
            "note": note or ""}


def _f(v):
    return float(v or 0)


def _r2(v):
    return round(_f(v), 2)


# ---------------------------------------------------------------------------
#  Shared shapes
# ---------------------------------------------------------------------------
def _posted(db, date_from=None, date_to=None, lines=True, products=True):
    """Posted GRNs, optionally within an invoice-date range.

    Dates are stored ISO (services/dates.py), so this compares chronologically.

    Yielded a batch at a time, each GRN arriving with its supplier, lines, line
    products, breakdown rows (and their products) and shortages already read.
    The reports walk exactly those, and read lazily it was several queries per
    LINE — over a million on a full store."""
    from sqlalchemy.orm import joinedload, selectinload
    P, PL, S = models.Purchase, models.PurchaseLine, models.PurchaseLineSplit
    heads = (db.query(P.id, P.invoice_date).filter(P.status == "posted")
               .order_by(P.invoice_date, P.id).all())
    lo, hi = date_svc.to_iso(date_from), date_svc.to_iso(date_to)
    if lo or hi:
        heads = [h for h in heads
                 if (d := date_svc.to_iso(h.invoice_date))
                 and (not lo or d >= lo) and (not hi or d <= hi)]
    ids = [h.id for h in heads]
    for i in range(0, len(ids), 300):
        batch = ids[i:i + 300]
        # `lines=False` for the reports that read only the receipt's header —
        # the tax registers — which otherwise paid for every line of every GRN
        opts = [joinedload(P.supplier)]
        # `products=False` for a report that never reads the product (HSN)
        if lines and products:
            opts += [selectinload(P.lines).joinedload(PL.product),
                     selectinload(P.lines).selectinload(PL.splits).joinedload(S.product),
                     selectinload(P.lines).selectinload(PL.shortages)]
        elif lines:
            opts += [selectinload(P.lines).selectinload(PL.splits),
                     selectinload(P.lines).selectinload(PL.shortages)]
        loaded = {p.id: p for p in db.query(P).options(*opts).filter(P.id.in_(batch))}
        for pid in batch:
            yield loaded[pid]


def _eager(rel):
    """Load a many-to-one (a supplier, say) in the same read as its rows — a
    register that prints it per row otherwise asks for each one separately."""
    from sqlalchemy.orm import joinedload
    return joinedload(rel)


def _in_window(value, lo, hi):
    """The `(lo and (not d or d < lo)) or (hi and ...)` test the registers skip
    rows with, inverted: True when `value` falls inside [lo, hi]."""
    d = date_svc.to_iso(value)
    return not ((lo and (not d or d < lo)) or (hi and (not d or d > hi)))


class _LightLine:
    __slots__ = ("qty", "rate", "accepted_qty")

    def __init__(self, qty, rate, accepted_qty):
        self.qty, self.rate, self.accepted_qty = qty, rate, accepted_qty


class _NoteView:
    """A dispatch note with its lines as three plain columns each, for the
    registers that only total them. Everything else is the note's own; the three
    totals are StockOutward's properties, the same arithmetic on the same values."""

    def __init__(self, note, lines):
        self._note, self.lines = note, lines

    def __getattr__(self, name):
        return getattr(self._note, name)

    @property
    def total_qty(self):
        return sum((l.qty or 0) for l in self.lines)

    @property
    def total_accepted(self):
        if self._note.status != "received":
            return 0.0
        return sum((l.accepted_qty if l.accepted_qty is not None else (l.qty or 0))
                   for l in self.lines)

    @property
    def shortfall(self):
        if self._note.status != "received":
            return 0.0
        return round(self.total_qty - self.total_accepted, 3)


def _outwards_light(db, query, keep=None):
    """_outwards for the registers that only count and total lines: each note's
    lines read as (qty, rate, accepted) columns rather than as objects with their
    products — the difference between minutes and seconds over every dispatch."""
    from sqlalchemy.orm import joinedload
    SO, SL = models.StockOutward, models.StockOutwardLine
    notes = [o for o in query.options(joinedload(SO.from_warehouse), joinedload(SO.to_warehouse),
                                      joinedload(SO.to_store)).all()
             if keep is None or keep(o)]
    for n in range(0, len(notes), 2000):
        batch = notes[n:n + 2000]
        lines = defaultdict(list)
        for oid, qty, rate, acc in (db.query(SL.outward_id, SL.qty, SL.rate, SL.accepted_qty)
                                      .filter(SL.outward_id.in_([o.id for o in batch]))
                                      .order_by(SL.id)):
            lines[oid].append(_LightLine(qty, rate, acc))
        for o in batch:
            yield _NoteView(o, lines.get(o.id, []))


def _outwards(db, query, keep=None):
    """The notes `query` selects, in its order, that `keep(note)` accepts — a
    batch at a time, each with its lines (and places) already read. The reports
    total the lines of every note, and lazily that was a query per note: tens of
    thousands on a full store.

    `keep` is the report's own filter (its date window), applied BEFORE any line
    is read: loading every note's lines and then keeping one month's made a
    month's report slower than the per-note reads it replaced."""
    from sqlalchemy.orm import joinedload, selectinload
    SO = models.StockOutward
    notes = [o for o in query.all() if keep is None or keep(o)]
    for n in range(0, len(notes), 500):
        batch = notes[n:n + 500]
        (db.query(SO).options(
            selectinload(SO.lines).joinedload(models.StockOutwardLine.product),
            joinedload(SO.from_warehouse),
            joinedload(SO.to_warehouse), joinedload(SO.to_store))
           .filter(SO.id.in_([o.id for o in batch])).populate_existing().all())
        yield from batch


def _received_rows(purchase, with_product=True):
    """What a GRN actually took into stock, as (line, split|None, product, qty, rate).

    A broken-down bundle received its variants, not itself — the same rule
    services/returns.py uses to decide what can come back — so every report that
    counts received goods walks this rather than the invoice lines."""
    out = []
    for line in purchase.lines:
        holders = line.splits if line.is_split else [line]
        for h in holders:
            qty = _f(h.qty) if line.is_split else line.received_qty
            if qty <= 0:
                continue
            rate = _f(h.effective_rate if line.is_split else line.rate)
            # the product only when the caller reads it — loading it lazily per
            # holder, for a report that ignores it, was a query per line
            out.append((line, h if line.is_split else None,
                        h.product if with_product else None, qty, rate))
    return out


def _taxes(purchase):
    """The tax block off the invoice this GRN was built from ({} if none)."""
    doc = purchase.document
    ex = doc.latest_extraction if doc else None
    return ((ex.data or {}).get("taxes") or {}) if ex else {}


def stock_report(db):
    cols = ["sku", "supplier_barcode", "description", "hsn", "supplier", "uom", "stock_qty", "avg_cost", "stock_value"]
    from sqlalchemy.orm import joinedload
    rows, tqty, tval = [], 0.0, 0.0
    # supplier in the same read — lazily, a query per supplier
    for p in (db.query(models.Product).options(joinedload(models.Product.primary_supplier))
                .order_by(models.Product.description).all()):
        rows.append({"sku": p.sku, "supplier_barcode": p.barcode or "", "description": p.description,
                     "hsn": p.hsn or "", "supplier": p.primary_supplier.name if p.primary_supplier else "",
                     "uom": p.uom, "stock_qty": p.stock_qty, "avg_cost": round(p.avg_cost or 0, 2),
                     "stock_value": p.stock_value})
        tqty += p.stock_qty or 0; tval += p.stock_value
    return _rep(cols, rows, {"stock_qty": round(tqty, 2), "stock_value": round(tval, 2), "products": len(rows)})


def stock_movement(db, kind=None, product_id=None):
    cols = ["date", "sku", "description", "kind", "qty_delta", "rate", "balance_after", "ref", "note"]
    q = db.query(models.StockMovement)
    if kind:
        q = q.filter(models.StockMovement.kind == kind)
    if product_id:
        q = q.filter(models.StockMovement.product_id == product_id)
    rows = []
    P = models.Product
    # the product's two columns joined in, not a lazy load per product
    for m, pid, sku, description in (q.add_columns(P.id, P.sku, P.description)
                                       .outerjoin(P, P.id == models.StockMovement.product_id)
                                       .order_by(models.StockMovement.id)):
        found = pid is not None
        rows.append({"date": m.created_at.strftime("%Y-%m-%d") if m.created_at else "",
                     "sku": sku if found else "", "description": description if found else "",
                     "kind": m.kind, "qty_delta": m.qty_delta, "rate": round(m.rate or 0, 2),
                     "balance_after": m.balance_after, "ref": f"{m.ref_type or ''} {m.ref_id or ''}".strip(),
                     "note": m.note or ""})
    inward = sum(m["qty_delta"] for m in rows if m["qty_delta"] > 0)
    outward = sum(-m["qty_delta"] for m in rows if m["qty_delta"] < 0)
    return _rep(cols, rows, {"movements": len(rows), "total_in": round(inward, 2), "total_out": round(outward, 2)})


def purchase_register(db):
    cols = ["date", "supplier", "invoice_number", "taxable", "tax", "grand_total", "paid", "returns", "outstanding"]
    from sqlalchemy import func
    from sqlalchemy.orm import joinedload
    rows, tg, to = [], 0.0, 0.0
    # pay.invoice_settled / invoice_returns / invoice_outstanding, for every
    # invoice in two grouped reads instead of four queries per invoice — the
    # same figures, rounded the same way
    PA, PR = models.PaymentAllocation, models.PurchaseReturn
    settled_by = dict(db.query(PA.purchase_id, func.sum(func.coalesce(PA.settled, 0)))
                        .group_by(PA.purchase_id).all())
    returns_by = dict(db.query(PR.purchase_id, func.sum(func.coalesce(PR.total, 0)))
                        .filter(PR.status == "posted").group_by(PR.purchase_id).all())
    for p in (db.query(models.Purchase).options(joinedload(models.Purchase.supplier))
                .filter(models.Purchase.status == "posted")
                .order_by(models.Purchase.invoice_date, models.Purchase.id).all()):
        settled = round(float(settled_by.get(p.id) or 0), 2)
        rets = round(float(returns_by.get(p.id) or 0), 2)
        outstanding = round((p.grand_total or 0) - settled - rets, 2)
        rows.append({"date": p.invoice_date or "", "supplier": p.supplier.name if p.supplier else "",
                     "invoice_number": p.invoice_number, "taxable": round(p.taxable_total or 0, 2),
                     "tax": round(p.tax_total or 0, 2), "grand_total": round(p.grand_total or 0, 2),
                     "paid": settled, "returns": rets, "outstanding": outstanding})
        tg += p.grand_total or 0; to += outstanding
    return _rep(cols, rows, {"purchases": len(rows), "grand_total": round(tg, 2), "outstanding": round(to, 2)})


def purchase_return_register(db):
    cols = ["date", "code", "supplier", "invoice_number", "taxable", "tax", "total", "status"]
    rows, tt = [], 0.0
    for r in (db.query(models.PurchaseReturn).options(_eager(models.PurchaseReturn.supplier))
                .order_by(models.PurchaseReturn.id).all()):
        rows.append({"date": r.date or "", "code": r.code, "supplier": r.supplier.name if r.supplier else "",
                     "invoice_number": r.invoice_number or "", "taxable": round(r.taxable_total or 0, 2),
                     "tax": round(r.tax_total or 0, 2), "total": round(r.total or 0, 2), "status": r.status})
        if r.status == "posted":
            tt += r.total or 0
    return _rep(cols, rows, {"returns": len(rows), "total": round(tt, 2)})


def grn_shortage_register(db):
    """What suppliers billed and did not deliver, and what has become of it.

    The register a warehouse actually argues from: every gap counted at the dock,
    valued at the rate the supplier charged, with the ones still unanswered
    separated from the ones a debit note has settled or someone has waived. Draft
    GRNs are in here too — a shortage is worth chasing before the receipt posts,
    which is exactly when there is still time to ring the supplier."""
    cols = ["invoice_date", "supplier", "invoice_number", "grn_no", "description",
            "variant", "kind", "reason", "qty", "rate", "amount", "recorded_by",
            "claimed_qty", "status"]
    rows, tq, tv, oq, ov = [], 0.0, 0.0, 0.0, 0.0
    for r in short.register(db):
        rows.append({"invoice_date": r["invoice_date"] or "", "supplier": r["supplier"],
                     "invoice_number": r["invoice_number"], "grn_no": r["grn_no"] or "",
                     "description": r["description"] or "", "variant": r["variant"] or "",
                     "kind": r["kind"], "reason": r["reason"] or "",
                     "qty": r["qty"], "rate": r["rate"], "amount": r["amount"],
                     "recorded_by": r["recorded_by"] or "",
                     "claimed_qty": r["claimed_qty"], "status": r["status"]})
        if r["claimable"]:
            tq += r["qty"]; tv += r["amount"]
            if r["status"] in ("open", "part-claimed"):
                oq += r["open_qty"]
                ov += round(r["open_qty"] * r["rate"], 2)
    return _rep(cols, rows, {"shortages": len(rows), "short_qty": round(tq, 2),
                             "short_value": round(tv, 2),
                             "unclaimed_qty": round(oq, 2),
                             "unclaimed_value": round(ov, 2)})


def supplier_pending_bills(db):
    cols = ["supplier", "gstin", "pending_bills", "outstanding"]
    rows, tot = [], 0.0
    # pay.pending_bills once, grouped by supplier — called per supplier it read
    # every payment and return again for each of four thousand suppliers
    supplier_of = dict(db.query(models.Purchase.id, models.Purchase.supplier_id)
                         .filter(models.Purchase.status == "posted"))
    by_supplier = defaultdict(list)
    for b in pay.pending_bills(db, None):
        sid = supplier_of.get(b["purchase_id"])
        if sid is not None:
            by_supplier[sid].append(b)
    for s in db.query(models.Supplier).order_by(models.Supplier.name, models.Supplier.id).all():
        bills = by_supplier.get(s.id)
        if not bills:
            continue
        out = round(sum(b["outstanding"] for b in bills), 2)
        rows.append({"supplier": s.name, "gstin": s.gstin or "", "pending_bills": len(bills), "outstanding": out})
        tot += out
    return _rep(cols, rows, {"suppliers": len(rows), "outstanding": round(tot, 2)})


def payments_register(db):
    cols = ["date", "receipt_no", "supplier", "mode", "ref_no", "gross", "discount", "tds", "debit", "paid"]
    rows, tp = [], 0.0
    for p in (db.query(models.Payment).options(_eager(models.Payment.supplier))
                .order_by(models.Payment.id).all()):
        rows.append({"date": p.date or "", "receipt_no": p.receipt_no,
                     "supplier": p.supplier.name if p.supplier else "", "mode": p.mode,
                     "ref_no": p.ref_no or "", "gross": round(p.gross_amount or 0, 2),
                     "discount": round(p.discount_total or 0, 2), "tds": round(p.tds_total or 0, 2),
                     "debit": round(p.debit_adjust_total or 0, 2), "paid": round(p.paid_amount or 0, 2)})
        tp += p.paid_amount or 0
    return _rep(cols, rows, {"payments": len(rows), "paid": round(tp, 2)})


def product_master(db):
    cols = ["sku", "supplier_barcode", "description", "hsn", "uom", "mrp", "avg_cost", "stock_qty", "supplier"]
    rows = [{"sku": p.sku, "supplier_barcode": p.barcode or "", "description": p.description, "hsn": p.hsn or "",
             "uom": p.uom, "mrp": p.mrp or "", "avg_cost": round(p.avg_cost or 0, 2), "stock_qty": p.stock_qty,
             "supplier": p.primary_supplier.name if p.primary_supplier else ""}
            for p in db.query(models.Product).options(_eager(models.Product.primary_supplier))
                          .order_by(models.Product.description).all()]
    return _rep(cols, rows, {"products": len(rows)})


def supplier_master(db):
    cols = ["name", "gstin", "state", "state_code", "phone", "email", "bank"]
    rows = []
    for s in db.query(models.Supplier).order_by(models.Supplier.name).all():
        bank = (s.bank or {}).get("name", "") if isinstance(s.bank, dict) else ""
        rows.append({"name": s.name, "gstin": s.gstin or "", "state": s.state or "",
                     "state_code": s.state_code or "", "phone": s.phone or "", "email": s.email or "",
                     "bank": bank})
    return _rep(cols, rows, {"suppliers": len(rows)})


def tax_master(db):
    """HSN-wise summary: how many products, current stock value under each HSN."""
    cols = ["hsn", "products", "stock_qty", "stock_value"]
    agg = defaultdict(lambda: {"products": 0, "stock_qty": 0.0, "stock_value": 0.0})
    # three columns per product, not every product as a full object
    P = models.Product
    for hsn, qty, cost in db.query(P.hsn, P.stock_qty, P.avg_cost):
        h = hsn or "(none)"
        agg[h]["products"] += 1
        agg[h]["stock_qty"] += qty or 0
        agg[h]["stock_value"] += round((qty or 0) * (cost or 0), 2)   # Product.stock_value
    rows = [{"hsn": h, "products": v["products"], "stock_qty": round(v["stock_qty"], 2),
             "stock_value": round(v["stock_value"], 2)} for h, v in sorted(agg.items())]
    return _rep(cols, rows, {"hsn_codes": len(rows)})


# ===========================================================================
#  Transport
# ===========================================================================
def transport_report(db, date_from=None, date_to=None):
    """The consignment register: what arrived, on whose lorry, at what freight."""
    # `freight` is the freight LINE; `charges_total` is the transporter's G. TOTAL
    # — freight plus L.R. charge, H.C., S.T. charge and whatever else the LR
    # printed. Both are shown because they answer different questions: what the
    # haulage cost, and what the lorry is actually paid.
    cols = ["recv_date", "lr_entry_no", "lr_no", "lr_date", "transport", "supplier",
            "agent", "bundles", "boxes", "pieces", "goods_value", "freight",
            "charges_total", "paid_topay", "received_by", "invoice"]
    lo, hi = date_svc.to_iso(date_from), date_svc.to_iso(date_to)
    rows, tq, tv, tf, tc = [], 0.0, 0.0, 0.0, 0.0
    for e in db.query(models.LREntry).order_by(models.LREntry.recv_date,
                                               models.LREntry.id).all():
        d = date_svc.to_iso(e.recv_date)
        if (lo and (not d or d < lo)) or (hi and (not d or d > hi)):
            continue
        rows.append({"recv_date": e.recv_date or "", "lr_entry_no": e.lr_entry_no or "",
                     "lr_no": e.lr_no or "", "lr_date": e.lr_date or "",
                     "transport": e.transport or "", "supplier": e.supplier_name or "",
                     "agent": e.agent or "", "bundles": _f(e.bundle), "boxes": _f(e.boxes),
                     "pieces": _f(e.qty), "goods_value": _r2(e.amount),
                     "freight": _r2(e.freight_amount),
                     # falls back to the freight line for consignments booked
                     # before the charge block was captured — those rows had no
                     # other charges recorded, so freight IS their total
                     "charges_total": _r2(_charges_total(e)),
                     "paid_topay": e.paid_topay or "",
                     "received_by": e.received_by or "", "invoice": e.inv_no or ""})
        tq += _f(e.qty); tv += _f(e.amount); tf += _f(e.freight_amount)
        tc += _f(_charges_total(e))
    return _rep(cols, rows, {"consignments": len(rows), "pieces": round(tq, 2),
                             "goods_value": _r2(tv), "freight": _r2(tf),
                             "charges_total": _r2(tc)})


def _charges_total(e):
    """What this consignment's transporter is owed.

    The printed G. TOTAL when the LR carried one, otherwise the freight line on
    its own — which is all an entry keyed before the charge block existed can
    honestly claim."""
    return e.freight_total if e.freight_total is not None else e.freight_amount


def transport_pending_bills(db):
    """Freight owed, by transporter.

    'Owed' means every TOPAY consignment: this system records what freight applies
    to a consignment but has no transport payment ledger, so nothing can be marked
    settled. Read it as freight incurred, not as an outstanding balance."""
    cols = ["transport", "consignments", "pieces", "goods_value", "freight",
            "charges_total"]
    agg = defaultdict(lambda: {"consignments": 0, "pieces": 0.0,
                               "goods_value": 0.0, "freight": 0.0,
                               "charges_total": 0.0})
    for e in db.query(models.LREntry).all():
        # a consignment whose LR shows only sundry charges and no freight line
        # still has a bill to pay, so the test is on what is OWED, not on freight
        owed = _f(_charges_total(e))
        if not (e.freight_applicable and owed):
            continue
        # normalised, because rows saved before the LR router normalised on write
        # still hold whatever the page printed — "TO PAY" with a space among them
        from ..routers.lr import normalise_paid_topay
        if (normalise_paid_topay(e.paid_topay) or "") not in ("TOPAY", ""):
            continue                      # PAID = the supplier settled it
        a = agg[e.transport or "(none)"]
        a["consignments"] += 1
        a["pieces"] += _f(e.qty)
        a["goods_value"] += _f(e.amount)
        a["freight"] += _f(e.freight_amount)
        a["charges_total"] += owed
    rows = [{"transport": k, **{f: round(v, 2) for f, v in a.items()}}
            for k, a in sorted(agg.items())]
    return _rep(cols, rows,
                {"transporters": len(rows),
                 "freight": _r2(sum(r["freight"] for r in rows)),
                 "charges_total": _r2(sum(r["charges_total"] for r in rows))},
                note="TOPAY consignments only. `charges_total` is the transporter's "
                     "G. TOTAL — freight plus L.R. charge, H.C., S.T. and the rest of "
                     "the charge block — and is what the lorry is paid against. "
                     "Transport payments are not recorded, so nothing here can be "
                     "marked settled.")


# ===========================================================================
#  Invoice
# ===========================================================================
def invoice_report(db, date_from=None, date_to=None):
    """Every invoice read into the system, whatever became of it."""
    cols = ["date", "supplier", "invoice_number", "taxable", "tax", "grand_total",
            "status", "read_by", "confidence", "grn"]
    lo, hi = date_svc.to_iso(date_from), date_svc.to_iso(date_to)
    rows, tt = [], 0.0
    for doc in db.query(models.Document).filter(
            models.Document.document_type == "invoice").order_by(models.Document.id).all():
        ex = doc.latest_extraction
        data = (ex.data if ex else {}) or {}
        inv, tot = data.get("invoice") or {}, data.get("totals") or {}
        d = date_svc.to_iso(inv.get("date"))
        if (lo and (not d or d < lo)) or (hi and (not d or d > hi)):
            continue
        grn = db.query(models.Purchase).filter(
            models.Purchase.document_id == doc.id).first()
        rows.append({"date": inv.get("date") or "",
                     "supplier": doc.supplier.name if doc.supplier else "",
                     "invoice_number": inv.get("number") or "",
                     "taxable": _r2(tot.get("taxable_total")),
                     "tax": _r2(tot.get("tax_total")),
                     "grand_total": _r2(tot.get("grand_total")),
                     "status": doc.status, "read_by": ex.provider if ex else "",
                     "confidence": round(_f(ex.confidence) * 100) if ex else "",
                     "grn": (grn.grn_no or f"#{grn.id}") if grn else ""})
        tt += _f(tot.get("grand_total"))
    return _rep(cols, rows, {"invoices": len(rows), "grand_total": _r2(tt)})


def invoice_detail_report(db, date_from=None, date_to=None):
    """One row per billed line, across every invoice — what was actually bought."""
    cols = ["date", "supplier", "invoice_number", "description", "hsn", "qty",
            "uom", "rate", "discount_pct", "taxable_value", "amount"]
    lo, hi = date_svc.to_iso(date_from), date_svc.to_iso(date_to)
    rows, tq, ta = [], 0.0, 0.0
    for doc in db.query(models.Document).order_by(models.Document.id).all():
        ex = doc.latest_extraction
        data = (ex.data if ex else {}) or {}
        inv = data.get("invoice") or {}
        d = date_svc.to_iso(inv.get("date"))
        if (lo and (not d or d < lo)) or (hi and (not d or d > hi)):
            continue
        for it in data.get("line_items") or []:
            amt = it.get("amount") if it.get("amount") is not None else it.get("taxable_value")
            rows.append({"date": inv.get("date") or "",
                         "supplier": doc.supplier.name if doc.supplier else "",
                         "invoice_number": inv.get("number") or "",
                         "description": it.get("description") or "",
                         "hsn": it.get("hsn") or "", "qty": _f(it.get("qty")),
                         "uom": it.get("uom") or "", "rate": _r2(it.get("rate")),
                         "discount_pct": _f(it.get("discount_pct")),
                         "taxable_value": _r2(it.get("taxable_value")),
                         "amount": _r2(amt)})
            tq += _f(it.get("qty")); ta += _f(amt)
    return _rep(cols, rows, {"lines": len(rows), "qty": round(tq, 2), "amount": _r2(ta)})


def wh_entry_report(db, date_from=None, date_to=None):
    """Warehouse entry register — every goods receipt and what it booked in."""
    cols = ["grn_no", "posted_on", "invoice_date", "supplier", "invoice_number",
            "lines", "items", "billed_qty", "received_qty", "short_qty", "value",
            "status", "cartons"]
    from sqlalchemy import case, func
    from sqlalchemy.orm import joinedload
    PL = models.PurchaseLine
    rows, tb, tr, ts = [], 0.0, 0.0, 0.0
    lo, hi = date_svc.to_iso(date_from), date_svc.to_iso(date_to)
    # Receipt by receipt, every line, every line's shortages and breakdown, and a
    # carton count were each a query of their own — millions on a full store, and
    # the report never came back. Now: cartons and per-line totals in grouped
    # reads (below); the arithmetic is unchanged.
    cartons = dict(db.query(models.Bundle.purchase_id, func.count(models.Bundle.id))
                     .group_by(models.Bundle.purchase_id).all())
    keep = []
    for p in (db.query(models.Purchase).options(joinedload(models.Purchase.supplier))
                .order_by(models.Purchase.id).all()):
        d = date_svc.to_iso(p.invoice_date)
        if (lo and (not d or d < lo)) or (hi and (not d or d > hi)):
            continue
        keep.append(p)
    # Per line, the database adds up its shortages (signed: excess counts in,
    # short/damaged out, as GrnShortage.signed_qty) and its breakdown rows; the
    # per-receipt arithmetic below is the model's own, on those figures. Loading
    # every line, shortage and split as objects was 1.2M lines on a full store.
    SH, SP = models.GrnShortage, models.PurchaseLineSplit
    claim = SH.kind.in_(SH.CLAIMABLE_KINDS)
    sh = (db.query(SH.line_id.label("line_id"),
                   func.sum(case((SH.kind == "excess", func.coalesce(SH.qty, 0)),
                                 else_=-func.coalesce(SH.qty, 0))).label("signed"),
                   func.sum(case((claim, func.coalesce(SH.qty, 0)), else_=0)).label("claim"))
            .group_by(SH.line_id).subquery())
    sp = (db.query(SP.line_id.label("line_id"), func.count(SP.id).label("n"),
                   func.sum(case((func.coalesce(SP.qty, 0) > 0, 1), else_=0)).label("pos"))
            .group_by(SP.line_id).subquery())
    wanted = {p.id for p in keep}
    per = defaultdict(lambda: [0, 0, 0.0, 0.0, 0.0])      # lines, items, billed, recv, shorts
    lines_q = (db.query(PL.purchase_id, PL.qty, sh.c.signed, sh.c.claim, sp.c.n, sp.c.pos)
                 .outerjoin(sh, sh.c.line_id == PL.id).outerjoin(sp, sp.c.line_id == PL.id))
    if len(wanted) <= 5000:            # a date window: only its receipts' lines
        lines_q = lines_q.filter(PL.purchase_id.in_(sorted(wanted)))
    for purchase_id, qty, signed, claimed, n_split, n_pos in lines_q:
        if purchase_id not in wanted:
            continue
        a = per[purchase_id]
        received = round(_f(qty) + float(signed or 0), 3)     # PurchaseLine.received_qty
        a[0] += 1
        a[1] += int(n_pos or 0) if n_split else (1 if received > 0 else 0)
        a[2] += _f(qty)
        a[3] += received
        a[4] += float(claimed or 0)
    for p in keep:
        n_lines, items, billed, recv, shorts = per.get(p.id, (0, 0, 0.0, 0.0, 0.0))
        rows.append({"grn_no": p.grn_no or f"#{p.id}",
                     "posted_on": p.posted_at.strftime("%Y-%m-%d") if p.posted_at else "",
                     "invoice_date": p.invoice_date or "",
                     "supplier": p.supplier.name if p.supplier else "",
                     "invoice_number": p.invoice_number or "",
                     "lines": n_lines, "items": items,
                     "billed_qty": round(billed, 2), "received_qty": round(recv, 2),
                     "short_qty": round(shorts, 2), "value": _r2(p.grand_total),
                     "status": p.status, "cartons": cartons.get(p.id, 0)})
        tb += billed; tr += recv; ts += shorts
    return _rep(cols, rows, {"receipts": len(rows), "billed_qty": round(tb, 2),
                             "received_qty": round(tr, 2), "short_qty": round(ts, 2)})


# ===========================================================================
#  Stock
# ===========================================================================
def stock_as_on(db, as_on=None):
    """Stock and its value as it stood on a given date.

    Replayed from the append-only ledger rather than read off the product, which
    is the only way to answer it: `stock_qty` is today's figure, and a weighted
    average is path-dependent, so it has to be re-mixed in the order things
    actually arrived."""
    on = date_svc.to_iso(as_on) or date_svc.today()
    cols = ["sku", "description", "hsn", "uom", "supplier", "stock_qty",
            "avg_cost", "stock_value"]
    from sqlalchemy.orm import joinedload
    SM, P = models.StockMovement, models.Product
    # The ledger in one ordered read, replayed per product; then only the products
    # still holding stock on that date are loaded. Product by product, each one's
    # movements were a query of their own — hundreds of thousands on a full store.
    state = {}                                   # product_id -> (qty, avg)
    for pid, created_at, qty_delta, kind, mv_rate in (
            db.query(SM.product_id, SM.created_at, SM.qty_delta, SM.kind, SM.rate)
              .filter(SM.product_id.isnot(None)).order_by(SM.product_id, SM.id)):
        when = created_at.date().isoformat() if created_at else None
        if when and when > on:
            continue
        qty, avg = state.get(pid, (0.0, 0.0))
        delta = _f(qty_delta)
        if delta > 0 and kind == "inward":
            nq = qty + delta
            rate = _f(mv_rate)
            avg = round(((qty * avg) + (delta * rate)) / nq, 4) if nq else rate
            qty = nq
        else:
            qty = round(qty + delta, 3)
        state[pid] = (qty, avg)
    held = {pid for pid, (qty, _a) in state.items() if round(qty, 3) != 0}
    # the order the database sorts descriptions in — its collation, not Python's
    order = [pid for (pid,) in db.query(P.id).order_by(P.description, P.id) if pid in held]
    by_id = {}
    for i in range(0, len(order), 5000):
        for p in (db.query(P).options(joinedload(P.primary_supplier))
                    .filter(P.id.in_(order[i:i + 5000]))):
            by_id[p.id] = p
    products = [by_id[pid] for pid in order]
    rows, tq, tv = [], 0.0, 0.0
    for p in products:
        qty, avg = state[p.id]
        value = round(qty * avg, 2)
        rows.append({"sku": p.sku, "description": p.description, "hsn": p.hsn or "",
                     "uom": p.uom, "supplier": p.primary_supplier.name if p.primary_supplier else "",
                     "stock_qty": round(qty, 3), "avg_cost": round(avg, 2),
                     "stock_value": value})
        tq += qty; tv += value
    return _rep(cols, rows, {"products": len(rows), "stock_qty": round(tq, 2),
                             "stock_value": _r2(tv)},
                note=f"Ledger replayed to {on}. Rows at zero on that date are omitted.")


def stock_transactions(db, date_from=None, date_to=None):
    """Every stock movement, in order, with the running balance it produced."""
    cols = ["date", "sku", "description", "kind", "in_qty", "out_qty", "rate",
            "balance_after", "reference", "note"]
    lo, hi = date_svc.to_iso(date_from), date_svc.to_iso(date_to)
    rows, ti, to_ = [], 0.0, 0.0
    SM, P = models.StockMovement, models.Product
    # The product's two columns joined in, rather than a lazy load per product;
    # and a date window narrowed in the database first (the exact test below
    # still decides), so a month's report does not read the whole ledger.
    q = (db.query(SM, P.id, P.sku, P.description).outerjoin(P, P.id == SM.product_id)
           .order_by(SM.id))
    if lo:
        q = q.filter(SM.created_at >= dt.datetime.fromisoformat(lo))
    if hi:
        q = q.filter((SM.created_at < dt.datetime.fromisoformat(hi) + dt.timedelta(days=1))
                     | SM.created_at.is_(None))
    for m, pid, sku, description in q:
        when = m.created_at.date().isoformat() if m.created_at else ""
        if (lo and when < lo) or (hi and when > hi):
            continue
        delta = _f(m.qty_delta)
        found = pid is not None                  # `prod` in the old per-row lookup
        rows.append({"date": when, "sku": sku if found else "",
                     "description": description if found else "",
                     "kind": m.kind,
                     "in_qty": round(delta, 3) if delta > 0 else "",
                     "out_qty": round(-delta, 3) if delta < 0 else "",
                     "rate": _r2(m.rate), "balance_after": m.balance_after,
                     "reference": f"{m.ref_type or ''} {m.ref_id or ''}".strip(),
                     "note": m.note or ""})
        ti += max(delta, 0.0); to_ += max(-delta, 0.0)
    return _rep(cols, rows, {"transactions": len(rows), "total_in": round(ti, 2),
                             "total_out": round(to_, 2)})


def stock_by_location(db):
    """What each warehouse actually HOLDS, and what has been sent to each store.

    REWRITTEN. This report used to open "This system keeps one stock figure, at
    the warehouse" and report every warehouse as a single row called WAREHOUSE.
    That stopped being true when stock was split per building
    (services/stock_locations, StockBalance): there are now real balances per
    warehouse, and a report collapsing them into one line was not merely
    incomplete — it was the wrong number under a heading that named a place.

    Two kinds of row, and the difference is the whole point:

      * a WAREHOUSE row is a BALANCE — what is standing there right now,
        valued at that building's own weighted-average cost;
      * a STORE row is a MOVEMENT — what has been dispatched to it. A store's
        own stock lives in the till's database, not this one, so what it holds
        today is its book and not ours. Saying otherwise would invent a figure.
    """
    cols = ["location", "kind", "direction", "documents", "products", "qty", "value"]
    rows = []

    for w in stock_loc.warehouse_totals(db):
        rows.append({
            "location": w["name"], "kind": "warehouse", "direction": "holds",
            "documents": "", "products": w["items"],
            "qty": round(w["qty"], 2), "value": w["value"],
        })

    agg = defaultdict(lambda: {"documents": set(), "products": set(),
                               "qty": 0.0, "value": 0.0})
    # Notes, then their lines, as plain columns — not each note's `lines` loaded
    # one note at a time (seventeen thousand queries on a full store).
    SO, SL = models.StockOutward, models.StockOutwardLine
    stores = {s.id: s.name for s in db.query(models.Store)}
    where = {}                                   # note id -> the row it counts under
    for oid, to_wh, to_store, dest in (db.query(SO.id, SO.to_warehouse_id, SO.to_store_id,
                                               SO.to_destination)
                                         .filter(SO.status.in_(("posted", "received")))):
        # A warehouse-to-warehouse transfer is NOT a destination row: both ends
        # are warehouses and both already appear above as balances. Counting it
        # here as well would report the same goods twice, once as stock held and
        # once as stock sent away.
        if to_wh:
            continue
        loc = stores[to_store] if to_store in stores else (dest or "(unnamed)")
        where[oid] = loc
        agg[loc]["documents"].add(oid)
    for oid, product_id, qty, rate in (db.query(SL.outward_id, SL.product_id, SL.qty, SL.rate)
                                         .join(SO, SO.id == SL.outward_id)
                                         .filter(SO.status.in_(("posted", "received")))
                                         .order_by(SL.id)):
        loc = where.get(oid)
        if loc is None:
            continue
        a = agg[loc]
        a["products"].add(product_id)
        a["qty"] += _f(qty)
        a["value"] += _f(qty) * _f(rate)
    for loc, a in sorted(agg.items()):
        rows.append({"location": loc, "kind": "store", "direction": "dispatched to",
                     "documents": len(a["documents"]), "products": len(a["products"]),
                     "qty": round(a["qty"], 2), "value": _r2(a["value"])})

    held = sum(r["qty"] for r in rows if r["kind"] == "warehouse")
    sent = sum(r["qty"] for r in rows if r["kind"] == "store")
    return _rep(cols, rows,
                {"locations": len(rows), "held_in_warehouses": round(held, 2),
                 "dispatched_to_stores": round(sent, 2)},
                note="Warehouse rows are BALANCES — what is standing there now. "
                     "Store rows are what has been SENT to them; a store's own "
                     "stock lives in the till's database, not this one. "
                     "Warehouse-to-warehouse transfers are not listed as "
                     "destinations — both ends are already counted as balances.")


def pos_sales_by_store(db, date_from=None, date_to=None, warehouse_id=None):
    """What each shop sold, and which warehouse supplies it.

    Read from the till's own database (services/pos_store_sales). When that
    cannot be reached the report says so in its note and returns no rows —
    "nothing sold" and "cannot ask" are different answers and must not look the
    same on a register somebody is reconciling against.
    """
    from . import pos_store_sales
    res = pos_store_sales.by_store(db, days=None if date_from else 30,
                                   date_from=date_from, date_to=date_to,
                                   warehouse_id=warehouse_id)
    cols = ["store", "code", "warehouse", "bills", "units", "gross",
            "discount", "tax", "returns", "net", "last_sale"]
    if not res.get("available"):
        return _rep(cols, [], {}, note=res.get("reason") or
                    "The shop (POS) module could not be read from here.")
    rows = [{"store": s["store"], "code": s.get("store_code") or "",
             "warehouse": s.get("warehouse") or "", "bills": s["bills"],
             "units": s["units"], "gross": s["gross"], "discount": s["discount"],
             "tax": s["tax"], "returns": s["returns"], "net": s["net"],
             "last_sale": s["last_sale"] or ""} for s in res["stores"]]
    # A branch the two systems spell differently is listed, with its money, but
    # marked — never dropped and never quietly folded into another warehouse.
    for u in res.get("unmatched", []):
        rows.append({"store": u["store"], "code": "", "warehouse": "(not matched)",
                     "bills": u["bills"], "units": u["units"], "gross": u["gross"],
                     "discount": u["discount"], "tax": u["tax"],
                     "returns": u["returns"], "net": u["net"],
                     "last_sale": u["last_sale"] or ""})
    return _rep(cols, rows, {**res["totals"], "window": res["window"]["from"] +
                             " to " + res["window"]["to"]},
                note=res.get("note"))


def pos_sales_by_day(db, date_from=None, date_to=None, warehouse_id=None):
    """Net shop takings per day. Every day appears, including the quiet ones."""
    from . import pos_store_sales
    d = pos_store_sales.daily(db, days=30, warehouse_id=warehouse_id)
    cols = ["day", "bills", "net"]
    if not d.get("available"):
        return _rep(cols, [], {},
                    note="The shop (POS) module could not be read from here.")
    rows = [{"day": lbl, "bills": d["bills"][i], "net": d["net"][i]}
            for i, lbl in enumerate(d["labels"])]
    return _rep(cols, rows,
                {"days": len(rows), "bills": sum(r["bills"] for r in rows),
                 "net": _r2(sum(r["net"] for r in rows))},
                note="Bills raised AT A BRANCH. Takings from before the till "
                     "recorded a branch belong to no shop and are not here.")


def warehouse_wise_stock(db, warehouse_id=None):
    """Stock and its value, one row per warehouse — the consolidated valuation.

    The figure the Central Dashboard's bar chart is drawn from, available as a
    report so it can be filtered, exported and printed like any other."""
    cols = ["warehouse", "code", "location", "trades_in", "stores", "items",
            "qty", "value", "status"]
    rows = []
    for w in stock_loc.warehouse_totals(db):
        if warehouse_id and w["warehouse_id"] != int(warehouse_id):
            continue
        wh = db.get(models.Warehouse, w["warehouse_id"])
        rows.append({
            "warehouse": w["name"], "code": w["code"] or "",
            "location": w["address"] or "", "stores": w["store_count"],
            "trades_in": (wh.catalogue.name if (wh and wh.catalogue) else ""),
            "items": w["items"], "qty": round(w["qty"], 2), "value": w["value"],
            "status": "Active" if w["active"] else "Closed",
        })
    return _rep(cols, rows,
                {"warehouses": len(rows),
                 "qty": round(sum(r["qty"] for r in rows), 2),
                 "value": _r2(sum(r["value"] for r in rows))},
                note="Quantity is counted in each product's own unit; value is at "
                     "that warehouse's own weighted-average cost.")


def stores_by_warehouse(db, warehouse_id=None):
    """Every store and till, under the warehouse that supplies it."""
    from . import locations as loc_svc
    cols = ["store", "code", "warehouse", "trades_in", "type", "tills",
            "till_names", "address", "status"]
    rows = [{
        "store": s["name"], "code": s["code"] or "",
        "warehouse": s["warehouse"] or "(not assigned)",
        "trades_in": s["catalogue"] or "", "type": s["type"],
        "tills": s["terminals"], "till_names": ", ".join(s["terminal_names"]),
        "address": s["address"] or "",
        "status": "Active" if s["active"] else "Closed",
    } for s in loc_svc.store_rows(db, warehouse_id)]
    return _rep(cols, rows,
                {"stores": len(rows),
                 "tills": sum(r["tills"] for r in rows)},
                note="A store with no till cannot bill. Stock at a store lives in "
                     "the till's own database — this lists the places, not their "
                     "balances.")


def transfer_register(db, date_from=None, date_to=None, warehouse_id=None):
    """Every movement between this company's own places, as a register.

    Warehouse-to-warehouse transfers, dispatches to stores, and anything sent
    elsewhere — with what was sent, what was accepted, and what is still in
    transit. In-transit is the column worth reading: those goods are standing in
    neither building."""
    cols = ["code", "date", "kind", "from", "to", "status", "lines",
            "sent_qty", "accepted_qty", "short_qty", "value"]
    q = db.query(models.StockOutward)
    rows = []
    def _wanted(o):
        return not ((date_from and (o.date or "") < date_from)
                    or (date_to and (o.date or "") > date_to)
                    or (warehouse_id and int(warehouse_id) not in (
                        o.from_warehouse_id, o.to_warehouse_id)))
    for o in _outwards_light(db, q.order_by(models.StockOutward.id.desc()), keep=_wanted):
        if date_from and (o.date or "") < date_from:
            continue
        if date_to and (o.date or "") > date_to:
            continue
        if warehouse_id and int(warehouse_id) not in (
                o.from_warehouse_id, o.to_warehouse_id):
            continue
        sent = float(o.total_qty or 0)
        rows.append({
            "code": o.code or "", "date": o.date or "",
            "kind": ("Warehouse transfer" if o.to_warehouse_id
                     else "To store" if o.to_store_id else "Dispatch"),
            "from": (o.from_warehouse.name if o.from_warehouse
                     else o.from_location or ""),
            "to": (o.to_warehouse.name if o.to_warehouse
                   else o.to_store.name if o.to_store
                   else o.to_destination or ""),
            "status": o.status, "lines": len(o.lines),
            "sent_qty": round(sent, 2),
            "accepted_qty": round(float(o.total_accepted or 0), 2),
            # Sent but not yet accepted reads as short only once it HAS been
            # received; before that it is in transit, not missing.
            "short_qty": round(o.shortfall, 2) if o.status == "received" else 0,
            "value": _r2(sum(_f(l.qty) * _f(l.rate) for l in o.lines)),
        })
    transit = sum(r["sent_qty"] for r in rows if r["status"] == "posted"
                  and r["kind"] == "Warehouse transfer")
    return _rep(cols, rows,
                {"documents": len(rows),
                 "sent_qty": round(sum(r["sent_qty"] for r in rows), 2),
                 "in_transit": round(transit, 2),
                 "value": _r2(sum(r["value"] for r in rows))},
                note="In transit = dispatched between warehouses and not yet "
                     "counted in at the far end, so standing in neither.")


def warehouse_stock_analysis(db):
    """Warehouse stock cut by section and category — where the money is sitting."""
    from sqlalchemy import select
    from . import integrity
    # Only products holding stock can reach a row, so only they are read — and
    # their provenance, rather than the whole catalogue's.
    held = models.Product.stock_qty > 0
    ctx = integrity.Context(db, product_ids=select(models.Product.id).where(held))
    cols = ["section", "category", "products", "units", "stock_value",
            "share_pct", "avg_cost", "undetailed"]
    agg = defaultdict(lambda: {"products": 0, "units": 0.0, "stock_value": 0.0,
                               "undetailed": 0})
    for p in db.query(models.Product).filter(held).all():
        if ctx.product_state(p) != integrity.POSTED or _f(p.stock_qty) <= 0:
            continue
        a = agg[(p.category_section or "(unmapped)", p.category or "(unmapped)")]
        a["products"] += 1
        a["units"] += _f(p.stock_qty)
        a["stock_value"] += p.stock_value
        a["undetailed"] += 0 if p.detailed else 1
    total = sum(a["stock_value"] for a in agg.values()) or 1.0
    rows = [{"section": s, "category": c, "products": a["products"],
             "units": round(a["units"], 2), "stock_value": _r2(a["stock_value"]),
             "share_pct": round(a["stock_value"] / total * 100, 1),
             "avg_cost": round(a["stock_value"] / a["units"], 2) if a["units"] else 0,
             "undetailed": a["undetailed"]}
            for (s, c), a in sorted(agg.items())]
    rows.sort(key=lambda r: -r["stock_value"])
    return _rep(cols, rows, {"lines": len(rows),
                             "units": round(sum(r["units"] for r in rows), 2),
                             "stock_value": _r2(sum(r["stock_value"] for r in rows))},
                note="Records that trace back to no posted GRN are excluded — they are "
                     "not stock. See Inventory → Repair.")


def stock_audit_report(db):
    """Every physical-count correction: what the system said, what was counted.

    A stock adjustment is the one place a human overrides the ledger, so it is the
    one place worth auditing. Written as a movement rather than an overwrite, which
    is what makes this report possible at all."""
    cols = ["date", "sku", "description", "system_qty", "counted_qty", "difference",
            "value_impact", "note"]
    rows, tdiff, tval = [], 0.0, 0.0
    SM, P = models.StockMovement, models.Product
    # the product's two columns joined in — a lazy load per product was most of
    # the ten minutes this took over every adjustment on a full store
    for m, pid, sku, description in (db.query(SM, P.id, P.sku, P.description)
                                       .outerjoin(P, P.id == SM.product_id)
                                       .filter(SM.kind == "adjustment").order_by(SM.id)):
        delta = _f(m.qty_delta)
        after = _f(m.balance_after)
        impact = round(delta * _f(m.rate), 2)
        found = pid is not None                  # `prod` in the old per-row lookup
        rows.append({"date": m.created_at.strftime("%Y-%m-%d") if m.created_at else "",
                     "sku": sku if found else "",
                     "description": description if found else "",
                     "system_qty": round(after - delta, 3), "counted_qty": round(after, 3),
                     "difference": round(delta, 3), "value_impact": impact,
                     "note": m.note or ""})
        tdiff += delta; tval += impact
    return _rep(cols, rows, {"adjustments": len(rows), "net_difference": round(tdiff, 3),
                             "value_impact": _r2(tval)})


# ===========================================================================
#  Purchase
# ===========================================================================
def purchase_items_report(db, date_from=None, date_to=None):
    """Every item received, invoice by invoice — the line-level purchase book."""
    cols = ["date", "supplier", "invoice_number", "sku", "description", "variant",
            "category", "hsn", "qty", "uom", "rate", "amount"]
    rows, tq, ta = [], 0.0, 0.0
    for p in _posted(db, date_from, date_to):
        for line, split, prod, qty, rate in _received_rows(p):
            amt = round(qty * rate, 2)
            rows.append({"date": p.invoice_date or "",
                         "supplier": p.supplier.name if p.supplier else "",
                         "invoice_number": p.invoice_number or "",
                         "sku": prod.sku if prod else "",
                         "description": line.description or "",
                         "variant": split.variant_label if split is not None else "",
                         "category": (prod.category if prod else None) or line.category or "",
                         "hsn": line.hsn or "", "qty": round(qty, 3),
                         "uom": line.uom or "", "rate": round(rate, 2), "amount": amt})
            tq += qty; ta += amt
    return _rep(cols, rows, {"items": len(rows), "qty": round(tq, 2), "value": _r2(ta)})


def purchase_hsn_report(db, date_from=None, date_to=None):
    """Purchases grouped by HSN — the cut a GST return is built from."""
    cols = ["hsn", "description", "invoices", "items", "qty", "taxable", "tax", "total"]
    agg = defaultdict(lambda: {"desc": "", "invoices": set(), "items": 0,
                               "qty": 0.0, "taxable": 0.0, "tax": 0.0})
    for p in _posted(db, date_from, date_to, products=False):
        rate = (_f(p.tax_total) / _f(p.taxable_total)) if _f(p.taxable_total) else 0.0
        for line, split, prod, qty, r in _received_rows(p, with_product=False):
            a = agg[line.hsn or "(none)"]
            a["desc"] = a["desc"] or (line.description or "")
            a["invoices"].add(p.id)
            a["items"] += 1
            a["qty"] += qty
            a["taxable"] += qty * r
            a["tax"] += qty * r * rate
    rows = [{"hsn": h, "description": a["desc"][:40], "invoices": len(a["invoices"]),
             "items": a["items"], "qty": round(a["qty"], 2), "taxable": _r2(a["taxable"]),
             "tax": _r2(a["tax"]), "total": _r2(a["taxable"] + a["tax"])}
            for h, a in sorted(agg.items())]
    return _rep(cols, rows, {"hsn_codes": len(rows),
                             "taxable": _r2(sum(r["taxable"] for r in rows)),
                             "tax": _r2(sum(r["tax"] for r in rows))},
                note="Tax is apportioned per line at the invoice's effective rate — "
                     "the invoice itself states tax only as a total.")


def purchase_tax_report(db, date_from=None, date_to=None):
    """Tax invoice by invoice, as the supplier's document states it."""
    cols = ["date", "supplier", "gstin", "invoice_number", "taxable", "cgst", "sgst",
            "igst", "tds", "charges", "round_off", "grand_total"]
    rows = []
    tot = defaultdict(float)
    for p in _posted(db, date_from, date_to, lines=False):
        t = _taxes(p)
        r = {"date": p.invoice_date or "",
             "supplier": p.supplier.name if p.supplier else "",
             "gstin": (p.supplier.gstin if p.supplier else "") or "",
             "invoice_number": p.invoice_number or "",
             "taxable": _r2(p.taxable_total), "cgst": _r2(t.get("cgst_amount")),
             "sgst": _r2(t.get("sgst_amount")), "igst": _r2(t.get("igst_amount")),
             "tds": _r2(t.get("tds_amount")),
             "charges": _r2(_f(t.get("other_charges")) + _f(t.get("freight"))),
             "round_off": _r2(t.get("round_off")), "grand_total": _r2(p.grand_total)}
        rows.append(r)
        for k in ("taxable", "cgst", "sgst", "igst", "tds", "grand_total"):
            tot[k] += r[k]
    return _rep(cols, rows, {"invoices": len(rows),
                             **{k: _r2(v) for k, v in tot.items()}})


def purchase_tax_summary(db, date_from=None, date_to=None):
    """Tax totalled by rate and kind — intra-state and inter-state kept apart,
    because they settle against different heads."""
    cols = ["tax_kind", "rate_pct", "invoices", "taxable", "tax_amount"]
    agg = defaultdict(lambda: {"invoices": 0, "taxable": 0.0, "tax": 0.0})
    for p in _posted(db, date_from, date_to, lines=False):
        t = _taxes(p)
        for kind, rk, ak in (("CGST+SGST", "cgst_rate", "cgst_amount"),
                             ("IGST", "igst_rate", "igst_amount")):
            amt = _f(t.get(ak))
            if not amt:
                continue
            rate = _f(t.get(rk))
            # CGST and SGST are levied as a matched pair at half the total rate
            total_rate = rate * 2 if kind == "CGST+SGST" else rate
            total_amt = amt * 2 if kind == "CGST+SGST" else amt
            a = agg[(kind, round(total_rate, 2))]
            a["invoices"] += 1
            a["taxable"] += _f(p.taxable_total)
            a["tax"] += total_amt
    rows = [{"tax_kind": k, "rate_pct": r, "invoices": a["invoices"],
             "taxable": _r2(a["taxable"]), "tax_amount": _r2(a["tax"])}
            for (k, r), a in sorted(agg.items())]
    return _rep(cols, rows, {"bands": len(rows),
                             "taxable": _r2(sum(r["taxable"] for r in rows)),
                             "tax_amount": _r2(sum(r["tax_amount"] for r in rows))})


def purchase_barcode_wise(db, date_from=None, date_to=None):
    """Purchases rolled up to the code on the goods — one row per SKU."""
    cols = ["sku", "barcode", "description", "size", "colour", "category",
            "receipts", "qty", "avg_rate", "value", "stock_now"]
    agg = defaultdict(lambda: {"receipts": set(), "qty": 0.0, "value": 0.0})
    for p in _posted(db, date_from, date_to):
        for line, split, prod, qty, rate in _received_rows(p):
            if not prod:
                continue
            a = agg[prod.id]
            a["receipts"].add(p.id)
            a["qty"] += qty
            a["value"] += qty * rate
    rows = []
    for pid, a in agg.items():
        prod = db.get(models.Product, pid)
        if not prod:
            continue
        rows.append({"sku": prod.sku, "barcode": prod.barcode or "",
                     "description": prod.description, "size": prod.size or "",
                     "colour": prod.color or "", "category": prod.category or "",
                     "receipts": len(a["receipts"]), "qty": round(a["qty"], 2),
                     "avg_rate": round(a["value"] / a["qty"], 2) if a["qty"] else 0,
                     "value": _r2(a["value"]), "stock_now": _f(prod.stock_qty)})
    rows.sort(key=lambda r: r["sku"] or "")
    return _rep(cols, rows, {"products": len(rows),
                             "qty": round(sum(r["qty"] for r in rows), 2),
                             "value": _r2(sum(r["value"] for r in rows))})


def section_wise_purchase(db, date_from=None, date_to=None):
    """Purchases by category section — LADIES / MENS / KIDS, the buying split."""
    cols = ["section", "category", "invoices", "items", "qty", "value", "share_pct"]
    agg = defaultdict(lambda: {"invoices": set(), "items": 0, "qty": 0.0, "value": 0.0})
    for p in _posted(db, date_from, date_to):
        for line, split, prod, qty, rate in _received_rows(p):
            section = (prod.category_section if prod else None) or "(unmapped)"
            cat = (prod.category if prod else None) or line.category or "(unmapped)"
            a = agg[(section, cat)]
            a["invoices"].add(p.id)
            a["items"] += 1
            a["qty"] += qty
            a["value"] += qty * rate
    total = sum(a["value"] for a in agg.values()) or 1.0
    rows = [{"section": s, "category": c, "invoices": len(a["invoices"]),
             "items": a["items"], "qty": round(a["qty"], 2), "value": _r2(a["value"]),
             "share_pct": round(a["value"] / total * 100, 1)}
            for (s, c), a in sorted(agg.items())]
    rows.sort(key=lambda r: -r["value"])
    return _rep(cols, rows, {"lines": len(rows), "value": _r2(total)})


# ===========================================================================
#  Purchase return
# ===========================================================================
def section_wise_purchase_return(db):
    """What went back, by section — the mirror of the buying split."""
    cols = ["section", "category", "debit_notes", "items", "qty", "value"]
    agg = defaultdict(lambda: {"notes": set(), "items": 0, "qty": 0.0, "value": 0.0})
    from sqlalchemy.orm import joinedload, selectinload
    # each return's lines, and each line's product, read with the returns
    for r in (db.query(models.PurchaseReturn)
                .options(selectinload(models.PurchaseReturn.lines)
                         .joinedload(models.PurchaseReturnLine.product))
                .filter(models.PurchaseReturn.status == "posted").all()):
        for l in r.lines:
            if _f(l.qty) <= 0:
                continue
            prod = l.product
            a = agg[((prod.category_section if prod else None) or "(unmapped)",
                     (prod.category if prod else None) or "(unmapped)")]
            a["notes"].add(r.id)
            a["items"] += 1
            a["qty"] += _f(l.qty)
            a["value"] += _f(l.amount)
    rows = [{"section": s, "category": c, "debit_notes": len(a["notes"]),
             "items": a["items"], "qty": round(a["qty"], 2), "value": _r2(a["value"])}
            for (s, c), a in sorted(agg.items())]
    rows.sort(key=lambda r: -r["value"])
    return _rep(cols, rows, {"lines": len(rows),
                             "value": _r2(sum(r["value"] for r in rows))})


def purchase_return_audit(db):
    """Every debit-note line and what it did to stock.

    The audit question is which lines moved stock and which did not: goods going
    back reverse it, a receiving shortage never entered it. Both reduce the
    payable identically, so the distinction is invisible on the money and has to
    be read here."""
    cols = ["date", "code", "supplier", "invoice_number", "sku", "description",
            "kind", "qty", "grn_rate", "amount", "stock_moved", "status"]
    rows, tq, tv = [], 0.0, 0.0
    for r in (db.query(models.PurchaseReturn).options(_eager(models.PurchaseReturn.supplier))
                .order_by(models.PurchaseReturn.id).all()):
        for l in r.lines:
            if _f(l.qty) <= 0:
                continue
            rows.append({"date": r.date or "", "code": r.code,
                         "supplier": r.supplier.name if r.supplier else "",
                         "invoice_number": r.invoice_number or "",
                         "sku": (l.product.sku if l.product else "") or l.barcode or "",
                         "description": l.description or "",
                         "kind": "shortage claim" if l.is_shortage_claim else "goods returned",
                         "qty": _f(l.qty), "grn_rate": _r2(l.rate),
                         "amount": _r2(l.amount),
                         "stock_moved": "no" if l.is_shortage_claim else
                                        ("yes" if r.status == "posted" else "not yet"),
                         "status": r.status})
            if r.status == "posted":
                tq += _f(l.qty); tv += _f(l.amount)
    return _rep(cols, rows, {"lines": len(rows), "posted_qty": round(tq, 2),
                             "posted_value": _r2(tv)})


# ===========================================================================
#  Outward
# ===========================================================================
def outward_report(db, date_from=None, date_to=None):
    """Dispatches out of the warehouse, and how much of each was accepted."""
    cols = ["date", "code", "from_location", "to_destination", "packed_by", "lines",
            "sent_qty", "accepted_qty", "short_qty", "value", "status",
            "received_by", "received_date"]
    lo, hi = date_svc.to_iso(date_from), date_svc.to_iso(date_to)
    rows, ts, ta = [], 0.0, 0.0
    for o in _outwards_light(db, db.query(models.StockOutward).order_by(models.StockOutward.id),
                       keep=lambda o: _in_window(o.date, lo, hi)):
        d = date_svc.to_iso(o.date)
        if (lo and (not d or d < lo)) or (hi and (not d or d > hi)):
            continue
        value = sum(_f(l.qty) * _f(l.rate) for l in o.lines)
        rows.append({"date": o.date or "", "code": o.code or f"#{o.id}",
                     "from_location": o.from_location or "", "to_destination": o.to_destination or "",
                     "packed_by": o.packed_by or "", "lines": len(o.lines),
                     "sent_qty": round(o.total_qty, 2),
                     "accepted_qty": round(o.total_accepted, 2),
                     "short_qty": round(o.shortfall, 2), "value": _r2(value),
                     "status": o.status, "received_by": o.received_by or "",
                     "received_date": o.received_date or ""})
        ts += o.total_qty; ta += o.total_accepted
    return _rep(cols, rows, {"dispatches": len(rows), "sent_qty": round(ts, 2),
                             "accepted_qty": round(ta, 2),
                             "short_qty": round(ts - ta, 2)})


def outward_details_report(db, date_from=None, date_to=None):
    """One row per dispatched item — the packing list, after the fact."""
    cols = ["date", "code", "to_destination", "sku", "description", "size", "colour",
            "sent_qty", "accepted_qty", "short_qty", "rate", "value", "status"]
    lo, hi = date_svc.to_iso(date_from), date_svc.to_iso(date_to)
    rows, ts, tv = [], 0.0, 0.0
    for o in _outwards(db, db.query(models.StockOutward).order_by(models.StockOutward.id),
                       keep=lambda o: _in_window(o.date, lo, hi)):
        d = date_svc.to_iso(o.date)
        if (lo and (not d or d < lo)) or (hi and (not d or d > hi)):
            continue
        for l in o.lines:
            prod = l.product
            acc = _f(l.accepted_qty) if (o.status == "received" and l.accepted_qty is not None) \
                else (_f(l.qty) if o.status == "received" else 0.0)
            rows.append({"date": o.date or "", "code": o.code or f"#{o.id}",
                         "to_destination": o.to_destination or "",
                         "sku": (prod.sku if prod else "") or l.barcode or "",
                         "description": l.description or "",
                         "size": (prod.size if prod else "") or "",
                         "colour": (prod.color if prod else "") or "",
                         "sent_qty": _f(l.qty), "accepted_qty": round(acc, 3),
                         "short_qty": round(_f(l.qty) - acc, 3) if o.status == "received" else "",
                         "rate": _r2(l.rate), "value": _r2(_f(l.qty) * _f(l.rate)),
                         "status": o.status})
            ts += _f(l.qty); tv += _f(l.qty) * _f(l.rate)
    return _rep(cols, rows, {"lines": len(rows), "sent_qty": round(ts, 2),
                             "value": _r2(tv)})


def pending_inward_report(db):
    """Dispatched and not yet accepted — stock that has left the warehouse and
    which nobody at the other end has counted in."""
    cols = ["date", "code", "to_destination", "packed_by", "lines", "sent_qty",
            "value", "days_out"]
    today = dt.date.today()
    rows, tq = [], 0.0
    for o in _outwards_light(db, db.query(models.StockOutward).filter(
            models.StockOutward.status == "posted").order_by(models.StockOutward.id)):
        d = date_svc.parse(o.date)
        value = sum(_f(l.qty) * _f(l.rate) for l in o.lines)
        rows.append({"date": o.date or "", "code": o.code or f"#{o.id}",
                     "to_destination": o.to_destination or "",
                     "packed_by": o.packed_by or "", "lines": len(o.lines),
                     "sent_qty": round(o.total_qty, 2), "value": _r2(value),
                     "days_out": (today - d).days if d else ""})
        tq += o.total_qty
    return _rep(cols, rows, {"transfers": len(rows), "sent_qty": round(tq, 2)},
                note="Posted but not received: the stock is out of the warehouse and "
                     "unacknowledged at the destination.")


def pending_outward_report(db):
    """Prepared and not yet dispatched — stock still here, already spoken for."""
    cols = ["date", "code", "to_destination", "packed_by", "lines", "qty", "value"]
    rows, tq = [], 0.0
    for o in _outwards_light(db, db.query(models.StockOutward).filter(
            models.StockOutward.status == "draft").order_by(models.StockOutward.id)):
        value = sum(_f(l.qty) * _f(l.rate) for l in o.lines)
        rows.append({"date": o.date or "", "code": o.code or f"#{o.id}",
                     "to_destination": o.to_destination or "",
                     "packed_by": o.packed_by or "", "lines": len(o.lines),
                     "qty": round(o.total_qty, 2), "value": _r2(value)})
        tq += o.total_qty
    return _rep(cols, rows, {"drafts": len(rows), "qty": round(tq, 2)},
                note="Still draft: nothing has left the warehouse and no stock has moved.")


# ===========================================================================
#  Masters
# ===========================================================================
def agent_master(db):
    """Agents, and the business booked through each."""
    cols = ["name", "phone", "consignments", "pieces", "goods_value", "commission_pct"]
    agg = defaultdict(lambda: {"consignments": 0, "pieces": 0.0, "goods_value": 0.0,
                               "commission": []})
    for e in db.query(models.LREntry).all():
        if not e.agent:
            continue
        a = agg[e.agent]
        a["consignments"] += 1
        a["pieces"] += _f(e.qty)
        a["goods_value"] += _f(e.amount)
        if e.agent_commission is not None:
            a["commission"].append(_f(e.agent_commission))
    phones = {x.name: x.phone for x in db.query(models.Agent).all()}
    names = set(agg) | set(phones)
    rows = [{"name": n, "phone": phones.get(n) or "",
             "consignments": agg[n]["consignments"] if n in agg else 0,
             "pieces": round(agg[n]["pieces"], 2) if n in agg else 0,
             "goods_value": _r2(agg[n]["goods_value"]) if n in agg else 0,
             "commission_pct": (round(sum(agg[n]["commission"]) / len(agg[n]["commission"]), 2)
                                if n in agg and agg[n]["commission"] else "")}
            for n in sorted(names)]
    return _rep(cols, rows, {"agents": len(rows),
                             "goods_value": _r2(sum(_f(r["goods_value"]) for r in rows))})


# ===========================================================================
#  Catalogue
# ===========================================================================
#: key -> (display name, group, function). Groups and order mirror the reference
#: app's Reports screen so the two can be read side by side.
REPORTS = {
    # --- transport ---
    "transport_report": ("Transport Report", "transport", transport_report),
    "transport_pending_bills": ("Transport Pending Bills", "transport", transport_pending_bills),
    # --- invoice ---
    "invoice_report": ("Invoice Report", "invoice", invoice_report),
    "invoice_detail_report": ("Invoice Detail Report", "invoice", invoice_detail_report),
    "wh_entry_report": ("WH Entry Report", "invoice", wh_entry_report),
    # --- stock ---
    "stock_report": ("Stock Report", "stock", stock_report),
    "stock_as_on": ("Stock - As on Date", "stock", stock_as_on),
    "stock_transactions": ("Stock - Transactions", "stock", stock_transactions),
    "stock_movement": ("Stock Movement", "stock", stock_movement),
    "stock_by_location": ("Stock Movement - Locationwise", "stock", stock_by_location),
    "warehouse_stock_analysis": ("Warehouse Stock Analysis", "stock", warehouse_stock_analysis),
    # The consolidated set — one row per place rather than one figure for the
    # company. These are what the Central Dashboard draws, offered as reports so
    # the same numbers can be filtered, exported and printed.
    "warehouse_wise_stock": ("Warehouse-wise Stock & Value", "stock",
                             warehouse_wise_stock),
    "stores_by_warehouse": ("Stores / POS by Warehouse", "stock",
                            stores_by_warehouse),
    "transfer_register": ("Stock Transfer Register", "outward", transfer_register),
    # What the shops sold. Their own group: these come from the till's database,
    # not the warehouse ledger, and a reader should be able to see at a glance
    # which side of the wall a figure came from.
    "pos_sales_by_store": ("Sales by Store", "sales", pos_sales_by_store),
    "pos_sales_by_day": ("Store Sales - Daily", "sales", pos_sales_by_day),
    "stock_audit_report": ("Stock Audit Report", "stock", stock_audit_report),
    # --- purchase ---
    "purchase_register": ("Purchase Report", "purchase", purchase_register),
    "purchase_items_report": ("Purchase Items Report", "purchase", purchase_items_report),
    "purchase_hsn_report": ("Purchase HSN Report", "purchase", purchase_hsn_report),
    "purchase_tax_report": ("Purchase Tax Report", "purchase", purchase_tax_report),
    "purchase_tax_summary": ("Purchase Tax Summary Report", "purchase", purchase_tax_summary),
    "purchase_barcode_wise": ("Purchase Report - Barcode wise", "purchase", purchase_barcode_wise),
    "section_wise_purchase": ("Section wise Purchase Report", "purchase", section_wise_purchase),
    "supplier_pending_bills": ("Supplier Pending Bills", "purchase", supplier_pending_bills),
    "payments_register": ("Supplier Payment Report", "purchase", payments_register),
    "grn_shortage_register": ("GRN Shortage Register", "purchase", grn_shortage_register),
    # --- purchase return ---
    "purchase_return_register": ("Purchase Return Report", "purchase_return", purchase_return_register),
    "section_wise_purchase_return": ("Section wise Purchase Return Report", "purchase_return", section_wise_purchase_return),
    "purchase_return_audit": ("Purchase Return Audit Report", "purchase_return", purchase_return_audit),
    # --- outward ---
    "outward_report": ("Outward Report", "outward", outward_report),
    "outward_details_report": ("Outward Details Report", "outward", outward_details_report),
    "pending_inward_report": ("Pending Inward Report", "outward", pending_inward_report),
    "pending_outward_report": ("Pending Outward Report", "outward", pending_outward_report),
    # --- masters ---
    "product_master": ("Product Master Report", "master", product_master),
    "supplier_master": ("Supplier Master Report", "master", supplier_master),
    "agent_master": ("Agent Master Report", "master", agent_master),
    "tax_master": ("Tax / HSN Master Report", "master", tax_master),
}

#: Display order and heading for each group, matching the reference screen.
GROUPS = [
    ("transport", "Transport Reports"),
    ("invoice", "Invoice Reports"),
    ("stock", "Stock Reports"),
    ("purchase", "Purchase Reports"),
    ("purchase_return", "Purchase Return Reports"),
    ("outward", "Outward Reports"),
    ("sales", "Shop Sales Reports"),
    ("master", "Other Reports"),
]


def _params(fn):
    """Which filters a report accepts, so the UI offers those and only those."""
    return [p for p in inspect.signature(fn).parameters if p != "db"]


def catalogue():
    order = {g: i for i, (g, _) in enumerate(GROUPS)}
    items = [{"key": k, "name": n, "group": g, "params": _params(fn)}
             for k, (n, g, fn) in REPORTS.items()]
    items.sort(key=lambda r: (order.get(r["group"], 99), r["name"]))
    return items


def group_headings():
    return [{"key": g, "name": n} for g, n in GROUPS]


def run(db, key, **kw):
    """Run a report, passing only the filters it actually accepts.

    Filtering by signature rather than by a per-report `if` in the router: a new
    report declaring `date_from` starts honouring it with no route change, and one
    that does not is never handed an argument it would choke on."""
    if key not in REPORTS:
        return None
    fn = REPORTS[key][2]
    allowed = set(_params(fn))
    return fn(db, **{k: v for k, v in kw.items() if k in allowed and v not in (None, "")})
