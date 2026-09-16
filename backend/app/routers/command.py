"""The Command Center: the dashboard, the question box and the trace.

Who may call which is decided in security.POLICY — the dashboard is super admin
and above, the question box and the trace are admin and above. Everything here is
answered inside the account's allotted warehouses (`request.state.warehouses`,
put there by the auth middleware), so an admin confined to Erode asking "today's
sales" is told Erode's.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import ask_anything, business_day, command_center, nlq, trace
from ..services.users import ROLE_RANK, rank

router = APIRouter(prefix="/api/command", tags=["command-center"])


class Question(BaseModel):
    q: str


def _who(request: Request) -> dict:
    return getattr(request.state, "user", None) or {}


def _allowed(request: Request):
    return getattr(request.state, "warehouses", None) or None


@router.get("/overview")
def overview(request: Request, day: Optional[str] = None,
             warehouse_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Every tile, chart, list and alert on the Command Center, in one call.

    `day` (ISO) looks back at another business day; it defaults to today.
    `warehouse_id` scopes the WHOLE screen to one building — its stock, its
    stores' takings, its GRNs, its people's activity — which is what the picker
    at the top sends. A building this account is not allotted is refused rather
    than widened to everything, the same as anywhere else."""
    d = business_day.parse_day(day) or business_day.today()
    role = _who(request).get("role")
    mine = _allowed(request)
    if warehouse_id and mine and int(warehouse_id) not in mine:
        raise HTTPException(403, "You are not allotted that warehouse.")
    return command_center.overview(db, allowed=mine, day=d, warehouse_id=warehouse_id,
                                   with_users=rank(role) >= ROLE_RANK["superadmin"])


@router.post("/ask")
def ask(body: Question, request: Request, db: Session = Depends(get_db)):
    """One question, one line back first — then the rows behind it."""
    return ask_anything.ask(db, body.q, role=_who(request).get("role"),
                            allowed=_allowed(request))


@router.get("/trace")
def track(request: Request, code: str = "", kind: Optional[str] = None,
          ref: Optional[str] = None, db: Session = Depends(get_db)):
    """Follow one code end to end. `kind` + `ref` pick one record when a code
    matched several (the answer lists them as `choices`)."""
    return trace.find(db, code, kind=kind, ref_id=ref, allowed=_allowed(request))


@router.get("/examples")
def examples():
    return {"engine": "model" if nlq.available() else "keywords",
            "examples": ask_anything.examples()}
