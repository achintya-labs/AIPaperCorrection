"""One-time migration helper: print the app's existing users as LLDAP bootstrap documents, one JSON object per line.

    python -m app.export_ldap_users            (run.sh does this for you the first time it starts in SSO mode)

Writes only to stdout (a summary goes to stderr): run.sh redirects it into ldap-bootstrap/users.jsonl ON THE HOST. The container
never writes to a bind mount, because rootless Podman maps the host user to a different uid than the one the api runs as, so a
write into a mounted host folder is refused. Each line is {"id", "email", "displayName", "groups"} for an ACTIVE user, the group
chosen by role (papergrader_admins / papergrader_tas); the lldap-bootstrap service splits the file into LLDAP user files.
No passwords: the stored bcrypt hashes can't be imported into LDAP, so people sign in with Google (if whitelisted) or an admin
sets a password in LLDAP. The first SSO request of each person matches their existing users row by email, so their papers and
grades stay theirs.
"""

import json
import re
import sys

from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.config import get_settings
from app.db import SessionLocal
from app.models import Role, User


def ldap_id(email: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9._-]", "", email.split("@")[0].lower()) or "user"
    candidate, n = base, 1
    while candidate in taken or candidate == "admin":  # "admin" is LLDAP's built-in administrator
        n += 1
        candidate = f"{base}{n}"
    taken.add(candidate)
    return candidate


def user_docs(users: list) -> list[dict]:
    s = get_settings()
    taken: set[str] = set()
    docs = []
    # LLDAP's built-in `admin` carries BOOTSTRAP_ADMIN_EMAIL. An old user with that same email is that person: exporting them
    # too would put two directory entries on one email, which the app refuses to resolve ("not enrolled").
    admin_email = s.bootstrap_admin_email.strip().lower()
    for u in sorted(users, key=lambda u: u.id):
        if admin_email and u.email.strip().lower() == admin_email:
            continue
        uid = ldap_id(u.email, taken)
        docs.append({
            "id": uid,
            "email": u.email.lower(),
            "displayName": u.name or uid,
            "groups": [s.ldap_admin_group if u.role == Role.admin else s.ldap_ta_group],
        })
    return docs


def load_users(db) -> list:
    """Active users, reading only the columns every version of the schema has: this runs BEFORE the api container has
    migrated the database, so it must not touch newer columns (selecting the whole User model would ask for ldap_uid)."""
    try:
        return list(db.execute(select(User.id, User.email, User.name, User.role).where(User.active)))
    except (ProgrammingError, OperationalError) as e:
        # No users table yet (Postgres: ProgrammingError, SQLite: OperationalError "no such table"): a brand-new install has
        # nobody to export. Any other OperationalError (database unreachable) is a real failure and must not look like "no users".
        if isinstance(e, OperationalError) and "no such table" not in str(e).lower():
            raise
        db.rollback()
        return []


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    with SessionLocal() as db:
        docs = user_docs(load_users(db))
    for d in docs:
        print(json.dumps(d, separators=(",", ":")))
    print(f"Exported {len(docs)} active user(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
