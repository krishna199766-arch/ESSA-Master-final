"""Ask anything about the business — and get ONE LINE back first.

    "Today's total sales"
    → Today's Sales: ₹8,42,650 across 4 warehouses, 12 stores and 18 POS counters.

The Reports screen's question box (services/nlq) picks one of forty registers
and hands back the table. That is the right answer for somebody about to work
through a register; it is the wrong one for the owner, who asked a question and
wants the number. So this answers first — a sentence with the figure in it, said
the way the business says money — and keeps the rows behind it for "View
details" and "Export".

HOW A QUESTION IS READ
----------------------
The same way nlq does, for the same reasons: the model's job is narrow and
checkable. It picks one INTENT from a fixed list and fills in the period, the
place and the thing asked about. It never writes a query. Every figure comes from
a function the dashboards already use, so the answer here and the tile on the
Command Center cannot disagree.

Without an API key the rules in `_offline` read the question instead — worse,
and the answer says it matched on keywords.

WHAT AN ACCOUNT IS SHOWN
------------------------
Everything is answered inside the warehouses the account is allotted
(Users & Access). A named warehouse the account is not allotted is refused, not
quietly widened. Product-level figures — dead stock, the stores' low stock — are
company-wide by nature, and say so.

A CODE IS NOT A QUESTION
------------------------
"GRN-2026-00045", "TG26-001245", a QR payload — anything that looks like a
document number or a tag is traced end to end (services/trace) rather than read.
"""
import difflib
import json
import re

from .. import models, runtime
from . import (audit as audit_svc, business_day, command_center as cc, nlq,
               pos_insights, stock_locations as stock_loc, trace as trace_svc)
from .figures import inr, inr_short, plural, qty as fmt_qty, spoken_inr

#: What each intent answers — the model reads these lines.
INTENTS = {
    "sales": "Sales taken at the stores' tills (POS) in a period. Optionally at one warehouse's stores, one store, a floor or a counter, or of a category/product such as 'ladies shirts'.",
    "sales_rank": "WHICH warehouse, store, floor, counter, cashier, category, product or payment method sold the MOST (or the least) in a period.",
    "discounts": "Discounts given on store bills in a period — the total, and who (which cashier) gave the most.",
    "profit": "Profit / gross margin on store sales in a period.",
    "purchases": "Goods received through GRN in a period — how many GRNs, how many units and products, and their value. 'How many products were received through GRN today'.",
    "invoices": "Supplier invoices entered in a period — how many came in, how many are still waiting for review, how many were confirmed and posted. 'Today's total invoices'. (Customer bills at the tills are `sales`.)",
    "lr": "LR / transport entries in a period — consignments booked into the register: how many, how many pieces, their value, and how many have not been received yet. 'Today's total LR entries'.",
    "payments": "Money paid to suppliers in a period.",
    "pending_payments": "Supplier bills still unpaid — how much is outstanding and to whom. 'Pending supplier payments'.",
    "pending_grns": "GRNs not yet posted and supplier invoices still waiting for review.",
    "returns": "Returns in a period — customer returns at the stores and debit notes to suppliers.",
    "stock": "Stock available right now — units and value. Optionally in one warehouse, on a store floor such as 'Ground Floor', or of a category/product.",
    "stock_rank": "WHICH warehouse (or category) holds the most — or least — stock value.",
    "low_stock": "Products at or below their reorder level in the stores — what is running low.",
    "dead_stock": "Dead stock: products with no sale or dispatch for N days (default 90) — how many and what they are worth. 'Products with no sales for 90 days'.",
    "transfers": "Stock transfers and dispatches — what is in transit between buildings, what was sent in a period.",
    "counts": "How many warehouses, stores, POS counters or users there are.",
    "activity": "What people did — actions on the audit trail in a period, optionally by one person. 'What did Ravi do today'.",
    "trace": "Track ONE item or document end to end: a product QR, SKU, piece code, GRN number, supplier invoice number, transfer code, LR number or store bill number.",
}
NONE = "none"
GROUPS = ("warehouse", "store", "floor", "counter", "cashier", "staff", "category",
          "product", "payment")


# ---------------------------------------------------------------------------
#  Reading the question — offline
# ---------------------------------------------------------------------------
_RANK = (" which ", " who ", " top ", "highest", " most ", " best ", "biggest", "largest",
         "lowest", " least ", "worst", "maximum", "minimum", "slowest")
_BOTTOM = ("lowest", " least ", "worst", "minimum", "slowest")
_GROUP_WORDS = (
    ("floor", (" floor",)),
    ("counter", (" counter", " till", " pos ")),
    ("warehouse", (" warehouse", " godown")),
    ("store", (" store", " shop", " branch", " outlet")),
    ("cashier", (" cashier", " biller", " staff", " salesman", " salesperson", " employee", " who ")),
    ("category", (" category", " categories", " section")),
    ("product", (" product", " item", " design", " garment")),
    ("payment", ("payment mode", "payment method", " upi", " card ", " tender")),
)
_SALES = (" sale", " sold", " sell", "revenue", "takings", "turnover", "billing",
          "விற்பனை", "விற்ற")
_STOCK = (" stock", "inventory", " on hand", " available", "holding", "இருப்பு", "சரக்கு")
_CODE_TOKEN = re.compile(r"^(?=.*\d)(?=.*[A-Za-z-])[A-Za-z0-9][A-Za-z0-9/_.:-]{3,}$|^\d{6,}$")


def _code_in(question):
    """A document number or tag in the question, or None."""
    q = question.strip()
    toks = q.split()
    if len(toks) == 1 and _CODE_TOKEN.match(q.strip("?.!,")):
        return q.strip("?.!,")
    if re.search(r"\b(track|trace|history of|journey of|where is|follow|locate|find)\b", q, re.I):
        for t in toks:
            t = t.strip("?.!,\"'“”")
            if _CODE_TOKEN.match(t) and not re.match(r"^\d{1,4}$", t):
                return t
    return None


def _has(t, words):
    return any(w in t for w in words)


def _norm(s):
    return " ".join(str(s or "").lower().split())


def _names_in(text, rows, key="name", extra=None):
    """Rows whose name (or code) appears in the text, longest name first."""
    t = f" {_norm(text)} "
    hits = []
    for r in rows:
        for cand in [r.get(key)] + ([r.get(extra)] if extra else []):
            n = _norm(cand)
            if n and len(n) >= 3 and re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", t):
                hits.append((len(n), r))
                break
    hits.sort(key=lambda x: -x[0])
    return [r for _, r in hits]


def _warehouse_rows(db):
    return [{"id": w.id, "name": w.name, "code": w.code} for w in db.query(models.Warehouse).all()]


def _store_rows(db):
    return [{"id": s.id, "name": s.name, "code": s.code, "warehouse_id": s.warehouse_id}
            for s in db.query(models.Store).all()]


def _offline(db, question):
    t = f" {question.lower().strip()} "
    today = business_day.today()
    lo, hi = nlq.relative_range(t, today=today)
    m_days = re.search(r"(\d{1,4})\s*\+?\s*days?", t)
    rank = _has(t, _RANK)
    group = next((g for g, words in _GROUP_WORDS if _has(t, words)), "")
    code = _code_in(question)

    if code:
        intent = "trace"
    elif _has(t, ("dead stock", "no sale", "not sold", "unsold", "not moving", "non moving",
                  "non-moving", "slow moving", "slow-moving", "no movement", " idle", "ageing",
                  "aging", "விற்காத")):
        intent = "dead_stock"
    elif _has(t, ("low stock", "low in stock", "running low", "reorder", "running out",
                  "out of stock", "short of stock", "stock out", "குறைந்த இருப்பு")):
        intent = "low_stock"
    elif _has(t, ("pending payment", "payments pending", "outstanding", "payable", " owe",
                  "unpaid", "supplier due", "dues", "yet to pay", "to be paid", "pending supplier",
                  "நிலுவை", "பாக்கி")):
        intent = "pending_payments"
    elif _has(t, ("pending grn", "grn pending", "not posted", "draft grn", "unposted",
                  "waiting for review", "pending invoice", "invoices pending", "yet to post")):
        intent = "pending_grns"
    elif _has(t, ("discount", "தள்ளுபடி")):
        intent = "discounts"
    elif _has(t, ("profit", "margin", "earning", "லாபம்")):
        intent = "profit"
    elif _has(t, (" return", "credit note", "debit note", "refund", "திரும்ப")):
        intent = "returns"
    elif _has(t, ("in transit", "transfer", "dispatch", "outward", "sent to store", "sent out")):
        intent = "transfers"
    elif _has(t, ("who did", "what did", "activity", "audit", " log ", "who changed",
                  "who posted", "who deleted", "actions by", "what has", "done today")) \
            and not _has(t, _SALES):
        intent = "activity"
    elif _has(t, ("payment", " paid", "paid to")) and not _has(t, _SALES):
        intent = "payments"
    elif _has(t, (" grn", "purchase", "bought", "received", " receipt", " inward", " buy ",
                  "கொள்முதல்", "வாங்கிய")):
        intent = "purchases"
    # "LR" as a word, not as two letters inside another one — `clr`, `colour` and
    # half the alphabet contain them.
    elif re.search(r"\blrs?\b|lorry receipt|consignment|transport entr|docket|"
                   r"போக்குவரத்து|லாரி", t):
        intent = "lr"
    elif _has(t, ("invoice", "bill entry", "document", "விலைப்பட்டியல்")):
        intent = "invoices"
    elif " how many " in t and _has(t, ("warehouses", "stores", "shops", "branches", "counters",
                                        "tills", " pos", "users", "people", "accounts")) \
            and not _has(t, _SALES + _STOCK):
        intent = "counts"
    elif _has(t, _STOCK):
        intent = "stock_rank" if rank and group in ("warehouse", "category") else "stock"
    elif _has(t, _SALES) or _has(t, (" sell", "business today")):
        intent = "sales_rank" if rank and group else "sales"
    else:
        intent = NONE

    # places named in the question
    wh = _names_in(question, _warehouse_rows(db), extra="code")
    st = _names_in(question, _store_rows(db), extra="code")
    floor = ""
    fm = re.search(r"\b([a-z0-9]+(?:\s+[a-z0-9]+)?\s+floor)\b", t)
    if fm and intent not in ("sales_rank", "stock_rank"):
        floor = fm.group(1).strip()
        floor = re.sub(r"^(on|in|at|the)\s+", "", floor)
    counter = ""
    cm = re.search(r"\b(?:counter|till|pos)\s*(?:no\.?|number|#)?\s*(\d{1,3})\b", t)
    if cm:
        counter = cm.group(1)
    wh_word = ""
    if not wh:
        wm = re.search(r"\bwarehouse\s+([a-z0-9-]+)\b", t)
        if wm and wm.group(1) not in ("has", "have", "is", "with", "in", "sell", "sold", "stock"):
            wh_word = f"warehouse {wm.group(1)}"

    item = ""
    im = re.search(r"\b(?:of|for)\s+([a-z][a-z&'\- ]{2,40}?)(?=\s+(?:this|last|today|yesterday|in|on|"
                   r"during|from|at|between|since|over|for|above|below|more|less)\b|[?.!,]|\s*$)", t)
    if im and intent in ("sales", "stock", "profit", "low_stock", "dead_stock", "sales_rank",
                         "pending_payments", "payments"):
        cand = im.group(1).strip()
        if not re.match(r"^(the day|today|yesterday|this|last|all|every|us|our|me|sale|sales)\b", cand) \
                and cand not in (floor,) and not _names_in(cand, _warehouse_rows(db) + _store_rows(db)):
            item = cand
    if intent in ("pending_payments", "payments") and not item:
        sm = re.search(r"\b(?:to|from)\s+([a-z][a-z0-9&.' -]{2,40}?)\s*[?.!]?\s*$", t)
        if sm:
            item = sm.group(1).strip()

    person = ""
    if intent in ("activity", "discounts"):
        pm = re.search(r"\b(?:did|by|has|of)\s+([a-z][a-z.]{1,20})\b(?=\s+(?:do|done|change|post|today|yesterday|this)|\s*[?.!]?\s*$)", t)
        if pm and pm.group(1) not in ("we", "you", "they", "the", "i", "it", "people", "someone", "anyone"):
            person = pm.group(1)

    return {
        "intent": intent, "reading": "matched on keywords",
        "date_from": lo or "", "date_to": hi or "",
        "warehouse": wh[0]["name"] if wh else wh_word, "store": st[0]["name"] if st else "",
        "floor": floor, "counter": counter, "person": person, "item": item,
        "code": code or "", "group_by": ("cashier" if intent == "discounts" and not group else group)
        if intent in ("sales_rank", "stock_rank", "discounts") else "",
        "order": "bottom" if _has(t, _BOTTOM) else ("top" if rank else ""),
        "days": m_days.group(1) if m_days else "",
        "confidence": "medium" if intent != NONE else "low",
        "engine": "keywords",
    }


# ---------------------------------------------------------------------------
#  Reading the question — the model
# ---------------------------------------------------------------------------
SYSTEM = """You read an Indian garment business owner's question and route it to ONE \
intent from a fixed list, filling in the period, the place and the thing asked about. \
You never compute figures — the system does.

The question may be English or Tamil, typed or spoken (so expect speech-recognition \
spellings: "grn" as "g r n", numbers as words).

INTENTS (key: what it answers):
{intents}
- none: nothing above answers it.

KNOWN NAMES (match what the person said to these, spelling as listed):
Warehouses: {warehouses}
Stores: {stores}
Store floors: {floors}
POS counters: {counters}

RULES
1. Pick the single best intent. A wrong intent is worse than "none".
2. Dates: resolve relative phrases against TODAY to ISO YYYY-MM-DD in date_from/date_to. \
Leave both empty when no period is mentioned — the system applies its own default.
3. warehouse / store / floor / counter: the name as listed above when the question names \
one, else empty. person: a person's name if the question is about someone. item: the \
product or category asked about in the person's own words ("ladies shirts"), else empty. \
code: a document number or tag code when intent is trace.
4. group_by: only for sales_rank / stock_rank / discounts — what is being ranked.
5. order: "top" for highest/most/best, "bottom" for lowest/least, else empty.
6. days: a number of days mentioned (e.g. dead stock "above 90 days"), else empty.
7. reading: one short plain-English line saying how you read the question.
8. Any field that does not apply is an empty string.

TODAY is {today}."""


def _schema():
    s = lambda d: {"type": "string", "description": d}          # noqa: E731
    return {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": sorted(INTENTS) + [NONE]},
            "reading": s("How the question was read, one line."),
            "date_from": s("ISO date or empty"), "date_to": s("ISO date or empty"),
            "warehouse": s("Warehouse name or empty"), "store": s("Store name or empty"),
            "floor": s("Store floor name or empty"), "counter": s("POS counter name or empty"),
            "person": s("Person's name or empty"), "item": s("Product/category words or empty"),
            "code": s("Document number or tag code, or empty"),
            "group_by": {"type": "string", "enum": [""] + list(GROUPS)},
            "order": {"type": "string", "enum": ["", "top", "bottom"]},
            "days": s("Number of days or empty"),
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": ["intent", "reading", "date_from", "date_to", "warehouse", "store",
                     "floor", "counter", "person", "item", "code", "group_by", "order",
                     "days", "confidence"],
        "additionalProperties": False,
    }


def _ask_model(db, question):
    import anthropic
    client = anthropic.Anthropic(api_key=runtime.get("anthropic_api_key"))
    names = lambda rows, k="name": ", ".join(sorted({str(r[k]) for r in rows if r.get(k)})[:80]) or "(none)"  # noqa: E731
    system = SYSTEM.format(
        intents="\n".join(f"- {k}: {v}" for k, v in INTENTS.items()),
        warehouses=names(_warehouse_rows(db)), stores=names(_store_rows(db)),
        floors=names(pos_insights.floors()), counters=names(pos_insights.counters()),
        today=business_day.today().isoformat())
    msg = client.messages.create(
        model=nlq.NLQ_MODEL, max_tokens=4096,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": question}],
        # see services/nlq for why output_config rides in extra_body
        extra_body={"output_config": {"effort": "low",
                                      "format": {"type": "json_schema", "schema": _schema()}}},
    )
    if msg.stop_reason == "refusal":
        raise RuntimeError("the model declined to answer this question")
    text = next((b.text for b in msg.content if getattr(b, "type", "") == "text"), "")
    data = json.loads(text)
    data["engine"] = "model"
    return data


def interpret(db, question):
    """The reading of a question. A code short-circuits both readers. Never raises."""
    code = _code_in(question)
    if code:
        out = _offline(db, question)
        out.update(intent="trace", code=code, reading=f"track {code} end to end",
                   confidence="high")
        return out
    if nlq.available():
        try:
            return _ask_model(db, question)
        except Exception as e:                         # noqa: BLE001
            out = _offline(db, question)
            out["degraded"] = f"the model could not be reached ({type(e).__name__}); matched on keywords instead"
            return out
    return _offline(db, question)


# ---------------------------------------------------------------------------
#  Period and place
# ---------------------------------------------------------------------------
def _period(read, default="today"):
    d0 = business_day.parse_day(read.get("date_from"))
    d1 = business_day.parse_day(read.get("date_to"))
    today = business_day.today()
    if not d0 and not d1:
        if default is None:
            return None
        d0 = d1 = today if default == "today" else today
        if default == "month":
            d0 = today.replace(day=1)
    d0, d1 = d0 or d1, d1 or d0
    if d1 < d0:
        d0, d1 = d1, d0
    lbl = business_day.label(d0, d1)
    start, end = business_day.bounds(d0, d1)
    return {"from": d0.isoformat(), "to": d1.isoformat(), "label": lbl,
            "start": start, "end": end}


def _titled(label, noun):
    """Today's Sales / Yesterday's Sales / Sales — This month."""
    if label in ("Today", "Yesterday"):
        return f"{label}'s {noun}"
    return f"{noun} — {label}"


def _phrase(label):
    """today / yesterday / this month / on 05-09-2026 / from … to …"""
    if label in ("Today", "Yesterday", "This month", "Last month") or label.startswith("Last "):
        return label.lower()
    if " to " in label:
        return f"from {label}"
    return f"on {label}"


def _best(text, rows, key="name", cutoff=0.72):
    if not text:
        return None
    want = _norm(text)
    for r in rows:
        if _norm(r.get(key)) == want or (r.get("code") and _norm(r["code"]) == want):
            return r
    for r in rows:
        n = _norm(r.get(key))
        if n and (want in n or n in want):
            return r
    names = {_norm(r.get(key)): r for r in rows if r.get(key)}
    close = difflib.get_close_matches(want, list(names), n=1, cutoff=cutoff)
    return names[close[0]] if close else None


class Refused(Exception):
    pass


def _scope(db, read, allowed):
    """Resolve the places a question names, inside what the account may see.

    Returns {wh_ids, at, place, notes, store, floor_ids, warehouse}. Raises Refused
    for a named warehouse or store the account is not allotted."""
    notes, place = [], None
    wh = st = None
    at = {}
    if read.get("warehouse"):
        wh = _best(read["warehouse"], _warehouse_rows(db))
        if wh is None:
            notes.append(f"No warehouse called “{read['warehouse']}” — answered for "
                         + ("all your warehouses." if allowed else "every warehouse."))
        elif allowed and wh["id"] not in allowed:
            raise Refused(f"You are not allotted the warehouse “{wh['name']}”.")
    if read.get("store"):
        st = _best(read["store"], _store_rows(db))
        if st is None:
            notes.append(f"No store called “{read['store']}” — the store filter was left off.")
        elif allowed and st["warehouse_id"] not in allowed:
            raise Refused(f"The store “{st['name']}” belongs to a warehouse you are not allotted.")

    if st:
        at["location_ids"] = pos_insights.location_ids_for_stores(
            db.query(models.Store).filter(models.Store.id == st["id"]).all())
        place = st["name"]
    elif wh:
        at["location_ids"] = pos_insights.location_ids_for_warehouses(db, [wh["id"]])
        place = wh["name"]
    elif allowed:
        at["location_ids"] = pos_insights.location_ids_for_warehouses(db, allowed)

    floor_ids = None
    if read.get("floor"):
        want = _norm(read["floor"])
        fl = pos_insights.floors()
        hits = [f for f in fl if _norm(f["name"]) == want] or \
               [f for f in fl if want in _norm(f["name"]) or _norm(f["name"]) in want]
        if at.get("location_ids") is not None:
            hits = [f for f in hits if f["location_id"] in at["location_ids"]]
        if hits:
            floor_ids = [f["id"] for f in hits]
            at["floor_ids"] = floor_ids
            place = hits[0]["name"] + (f" ({place})" if place else "")
        else:
            notes.append(f"No store floor called “{read['floor']}”.")
            floor_ids = []
            at["floor_ids"] = []
    if read.get("counter"):
        want = _norm(read["counter"])
        cs = pos_insights.counters()
        hits = [c for c in cs if _norm(c["name"]) == want
                or re.search(rf"(?<!\d)0*{re.escape(want)}(?!\d)", _norm(c["name"]) or "")]
        if at.get("location_ids") is not None:
            hits = [c for c in hits if c["location_id"] in at["location_ids"]]
        if hits:
            at["counter_ids"] = [c["id"] for c in hits]
            place = hits[0]["name"] + (f", {hits[0]['branch']}" if hits[0].get("branch") else "")
        else:
            notes.append(f"No POS counter called “{read['counter']}”.")
    return {"wh_ids": [wh["id"]] if wh else (list(allowed) if allowed else None),
            "at": at or None, "place": place, "notes": notes, "store": st,
            "warehouse": wh, "floor_ids": floor_ids}


# ---------------------------------------------------------------------------
#  Answers
# ---------------------------------------------------------------------------
def _out(title, headline, line, speak=None, facts=None, columns=None, rows=None,
         note=None, open_=None, ok=True):
    return {"ok": ok, "title": title, "headline": headline, "line": line,
            "speak": speak or re.sub(r"₹\s?([\d,\.]+)", r"\1 rupees", line),
            "facts": [{"label": k, "value": v} for k, v in (facts or []) if v not in (None, "")],
            "columns": columns or [], "rows": rows or [], "note": note, "open": open_}


def _no_pos():
    return _out("Store sales", "—",
                "The stores' till database cannot be read from here, so there are no store figures to answer with.",
                ok=False)


def _local_time(ts):
    loc = business_day.local(ts)
    return loc.strftime("%d-%m-%Y %H:%M") if loc else ""


def a_sales(db, read, sc, per):
    t = pos_insights.totals(per["start"], per["end"], sc["at"], read.get("item") or None)
    if not t.get("available"):
        return _no_pos()
    item = (read.get("item") or "").strip()
    stores = pos_insights.breakdown(per["start"], per["end"], "store", sc["at"], item or None)
    bw = pos_insights.branch_warehouses(db)
    whs = {bw[_norm(r["label"])][0] for r in stores if _norm(r["label"]) in bw}
    n_stores = len([r for r in stores if r["label"] != "(no branch)"])
    n_counters = t.get("counters") if not item else len(
        pos_insights.breakdown(per["start"], per["end"], "counter", sc["at"], item))
    noun = f"Sales of {item.title()}" if item else "Sales"
    title = _titled(per["label"], noun)
    if sc["place"]:
        title += f" at {sc['place']}"
    if item:
        line = (f"{title}: {inr(t['net'])} — {plural(t['units'], 'piece')} of "
                f"{plural(t.get('products', 0), 'product')} on {plural(t['bills'], 'bill')}.")
        speak = f"{title}: {spoken_inr(t['net'])}, {fmt_qty(t['units'])} pieces."
    elif sc["place"]:
        line = f"{title}: {inr(t['net'])} on {plural(t['bills'], 'bill')} — {plural(t['units'], 'unit')}."
        speak = f"{title}: {spoken_inr(t['net'])}."
    else:
        line = (f"{title}: {inr(t['net'])} across {plural(len(whs), 'warehouse')}, "
                f"{plural(n_stores, 'store')} and {plural(n_counters or 0, 'POS counter')}.")
        speak = f"{title}: {spoken_inr(t['net'])} across {plural(len(whs), 'warehouse')}."
    facts = [("Bills", fmt_qty(t["bills"])), ("Units", fmt_qty(t["units"])),
             ("Billed", inr(t["gross"])), ("Returns", inr(t["returns"]))]
    if not item:
        facts += [("Discount", inr(t["discount"])), ("Tax", inr(t["tax"]))]
    rows = [{"Store": r["label"], "Warehouse": (bw.get(_norm(r["label"])) or (None, None))[1] or "—",
             "Bills": r["bills"], "Sales": r["amount"]} for r in stores]
    note = ("Sales are net of customer returns made in the same period."
            + (" Line values include tax; a whole-bill discount cannot be split onto lines."
               if item else ""))
    return _out(title, inr(t["net"]), line, speak, facts, ["Store", "Warehouse", "Bills", "Sales"],
                rows, note, {"tab": "central"})


_GROUP_NOUN = {"warehouse": "Warehouse", "store": "Store", "floor": "Floor", "counter": "Counter",
               "cashier": "Cashier", "staff": "Salesperson", "category": "Category",
               "product": "Product", "payment": "Payment method"}


def a_sales_rank(db, read, sc, per):
    if not pos_insights.available():
        return _no_pos()
    by = read.get("group_by") or "store"
    item = read.get("item") or None
    if by == "warehouse":
        bw = pos_insights.branch_warehouses(db)
        agg = {}
        for r in pos_insights.breakdown(per["start"], per["end"], "store", sc["at"], item):
            name = (bw.get(_norm(r["label"])) or (None, "(store not matched)"))[1] or "(store not matched)"
            a = agg.setdefault(name, {"label": name, "bills": 0, "amount": 0.0})
            a["bills"] += r["bills"]
            a["amount"] = round(a["amount"] + r["amount"], 2)
        rows = sorted(agg.values(), key=lambda x: -x["amount"])
    else:
        rows = pos_insights.breakdown(per["start"], per["end"], by, sc["at"], item)
    rows = [r for r in rows if r["amount"]]
    noun = _GROUP_NOUN.get(by, by.title())
    if not rows:
        line = f"No store sales {_phrase(per['label'])} to rank by {noun.lower()}."
        return _out(f"Sales by {noun}", "—", line, ok=True)
    bottom = read.get("order") == "bottom"
    pick = rows[-1] if bottom else rows[0]
    total = sum(r["amount"] for r in rows) or 1
    word = "Lowest" if bottom else "Highest"
    title = f"{word} Sales by {noun} — {per['label']}"
    line = f"{word} Sales {_phrase(per['label'])}: {pick['label']} — {inr_short(pick['amount'])}."
    speak = f"{word} sales: {pick['label']}, {spoken_inr(pick['amount'])}."
    others = rows[1] if (not bottom and len(rows) > 1) else (rows[-2] if bottom and len(rows) > 1 else None)
    facts = [("Share", f"{pick['amount'] / total * 100:.1f}%"), ("Bills", fmt_qty(pick["bills"])),
             ("Next", f"{others['label']} — {inr_short(others['amount'])}" if others else None),
             (f"{noun}s selling", fmt_qty(len(rows)))]
    return _out(title, inr_short(pick["amount"]), line, speak, facts,
                [noun, "Bills", "Sales", "Share %"],
                [{noun: r["label"], "Bills": r["bills"], "Sales": r["amount"],
                  "Share %": round(r["amount"] / total * 100, 1)} for r in rows],
                None, {"tab": "central"})


def a_discounts(db, read, sc, per):
    if not pos_insights.available():
        return _no_pos()
    by = "staff" if read.get("group_by") == "staff" else "cashier"
    rows = pos_insights.discounts(per["start"], per["end"], by, sc["at"])
    if read.get("person"):
        want = _norm(read["person"])
        rows = [r for r in rows if want in _norm(r["label"])] or rows
    title = _titled(per["label"], "Discounts")
    if not rows:
        return _out(title, inr(0), f"No discounts were given {_phrase(per['label'])}.")
    total = sum(r["discount"] for r in rows)
    bills = sum(r["bills"] for r in rows)
    top = rows[0]
    line = (f"Highest Discount {_phrase(per['label'])}: {top['label']} — {inr(top['discount'])} "
            f"on {plural(top['bills'], 'bill')} (all discounts: {inr(total)} on {plural(bills, 'bill')}).")
    speak = f"Highest discount: {top['label']}, {spoken_inr(top['discount'])}."
    return _out(title, inr(top["discount"]), line, speak,
                [("Total discount", inr(total)), ("Bills discounted", fmt_qty(bills)),
                 ("Of which coupons", inr(sum(r["coupon"] for r in rows)))],
                ["Given by", "Bills", "Discount", "Coupons", "Billed"],
                [{"Given by": r["label"], "Bills": r["bills"], "Discount": r["discount"],
                  "Coupons": r["coupon"], "Billed": r["gross"]} for r in rows], None, None)


def a_profit(db, read, sc, per):
    m = pos_insights.margin(per["start"], per["end"], sc["at"], read.get("item") or None)
    if not m.get("available"):
        return _no_pos()
    title = _titled(per["label"], "Profit")
    if sc["place"]:
        title += f" at {sc['place']}"
    pct = f"{m['margin_pct']}%" if m["margin_pct"] is not None else "—"
    line = (f"{title}: {inr_short(m['profit'])} — {pct} margin on {inr_short(m['revenue'])} "
            "of sales before tax.")
    note = "Cost is each product's current cost at the till (the warehouse's average cost)."
    if m["uncosted_revenue"]:
        note += (f" {inr(m['uncosted_revenue'])} of the sales are of products with no cost price, "
                 "so their margin is overstated.")
    return _out(title, inr_short(m["profit"]), line,
                f"{title}: {spoken_inr(m['profit'])}, {pct} margin.",
                [("Sales before tax", inr(m["revenue"])), ("Cost of goods", inr(m["cost"])),
                 ("Margin", pct)], note=note)


def a_purchases(db, read, sc, per):
    p = cc.purchases_between(db, per["start"], per["end"], sc["wh_ids"])
    title = _titled(per["label"], "GRN Receipts")
    if sc["warehouse"]:
        title += f" at {sc['warehouse']['name']}"
    if not p["grns"]:
        return _out(title, "0", f"No GRNs were posted {_phrase(per['label'])}.",
                    open_={"tab": "purchases"})
    line = (f"{title}: {fmt_qty(p['units'])} units of {plural(p['products'], 'product')} on "
            f"{plural(p['grns'], 'GRN')}, worth {inr(p['value'])}.")
    rows = [{"GRN": r.grn_no or f"#{r.id}", "Supplier": r.supplier.name if r.supplier else "—",
             "Warehouse": r.warehouse.name if r.warehouse else "—",
             "Units": round(sum(float(l.qty or 0) for l in r.lines), 3),
             "Value": r.grand_total, "Posted": _local_time(r.posted_at)} for r in p["rows"]]
    return _out(title, fmt_qty(p["units"]), line,
                f"{title}: {fmt_qty(p['units'])} units on {plural(p['grns'], 'GRN')}, worth {spoken_inr(p['value'])}.",
                [("GRNs", fmt_qty(p["grns"])), ("Products", fmt_qty(p["products"])),
                 ("Value", inr(p["value"]))],
                ["GRN", "Supplier", "Warehouse", "Units", "Value", "Posted"], rows,
                "Counted by the day each GRN was POSTED — the day the goods became stock.",
                {"tab": "purchases"})


def a_invoices(db, read, sc, per):
    """Supplier invoices entered — and, because the word means two things in a
    shop, what the tills billed said beside it rather than instead of it."""
    q = db.query(models.Document).filter(models.Document.uploaded_at >= per["start"],
                                         models.Document.uploaded_at < per["end"])
    if sc["wh_ids"]:
        q = q.filter(models.Document.warehouse_id.in_(sc["wh_ids"]))
    rows = q.order_by(models.Document.uploaded_at.desc()).all()
    by_status = {}
    for d in rows:
        by_status[d.status] = by_status.get(d.status, 0) + 1
    waiting = by_status.get("needs_review", 0) + by_status.get("extracted", 0) \
        + by_status.get("uploaded", 0)
    done = by_status.get("confirmed", 0) + by_status.get("posted", 0)
    title = _titled(per["label"], "Invoice Entries")
    if sc["warehouse"]:
        title += f" at {sc['warehouse']['name']}"
    # Document carries a warehouse_id and no relationship to go with it, so the
    # names are looked up once rather than per row.
    wh_names = {w.id: w.name for w in db.query(models.Warehouse).all()}
    sales = pos_insights.totals(per["start"], per["end"], sc["at"]) if pos_insights.available() else {}
    line = (f"{title}: {plural(len(rows), 'supplier invoice')}"
            + (f" — {waiting} still to review, {done} confirmed." if rows else " — none came in."))
    if sales.get("bills"):
        line += (f" The tills billed {plural(sales['bills'], 'customer invoice')} "
                 f"worth {inr(sales['net'])}.")
    return _out(title, fmt_qty(len(rows)), line,
                f"{title}: {plural(len(rows), 'supplier invoice')}, {waiting} still to review.",
                [("To review", fmt_qty(waiting)), ("Confirmed or posted", fmt_qty(done)),
                 ("Customer bills", fmt_qty(sales.get("bills", 0)) if sales else None)],
                ["Invoice", "Supplier", "Status", "Warehouse", "Entered"],
                [{"Invoice": d.filename, "Supplier": d.supplier.name if d.supplier else "—",
                  "Status": d.status, "Warehouse": wh_names.get(d.warehouse_id, "—"),
                  "Entered": _local_time(d.uploaded_at)} for d in rows],
                "Counted by when the invoice was ENTERED here, not by the date the "
                "supplier printed on it.", {"tab": "documents"})


def a_lr(db, read, sc, per):
    """Consignments booked into the transport register."""
    q = db.query(models.LREntry).filter(models.LREntry.created_at >= per["start"],
                                        models.LREntry.created_at < per["end"])
    if sc["wh_ids"]:
        q = q.filter(models.LREntry.warehouse_id.in_(sc["wh_ids"]))
    rows = q.order_by(models.LREntry.id.desc()).all()
    pieces = round(sum(float(e.qty or 0) for e in rows), 3)
    value = round(sum(float(e.amount or 0) for e in rows), 2)
    unreceived = [e for e in rows if not (e.received_by or "").strip()]
    title = _titled(per["label"], "LR Entries")
    if sc["warehouse"]:
        title += f" at {sc['warehouse']['name']}"
    line = (f"{title}: {plural(len(rows), 'consignment')}"
            + (f" — {fmt_qty(pieces)} pieces worth {inr(value)}"
               + (f"; {len(unreceived)} not received yet." if unreceived else ", all received.")
               if rows else " — nothing was booked in."))
    return _out(title, fmt_qty(len(rows)), line,
                f"{title}: {plural(len(rows), 'consignment')}, {fmt_qty(pieces)} pieces.",
                [("Pieces", fmt_qty(pieces)), ("Goods value", inr(value)),
                 ("Not received", fmt_qty(len(unreceived)))],
                ["LR no", "Entry no", "Supplier", "Transport", "Pieces", "Value", "Received"],
                [{"LR no": e.lr_no, "Entry no": e.lr_entry_no, "Supplier": e.supplier_name,
                  "Transport": e.transport, "Pieces": e.qty, "Value": e.amount,
                  "Received": e.received_by or "—"} for e in rows],
                None, {"tab": "lr"})


def a_payments(db, read, sc, per):
    pays = cc.payments_between(db, per["start"], per["end"])
    rows = pays["rows"]
    who = (read.get("item") or read.get("person") or "").strip()
    if who:
        rows = [p for p in rows if p.supplier and _norm(who) in _norm(p.supplier.name)] or rows
    total = round(sum(float(p.paid_amount or 0) for p in rows), 2)
    title = _titled(per["label"], "Supplier Payments")
    line = (f"{title}: {inr(total)} across {plural(len(rows), 'payment')}."
            if rows else f"No supplier payments were recorded {_phrase(per['label'])}.")
    return _out(title, inr(total), line, None,
                [("Payments", fmt_qty(len(rows)))],
                ["Receipt", "Supplier", "Mode", "Paid", "Discount", "TDS", "Recorded"],
                [{"Receipt": p.receipt_no, "Supplier": p.supplier.name if p.supplier else "—",
                  "Mode": p.mode, "Paid": p.paid_amount, "Discount": p.discount_total,
                  "TDS": p.tds_total, "Recorded": _local_time(p.created_at)} for p in rows],
                "Supplier payments are company-wide — they are not filed under a warehouse.",
                {"tab": "payments"})


def a_pending_payments(db, read, sc, per):
    owed = cc.outstanding(db, supplier_name=(read.get("item") or read.get("person") or None))
    if not owed and (read.get("item") or read.get("person")):
        owed = cc.outstanding(db)
    agg = {}
    for b in owed:
        a = agg.setdefault(b["supplier"], {"Supplier": b["supplier"], "Bills": 0, "Outstanding": 0.0,
                                           "Oldest (days)": 0})
        a["Bills"] += 1
        a["Outstanding"] = round(a["Outstanding"] + b["outstanding"], 2)
        a["Oldest (days)"] = max(a["Oldest (days)"], b["days"] or 0)
    rows = sorted(agg.values(), key=lambda r: -r["Outstanding"])
    total = round(sum(b["outstanding"] for b in owed), 2)
    overdue = round(sum(b["outstanding"] for b in owed if (b["days"] or 0) > 30), 2)
    title = "Pending Supplier Payments"
    if not owed:
        return _out(title, inr(0), "Nothing is owed to suppliers — every posted bill is settled.",
                    open_={"tab": "payments"})
    line = (f"{title}: {inr_short(total)} across {plural(len(owed), 'bill')} from "
            f"{plural(len(rows), 'supplier')}"
            + (f" — {inr_short(overdue)} is more than 30 days old." if overdue else "."))
    return _out(title, inr_short(total), line,
                f"Pending supplier payments: {spoken_inr(total)} across {plural(len(owed), 'bill')}.",
                [("Largest", f"{rows[0]['Supplier']} — {inr_short(rows[0]['Outstanding'])}"),
                 ("Over 30 days", inr(overdue))],
                ["Supplier", "Bills", "Outstanding", "Oldest (days)"], rows,
                "Outstanding = the bill less what has been paid and what went back on debit notes.",
                {"tab": "payments"})


def a_pending_grns(db, read, sc, per):
    pend = cc.pending_grns(db, sc["wh_ids"])
    title = "Pending GRNs"
    line = (f"{title}: {plural(pend['count'], 'draft GRN')} worth {inr_short(pend['value'])} not yet "
            f"posted, and {plural(pend['documents'], 'invoice')} waiting for review.")
    rows = [{"GRN": p.grn_no or f"#{p.id}", "Supplier": p.supplier.name if p.supplier else "—",
             "Warehouse": p.warehouse.name if p.warehouse else "—", "Invoice": p.invoice_number,
             "Value": p.grand_total, "Raised": _local_time(p.created_at)} for p in pend["rows"]]
    return _out(title, fmt_qty(pend["count"]), line, None,
                [("Draft value", inr(pend["value"])), ("Invoices to review", fmt_qty(pend["documents"]))],
                ["GRN", "Supplier", "Warehouse", "Invoice", "Value", "Raised"], rows, None,
                {"tab": "purchases"})


def a_returns(db, read, sc, per):
    t = pos_insights.totals(per["start"], per["end"], sc["at"]) if pos_insights.available() else {}
    dn = cc.debit_notes_between(db, per["start"], per["end"], sc["wh_ids"])
    store_v = float(t.get("returns") or 0)
    total = round(store_v + dn["value"], 2)
    title = _titled(per["label"], "Returns")
    line = (f"{title}: {inr(total)} — {plural(t.get('return_notes', 0), 'customer return')} "
            f"({inr(store_v)}) and {plural(dn['count'], 'debit note')} to suppliers ({inr(dn['value'])}).")
    rows = [{"Kind": "Debit note", "Number": r.code, "Party": r.supplier.name if r.supplier else "—",
             "Value": r.total, "When": _local_time(r.posted_at)} for r in dn["rows"]]
    if t.get("return_notes"):
        rows.insert(0, {"Kind": "Customer returns", "Number": f"{t['return_notes']} credit note(s)",
                        "Party": "Stores", "Value": store_v, "When": per["label"]})
    return _out(title, inr(total), line, None,
                [("Customer returns", inr(store_v)), ("Debit notes", inr(dn["value"]))],
                ["Kind", "Number", "Party", "Value", "When"], rows, None, {"tab": "returns"})


def _match_words(words, *fields):
    hay = " ".join(_norm(f) for f in fields if f)
    return all(pos_insights._stem(w) in hay or w in hay for w in words)


def a_stock(db, read, sc, per):
    item = (read.get("item") or "").strip()
    words = pos_insights.words_of(item)
    if sc["floor_ids"] is not None:
        s = pos_insights.stock(sc["floor_ids"], item or None)
        if not s.get("available"):
            return _no_pos()
        title = f"Stock on {sc['place'] or read.get('floor')}"
        line = (f"{title}: {plural(s['units'], 'piece')} of {plural(s['items'], 'product')} "
                f"worth {inr_short(s['value'])} at cost.")
        return _out(title, fmt_qty(s["units"]), line,
                    f"{title}: {fmt_qty(s['units'])} pieces worth {spoken_inr(s['value'])}.",
                    [("Products", fmt_qty(s["items"])), ("Value at cost", inr(s["value"]))],
                    ["SKU", "Product", "Category", "Floor", "Qty", "Value"],
                    [{"SKU": r["sku"], "Product": r["product"], "Category": r["category"],
                      "Floor": r["floor"] or "—", "Qty": r["qty"], "Value": r["value"]}
                     for r in s["rows"][:200]],
                    "A store's stock is one figure for the whole shop; the floor is where the pieces are kept.")

    if sc["store"]:
        s = pos_insights.stock(None, item or None)
        if not s.get("available"):
            return _no_pos()
        title = "Stock in the stores" + (f" — {item.title()}" if item else "")
        line = (f"{title}: {plural(s['units'], 'piece')} of {plural(s['items'], 'product')} "
                f"worth {inr_short(s['value'])} at cost.")
        return _out(title, fmt_qty(s["units"]), line, None,
                    [("Products", fmt_qty(s["items"])), ("Value at cost", inr(s["value"]))],
                    ["SKU", "Product", "Category", "Floor", "Qty", "Value"],
                    [{"SKU": r["sku"], "Product": r["product"], "Category": r["category"],
                      "Floor": r["floor"] or "—", "Qty": r["qty"], "Value": r["value"]}
                     for r in s["rows"][:200]],
                    f"The till keeps one stock figure for every branch it serves, so this is not "
                    f"{sc['store']['name']} alone — ask by floor to narrow it.")

    q = db.query(models.StockBalance).filter(models.StockBalance.qty > stock_loc.TOLERANCE)
    if sc["wh_ids"]:
        q = q.filter(models.StockBalance.warehouse_id.in_(sc["wh_ids"]))
    per_product, whs = {}, set()
    for b in q.all():
        p = b.product
        if not p or (words and not _match_words(words, p.description, p.category,
                                                p.category_section, p.sku, p.brand)):
            continue
        r = per_product.setdefault(p.id, {"SKU": p.sku, "Product": p.description,
                                          "Category": p.category or "", "Qty": 0.0, "Value": 0.0})
        r["Qty"] = round(r["Qty"] + float(b.qty or 0), 3)
        r["Value"] = round(r["Value"] + b.value, 2)
        whs.add(b.warehouse_id)
    rows = sorted(per_product.values(), key=lambda r: -r["Value"])
    wq, wv = round(sum(r["Qty"] for r in rows), 3), round(sum(r["Value"] for r in rows), 2)

    if sc["warehouse"]:
        title = f"Stock at {sc['warehouse']['name']}" + (f" — {item.title()}" if item else "")
        line = f"{title}: {plural(wq, 'unit')} of {plural(len(rows), 'product')} worth {inr_short(wv)}."
        facts = [("Products", fmt_qty(len(rows))), ("Value", inr(wv))]
    else:
        s = pos_insights.stock(None, item or None) if pos_insights.available() else {}
        title = "Stock Available" + (f" — {item.title()}" if item else "")
        line = (f"{title}: {plural(wq, 'unit')} worth {inr_short(wv)} in {plural(len(whs), 'warehouse')}"
                + (f", plus {plural(s['units'], 'unit')} worth {inr_short(s['value'])} in the stores."
                   if s.get("available") else "."))
        facts = [("In warehouses", f"{fmt_qty(wq)} units · {inr_short(wv)}"),
                 ("In stores", f"{fmt_qty(s['units'])} units · {inr_short(s['value'])}" if s.get("available") else None),
                 ("Products", fmt_qty(len(rows)))]
    return _out(title, fmt_qty(wq), line, None, facts,
                ["SKU", "Product", "Category", "Qty", "Value"], rows[:200],
                "Warehouse stock is valued at each warehouse's own average cost.",
                {"tab": "inventory"})


def a_stock_rank(db, read, sc, per):
    bottom = read.get("order") == "bottom"
    if read.get("group_by") == "category":
        agg = {}
        q = db.query(models.StockBalance).filter(models.StockBalance.qty > stock_loc.TOLERANCE)
        if sc["wh_ids"]:
            q = q.filter(models.StockBalance.warehouse_id.in_(sc["wh_ids"]))
        for b in q.all():
            cat = (b.product.category if b.product else None) or "(uncategorised)"
            a = agg.setdefault(cat, {"label": cat, "value": 0.0, "qty": 0.0})
            a["value"] = round(a["value"] + b.value, 2)
            a["qty"] = round(a["qty"] + float(b.qty or 0), 3)
        rows, noun = sorted(agg.values(), key=lambda r: -r["value"]), "Category"
    else:
        rows = [{"label": r["name"], "value": r["value"], "qty": r["qty"]}
                for r in stock_loc.warehouse_totals(db, warehouse_ids=sc["wh_ids"])]
        rows.sort(key=lambda r: -r["value"])
        noun = "Warehouse"
    if not rows:
        return _out(f"Stock Value by {noun}", "—", "No stock is recorded anywhere yet.")
    pick = rows[-1] if bottom else rows[0]
    word = "Lowest" if bottom else "Highest"
    line = f"{word} Stock Value: {pick['label']} — {inr_short(pick['value'])}."
    total = sum(r["value"] for r in rows) or 1
    return _out(f"{word} Stock Value by {noun}", inr_short(pick["value"]), line,
                f"{word} stock value: {pick['label']}, {spoken_inr(pick['value'])}.",
                [("Share", f"{pick['value'] / total * 100:.1f}%"), ("Units", fmt_qty(pick["qty"]))],
                [noun, "Units", "Value", "Share %"],
                [{noun: r["label"], "Units": r["qty"], "Value": r["value"],
                  "Share %": round(r["value"] / total * 100, 1)} for r in rows],
                None, {"tab": "central"})


def a_low_stock(db, read, sc, per):
    low = pos_insights.low_stock(limit=500, item=read.get("item") or None)
    if not low.get("available"):
        return _no_pos()
    title = "Low Stock"
    rows = low["rows"]
    line = (f"{title}: {plural(low['count'], 'product')} at or below reorder level in the stores"
            + (f" — emptiest is {rows[0]['product']} with {fmt_qty(rows[0]['stock'])} left." if rows else "."))
    return _out(title, fmt_qty(low["count"]), line,
                f"Low stock: {plural(low['count'], 'product')} at or below reorder level.",
                [("Out of stock", fmt_qty(sum(1 for r in rows if r["stock"] <= 0)))],
                ["SKU", "Product", "Category", "Floor", "Stock", "Reorder level"],
                [{"SKU": r["sku"], "Product": r["product"], "Category": r["category"],
                  "Floor": r["floor"] or "—", "Stock": r["stock"],
                  "Reorder level": r["reorder_level"]} for r in rows],
                "Only the stores keep reorder levels — warehouse stock has none, so it is not part of this.")


def a_dead_stock(db, read, sc, per):
    days = int(read["days"]) if str(read.get("days") or "").isdigit() else None
    d = cc.dead_counts(db, days)
    words = pos_insights.words_of(read.get("item") or "")
    rows = [r for r in d["rows"] if not words or _match_words(words, r["name"], r["category"], r["sku"])]
    value = round(sum(r["stock_value"] for r in rows), 2)
    title = f"Dead Stock ({d['days']}+ days)"
    line = (f"Dead Stock: {plural(len(rows), 'product')} worth {inr_short(value)} with no sale or "
            f"dispatch for {d['days']}+ days.")
    rows.sort(key=lambda r: -r["stock_value"])
    return _out(title, fmt_qty(len(rows)), line,
                f"Dead stock: {plural(len(rows), 'product')} worth {spoken_inr(value)}.",
                [("Value at cost", inr(value)), ("Idle 180+ days", fmt_qty(sum(1 for r in rows if r['days_idle'] >= 180)))],
                ["SKU", "Product", "Category", "Qty", "Value", "Idle days", "Last sold"],
                [{"SKU": r["sku"], "Product": r["name"], "Category": r["category"], "Qty": r["qty"],
                  "Value": r["stock_value"], "Idle days": r["days_idle"],
                  "Last sold": r["last_sold"] or "never"} for r in rows[:500]],
                "Idle means no till sale and no dispatch since the date shown. Company-wide.",
                {"tab": "deadstock"})


def a_transfers(db, read, sc, per):
    q = db.query(models.StockOutward).filter(models.StockOutward.status == "posted")
    if sc["wh_ids"]:
        q = q.filter(models.StockOutward.from_warehouse_id.in_(sc["wh_ids"])
                     | models.StockOutward.to_warehouse_id.in_(sc["wh_ids"]))
    transit = [o for o in q.all() if o.to_warehouse_id]
    sent = db.query(models.StockOutward).filter(models.StockOutward.posted_at >= per["start"],
                                                models.StockOutward.posted_at < per["end"])
    if sc["wh_ids"]:
        sent = sent.filter(models.StockOutward.from_warehouse_id.in_(sc["wh_ids"]))
    sent = sent.all()
    t_units = round(sum(o.total_qty for o in transit), 3)
    s_units = round(sum(o.total_qty for o in sent), 3)
    title = "Transfers"
    line = (f"In Transit: {plural(t_units, 'unit')} on {plural(len(transit), 'transfer')}; "
            f"{_phrase(per['label'])} {plural(len(sent), 'dispatch', 'dispatches')} went out ({fmt_qty(s_units)} units).")
    rows = [{"Code": o.code, "From": o.from_warehouse.name if o.from_warehouse else o.from_location,
             "To": o.to_destination, "Units": o.total_qty,
             "Status": "in transit" if o in transit else o.status,
             "Dispatched": _local_time(o.posted_at)} for o in (transit + [x for x in sent if x not in transit])]
    return _out(title, fmt_qty(t_units), line, None,
                [("Sent " + _phrase(per["label"]), fmt_qty(s_units))],
                ["Code", "From", "To", "Units", "Status", "Dispatched"], rows, None,
                {"tab": "outward"})


def a_counts(db, read, sc, per, role):
    from .users import rank, ROLE_RANK
    c = cc.counts(db, sc["wh_ids"], with_users=rank(role) >= ROLE_RANK["superadmin"])
    line = (f"{plural(c['warehouses'], 'warehouse')}, {plural(c['stores'], 'store')} and "
            f"{plural(c['counters'], 'POS counter')}"
            + (f", with {plural(c['users'], 'active user')}." if "users" in c else "."))
    return _out("Locations & People", fmt_qty(c["warehouses"]), line, None,
                [(k.title(), fmt_qty(v)) for k, v in c.items()], open_={"tab": "locations"})


def a_activity(db, read, sc, per, role, allowed):
    from .users import rank, ROLE_RANK
    who = None
    if read.get("person"):
        users = [{"name": u.full_name or u.username, "username": u.username}
                 for u in db.query(models.User).all()]
        who = _best(read["person"], users) or _best(read["person"], users, key="username")
    hide = () if rank(role) >= ROLE_RANK["superadmin"] else ("users", "settings")
    f = audit_svc.feed(db, limit=200, username=who["username"] if who else None,
                       date_from=per["from"], date_to=per["to"],
                       allowed_warehouses=allowed, hide_screens=hide)
    ev = f["events"]
    people = {e["username"] for e in ev if e["username"]}
    title = _titled(per["label"], "Activity") + (f" — {who['name']}" if who else "")
    if not ev:
        return _out(title, "0", f"Nothing was recorded on the audit trail {_phrase(per['label'])}"
                    + (f" for {who['name']}." if who else "."), open_={"tab": "audit"})
    last = ev[0]
    line = (f"{title}: {plural(len(ev), 'action')} by {plural(len(people), 'person', 'people')} — "
            f"latest, {last['who']} {last['summary']} at {(last['local'] or '')[11:16]}.")
    return _out(title, fmt_qty(len(ev)), line, None,
                [("Refused attempts", fmt_qty(sum(1 for e in ev if e["outcome"] == "refused")))],
                ["Time", "Who", "Module", "What", "Where"],
                [{"Time": (e["local"] or "").replace("T", " "), "Who": e["who"], "Module": e["module"],
                  "What": e["summary"], "Where": e["warehouse"] or "—"} for e in ev],
                None, {"tab": "audit"})


# ---------------------------------------------------------------------------
#  The whole answer
# ---------------------------------------------------------------------------
_DEFAULT_PERIOD = {"sales": "today", "sales_rank": "today", "discounts": "today",
                   "profit": "today", "purchases": "today", "payments": "today",
                   "returns": "today", "transfers": "today", "activity": "today",
                   "invoices": "today", "lr": "today"}


def ask(db, question, role="admin", allowed=None):
    """Read the question, answer it in one line, keep the rows behind it.
    Always returns a dict; never raises for a question it cannot answer."""
    question = (question or "").strip()
    if not question:
        return {"ok": False, "question": question, "line": "Ask something — or press the microphone.",
                "interpretation": {"engine": "none", "reading": "nothing was asked"}}

    read = interpret(db, question)
    intent = read.get("intent") or NONE
    interp = {"intent": intent, "reading": read.get("reading") or "",
              "engine": read.get("engine"), "confidence": read.get("confidence"),
              "degraded": read.get("degraded"),
              "applied": {k: read.get(k) for k in ("date_from", "date_to", "warehouse", "store",
                                                   "floor", "counter", "person", "item", "code",
                                                   "group_by", "order", "days") if read.get(k)},
              "notes": []}

    if intent == "trace":
        out = trace_svc.find(db, read.get("code") or question, allowed=allowed)
        out.setdefault("title", "Track")
        out.setdefault("headline", "")
        out.setdefault("facts", [])
        out.setdefault("columns", [])
        out.setdefault("rows", [])
        return {**out, "question": question, "intent": "trace", "interpretation": interp}

    if intent == NONE or intent not in INTENTS:
        return {"ok": False, "question": question, "intent": NONE, "title": "Not sure",
                "line": "I couldn't match that to something I can answer yet — try one of the examples, "
                        "or type a GRN, bill or QR code to track it.",
                "interpretation": interp, "suggestions": [e["q"] for e in examples()[:6]]}

    try:
        sc = _scope(db, read, allowed)
    except Refused as r:
        return {"ok": False, "question": question, "intent": intent, "title": "Not allotted",
                "line": str(r), "interpretation": interp}
    interp["notes"] = sc["notes"]

    per = _period(read, _DEFAULT_PERIOD.get(intent, "today"))
    builders = {
        "sales": a_sales, "sales_rank": a_sales_rank, "discounts": a_discounts,
        "profit": a_profit, "purchases": a_purchases, "payments": a_payments,
        "invoices": a_invoices, "lr": a_lr,
        "pending_payments": a_pending_payments, "pending_grns": a_pending_grns,
        "returns": a_returns, "stock": a_stock, "stock_rank": a_stock_rank,
        "low_stock": a_low_stock, "dead_stock": a_dead_stock, "transfers": a_transfers,
    }
    try:
        if intent == "counts":
            out = a_counts(db, read, sc, per, role)
        elif intent == "activity":
            out = a_activity(db, read, sc, per, role, allowed)
        else:
            out = builders[intent](db, read, sc, per)
    except Exception as e:                             # noqa: BLE001
        return {"ok": False, "question": question, "intent": intent, "title": "Could not answer",
                "line": f"Something went wrong working that out ({type(e).__name__}). "
                        "The figures on the dashboards are unaffected.",
                "interpretation": interp}

    timeless = intent in ("pending_payments", "pending_grns", "stock", "stock_rank",
                          "low_stock", "dead_stock", "counts")
    return {**out, "question": question, "intent": intent,
            "period": None if timeless else {k: per[k] for k in ("from", "to", "label")},
            "interpretation": interp}


def examples():
    """Questions that work — the ones from the brief, and the shape of each answer."""
    return [
        {"q": "Today's total sales", "note": "one line, every store"},
        {"q": "Today's total invoices", "note": "supplier invoices entered"},
        {"q": "Today's total LR entries", "note": "consignments booked in"},
        {"q": "Which floor has the highest sales?", "note": "ranked"},
        {"q": "How much stock is available in Ground Floor?", "note": "store floor"},
        {"q": "Show sales of ladies shirts this month", "note": "by category words"},
        {"q": "Which products are low in stock?", "note": "reorder level"},
        {"q": "Show products with no sales for 90 days", "note": "dead stock"},
        {"q": "Who gave the highest discount today?", "note": "by cashier"},
        {"q": "Show pending supplier payments", "note": "outstanding"},
        {"q": "How many products were received through GRN today?", "note": "GRN receipts"},
        {"q": "Which warehouse has the highest stock value?", "note": "ranked"},
        {"q": "What did people do today?", "note": "audit trail"},
        {"q": "Today's profit", "note": "margin at cost"},
    ]
