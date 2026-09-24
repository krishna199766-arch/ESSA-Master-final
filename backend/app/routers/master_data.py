"""
One CRUD surface for all seventeen masters.

Every route here takes the master's key and reads its definition from
services/master_defs.py — so this file does not grow when a master is added, and
a field added to a definition is immediately typed, validated, listed and saved
without a line changing here. Validation is driven from the same definition the
form renders from, which is the point: the * on screen and the refusal on save
cannot drift apart, because they are the same `req` flag.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models
from ..services import master_defs as defs
from ..services import masters as masters_svc

router = APIRouter(prefix="/api/master-data", tags=["masters"])


class RecordIn(BaseModel):
    """A master row as the form sends it. `data` carries every declared field by
    key; grids and matrix are the child tables, when the master has them."""
    data: Dict[str, Any] = {}
    grids: Optional[Dict[str, List[Dict[str, Any]]]] = None
    matrix: Optional[Dict[str, Dict[str, bool]]] = None


def _def(key: str):
    d = defs.get(key)
    if not d:
        raise HTTPException(404, f"no master called '{key}'")
    return d


def _blank(v):
    return v is None or (isinstance(v, str) and not v.strip()) or v == []


def _validate(d, data):
    """The mandatory fields, checked against the same definition the form marks.

    Reported all at once rather than one at a time: a form that rejects six
    fields by naming one of them is six round trips."""
    missing = [f["label"] for f in defs.required(d) if _blank(data.get(f["key"]))]
    if missing:
        raise HTTPException(400, "Required: " + ", ".join(missing))


def _out(d, r: models.MasterRecord):
    return {
        "id": r.id, "master": r.master, "code": r.code, "name": r.name,
        "data": r.data or {}, "grids": r.grids or {}, "matrix": r.matrix or {},
        "active": bool(r.active),
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def _title(d, data):
    """What a dropdown shows for this row — the definition names the field."""
    key = d.get("title_field") or "name"
    return str(data.get(key) or data.get("name") or data.get("code") or "").strip()


# ---------------------------------------------------------------------------
#  The hub, and one master's definition
# ---------------------------------------------------------------------------
@router.get("")
def list_masters(db: Session = Depends(get_db)):
    """Every master, with how many records each holds — the Masters hub screen."""
    counts = dict(db.query(models.MasterRecord.master, func.count(models.MasterRecord.id))
                  .group_by(models.MasterRecord.master).all())
    out = []
    for m in defs.summary():
        m["count"] = counts.get(m["key"], 0)
        out.append(m)
    return out


@router.get("/{key}/definition")
def definition(key: str, db: Session = Depends(get_db)):
    """The master's fields, with every dropdown already resolved.

    Resolved here rather than in the browser because the choices come from three
    different places — other masters, the attribute vocabularies, the keyed
    option lists — and a form should not have to know which."""
    d = _def(key)
    out = {k: v for k, v in d.items() if k != "groups"}
    groups = []
    for g in d.get("groups", []):
        fields = []
        for f in g["fields"]:
            f = dict(f)
            src = f.pop("source", None)
            if src:
                f["options"] = _resolve(db, src)
                f["source"] = src
            fields.append(f)
        groups.append({"title": g["title"], "fields": fields})
    out["groups"] = groups
    for grid in out.get("grids", []) or []:
        grid["columns"] = [
            {**{k: v for k, v in c.items() if k != "source"},
             **({"options": _resolve(db, c["source"])} if c.get("source") else {})}
            for c in grid["columns"]]
    return out


def _resolve(db, source):
    """Where a dropdown's choices come from.

    Three kinds, because these masters genuinely draw on three different stores:
      master:<key>  another master's records (Tax, Brand, Employee …)
      attr:<field>  the stock vocabularies (300 brands, 264 styles …)
      option:<kind> the small keyed lists (cities, sections, companies)
    An unknown source returns [] rather than raising: a dropdown with nothing in
    it is a master nobody has filled in yet, which is a normal state on day one.
    """
    kind, _, name = source.partition(":")
    if kind == "master":
        rows = db.query(models.MasterRecord).filter(
            models.MasterRecord.master == name,
            models.MasterRecord.active == True).order_by(   # noqa: E712
            models.MasterRecord.name).all()
        vals = [r.name for r in rows if r.name]
        # the three masters that live in their own tables
        if name == "supplier":
            vals += [s.name for s in db.query(models.Supplier).all() if s.name]
        elif name == "agent":
            vals += [a.name for a in db.query(models.Agent).all() if a.name]
        elif name == "transport":
            vals += [t.name for t in db.query(models.Transport).all() if t.name]
        elif name == "unit":
            vals += [u.code for u in db.query(models.UnitType).all()]
        elif name == "product":
            vals += [c.name for c in db.query(models.Category).all()]
        seen, out = set(), []
        for v in vals:
            if v.strip().lower() not in seen:
                seen.add(v.strip().lower())
                out.append(v)
        return sorted(out, key=str.lower)
    if kind == "attr":
        from .inventory import BASE_OPTIONS
        return list(BASE_OPTIONS.get(name, []))
    if kind == "option":
        if name in masters_svc.OPTION_KINDS:
            return masters_svc.options(db, name)
        # a list nobody has declared yet fills itself from what gets typed
        rows = db.query(models.MasterOption).filter(
            models.MasterOption.kind == name).order_by(models.MasterOption.value).all()
        return [o.value for o in rows]
    return []


# ---------------------------------------------------------------------------
#  Records
# ---------------------------------------------------------------------------
@router.get("/{key}/records")
def list_records(key: str, q: str = "", include_inactive: bool = False,
                 limit: int = 500, db: Session = Depends(get_db)):
    d = _def(key)
    query = db.query(models.MasterRecord).filter(models.MasterRecord.master == key)
    if not include_inactive:
        query = query.filter(models.MasterRecord.active == True)   # noqa: E712
    # Searched in the database, BEFORE the limit. Filtering the fetched page in
    # Python only ever searched the newest `limit` rows, which stops being "the
    # whole master" once one holds the 220k items brought over from the old system.
    for word in q.split():
        like = f"%{word}%"
        query = query.filter(or_(models.MasterRecord.name.ilike(like),
                                 models.MasterRecord.code.ilike(like),
                                 cast(models.MasterRecord.data, String).ilike(like)))
    total = query.count()
    rows = query.order_by(models.MasterRecord.id.desc()).limit(max(1, min(limit, 2000))).all()
    return {"master": key, "label": d["label"], "count": len(rows), "total": total,
            "records": [_out(d, r) for r in rows]}


@router.get("/{key}/records/{rec_id}")
def get_record(key: str, rec_id: int, db: Session = Depends(get_db)):
    d = _def(key)
    r = db.get(models.MasterRecord, rec_id)
    if not r or r.master != key:
        raise HTTPException(404, "record not found")
    return _out(d, r)


@router.post("/{key}/records")
def create_record(key: str, body: RecordIn, db: Session = Depends(get_db)):
    d = _def(key)
    data = dict(body.data or {})
    _validate(d, data)
    # A singleton master (Configuration, HR Configuration) is settings, not a
    # list: saving it again edits the one row rather than adding a second set of
    # rules for the same thing to disagree with.
    if d.get("singleton"):
        existing = db.query(models.MasterRecord).filter(
            models.MasterRecord.master == key).first()
        if existing:
            return update_record(key, existing.id, body, db)
    code = str(data.get("code") or "").strip() or None
    if code and db.query(models.MasterRecord).filter(
            models.MasterRecord.master == key,
            models.MasterRecord.code == code).first():
        raise HTTPException(400, f"“{code}” is already a {d['label']} code")
    r = models.MasterRecord(
        master=key, code=code, name=_title(d, data) or code,
        data=data, grids=body.grids or {}, matrix=body.matrix or {},
        active=bool(data.get("active", True)))
    db.add(r)
    db.commit()
    db.refresh(r)
    return _out(d, r)


@router.put("/{key}/records/{rec_id}")
def update_record(key: str, rec_id: int, body: RecordIn, db: Session = Depends(get_db)):
    d = _def(key)
    r = db.get(models.MasterRecord, rec_id)
    if not r or r.master != key:
        raise HTTPException(404, "record not found")
    data = dict(body.data or {})
    _validate(d, data)
    code = str(data.get("code") or "").strip() or None
    if code and db.query(models.MasterRecord).filter(
            models.MasterRecord.master == key, models.MasterRecord.code == code,
            models.MasterRecord.id != r.id).first():
        raise HTTPException(400, f"“{code}” is already a {d['label']} code")
    r.code, r.name, r.data = code, (_title(d, data) or code), data
    if body.grids is not None:
        r.grids = body.grids
    if body.matrix is not None:
        r.matrix = body.matrix
    r.active = bool(data.get("active", True))
    db.commit()
    db.refresh(r)
    return _out(d, r)


@router.delete("/{key}/records/{rec_id}")
def delete_record(key: str, rec_id: int, db: Session = Depends(get_db)):
    """Remove a master row. Refused while another master points at it by name —
    a dropdown resolving to nothing is worse than a row nobody uses."""
    _def(key)
    r = db.get(models.MasterRecord, rec_id)
    if not r or r.master != key:
        raise HTTPException(404, "record not found")
    if r.name:
        for other in defs.MASTERS:
            wants = [f for f in defs.fields(other)
                     if f.get("source") == f"master:{key}"]
            if not wants:
                continue
            for row in db.query(models.MasterRecord).filter(
                    models.MasterRecord.master == other["key"]).all():
                if any((row.data or {}).get(f["key"]) == r.name for f in wants):
                    raise HTTPException(
                        400, f"“{r.name}” is in use by {other['label']} "
                             f"“{row.name or row.id}” — clear it there first")
    db.delete(r)
    db.commit()
    return {"ok": True}
