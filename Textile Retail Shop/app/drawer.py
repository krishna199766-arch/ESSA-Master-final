"""The cash drawer — the float put in, the count taken out, and what it should be.

A session belongs to one till. Between opening and closing, the cash that ought
to be in that drawer is:

    opening float
  + cash settled on bills rung at this till        (bills still standing)
  + advances taken in cash at this till
  − cash refunded on credit notes raised at this till
  − advances handed back in cash at this till

Card and UPI never touch a drawer, and store credit or an advance spent on a bill
is not cash either — so none of those are in it. A cancelled bill's cash is left
out, because cancelling hands it back.

`expected` is written onto the session when it is closed, so a closed drawer
keeps saying what the books expected at the time it was counted.
"""
from datetime import datetime

from sqlalchemy import func

from app import db
from app.models import CreditNote, CustomerAdvance, DrawerSession, Invoice, InvoicePayment


def current(counter_id):
    """The open session on this till, or None."""
    q = DrawerSession.query.filter(DrawerSession.closed_at.is_(None))
    q = (q.filter(DrawerSession.counter_id == counter_id) if counter_id
         else q.filter(DrawerSession.counter_id.is_(None)))
    return q.order_by(DrawerSession.opened_at.desc()).first()


def breakdown(session, until=None):
    """The expected cash, and the parts it is made of."""
    start, end = session.opened_at, until or session.closed_at or datetime.utcnow()
    till = session.counter_id

    def at_till(col):
        return col == till if till else col.is_(None)

    tendered = (db.session.query(func.coalesce(func.sum(InvoicePayment.amount), 0))
                .join(Invoice, Invoice.id == InvoicePayment.invoice_id)
                .filter(InvoicePayment.method == "cash", Invoice.live(),
                        at_till(Invoice.counter_id),
                        Invoice.invoice_date >= start, Invoice.invoice_date <= end)
                .scalar() or 0)
    # bills with no settlement rows (the floor-sales path) are one tender each
    unsplit = (db.session.query(func.coalesce(func.sum(Invoice.total), 0))
               .filter(Invoice.live(), at_till(Invoice.counter_id),
                       Invoice.payment_method == "cash",
                       Invoice.invoice_date >= start, Invoice.invoice_date <= end,
                       ~Invoice.payments.any())
               .scalar() or 0)
    advances_in = (db.session.query(func.coalesce(func.sum(CustomerAdvance.amount), 0))
                   .filter(CustomerAdvance.method == "cash", at_till(CustomerAdvance.counter_id),
                           CustomerAdvance.created_at >= start, CustomerAdvance.created_at <= end)
                   .scalar() or 0)
    refunds = (db.session.query(func.coalesce(func.sum(CreditNote.total), 0))
               .filter(CreditNote.refund_method == "cash", at_till(CreditNote.counter_id),
                       CreditNote.created_at >= start, CreditNote.created_at <= end)
               .scalar() or 0)
    advances_out = (db.session.query(func.coalesce(func.sum(CustomerAdvance.refunded), 0))
                    .filter(CustomerAdvance.refund_method == "cash",
                            at_till(CustomerAdvance.counter_id),
                            CustomerAdvance.refunded_at >= start,
                            CustomerAdvance.refunded_at <= end)
                    .scalar() or 0)
    parts = {
        "Opening float": round(session.opening_float or 0, 2),
        "Cash on bills": round(float(tendered) + float(unsplit), 2),
        "Advances taken in cash": round(float(advances_in), 2),
        # `or 0.0`: minus nothing is -0.0, which prints as "₹-0.00"
        "Cash refunds": -round(float(refunds), 2) or 0.0,
        "Advances refunded in cash": -round(float(advances_out), 2) or 0.0,
    }
    return round(sum(parts.values()), 2), parts


def open_session(user, company=None, location=None, counter=None, opening_float=0.0,
                 notes=None):
    if current(counter.id if counter else None):
        raise ValueError("The drawer on this till is already open — close it first.")
    if opening_float is None or opening_float < 0:
        raise ValueError("The opening float cannot be negative.")
    s = DrawerSession(company_id=company.id if company else None,
                      location_id=location.id if location else None,
                      counter_id=counter.id if counter else None,
                      opened_by_id=user.id, opening_float=round(opening_float, 2),
                      notes=(notes or "").strip()[:256] or None)
    db.session.add(s)
    return s


def close_session(session, user, counted, notes=None):
    if session.closed_at:
        raise ValueError("This drawer is already closed.")
    if counted is None or counted < 0:
        raise ValueError("Enter the cash counted in the drawer.")
    now = datetime.utcnow()
    expected, _ = breakdown(session, until=now)
    session.closed_at = now
    session.closed_by_id = user.id
    session.counted_cash = round(counted, 2)
    session.expected_cash = expected
    if notes:
        session.notes = ((session.notes + " · ") if session.notes else "") + notes.strip()[:200]
    return session
