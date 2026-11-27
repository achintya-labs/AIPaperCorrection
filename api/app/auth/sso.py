"""SSO sign-in: the gateway has already authenticated the person; map them onto a (cached) users row.

LDAP is the source of truth for who may use the app and as what. The `users` table stays only because papers, grades and
assignments reference it by id: rows are matched by LDAP uid, else by case-insensitive email (so every pre-SSO user keeps
their id and their work), and created, renamed, re-roled or deactivated to follow the directory.
"""

from fastapi import HTTPException, status
import logging
import threading
import time

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.directory import Directory, DirectoryError, DirUser
from app.models import Grade, User, submission_tas

log = logging.getLogger(__name__)


def _find_cached(db: Session, entry: DirUser) -> User | None:
    user = db.scalar(select(User).where(User.ldap_uid == entry.uid))
    if user is None:
        matches = db.scalars(select(User).where(func.lower(User.email) == entry.email.lower())).all()
        user = matches[0] if len(matches) == 1 else None  # case-variant duplicates: refuse to guess
    return user


def _merge(db: Session, keep: User, drop: User) -> None:
    """Two cache rows for one email are one person (the email is the key): move everything of `drop` onto `keep`, then delete it.

    This happens when the directory's email for a person now equals an older row's email, e.g. a migrated legacy user whose old
    LDAP link no longer exists, or the built-in admin whose email was changed. Their papers, assignments and reviewed grades follow
    them instead of the sign-in failing on the unique email."""
    log.warning("merging users row %s (%s) into %s (same email %s)", drop.id, drop.ldap_uid, keep.id, keep.email)
    already = set(db.scalars(select(submission_tas.c.submission_id).where(submission_tas.c.user_id == keep.id)))
    mine = list(db.scalars(select(submission_tas.c.submission_id).where(submission_tas.c.user_id == drop.id)))
    db.execute(delete(submission_tas).where(submission_tas.c.user_id == drop.id))
    for sid in mine:
        if sid not in already:
            db.execute(submission_tas.insert().values(submission_id=sid, user_id=keep.id))
    db.execute(update(Grade).where(Grade.reviewed_by == drop.id).values(reviewed_by=keep.id))
    db.delete(drop)
    db.flush()


def _apply(user: User, entry: DirUser, name_hint: str = "") -> bool:
    """Copy the directory's view onto the cache row. True if anything changed."""
    want = {
        "ldap_uid": entry.uid,
        "email": entry.email,
        "name": entry.name or name_hint or user.name,
        "role": entry.role,
        "active": True,
    }
    changed = False
    for k, v in want.items():
        if getattr(user, k) != v:
            setattr(user, k, v)
            changed = True
    return changed


def sync_user(db: Session, entry: DirUser, name_hint: str = "") -> User:
    """Create or update the cache row for an ENROLLED directory user and return it."""
    for attempt in (1, 2):
        user = _find_cached(db, entry)
        if user is None:
            user = User(email=entry.email, name=entry.name or name_hint, password_hash="", role=entry.role)
            user.ldap_uid = entry.uid
            db.add(user)
            try:
                db.commit()
            except IntegrityError:  # two first requests at once: the other one created it
                db.rollback()
                if attempt == 2:
                    raise
                continue
        else:
            clashes = db.scalars(select(User).where(func.lower(User.email) == entry.email.lower(), User.id != user.id)).all()
            for other in clashes:
                _merge(db, keep=user, drop=other)
            if _apply(user, entry, name_hint) or clashes:
                db.commit()
        return user
    raise RuntimeError("unreachable")


def sync_directory(db: Session, directory: Directory) -> None:
    """Make the cache match the whole directory: add/update every enrolled user, deactivate cached users who are no
    longer enrolled. Done when an admin lists users or imports papers, so TAs who never signed in can be assigned."""
    try:
        members = directory.members()
    except DirectoryError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The user directory is unavailable") from e
    seen: set[int] = set()
    for entry in members:
        seen.add(sync_user(db, entry).id)
    stale = db.scalars(select(User).where(User.active, User.id.not_in(seen) if seen else User.active)).all()
    for u in stale:
        u.active = False
    if stale:
        db.commit()


def deactivate_cached(db: Session, email: str) -> None:
    """A person the gateway let in is no longer enrolled: make sure their cache row stops counting (TA pools, imports)."""
    for u in db.scalars(select(User).where(func.lower(User.email) == email.lower(), User.active)):
        u.active = False
    db.commit()


_SYNC_EVERY = 60.0  # seconds between background full syncs
_last_sync = float("-inf")  # time.monotonic() can be small (fresh VM), so 0 would not mean "long ago"
_sync_lock = threading.Lock()


def sync_directory_if_due(db: Session, directory: Directory, every: float = _SYNC_EVERY) -> None:
    """Keep every cached row (names, emails, roles, active) following the directory without anyone asking: called on
    requests, it does a full sync at most once per `every` seconds per process. A directory outage is not the caller's problem."""
    global _last_sync
    now = time.monotonic()
    if now - _last_sync < every or not _sync_lock.acquire(blocking=False):
        return
    try:
        if now - _last_sync < every:
            return
        _last_sync = now
        sync_directory(db, directory)
    except HTTPException:
        log.warning("periodic directory sync skipped: directory unavailable")
    except Exception:
        db.rollback()
        log.exception("periodic directory sync failed")
    finally:
        _sync_lock.release()
