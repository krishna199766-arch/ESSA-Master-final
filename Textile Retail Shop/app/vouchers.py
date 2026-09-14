"""Money a customer already has with the shop — store credit, advances, coupons.

Three things that look alike at the till and are different underneath:

  store credit  a credit note kept rather than refunded. Spent as a TENDER — the
                customer already paid for it — with the note's number as the
                payment's reference.
  advance       money left with the shop before a bill exists. Also a tender,
                drawn on the customer's own advances, oldest first.
  coupon        not money the customer paid at all, but a reduction the shop
                gives. So it comes off the bill as a DISCOUNT, not as a tender.

What is left of a credit note or an advance is never stored. It is always the
document's amount less the payments that name it on bills that still stand — so
cancelling a bill hands the money back without anybody writing a correction, and
the balance can never drift from the bills it was spent on.
"""
import secrets
from datetime import date, datetime, timedelta

from sqlalchemy import func

from app import db
from app.models import (VOUCHER_METHODS, Coupon, CouponCampaign, CouponRedemption,
                        CreditNote, CustomerAdvance, Invoice, InvoicePayment)

TOL = 0.01


def _spent(method, reference):
    """How much of one document has been spent on bills that still stand."""
    if not reference:
        return 0.0
    q = (db.session.query(func.coalesce(func.sum(InvoicePayment.amount), 0))
         .join(Invoice, Invoice.id == InvoicePayment.invoice_id)
         .filter(InvoicePayment.method == method,
                 InvoicePayment.reference == reference,
                 Invoice.live()))
    return round(float(q.scalar() or 0), 2)


# ---------------------------------------------------------------------------
#  store credit
# ---------------------------------------------------------------------------
def find_credit_note(code):
    code = (code or "").strip().upper()
    if not code:
        return None
    return CreditNote.query.filter(func.upper(CreditNote.number) == code).first()


def credit_note_used(note):
    return _spent("credit_note", note.number)


def credit_note_balance(note):
    """What is left to spend. Zero for a note that was refunded as money."""
    if (note.refund_method or "") != "store_credit":
        return 0.0
    return round(max(0.0, (note.total or 0) - credit_note_used(note)), 2)


# ---------------------------------------------------------------------------
#  advances
# ---------------------------------------------------------------------------
def advance_used(adv):
    return _spent("advance", adv.number)


def advance_balance(adv):
    return round(max(0.0, (adv.amount or 0) - (adv.refunded or 0) - advance_used(adv)), 2)


def open_advances(customer_id):
    """The customer's advances with something left on them, oldest first."""
    if not customer_id:
        return []
    rows = (CustomerAdvance.query.filter_by(customer_id=customer_id)
            .order_by(CustomerAdvance.created_at, CustomerAdvance.id).all())
    return [(a, advance_balance(a)) for a in rows if advance_balance(a) > TOL]


def customer_advance_balance(customer_id):
    return round(sum(b for _, b in open_advances(customer_id)), 2)


# ---------------------------------------------------------------------------
#  tenders at the till
# ---------------------------------------------------------------------------
def settle_vouchers(payments, customer_id):
    """Check every store-credit and advance tender, and pin each to a document.

    Returns the payment list with advance tenders split one row per advance they
    draw on (oldest first), so each payment names exactly the document it spent.
    Raises ValueError with a sentence the cashier can act on.
    """
    out, wanted = [], {}
    for p in payments:
        if p["method"] not in VOUCHER_METHODS:
            out.append(p)
            continue
        if p["method"] == "credit_note":
            note = find_credit_note(p.get("reference"))
            if note is None:
                raise ValueError(f"No credit note “{p.get('reference') or ''}” — "
                                 "type the number printed on it")
            if (note.refund_method or "") != "store_credit":
                raise ValueError(f"{note.number} was refunded as "
                                 f"{(note.refund_method or 'cash').replace('_', ' ')} — "
                                 "it is not store credit")
            key = ("credit_note", note.number)
            wanted[key] = round(wanted.get(key, 0) + p["amount"], 2)
            if wanted[key] > credit_note_balance(note) + TOL:
                raise ValueError(f"{note.number} has ₹{credit_note_balance(note):.2f} "
                                 f"left, not ₹{wanted[key]:.2f}")
            out.append({**p, "reference": note.number, "tendered": p["amount"]})
            continue

        # an advance: the customer's own money, so it needs the customer
        if not customer_id:
            raise ValueError("Attach the customer before paying from their advance")
        need = p["amount"]
        ref = (p.get("reference") or "").strip().upper()
        pool = open_advances(customer_id)
        if ref:
            pool = [(a, b) for a, b in pool if a.number.upper() == ref]
            if not pool:
                raise ValueError(f"No advance “{ref}” with a balance for this customer")
        for adv, balance in pool:
            balance = round(balance - wanted.get(("advance", adv.number), 0), 2)
            if balance <= TOL:
                continue
            take = round(min(balance, need), 2)
            out.append({"method": "advance", "amount": take, "tendered": take,
                        "reference": adv.number})
            wanted[("advance", adv.number)] = round(wanted.get(("advance", adv.number), 0) + take, 2)
            need = round(need - take, 2)
            if need <= TOL:
                break
        if need > TOL:
            raise ValueError(f"The customer's advance balance is "
                             f"₹{customer_advance_balance(customer_id):.2f} — "
                             f"₹{need:.2f} short of what was entered")
    return out


# ---------------------------------------------------------------------------
#  coupons
# ---------------------------------------------------------------------------
class CouponError(ValueError):
    pass


def find_coupon(code):
    code = (code or "").strip().upper()
    if not code:
        return None
    return Coupon.query.filter(func.upper(Coupon.code) == code).first()


def coupon_amount(coupon, bill, customer_id=None, today=None, discount_room=None):
    """What `coupon` takes off a bill of `bill` rupees, or CouponError saying why not.

    `discount_room` caps it at what the goods themselves are worth, so a coupon
    can take a bill down to its tax and no further.
    """
    today = today or date.today()
    if coupon is None:
        raise CouponError("No such coupon")
    c = coupon.campaign
    if not coupon.active or not (c and c.active):
        raise CouponError(f"{coupon.code} has been withdrawn")
    if coupon.valid_from and today < coupon.valid_from:
        raise CouponError(f"{coupon.code} is valid from {coupon.valid_from.strftime('%d-%m-%Y')}")
    if coupon.valid_to and today > coupon.valid_to:
        raise CouponError(f"{coupon.code} expired on {coupon.valid_to.strftime('%d-%m-%Y')}")
    if coupon.uses_allowed is not None and coupon.uses >= coupon.uses_allowed:
        raise CouponError(f"{coupon.code} has already been used")
    if coupon.customer_id and coupon.customer_id != customer_id:
        raise CouponError(f"{coupon.code} belongs to {coupon.customer.name if coupon.customer else 'another customer'} "
                          "— attach that customer to use it")
    if (c.min_bill or 0) > bill + TOL:
        raise CouponError(f"{coupon.code} needs a bill of ₹{c.min_bill:g} or more")
    if c.kind == "percent":
        amount = bill * (c.value or 0) / 100.0
        if c.max_discount:
            amount = min(amount, c.max_discount)
    else:
        amount = c.value or 0
    amount = min(amount, bill)
    if discount_room is not None:
        amount = min(amount, max(0.0, discount_room))
    return round(max(0.0, amount), 2)


def _new_code(prefix="CP"):
    while True:
        code = prefix + secrets.token_hex(3).upper()
        if not Coupon.query.filter_by(code=code).first():
            return code


def issue(campaign, customer_id=None, via="manual", invoice_id=None, user_id=None,
          today=None, code=None):
    today = today or date.today()
    days = max(1, int(campaign.valid_days or 30))
    coupon = Coupon(code=(code or _new_code()).strip().upper(), campaign_id=campaign.id,
                    customer_id=customer_id, valid_from=today,
                    valid_to=today + timedelta(days=days - 1),
                    uses_allowed=campaign.uses_per_coupon, issued_via=via,
                    issued_invoice_id=invoice_id, created_by_id=user_id)
    db.session.add(coupon)
    return coupon


def issue_at_settlement(invoice, user_id=None):
    """The coupons a bill earns by itself, one per campaign that pays out."""
    out = []
    for c in CouponCampaign.query.filter_by(active=True, issue_at_settlement=True).all():
        if (invoice.total or 0) + TOL >= (c.settlement_min_bill or 0):
            out.append(issue(c, customer_id=invoice.customer_id, via="settlement",
                             invoice_id=invoice.id, user_id=user_id))
    return out


def redeem(coupon, invoice, amount):
    db.session.add(CouponRedemption(coupon_id=coupon.id, invoice_id=invoice.id,
                                    amount=amount))


def settlement_coupons_used(invoice):
    """Coupons this bill earned that have already been spent elsewhere."""
    rows = Coupon.query.filter_by(issued_invoice_id=invoice.id).all()
    return [c for c in rows if c.uses]


def void_for_invoice(invoice):
    """A cancelled bill's coupon comes back, and the coupons it earned go."""
    now = datetime.utcnow()
    for r in CouponRedemption.query.filter_by(invoice_id=invoice.id, voided_at=None).all():
        r.voided_at = now
    for c in Coupon.query.filter_by(issued_invoice_id=invoice.id).all():
        c.active = False
