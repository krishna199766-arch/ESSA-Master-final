"""
User management — the super admin's screen, and the Super Boss's.

Reachable only by a super admin or above; that is enforced in security.POLICY,
not here, so this file is about the rules that are specific to accounts rather
than about who may open it.

Those rules exist to stop the two ways an install locks itself out. A super
admin may not demote, deactivate or delete themselves — the account you are
signed in as is the one holding the door open. And the last active account that
can manage users may not be removed by any route, because the screen that would
fix the mistake is the one that just became unreachable.

And one rule that keeps the ladder a ladder: nobody may act on an account that
outranks them, or hand out a role above their own (services/users.may_manage,
grantable_roles). A super admin cannot reset the Super Boss's password — which
would be signing in as the Super Boss by another name.

Every change here is written to the audit trail in words only this file can
supply — nothing after the fact remembers what the role WAS.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..models import User
from ..services import audit as audit_svc, permissions as perms_svc, users as users_svc

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


_MANAGER_RANK = users_svc.ROLE_RANK["superadmin"]


def _guard_last_superadmin(db: Session, user: User, new_role: str = None) -> None:
    """The last active account that can open this screen stays.

    Counted by RANK, not by the word "superadmin": a Super Boss manages users
    too, so a super admin may step down while a Super Boss still holds the door —
    and a super admin promoted to Super Boss is not leaving at all."""
    if users_svc.rank(user.role) < _MANAGER_RANK or not user.active:
        return
    if new_role is not None and users_svc.rank(new_role) >= _MANAGER_RANK:
        return
    others = [u for u in db.query(User).filter(User.active == True,  # noqa: E712
                                               User.id != user.id).all()
              if users_svc.rank(u.role) >= _MANAGER_RANK]
    if not others:
        raise HTTPException(400, "This is the last account that can manage users — "
                                 "promote someone else first, or nobody can.")


def _guard_self(request: Request, user: User) -> None:
    if user.username == _me(request).get("username"):
        raise HTTPException(400, "You cannot change your own role or access — "
                                 "ask another super admin.")


def _guard_rank(request: Request, user: User) -> None:
    """Nobody changes an account that outranks them."""
    mine = _me(request).get("role")
    if not users_svc.may_manage(mine, user.role):
        label = users_svc.ROLE_LABEL.get(user.role, user.role)
        raise HTTPException(403, f"Only a {label} can change a {label} account.")


def _guard_grant(db: Session, request: Request, role: str) -> None:
    """Nobody hands out a role above their own — see grantable_roles."""
    if role not in users_svc.grantable_roles(db, _me(request).get("role")):
        label = users_svc.ROLE_LABEL.get(role, role)
        raise HTTPException(403, f"You cannot give the {label} role — only a "
                                 f"{label} can.")


def _label(role):
    return users_svc.ROLE_LABEL.get(role, role)


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
def list_users(request: Request, db: Session = Depends(get_db)):
    rows = db.query(User).order_by(User.active.desc(), User.username).all()
    mine = _me(request).get("role")
    grantable = users_svc.grantable_roles(db, mine)
    return {"users": [{**users_svc.out(u),
                       # whether the signed-in account may change this row at all,
                       # so the screen can show a Super Boss's row as read-only to
                       # a super admin instead of offering controls the server refuses
                       "manageable": users_svc.may_manage(mine, u.role)} for u in rows],
            # Only the roles this account may hand out. A dropdown offering Super
            # Boss to a super admin would be a control that answers 403.
            "roles": [{"value": r, "label": users_svc.ROLE_LABEL[r]} for r in users_svc.ROLES
                      if r in grantable],
            "all_roles": [{"value": r, "label": users_svc.ROLE_LABEL[r]} for r in users_svc.ROLES],
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
    _guard_rank(request, user)
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
    parts = []
    if clean.get("screens"):
        parts.append(f"{len(clean['screens'])} screen(s)")
    if clean.get("warehouses"):
        parts.append(f"{len(clean['warehouses'])} warehouse(s)")
    if clean.get("data"):
        parts.append(f"{len(clean['data'])} figure(s) withheld")
    if clean.get("store") == "user":
        parts.append("Store: billing and reports only")
    audit_svc.note(request, f"changed what {user.username} may open — "
                            + (", ".join(parts) if parts else "back to their role, nothing restricted"),
                   ref=user.username)
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
    _guard_grant(db, request, body.role)
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(400, f"'{username}' already exists")
    problem = users_svc.password_problem(body.password)
    if problem:
        raise HTTPException(400, problem)
    user = users_svc.create_user(db, username, body.password, body.role,
                                 body.full_name, created_by=_me(request).get("username", ""))
    audit_svc.note(request, f"created account {username} as {_label(body.role)}",
                   ref=username)
    return users_svc.out(user)


@router.patch("/{uid}")
def update_user(uid: int, body: UserPatch, request: Request,
                db: Session = Depends(get_db)):
    user = _get(db, uid)
    _guard_rank(request, user)
    said = []

    if body.full_name is not None and (body.full_name.strip() or None) != user.full_name:
        user.full_name = body.full_name.strip() or None
        said.append(f"renamed {user.username} to “{user.full_name or '—'}”")

    if body.role is not None and body.role != user.role:
        if body.role not in users_svc.ROLES:
            raise HTTPException(400, f"Role must be one of {', '.join(users_svc.ROLES)}")
        _guard_self(request, user)
        _guard_grant(db, request, body.role)
        _guard_last_superadmin(db, user, new_role=body.role)
        said.append(f"changed {user.username}'s role from {_label(user.role)} "
                    f"to {_label(body.role)}")
        user.role = body.role

    if body.active is not None and bool(body.active) != bool(user.active):
        if not body.active:
            _guard_self(request, user)
            _guard_last_superadmin(db, user)
        user.active = bool(body.active)
        said.append(f"{'reactivated' if user.active else 'deactivated'} {user.username}")
        # Deactivating cuts the phone and desktop off at the next request rather
        # than at the next login — resolve_token re-reads this row every time.

    db.commit()
    if said:
        audit_svc.note(request, "; ".join(said), ref=user.username)
    return users_svc.out(user)


@router.post("/{uid}/password")
def reset_password(uid: int, body: ResetIn, request: Request,
                   db: Session = Depends(get_db)):
    """A reset, not a change — the super admin sets a new password without
    knowing the old one, and every device holding a token for that account is
    signed out by the seed rotation inside set_password.

    Never on an account that outranks the one doing it: resetting the Super
    Boss's password is signing in as the Super Boss."""
    user = _get(db, uid)
    _guard_rank(request, user)
    problem = users_svc.password_problem(body.new_password)
    if problem:
        raise HTTPException(400, problem)
    users_svc.set_password(db, user, body.new_password)
    audit_svc.note(request, f"reset {user.username}'s password — they were signed "
                            "out everywhere", ref=user.username, action="password")
    return {"ok": True}


@router.delete("/{uid}")
def delete_user(uid: int, request: Request, db: Session = Depends(get_db)):
    user = _get(db, uid)
    _guard_self(request, user)
    _guard_rank(request, user)
    _guard_last_superadmin(db, user)
    name, role = user.username, user.role
    db.delete(user)
    db.commit()
    audit_svc.note(request, f"deleted account {name} ({_label(role)})", ref=name)
    return {"ok": True}
