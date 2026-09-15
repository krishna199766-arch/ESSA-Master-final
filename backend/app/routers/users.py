"""
User management — the super admin's screen.

Reachable only by a super admin; that is enforced in security.POLICY, not here,
so this file is about the rules that are specific to accounts rather than about
who may open it.

Those rules exist to stop the two ways an install locks itself out. A super
admin may not demote, deactivate or delete themselves — the account you are
signed in as is the one holding the door open. And the last active super admin
may not be removed by any route, even by another super admin, because the
screen that would fix the mistake is the one that just became unreachable.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..models import User
from ..services import permissions as perms_svc, users as users_svc

router = APIRouter(prefix="/api/users", tags=["users"])


class UserIn(BaseModel):
    username: str
    password: str
    role: str = "user"
    full_name: str = ""


class UserPatch(BaseModel):
    role: str | None = None
    full_name: str | None = None
    active: bool | None = None


class ResetIn(BaseModel):
    new_password: str


class PermissionsIn(BaseModel):
    """A whole grant map at once, not one checkbox at a time.

    The editor shows seventeen screens by five actions and it is normal to change
    a dozen boxes in one sitting. Sending each as its own request would make a
    half-applied set of permissions a thing that can exist — which, on the screen
    that decides who may do what, is worth avoiding.
    """
    screens: dict[str, list[str]] | None = None
    data: list[str] | None = None
    #: Which warehouses this account may work inside. Ids, and an EMPTY list
    #: means every warehouse — see services/permissions.allotted.
    warehouses: list[int] | None = None
    #: How much of the Store: "admin" (every screen, the default) or "user"
    #: (Billing Counter and Store Reports) — see services/permissions.STORE_ACCESS.
    store: str | None = None


def _me(request: Request) -> dict:
    """The signed-in user, put on the request by the auth middleware."""
    return getattr(request.state, "user", None) or {}


def _get(db: Session, uid: int) -> User:
    user = db.query(User).get(uid)
    if not user:
        raise HTTPException(404, "No such user")
    return user


def _guard_last_superadmin(db: Session, user: User) -> None:
    if user.role != "superadmin" or not user.active:
        return
    others = (db.query(User)
              .filter(User.role == "superadmin", User.active == True,  # noqa: E712
                      User.id != user.id).count())
    if not others:
        raise HTTPException(400, "This is the last super admin — promote someone "
                                 "else first, or nobody can manage users.")


def _guard_self(request: Request, user: User) -> None:
    if user.username == _me(request).get("username"):
        raise HTTPException(400, "You cannot change your own role or access — "
                                 "ask another super admin.")


def _catalog(db: Session) -> dict:
    """The access editor's vocabulary: screens, actions, data flags, warehouses.

    ONE builder for both places that serve it. The editor prefers the catalog
    that rides along with the user list and only fetches /catalog when that one
    looks empty — so a key added to just the standalone route would never reach
    the screen. That is exactly how the warehouse ticks went missing the first
    time this was written.
    """
    out = perms_svc.catalog()
    out["warehouses"] = [
        {"id": w.id, "name": w.name, "code": w.code, "active": bool(w.active)}
        for w in db.query(models.Warehouse).order_by(models.Warehouse.name).all()]
    return out


@router.get("")
def list_users(db: Session = Depends(get_db)):
    rows = db.query(User).order_by(User.active.desc(), User.username).all()
    return {"users": [users_svc.out(u) for u in rows],
            "roles": [{"value": r, "label": users_svc.ROLE_LABEL[r]} for r in users_svc.ROLES],
            # the screens and actions the editor draws itself from, so the two
            # sides cannot disagree about what a screen is called
            "catalog": _catalog(db)}


@router.put("/{uid}/permissions")
def set_permissions(uid: int, body: PermissionsIn, request: Request,
                    db: Session = Depends(get_db)):
    """Replace what one account may do, screen by screen.

    Refused on yourself, for the same reason a super admin may not demote
    themselves: the account you are signed in as is the one holding the door
    open, and a mis-tick here would shut it with everybody outside.
    """
    user = _get(db, uid)
    _guard_self(request, user)
    # A misspelt level is refused rather than dropped: dropped, it would save as
    # the default — every Store screen — which is the opposite of narrowing.
    if body.store not in (None, "", *perms_svc.STORE_KEYS):
        raise HTTPException(400, "Store access must be one of "
                                 f"{', '.join(perms_svc.STORE_KEYS)}")
    clean = perms_svc.normalise(body.model_dump())
    # An id that names no warehouse is dropped — but if the caller named some and
    # NONE of them survive, that is refused rather than saved. Silently emptying
    # the list would flip the account from "these two buildings" to "every
    # building", which is the exact opposite of what was asked for.
    asked = [int(x) for x in (body.warehouses or [])
             if str(x).strip().lstrip("-").isdigit() and int(x) > 0]
    if asked:
        real = {w.id for w in db.query(models.Warehouse.id).all()}
        kept = [w for w in clean.get("warehouses", []) if w in real]
        if not kept:
            raise HTTPException(400, f"none of those warehouses exist: {asked}")
        clean["warehouses"] = kept
    user.permissions = clean or None
    db.commit()
    # No token to rotate and nobody to sign out: the middleware resolves the user
    # row on every call and reads the map off it, so a permission removed here is
    # refused on that account's very next request. Their menu catches up on the
    # next reload.
    db.refresh(user)
    return users_svc.out(user)


@router.get("/catalog")
def catalog(db: Session = Depends(get_db)):
    """Every screen, action, data flag and warehouse this app can enforce.

    The warehouses ride along so the access editor can offer them without a
    second call — and so it shows the same names the rest of the app does."""
    return _catalog(db)


@router.post("")
def create_user(body: UserIn, request: Request, db: Session = Depends(get_db)):
    username = (body.username or "").strip()
    if not username:
        raise HTTPException(400, "Username is required")
    if body.role not in users_svc.ROLES:
        raise HTTPException(400, f"Role must be one of {', '.join(users_svc.ROLES)}")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(400, f"'{username}' already exists")
    problem = users_svc.password_problem(body.password)
    if problem:
        raise HTTPException(400, problem)
    user = users_svc.create_user(db, username, body.password, body.role,
                                 body.full_name, created_by=_me(request).get("username", ""))
    return users_svc.out(user)


@router.patch("/{uid}")
def update_user(uid: int, body: UserPatch, request: Request,
                db: Session = Depends(get_db)):
    user = _get(db, uid)

    if body.full_name is not None:
        user.full_name = body.full_name.strip() or None

    if body.role is not None and body.role != user.role:
        if body.role not in users_svc.ROLES:
            raise HTTPException(400, f"Role must be one of {', '.join(users_svc.ROLES)}")
        _guard_self(request, user)
        _guard_last_superadmin(db, user)
        user.role = body.role

    if body.active is not None and bool(body.active) != bool(user.active):
        if not body.active:
            _guard_self(request, user)
            _guard_last_superadmin(db, user)
        user.active = bool(body.active)
        # Deactivating cuts the phone and desktop off at the next request rather
        # than at the next login — resolve_token re-reads this row every time.

    db.commit()
    return users_svc.out(user)


@router.post("/{uid}/password")
def reset_password(uid: int, body: ResetIn, db: Session = Depends(get_db)):
    """A reset, not a change — the super admin sets a new password without
    knowing the old one, and every device holding a token for that account is
    signed out by the seed rotation inside set_password."""
    user = _get(db, uid)
    problem = users_svc.password_problem(body.new_password)
    if problem:
        raise HTTPException(400, problem)
    users_svc.set_password(db, user, body.new_password)
    return {"ok": True}


@router.delete("/{uid}")
def delete_user(uid: int, request: Request, db: Session = Depends(get_db)):
    user = _get(db, uid)
    _guard_self(request, user)
    _guard_last_superadmin(db, user)
    db.delete(user)
    db.commit()
    return {"ok": True}
