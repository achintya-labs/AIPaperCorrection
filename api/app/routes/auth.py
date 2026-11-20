from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
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
from app.db import get_db
from app.models import Grade, Role, User, submission_tas

router = APIRouter(prefix="/auth", tags=["auth"])

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
    assigned_papers: int
    reviewed_grades: int


class UserUpdate(BaseModel):
    role: Role | None = None
    active: bool | None = None


class UserCreate(BaseModel):
    email: str
    name: str = ""
    password: NewPassword
    role: Role = Role.ta


class PasswordChange(BaseModel):
    current_password: str
    new_password: NewPassword


class PasswordReset(BaseModel):
    password: NewPassword


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email))
    if not user or not user.active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    set_session_cookie(response, user)
    return user


@router.post("/logout", status_code=204)
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.put("/me/password", status_code=204)
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


@router.post("/users", response_model=UserOut, status_code=201, dependencies=[Depends(require_admin)])
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
            assigned_papers=assigned.get(u.id, 0),
            reviewed_grades=reviewed.get(u.id, 0),
        )
        for u in users
    ]


@router.get("/users", response_model=list[UserAdminOut], dependencies=[Depends(require_admin)])
def list_users(db: Session = Depends(get_db)):
    return _admin_out(db, list(db.scalars(select(User).order_by(User.id)).all()))


@router.put("/users/{user_id}/password", status_code=204)
def reset_user_password(
    user_id: int,
    body: PasswordReset,
    me: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin sets another user's password (e.g. they forgot it, or the account may be compromised).
    All of that user's sessions are signed out. For your own account use PUT /auth/me/password, which
    needs the current password, so a hijacked admin session can't quietly take the account over."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == me.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Use Change password for your own account")
    user.password_hash = hash_password(body.password)
    user.token_version += 1
    db.commit()


def _guard_last_admin(db: Session, target: User) -> None:
    """Refuse to leave the system without an active admin. Locks admin rows so two admins
    demoting each other at the same instant can't both pass the check (Postgres)."""
    admins = db.scalars(
        select(User).where(User.role == Role.admin, User.active).with_for_update()
    ).all()
    if target.id in {a.id for a in admins} and len(admins) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "At least one active admin is required")


@router.patch("/users/{user_id}", response_model=UserAdminOut)
def update_user(
    user_id: int,
    body: UserUpdate,
    me: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Change a user's role and/or active flag. Takes effect immediately: the session cookie holds
    only the user id, and role/active are read from the database on every request."""
    if body.role is None and body.active is None:
        raise HTTPException(422, "Provide role and/or active")
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
    db.commit()
    return _admin_out(db, [user])[0]


@router.delete("/users/{user_id}", status_code=204, dependencies=[Depends(require_admin)])
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
