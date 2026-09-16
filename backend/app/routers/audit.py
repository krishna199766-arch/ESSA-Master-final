"""The audit trail, read.

Admin and above (security.POLICY). What an admin is shown is narrower than what a
super admin is: only the warehouses they are allotted, and never the lines about
account management or the server's settings — who was given what access is the
super admin's business, and an admin reading it would learn exactly the kind of
thing that screen is locked to keep from them.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import audit as audit_svc
from ..services.users import ROLE_RANK, rank

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
def events(request: Request, limit: int = 100, before: Optional[int] = None,
           user: Optional[str] = None, screen: Optional[str] = None,
           warehouse_id: Optional[int] = None, date_from: Optional[str] = None,
           date_to: Optional[str] = None, q: Optional[str] = None,
           outcome: Optional[str] = None, db: Session = Depends(get_db)):
    me = getattr(request.state, "user", None) or {}
    senior = rank(me.get("role")) >= ROLE_RANK["superadmin"]
    out = audit_svc.feed(
        db, limit=limit, before_id=before, username=user or None, screen=screen or None,
        warehouse_id=warehouse_id, date_from=date_from, date_to=date_to, q=q,
        outcome=outcome or None,
        allowed_warehouses=getattr(request.state, "warehouses", None) or None,
        hide_screens=() if senior else ("users", "settings", "session"))
    # The filter lists the screen offers, drawn from what is actually on the trail.
    from .. import models
    E = models.AuditEvent
    people = [u for (u,) in db.query(E.username).distinct().all() if u]
    out["people"] = sorted(people, key=str.lower)
    out["modules"] = [{"key": k, "label": v} for k, v in sorted(audit_svc.SCREEN_LABEL.items(),
                                                                key=lambda kv: kv[1])
                      if senior or k not in ("users", "settings", "session")]
    return out
