"""The Command Center — the whole business on one screen, for the people who own it.

One call, like the Central Dashboard's, because the screen draws twenty figures,
two charts and three lists at once and asking for each separately turns opening
it into twenty round trips.

WHAT "TODAY" IS: the business's own calendar day (services/business_day), not
UTC's. Everything here is asked of that window.

WHAT EACH FIGURE COUNTS, because two screens disagreeing about a number nobody
can reconcile is worse than either being slightly off:

  sales       store bills (net of customer returns) — the same definition the
              Central Dashboard's store sales use (pos_store_sales)
  purchases   GRNs POSTED today, by `posted_at` — goods that became stock today,
              not invoices dated today
  payments    supplier payments RECORDED today (`created_at`)
  profit      store revenue before tax less what those goods cost at the till's
              current cost price (pos_insights.margin)
  stock value quantity × each warehouse's own average cost (stock_locations)

Figures that belong to a PRODUCT rather than a building — dead stock, store low
stock — are company-wide whatever the account's allotment, and the response says
which ones those are.

Never raises for a missing half. The shop's till database may be absent; the
dashboard still draws from the warehouse's and says the store figures are unknown.
"""
import datetime as dt

from .. import models
from . import (audit as audit_svc, business_day, dead_stock, payments as pay_svc,
               pos_insights, stock_locations as stock_loc)
from .figures import inr, inr_short, qty as fmt_qty


def _safe(fn, default):
    try:
        return fn()
    except Exception:                                  # noqa: BLE001 — one tile, not the screen
        return default


def _in_allowed(q, col, allowed):
    return q.filter(col.in_(list(allowed))) if allowed else q


# ---------------------------------------------------------------------------
#  Counts
# ---------------------------------------------------------------------------
def counts(db, allowed=None, with_users=True):
    wh = _in_allowed(db.query(models.Warehouse).filter(models.Warehouse.active.is_(True)),
                     models.Warehouse.id, allowed).count()
    st_q = _in_allowed(db.query(models.Store).filter(models.Store.active.is_(True)),
                       models.Store.warehouse_id, allowed)
    stores = st_q.count()
    tq = (db.query(models.PosTerminal).join(models.Store, models.Store.id == models.PosTerminal.store_id)
            .filter(models.PosTerminal.active.is_(True)))
    tq = _in_allowed(tq, models.Store.warehouse_id, allowed)
    out = {"warehouses": wh, "stores": stores, "counters": tq.count()}
    if with_users:
        out["users"] = db.query(models.User).filter(models.User.active == True).count()  # noqa: E712
    return out


# ---------------------------------------------------------------------------
#  The warehouse's own flows over a UTC window
# ---------------------------------------------------------------------------
def purchases_between(db, start, end, allowed=None, warehouse_id=None):
    q = (db.query(models.Purchase)
           .filter(models.Purchase.status == "posted",
                   models.Purchase.posted_at >= start, models.Purchase.posted_at < end))
    q = _in_allowed(q, models.Purchase.warehouse_id, allowed)
    if warehouse_id:
        q = q.filter(models.Purchase.warehouse_id == int(warehouse_id))
    rows = q.order_by(models.Purchase.posted_at.desc()).all()
    ids = [p.id for p in rows]
    units, products = 0.0, set()
    if ids:
        for mv in (db.query(models.StockMovement)
                     .filter(models.StockMovement.ref_type == "purchase",
                             models.StockMovement.kind == "inward",
                             models.StockMovement.ref_id.in_(ids)).all()):
            units += float(mv.qty_delta or 0)
            products.add(mv.product_id)
    return {"grns": len(rows), "value": round(sum(float(p.grand_total or 0) for p in rows), 2),
            "units": round(units, 3), "products": len(products), "rows": rows}


def payments_between(db, start, end):
    rows = (db.query(models.Payment)
              .filter(models.Payment.created_at >= start, models.Payment.created_at < end)
              .order_by(models.Payment.created_at.desc()).all())
    return {"count": len(rows), "value": round(sum(float(p.paid_amount or 0) for p in rows), 2),
            "rows": rows}


def debit_notes_between(db, start, end, allowed=None):
    rows = (db.query(models.PurchaseReturn)
              .filter(models.PurchaseReturn.status == "posted",
                      models.PurchaseReturn.posted_at >= start,
                      models.PurchaseReturn.posted_at < end).all())
    if allowed:
        rows = [r for r in rows if r.purchase and r.purchase.warehouse_id in allowed]
    return {"count": len(rows), "value": round(sum(float(r.total or 0) for r in rows), 2),
            "rows": rows}


def movement_between(db, start, end, allowed=None):
    q = db.query(models.StockMovement).filter(models.StockMovement.created_at >= start,
                                              models.StockMovement.created_at < end)
    q = _in_allowed(q, models.StockMovement.warehouse_id, allowed)
    inward = outward = 0.0
    for mv in q.all():
        d = float(mv.qty_delta or 0)
        if d > 0:
            inward += d
        else:
            outward -= d
    return {"inward": round(inward, 3), "outward": round(outward, 3)}


def outwards_posted_between(db, start, end, allowed=None):
    q = db.query(models.StockOutward).filter(models.StockOutward.posted_at >= start,
                                             models.StockOutward.posted_at < end)
    q = _in_allowed(q, models.StockOutward.from_warehouse_id, allowed)
    return q.count()


def pending_grns(db, allowed=None):
    drafts = _in_allowed(db.query(models.Purchase).filter(models.Purchase.status == "draft"),
                         models.Purchase.warehouse_id, allowed).all()
    docs = db.query(models.Document).filter(models.Document.status == "needs_review")
    if allowed:
        docs = docs.filter((models.Document.warehouse_id.is_(None))
                           | (models.Document.warehouse_id.in_(list(allowed))))
    return {"count": len(drafts), "value": round(sum(float(p.grand_total or 0) for p in drafts), 2),
            "documents": docs.count(), "rows": drafts}


def outstanding(db, supplier_name=None):
    """Every posted bill still owed on: [{purchase, supplier, outstanding, days}].

    The same arithmetic as payments.invoice_outstanding — bill less what was paid
    less what went back on debit notes — but totalled in two grouped queries
    rather than two per bill. This runs on the owner's landing page, and a
    warehouse with four thousand posted GRNs made that eight thousand queries.
    """
    from sqlalchemy import func
    today = dt.date.today()
    settled = dict(db.query(models.PaymentAllocation.purchase_id,
                            func.sum(models.PaymentAllocation.settled))
                     .group_by(models.PaymentAllocation.purchase_id).all())
    returned = dict(db.query(models.PurchaseReturn.purchase_id,
                             func.sum(models.PurchaseReturn.total))
                      .filter(models.PurchaseReturn.status == "posted")
                      .group_by(models.PurchaseReturn.purchase_id).all())
    out = []
    for p in db.query(models.Purchase).filter(models.Purchase.status == "posted").all():
        owed = round(float(p.grand_total or 0) - float(settled.get(p.id) or 0)
                     - float(returned.get(p.id) or 0), 2)
        if owed <= 0.01:
            continue
        name = p.supplier.name if p.supplier else "(no supplier)"
        if supplier_name and supplier_name.lower() not in name.lower():
            continue
        d = pay_svc._parse_date(p.invoice_date)
        out.append({"purchase": p, "supplier": name, "outstanding": round(owed, 2),
                    "days": (today - d).days if d else None})
    return out


def dead_counts(db, days=None):
    rules = dead_stock.get_rules()
    limit = int(days or rules["dead_after_days"])
    rows = [r for r in dead_stock.product_rows(db, rules=rules, include_healthy=True)
            if r["days_idle"] >= limit]
    return {"days": limit, "count": len(rows),
            "value": round(sum(r["stock_value"] for r in rows), 2),
            "critical": sum(1 for r in rows if r["status"] == "critical"),
            "rows": rows}


# ---------------------------------------------------------------------------
#  The series: one bucket per business day
# ---------------------------------------------------------------------------
def _bucket(values, when_value_pairs, days_list):
    index = {d: i for i, d in enumerate(days_list)}
    for when, value in when_value_pairs:
        local = business_day.local(when)
        if local is None:
            continue
        i = index.get(local.date())
        if i is not None:
            values[i] = round(values[i] + float(value or 0), 2)
    return values


def series(db, day, n_days=14, allowed=None, at=None):
    first = day - dt.timedelta(days=n_days - 1)
    start, end = business_day.bounds(first, day)
    days_list = business_day.days(first, day)
    z = lambda: [0.0] * len(days_list)                 # noqa: E731

    sales = _bucket(z(), pos_insights.invoice_times(start, end, at), days_list)
    pos_returns = _bucket(z(), pos_insights.return_times(start, end, at), days_list)
    pur = purchases_between(db, start, end, allowed)["rows"]
    purchases = _bucket(z(), [(p.posted_at, p.grand_total) for p in pur], days_list)
    pays = payments_between(db, start, end)["rows"]
    payments = _bucket(z(), [(p.created_at, p.paid_amount) for p in pays], days_list)
    dn = debit_notes_between(db, start, end, allowed)["rows"]
    returns = _bucket(pos_returns, [(r.posted_at, r.total) for r in dn], days_list)

    inward, outward = z(), z()
    index = {d: i for i, d in enumerate(days_list)}
    mq = _in_allowed(db.query(models.StockMovement).filter(
        models.StockMovement.created_at >= start, models.StockMovement.created_at < end),
        models.StockMovement.warehouse_id, allowed)
    for mv in mq.all():
        i = index.get(business_day.local(mv.created_at).date()) if mv.created_at else None
        if i is None:
            continue
        d = float(mv.qty_delta or 0)
        if d > 0:
            inward[i] = round(inward[i] + d, 3)
        else:
            outward[i] = round(outward[i] - d, 3)
    return {"labels": [d.strftime("%d %b") for d in days_list],
            "days": [d.isoformat() for d in days_list],
            "sales": sales, "purchases": purchases, "payments": payments,
            "returns": returns, "inward": inward, "outward": outward}


# ---------------------------------------------------------------------------
#  Alerts and activity
# ---------------------------------------------------------------------------
_NOTICE_TAB = {"documents": "documents", "purchases": "purchases", "lr": "lr",
               "payments": "payments", "returns": "returns", "outward": "outward",
               "inward": "inward", "deadstock": "deadstock", "inventory": "inventory"}


def alerts(db, day, allowed=None, at=None, k=None):
    start, end = business_day.bounds(day)
    out = []
    for n in _safe(lambda: _notices(db), []):
        if n.get("level") in ("critical", "warn"):
            out.append({"level": n["level"], "title": n["title"], "body": n.get("body") or "",
                        "tab": _NOTICE_TAB.get(n.get("module") or "", None),
                        "waiting": n.get("waiting")})
    ac = _safe(lambda: audit_svc.counts_since(db, start, allowed), {})
    if ac.get("refused"):
        out.append({"level": "warn", "tab": "audit",
                    "title": f"{ac['refused']} refused attempt(s) today",
                    "body": "Someone tried a change their account is not allowed to make."})
    if ac.get("failed_signins", 0) >= 3:
        out.append({"level": "warn", "tab": "audit",
                    "title": f"{ac['failed_signins']} failed sign-ins today",
                    "body": "Wrong usernames or passwords — check nobody is guessing."})
    cancel = _safe(lambda: pos_insights.cancelled(start, end, at), {"bills": 0})
    if cancel.get("bills"):
        out.append({"level": "warn", "tab": None,
                    "title": f"{cancel['bills']} store bill(s) cancelled today",
                    "body": f"Worth {inr(cancel['amount'])} — every cancellation is in the till's register."})
    if k and k.get("dead_stock", {}).get("critical"):
        d = k["dead_stock"]
        out.append({"level": "warn", "tab": "deadstock",
                    "title": f"{d['critical']} product(s) idle 180+ days",
                    "body": f"Dead stock totals {inr_short(d['value'])} across {d['count']} product(s)."})
    order = {"critical": 0, "warn": 1, "info": 2}
    out.sort(key=lambda a: order.get(a["level"], 3))
    return out


def _notices(db):
    from . import notifications
    return notifications.collect(db)


def activity(db, limit=15, allowed=None, at=None, hide_screens=()):
    """The audit trail's newest lines, with the stores' newest bills woven in —
    "POS Counter 04 generated bill TG26-001245" belongs in the same stream as
    "Ravi posted GRN-2045", and the till is the only one that knows it."""
    events = _safe(lambda: audit_svc.feed(db, limit=limit, allowed_warehouses=allowed,
                                          hide_screens=hide_screens)["events"], [])
    rows = [{"kind": "event", "at": e["at"], "local": e["local"], "who": e["who"],
             "role": e["role_label"], "module": e["module"], "summary": e["summary"],
             "where": e["warehouse"], "ref": e["ref"], "outcome": e["outcome"]}
            for e in events]
    for b in _safe(lambda: pos_insights.recent_bills(limit=limit, at=at), []):
        local = business_day.local(b["at"])
        at_iso = b["at"].replace(" ", "T")[:19] if b["at"] else None
        where = " · ".join(x for x in (b["branch"], b["floor"]) if x)
        rows.append({"kind": "bill", "at": at_iso,
                     "local": local.isoformat(timespec="minutes") if local else None,
                     "who": b["cashier"] or "—", "role": "Store",
                     "module": "POS", "where": where or None, "ref": b["bill_no"],
                     "outcome": "cancelled" if b["status"] == "cancelled" else "ok",
                     "summary": f"{b['counter'] or 'A counter'} generated bill {b['bill_no']} — {inr(b['total'])}"
                                + (" (cancelled)" if b["status"] == "cancelled" else "")})
    rows.sort(key=lambda r: r["at"] or "", reverse=True)
    return rows[:limit]


# ---------------------------------------------------------------------------
#  The whole screen
# ---------------------------------------------------------------------------
def overview(db, allowed=None, day=None, with_users=True):
    day = day or business_day.today()
    start, end = business_day.bounds(day)
    loc_ids = pos_insights.location_ids_for_warehouses(db, allowed) if allowed else None
    at = {"location_ids": loc_ids} if loc_ids is not None else None
    pos_ok = pos_insights.available()

    sales = _safe(lambda: pos_insights.totals(start, end, at), {"available": False})
    prof = _safe(lambda: pos_insights.margin(start, end, at), {"available": False})
    pur = _safe(lambda: purchases_between(db, start, end, allowed),
                {"grns": 0, "value": 0, "units": 0, "products": 0})
    pays = _safe(lambda: payments_between(db, start, end), {"count": 0, "value": 0})
    dn = _safe(lambda: debit_notes_between(db, start, end, allowed), {"count": 0, "value": 0})
    stock_rows = _safe(lambda: stock_loc.warehouse_totals(db, warehouse_ids=allowed), [])
    moves = _safe(lambda: movement_between(db, start, end, allowed), {"inward": 0, "outward": 0})
    transit = _safe(lambda: stock_loc.transfer_summary(db)["totals"].get("in_transit", 0), 0)
    pend = _safe(lambda: pending_grns(db, allowed), {"count": 0, "value": 0, "documents": 0})
    owed = _safe(lambda: outstanding(db), [])
    dead = _safe(lambda: dead_counts(db), {"days": 90, "count": 0, "value": 0, "critical": 0, "rows": []})
    low = _safe(lambda: pos_insights.low_stock(limit=8), {"available": False, "count": 0, "rows": []})
    sent_today = _safe(lambda: outwards_posted_between(db, start, end, allowed), 0)
    top = _safe(lambda: pos_insights.breakdown(
        *business_day.bounds(day - dt.timedelta(days=29), day), "product", at, limit=6), [])
    top_floors = _safe(lambda: pos_insights.breakdown(start, end, "floor", at, limit=6), [])

    k = {
        "sales": {"value": sales.get("net", 0), "gross": sales.get("gross", 0),
                  "bills": sales.get("bills", 0), "units": sales.get("units", 0),
                  "returns": sales.get("returns", 0), "available": bool(sales.get("available"))},
        "purchases": {"value": pur["value"], "grns": pur["grns"], "units": pur["units"],
                      "products": pur["products"]},
        "payments": {"value": pays["value"], "count": pays["count"]},
        "stock_value": {"value": round(sum(r["value"] for r in stock_rows), 2),
                        "qty": round(sum(r["qty"] for r in stock_rows), 3),
                        "items": sum(r["items"] for r in stock_rows)},
        "profit": {"value": prof.get("profit", 0), "margin_pct": prof.get("margin_pct"),
                   "revenue": prof.get("revenue", 0), "cost": prof.get("cost", 0),
                   "available": bool(prof.get("available"))},
        "discounts": {"value": sales.get("discount", 0)},
        "returns": {"value": round(float(sales.get("returns", 0) or 0) + dn["value"], 2),
                    "store": sales.get("returns", 0), "store_notes": sales.get("return_notes", 0),
                    "debit_notes": dn["count"], "debit_value": dn["value"]},
        "low_stock": {"count": low.get("count", 0), "available": bool(low.get("available"))},
        "dead_stock": {"count": dead["count"], "value": dead["value"],
                       "critical": dead["critical"], "days": dead["days"]},
        "pending_grns": {"count": pend["count"], "value": pend["value"],
                         "documents": pend["documents"]},
        "pending_payments": {"value": round(sum(b["outstanding"] for b in owed), 2),
                             "bills": len(owed),
                             "overdue": round(sum(b["outstanding"] for b in owed
                                                  if (b["days"] or 0) > 30), 2)},
        "movement": {"inward": moves["inward"], "outward": moves["outward"],
                     "in_transit": transit, "dispatched": sent_today},
        "transactions": {"count": int(sales.get("bills", 0) or 0) + pur["grns"] + pays["count"]
                                  + dn["count"] + int(sent_today or 0),
                         "bills": sales.get("bills", 0), "grns": pur["grns"],
                         "payments": pays["count"], "debit_notes": dn["count"],
                         "dispatches": sent_today},
    }

    hide = () if with_users else ("users", "settings")
    return {
        "day": day.isoformat(), "label": business_day.label(day, day),
        "generated_at": business_day.now_local().isoformat(timespec="minutes"),
        "scope": {"restricted": bool(allowed),
                  "warehouses": [r["name"] for r in stock_rows] if allowed else None,
                  "company_wide": ["dead_stock", "low_stock", "pending_payments", "payments"]},
        "pos": {"available": pos_ok},
        "counts": _safe(lambda: counts(db, allowed, with_users), {}),
        "kpis": k,
        "series": _safe(lambda: series(db, day, 14, allowed, at), None),
        "top_products": top,
        "top_floors": top_floors,
        "warehouses": sorted(({"name": r["name"], "value": r["value"], "qty": r["qty"]}
                              for r in stock_rows), key=lambda r: -r["value"]),
        "low_stock": low.get("rows", []),
        "dead_stock": [{"sku": r["sku"], "name": r["name"], "category": r["category"],
                        "qty": r["qty"], "value": r["stock_value"], "days": r["days_idle"]}
                       for r in sorted(dead["rows"], key=lambda r: -r["stock_value"])[:6]],
        "alerts": _safe(lambda: alerts(db, day, allowed, at, k), []),
        "activity": _safe(lambda: activity(db, 15, allowed, at, hide), []),
        "headline": (f"{business_day.label(day, day)}: {inr(k['sales']['value'])} store sales, "
                     f"{inr_short(k['purchases']['value'])} received on {k['purchases']['grns']} GRN(s), "
                     f"{fmt_qty(k['transactions']['count'])} transactions")
                    if pos_ok else None,
    }
