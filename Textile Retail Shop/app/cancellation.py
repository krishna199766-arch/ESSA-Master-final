"""Cancelling a bill — undoing everything it did, and keeping the bill to say so.

A bill cannot simply be deleted: its number is part of a statutory series, and
the one thing that series has to survive is being read in order with nothing
missing. So a cancelled bill stays, marked CANCELLED, and everything it moved is
put back:

  stock      every line — the free ones too — back on the shelf and on the
             branch it came off, with a `cancel` movement against the bill.
  loyalty    points it earned taken back, points it spent handed back.
  promotion  its promotions reversed, so the scheme reports stop counting them.
  coupons    a coupon spent on it comes back; coupons it earned are withdrawn.
  vouchers   store credit or an advance spent on it is available again — that
             happens by itself, because those balances only count live bills.
  money      what was taken by cash, card or UPI is to be handed back; the
             result lists it so the counter can say it.

Deliberately narrow: only a manager, only the same day, and only a bill nothing
else has happened to yet. A bill whose goods went home, came back, or went to a
tailor has a document hanging off it that a cancellation would contradict — that
bill is corrected with a return, which leaves its own trail.
"""
from datetime import datetime

from app import db, transfers, vouchers
from app.models import LoyaltyTxn, Product, PromotionAudit, StockMovement

MOVEMENT_REASON = "cancel"


def why_not(inv, user, today=None):
    """The reason this bill cannot be cancelled by this person, or None if it can."""
    # The bill's day as the bill records it: `invoice_date` is written in UTC, and
    # so is the day every sales report files it under.
    today = today or datetime.utcnow().date()
    if inv.is_cancelled:
        return "This bill is already cancelled."
    if not (user and getattr(user, "is_manager", False)):
        return "Only a manager can cancel a bill."
    if inv.invoice_date and inv.invoice_date.date() != today:
        return ("Only a bill raised today can be cancelled. An older bill is "
                "corrected with a return, which keeps its own record.")
    if inv.credit_notes:
        return "Goods have already been returned against this bill — raise a return for the rest."
    if any(i.delivery_lines for i in inv.items):
        return "Goods on this bill have been handed over at the delivery desk."
    if inv.alterations:
        return "A garment on this bill is out for alteration."
    used = vouchers.settlement_coupons_used(inv)
    if used:
        return (f"The coupon this bill earned ({used[0].code}) has already been used "
                "on another bill.")
    return None


def cancel(inv, user, reason):
    """Cancel `inv`. Call only after `why_not` has returned None. Commits nothing.

    Returns {"refund": {method: amount}, "vouchers": [(method, reference, amount)]}
    — what has to go back to the customer, so the screen can say it.
    """
    reason = (reason or "").strip()[:256]
    if not reason:
        raise ValueError("Say why the bill is being cancelled.")

    # ---- stock ---------------------------------------------------------------
    for item in inv.items:
        product = db.session.get(Product, item.product_id)
        qty = float(item.quantity or 0)
        if not product or qty <= 0:
            continue
        product.stock_qty = (product.stock_qty or 0) + qty
        if inv.location_id:
            transfers.move(inv.location_id, product.id, qty)
        db.session.add(StockMovement(
            product_id=product.id, change=qty, reason=MOVEMENT_REASON,
            reference=inv.invoice_number
                      + (f" @ {inv.location.name}" if inv.location else "")))

    # ---- promotions ------------------------------------------------------------
    for app_row in inv.promotions:
        if app_row.status == "reversed":
            continue
        app_row.status = "reversed"
        app_row.times_applied = 0
        app_row.qualifying_qty = 0.0
        app_row.reward_qty = 0.0
        app_row.benefit_value = 0.0
        db.session.add(PromotionAudit(
            scheme_id=app_row.scheme_id, scheme_code=app_row.scheme_code,
            scheme_name=app_row.scheme_name, invoice_id=inv.id,
            application_id=app_row.id, event="reversed",
            detail=f"{inv.invoice_number} cancelled: {reason}",
            company_id=inv.company_id, location_id=inv.location_id,
            counter_id=inv.counter_id, user_id=user.id if user else None))

    # ---- loyalty -----------------------------------------------------------------
    customer = inv.customer
    if customer:
        earned = float(inv.loyalty_earned or 0)
        if earned > 0:
            # never below zero — points already spent elsewhere are gone
            back = min(earned, float(customer.loyalty_points or 0))
            if back > 0:
                customer.loyalty_points = round((customer.loyalty_points or 0) - back, 2)
                db.session.add(LoyaltyTxn(customer_id=customer.id, points=-back,
                                          reason=f"cancel {inv.invoice_number}",
                                          invoice_id=inv.id))
        spent = float(inv.loyalty_redeemed or 0)
        if spent > 0:
            customer.loyalty_points = round((customer.loyalty_points or 0) + spent, 2)
            db.session.add(LoyaltyTxn(customer_id=customer.id, points=spent,
                                      reason=f"cancel {inv.invoice_number}",
                                      invoice_id=inv.id))
        customer.total_spent = round(max(0.0, (customer.total_spent or 0) - (inv.total or 0)), 2)

    # ---- coupons -------------------------------------------------------------------
    vouchers.void_for_invoice(inv)

    # ---- the bill itself -----------------------------------------------------------
    refund, restored = {}, []
    tenders = inv.payments or []
    if tenders:
        for p in tenders:
            if p.method in ("credit_note", "advance"):
                restored.append((p.method, p.reference, round(p.amount or 0, 2)))
            else:
                refund[p.method] = round(refund.get(p.method, 0) + (p.amount or 0), 2)
    else:
        refund[inv.payment_method or "cash"] = round(inv.total or 0, 2)

    inv.payment_status = "cancelled"
    inv.cancelled_at = datetime.utcnow()
    inv.cancelled_by_id = user.id if user else None
    inv.cancel_reason = reason
    return {"refund": refund, "vouchers": restored}
