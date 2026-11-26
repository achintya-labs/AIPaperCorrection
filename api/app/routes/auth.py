import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from pydantic import AfterValidator, BaseModel
from pydantic_core import PydanticCustomError
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.auth import (
    COOKIE_NAME,
    current_user,
    hash_password,
    require_admin,
    set_session_cookie,
    verify_password,
)
from app.auth import google, sso
from app.auth.directory import Directory, get_directory
from app.config import get_settings
from app.db import get_db
from app.models import Grade, Role, User, submission_tas

router = APIRouter(prefix="/auth", tags=["auth"])


def local_only() -> None:
    """Passwords, Google sign-in and user management exist only in AUTH_MODE=local. With SSO the gateway signs people in
    and LDAP holds the accounts."""
    if get_settings().sso:
        raise HTTPException(status.HTTP_409_CONFLICT, "Accounts are managed in LDAP (single sign-on is enabled)")

log = logging.getLogger(__name__)

MIN_PASSWORD_CHARS = 8
# bcrypt ignores everything past 72 bytes. Reject longer passwords rather than silently truncating.
MAX_PASSWORD_BYTES = 72


def _check_password_policy(value: str) -> str:
    if len(value) < MIN_PASSWORD_CHARS:
        raise PydanticCustomError("password_policy", f"Password must be at least {MIN_PASSWORD_CHARS} characters")
    if len(value.encode()) > MAX_PASSWORD_BYTES:
        raise PydanticCustomError("password_policy", f"Password must be at most {MAX_PASSWORD_BYTES} bytes")
    return value


NewPassword = Annotated[str, AfterValidator(_check_password_policy)]


def _blank_or_policy(value: str) -> str:
    return value if value == "" else _check_password_policy(value)


# An admin may leave a password blank when creating or resetting ANOTHER user: that account then has no password
# and can sign in only with Google. Changing your own password (and resetting your own) never allows blank.
BlankOrNewPassword = Annotated[str, AfterValidator(_blank_or_policy)]


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: Role

    model_config = {"from_attributes": True}


class UserAdminOut(UserOut):
    """What admins see when managing users: includes activity so deletion can be an informed choice."""

    active: bool
    has_password: bool  # false: Google sign-in only
    assigned_papers: int
    reviewed_grades: int


class UserUpdate(BaseModel):
    role: Role | None = None
    active: bool | None = None
    name: str | None = None  # the email is the user's key and can't be changed


class UserCreate(BaseModel):
    email: str
    name: str = ""
    password: BlankOrNewPassword
    role: Role = Role.ta


class PasswordChange(BaseModel):
    current_password: str
    new_password: NewPassword


class PasswordReset(BaseModel):
    password: BlankOrNewPassword


@router.post("/login", response_model=UserOut, dependencies=[Depends(local_only)])
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email))
    # A blank password never signs anyone in, even if the account has none set (those use Google).
    if not body.password or not user or not user.active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    set_session_cookie(response, user)
    return user


class ProvidersOut(BaseModel):
    google: bool
    mode: str = "local"  # "sso": the gateway signs people in, there is no login page


@router.get("/providers", response_model=ProvidersOut)
def providers():
    """Which sign-in methods are configured, so the login page knows whether to show the Google button."""
    sso_mode = get_settings().sso
    return ProvidersOut(google=google.enabled() and not sso_mode, mode="sso" if sso_mode else "local")


def _require_google() -> None:
    if not google.enabled():
        raise HTTPException(404, "Google sign-in is not configured")


@router.get("/google/login", status_code=302, dependencies=[Depends(local_only)])
def google_login():
    """Start Google sign-in: sets a short-lived signed cookie and redirects to Google."""
    _require_google()
    url, tx = google.start()
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(
        google.TX_COOKIE, tx, max_age=google.TX_MAX_AGE, httponly=True, samesite="lax",
        secure=get_settings().cookie_secure, path="/",
    )
    return resp


@router.get("/google/callback", status_code=302, dependencies=[Depends(local_only)])
def google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    tx_cookie: str | None = Cookie(default=None, alias=google.TX_COOKIE),
    db: Session = Depends(get_db),
):
    """Google redirects here. Signs in the existing, active user whose email Google has verified.
    Every failure redirects to /login?error=... with a generic code (no hint whether an account exists)."""
    _require_google()

    def fail(code_: str) -> RedirectResponse:
        r = RedirectResponse(f"/login?error={code_}", status_code=302)
        r.delete_cookie(google.TX_COOKIE, path="/")
        return r

    if error or not code or not state:
        return fail("google_cancelled" if error == "access_denied" else "google_failed")
    try:
        tx = google.read_transaction(tx_cookie)
        if not secrets.compare_digest(state, str(tx["state"])):
            raise google.GoogleAuthError("state mismatch")
        claims = google.complete(code, tx)
    except google.GoogleAuthError as e:
        log.warning("google sign-in rejected: %s", e)
        return fail("google_failed")

    matches = db.scalars(select(User).where(func.lower(User.email) == claims["email"].lower())).all()
    user = matches[0] if len(matches) == 1 else None  # ambiguous case-variants: refuse rather than guess
    if not user or not user.active:
        log.warning("google sign-in for unknown or inactive account")
        return fail("google_not_allowed")
    if not user.name and claims.get("name"):
        user.name = str(claims["name"])[:255]
        db.commit()
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie(google.TX_COOKIE, path="/")
    set_session_cookie(resp, user)
    return resp


@router.post("/logout", status_code=204)
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.put("/me/password", status_code=204, dependencies=[Depends(local_only)])
def change_own_password(
    body: PasswordChange,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Change your own password. Every other session of yours is signed out; this one stays signed in.

    A wrong current password is a 400, not a 401: the web client treats 401 as "session expired".
    """
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    if body.new_password == body.current_password:
        raise HTTPException(422, "New password must be different from the current one")
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    db.commit()
    set_session_cookie(response, user)  # a fresh token under the new version keeps this browser signed in


@router.post("/users", response_model=UserOut, status_code=201, dependencies=[Depends(require_admin), Depends(local_only)])
def create_user(body: UserCreate, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already exists")
    user = User(
        email=body.email,
        name=body.name,
        password_hash=hash_password(body.password),
        role=body.role,
    )
    db.add(user)
    db.commit()
    return user


def _admin_out(db: Session, users: list[User]) -> list[UserAdminOut]:
    ids = [u.id for u in users]
    assigned = dict(
        db.execute(
            select(submission_tas.c.user_id, func.count())
            .where(submission_tas.c.user_id.in_(ids))
            .group_by(submission_tas.c.user_id)
        ).all()
    )
    reviewed = dict(
        db.execute(
            select(Grade.reviewed_by, func.count()).where(Grade.reviewed_by.in_(ids)).group_by(Grade.reviewed_by)
        ).all()
    )
    return [
        UserAdminOut(
            id=u.id,
            email=u.email,
            name=u.name,
            role=u.role,
            active=u.active,
            has_password=bool(u.password_hash),
            assigned_papers=assigned.get(u.id, 0),
            reviewed_grades=reviewed.get(u.id, 0),
        )
        for u in users
    ]


@router.get("/users", response_model=list[UserAdminOut], dependencies=[Depends(require_admin)])
def list_users(db: Session = Depends(get_db), directory: Directory = Depends(get_directory)):
    """Local mode: the users table. SSO mode: the LDAP directory (enrolled users); the cache is synced first so every
    listed person has an id (for TA assignment) and people who left the groups drop out."""
    if get_settings().sso:
        sso.sync_directory(db, directory)
    return _admin_out(db, list(db.scalars(select(User).order_by(User.id)).all()))


@router.put("/users/{user_id}/password", status_code=204, dependencies=[Depends(local_only)])
def reset_user_password(
    user_id: int,
    body: PasswordReset,
    response: Response,
    me: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin sets a user's password directly without requiring the current password.
    All other sessions of that user are signed out; if resetting your own, this session stays signed in."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if body.password == "" and user.id == me.id:
        raise HTTPException(422, "You can't remove your own password")
    user.password_hash = hash_password(body.password)
    user.token_version += 1
    db.commit()
    if user.id == me.id:
        set_session_cookie(response, user)


def _guard_last_admin(db: Session, target: User) -> None:
    """Refuse to leave the system without an active admin. Locks admin rows so two admins
    demoting each other at the same instant can't both pass the check (Postgres)."""
    admins = db.scalars(
        select(User).where(User.role == Role.admin, User.active).with_for_update()
    ).all()
    if target.id in {a.id for a in admins} and len(admins) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "At least one active admin is required")


@router.patch("/users/{user_id}", response_model=UserAdminOut, dependencies=[Depends(local_only)])
def update_user(
    user_id: int,
    body: UserUpdate,
    me: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Change a user's role, active flag and/or name. Takes effect immediately: the session cookie holds
    only the user id, and role/active are read from the database on every request."""
    if body.role is None and body.active is None and body.name is None:
        raise HTTPException(422, "Provide role, active and/or name")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")

    new_role = body.role if body.role is not None else user.role
    new_active = body.active if body.active is not None else user.active
    loses_admin = user.role == Role.admin and user.active and (new_role != Role.admin or not new_active)
    if loses_admin:
        if user.id == me.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "You can't demote or deactivate yourself; ask another admin")
        _guard_last_admin(db, user)

    user.role, user.active = new_role, new_active
    if body.name is not None:
        user.name = body.name.strip()
    db.commit()
    return _admin_out(db, [user])[0]


@router.delete("/users/{user_id}", status_code=204, dependencies=[Depends(require_admin), Depends(local_only)])
def delete_user(
    user_id: int,
    force: bool = False,
    me: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Permanently delete a user. Their paper assignments are removed. If they have reviewed grades the
    delete is refused (deactivate instead, which keeps the attribution) unless force=true, which keeps
    the grades but clears who reviewed them."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == me.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't delete yourself")
    if user.role == Role.admin and user.active:
        _guard_last_admin(db, user)

    reviewed = db.scalar(select(func.count()).select_from(Grade).where(Grade.reviewed_by == user_id)) or 0
    if reviewed and not force:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{user.email} has reviewed {reviewed} grade(s). Deactivate the account to keep that history, "
            "or delete with force=true to clear the reviewer on those grades.",
        )
    db.execute(delete(submission_tas).where(submission_tas.c.user_id == user_id))
    db.execute(update(Grade).where(Grade.reviewed_by == user_id).values(reviewed_by=None))
    db.delete(user)
    db.commit()
