"""Money and quantities the way this business says them.

₹8,42,650 — not ₹842,650. Indian digit grouping puts the commas at thousand, lakh
and crore, and a figure grouped the Western way is read wrongly by exactly the
people it is written for. The short form is how the owner says it out loud —
₹8.42 L, ₹2.84 Cr — and the spoken form is what the voice answer reads back.
"""


def _grouped(whole: int) -> str:
    s = str(abs(int(whole)))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def inr(v, paise=False) -> str:
    """₹8,42,650 — rounded to the rupee unless `paise`."""
    v = _num(v)
    sign = "-" if v < 0 else ""
    v = abs(v)
    if paise:
        whole = int(v)
        frac = int(round((v - whole) * 100))
        if frac == 100:
            whole, frac = whole + 1, 0
        return f"{sign}₹{_grouped(whole)}.{frac:02d}"
    return f"{sign}₹{_grouped(int(round(v)))}"


def inr_short(v) -> str:
    """₹8.42 L / ₹2.84 Cr / ₹4,250 — for tiles and one-line answers."""
    v = _num(v)
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:.2f} Cr"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:.2f} L"
    return sign + inr(a)


def spoken_inr(v) -> str:
    """What the voice answer says: "8.42 lakh rupees", "2.84 crore rupees"."""
    v = _num(v)
    a = abs(v)
    neg = "minus " if v < 0 else ""
    if a >= 1e7:
        return f"{neg}{a / 1e7:.2f} crore rupees"
    if a >= 1e5:
        return f"{neg}{a / 1e5:.2f} lakh rupees"
    return f"{neg}{int(round(a)):,} rupees"


def qty(v) -> str:
    """1,284 — or 12.5 where the unit is divisible. Grouped the Indian way."""
    v = _num(v)
    if abs(v - round(v)) < 1e-9:
        return ("-" if v < 0 else "") + _grouped(int(round(abs(v))))
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def plural(n, one, many=None) -> str:
    n = _num(n)
    word = one if abs(n - 1) < 1e-9 else (many or one + "s")
    return f"{qty(n)} {word}"
