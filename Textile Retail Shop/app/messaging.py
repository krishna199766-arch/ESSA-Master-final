"""Scheduled messages — birthday and anniversary wishes, offers, one-off notes.

A message is queued for a time and sent when it comes due. Sending needs a
provider: set MESSAGE_WEBHOOK_URL (and MESSAGE_WEBHOOK_TOKEN if the provider asks
for one) and each due message is POSTed there as {"to", "message"} — the shape
most SMS and WhatsApp gateways accept, or can be bridged to in a few lines.

Without a provider nothing leaves the building, and the log says exactly that:
a due message is marked `logged` rather than `sent`. A log that claimed delivery
of messages nobody sent would be worse than no log.

There is no background worker in the shop, so due messages go out when somebody
presses "Send due messages" on the Messages screen (or anything calls
`send_due`). That is honest about when they leave, and needs no scheduler to be
installed and kept running.

Times here are the shop's wall clock (`datetime.now()`), not UTC like the rest of
the records: "send at 9 in the morning" means 9 on the clock above the counter,
and it is typed into a datetime-local box that knows nothing about time zones.
"""
import json
import os
import urllib.request
from datetime import date, datetime, time, timedelta

from flask import current_app

from app import db
from app.models import Customer, ScheduledMessage

DEFAULT_TEMPLATES = {
    "birthday": "Happy birthday, {name}! Wishing you a wonderful year from all of us at {shop}.",
    "anniversary": "Happy anniversary, {name}! Warm wishes from all of us at {shop}.",
}


def provider():
    """(url, token) for the configured gateway, or (None, None)."""
    url = (os.environ.get("MESSAGE_WEBHOOK_URL") or
           (current_app.config.get("MESSAGE_WEBHOOK_URL") if current_app else "") or "").strip()
    token = (os.environ.get("MESSAGE_WEBHOOK_TOKEN") or
             (current_app.config.get("MESSAGE_WEBHOOK_TOKEN") if current_app else "") or "").strip()
    return (url or None), (token or None)


def template(kind):
    key = f"{kind.upper()}_MESSAGE"
    return (os.environ.get(key) or current_app.config.get(key) or DEFAULT_TEMPLATES.get(kind)
            or "{name}")


def render(text, customer):
    shop = current_app.config.get("SHOP_NAME", "our store")
    name = (customer.name if customer else "") or "there"
    return text.replace("{name}", name).replace("{shop}", shop)


def next_occurrence(day, today=None):
    """The next date on or after `today` that falls on `day`'s day and month.

    29 February falls on the 28th in a year without one — a customer born on a
    leap day still has a birthday every year.
    """
    if not day:
        return None
    today = today or date.today()
    for year in (today.year, today.year + 1):
        try:
            d = date(year, day.month, day.day)
        except ValueError:
            d = date(year, 2, 28)
        if d >= today:
            return d
    return None


def due_wishes(kind, days_ahead=7, today=None):
    """[(customer, date)] whose birthday/anniversary falls in the next `days_ahead` days."""
    today = today or date.today()
    col = Customer.dob if kind == "birthday" else Customer.anniversary
    out = []
    for c in Customer.query.filter(col.isnot(None)).all():
        when = next_occurrence(getattr(c, "dob" if kind == "birthday" else "anniversary"), today)
        if when and (when - today).days <= days_ahead:
            out.append((c, when))
    out.sort(key=lambda cw: cw[1])
    return out


def queue_wishes(kind, days_ahead=7, user_id=None, today=None, at_hour=9):
    """Queue the wishes coming up. Skips any already queued for that customer and day."""
    made = 0
    for customer, when in due_wishes(kind, days_ahead, today):
        send_at = datetime.combine(when, time(at_hour, 0))
        exists = (ScheduledMessage.query
                  .filter(ScheduledMessage.customer_id == customer.id,
                          ScheduledMessage.kind == kind,
                          ScheduledMessage.status != "cancelled",
                          ScheduledMessage.scheduled_for >= datetime.combine(when, time.min),
                          ScheduledMessage.scheduled_for <= datetime.combine(when, time.max))
                  .first())
        if exists:
            continue
        db.session.add(ScheduledMessage(customer_id=customer.id, phone=customer.phone,
                                        kind=kind, body=render(template(kind), customer),
                                        scheduled_for=send_at, created_by_id=user_id))
        made += 1
    return made


def queue_message(customers, body, when=None, kind="custom", user_id=None):
    made = 0
    for c in customers:
        db.session.add(ScheduledMessage(customer_id=c.id, phone=c.phone, kind=kind,
                                        body=render(body, c),
                                        scheduled_for=when or datetime.now(),
                                        created_by_id=user_id))
        made += 1
    return made


def _post(url, token, to, message):
    data = json.dumps({"to": to, "message": message}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 — admin-configured URL
        if resp.status >= 300:
            raise RuntimeError(f"gateway answered HTTP {resp.status}")


def send_due(now=None):
    """Send (or log) every queued message that has come due. Returns counts by outcome."""
    now = now or datetime.now()
    url, token = provider()
    counts = {"sent": 0, "failed": 0, "logged": 0}
    for m in (ScheduledMessage.query.filter(ScheduledMessage.status == "queued",
                                            ScheduledMessage.scheduled_for <= now)
              .order_by(ScheduledMessage.scheduled_for).all()):
        m.attempts = (m.attempts or 0) + 1
        if not (m.phone or "").strip():
            m.status, m.error = "failed", "No phone number on the customer"
        elif not url:
            m.status, m.error = "logged", "No SMS/WhatsApp provider configured — not sent"
        else:
            try:
                _post(url, token, m.phone.strip(), m.body)
                m.status, m.sent_at, m.error = "sent", datetime.now(), None
            except Exception as exc:                          # noqa: BLE001
                m.status, m.error = "failed", str(exc)[:250]
        counts[m.status] = counts.get(m.status, 0) + 1
    return counts
