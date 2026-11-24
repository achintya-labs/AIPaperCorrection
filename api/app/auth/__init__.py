from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Cookie, Depends, HTTPException, Response, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session

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


def current_user(
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: Session = Depends(get_db),
) -> User:
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
