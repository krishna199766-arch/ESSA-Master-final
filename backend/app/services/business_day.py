"""What "today" means to the business — its own calendar day, not UTC's.

Every timestamp this system writes is naive UTC (models.now), and so is every one
the till writes. That is right for storage and wrong for a question like "today's
sales": in Tiruppur the day starts at 18:30 UTC the evening before, so a UTC day
files the first five and a half hours of trading under yesterday.

So a business day is turned into a UTC window here, once, and every Command
Center figure is asked of that window. The offset is configuration
(config.BUSINESS_UTC_OFFSET_MINUTES) rather than a timezone database: India has
no daylight saving, and Windows ships no tz database for zoneinfo to read.
"""
import datetime as dt

from ..config import BUSINESS_UTC_OFFSET_MINUTES

OFFSET = dt.timedelta(minutes=BUSINESS_UTC_OFFSET_MINUTES)


def now_local() -> dt.datetime:
    return dt.datetime.utcnow() + OFFSET


def today() -> dt.date:
    return now_local().date()


def local(ts):
    """A stored UTC timestamp as the business's wall-clock time."""
    if ts is None:
        return None
    if isinstance(ts, str):
        try:
            ts = dt.datetime.fromisoformat(ts.replace("T", " ")[:26])
        except ValueError:
            return None
    return ts + OFFSET


def start_of(day: dt.date) -> dt.datetime:
    """The UTC instant a business day begins."""
    return dt.datetime.combine(day, dt.time()) - OFFSET


def bounds(date_from: dt.date, date_to: dt.date = None):
    """[start, end) in UTC for business days date_from..date_to inclusive."""
    date_to = date_to or date_from
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    return start_of(date_from), start_of(date_to + dt.timedelta(days=1))


def parse_day(value):
    """An ISO date (or datetime) string → date, else None."""
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def days(date_from: dt.date, date_to: dt.date):
    out, d = [], date_from
    while d <= date_to:
        out.append(d)
        d += dt.timedelta(days=1)
    return out


def label(date_from: dt.date, date_to: dt.date, now: dt.date = None) -> str:
    """How a period is said: Today, Yesterday, This month, 01-09-2026 to 15-09-2026."""
    now = now or today()
    if date_from == date_to:
        if date_from == now:
            return "Today"
        if date_from == now - dt.timedelta(days=1):
            return "Yesterday"
        return date_from.strftime("%d-%m-%Y")
    if date_from == now.replace(day=1) and date_to == now:
        return "This month"
    first_this = now.replace(day=1)
    last_prev = first_this - dt.timedelta(days=1)
    if date_from == last_prev.replace(day=1) and date_to == last_prev:
        return "Last month"
    if date_to == now and (date_to - date_from).days + 1 in (7, 14, 30, 90):
        return f"Last {(date_to - date_from).days + 1} days"
    return f"{date_from.strftime('%d-%m-%Y')} to {date_to.strftime('%d-%m-%Y')}"
