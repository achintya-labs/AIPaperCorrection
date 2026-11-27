from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Cookie, Depends, HTTPException, Request, Response, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.auth import sso
from app.auth.directory import Directory, DirectoryError, get_directory
from app.config import get_settings
from app.db import get_db
from app.models import Role, User

ALGORITHM = "HS256"
COOKIE_NAME = "session"


def hash_password(password: str) -> str:
    """Hash a password. A blank password means "no password": the account can only sign in another way
    (Google) and is stored as an empty hash, which verify_password never accepts."""
    if password == "":
        return ""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=get_settings().bcrypt_rounds)).decode()


def verify_password(password: str, hashed: str) -> bool:
    if not password or not hashed:  # blank never matches, whatever is stored
        return False
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:  # malformed stored hash
        return False


def create_token(user: User) -> str:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(minutes=s.jwt_expire_minutes)
    claims = {"sub": str(user.id), "exp": exp, "v": user.token_version}
    return jwt.encode(claims, s.jwt_secret, algorithm=ALGORITHM)


def set_session_cookie(response: Response, user: User) -> None:
    s = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        create_token(user),
        httponly=True,
        samesite="lax",
        secure=s.cookie_secure,
        max_age=s.jwt_expire_minutes * 60,
    )


def _sso_user(remote_email: str | None, remote_name: str | None, db: Session, directory: Directory) -> User:
    """SSO: the gateway sets Remote-Email after authenticating (and removes any client-sent copy; only the gateway can
    reach this service). Enrolled = member of the admin or TA group in LDAP."""
    email = (remote_email or "").strip()
    if not email:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        entry = directory.find(email)
    except DirectoryError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The user directory is unavailable")
    if entry is None or entry.role is None:
        sso.deactivate_cached(db, email)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your account is not enrolled in Paper Grader")
    user = sso.sync_user(db, entry, (remote_name or "").strip()[:255])
    sso.sync_directory_if_due(db, directory)
    return user


def current_user(
    request: Request,
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: Session = Depends(get_db),
    directory: Directory = Depends(get_directory),
) -> User:
    if get_settings().sso:  # read from the request, not declared as Header params: they'd litter every OpenAPI operation
        return _sso_user(request.headers.get("remote-email"), request.headers.get("remote-name"), db, directory)
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if not session:
        raise unauthorized
    try:
        payload = jwt.decode(session, get_settings().jwt_secret, algorithms=[ALGORITHM])
        user = db.get(User, int(payload["sub"]))
    except (JWTError, KeyError, ValueError):
        raise unauthorized
    if user is None or not user.active:
        raise unauthorized
    # A password change bumps token_version and so ends older sessions. Tokens issued before this
    # field existed carry no "v" and count as version 0, which is every user's starting value.
    if payload.get("v", 0) != user.token_version:
        raise unauthorized
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != Role.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return user
