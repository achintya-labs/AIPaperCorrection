"""The user directory (LDAP) behind SSO sign-in.

The gateway authenticates people; this module answers "is this email enrolled in Paper Grader, and as what?" from LDAP
group membership: members of the admin group are admins, members of the TA group are TAs, everyone else is not enrolled.
It only ever reads (a read-only bind account).
"""

import threading
import time
from dataclasses import dataclass
from typing import Protocol

from app.config import Settings, get_settings
from app.models import Role


class DirectoryError(Exception):
    """The directory could not be reached or answered unexpectedly."""


@dataclass(frozen=True)
class DirUser:
    uid: str
    email: str
    name: str
    role: Role | None  # None: in the directory but in neither group, so not enrolled


class Directory(Protocol):
    def find(self, email: str) -> DirUser | None: ...
    def members(self) -> list[DirUser]: ...


def _first(value) -> str:
    """ldap3 attribute values are lists (or a scalar for single-valued ones); take the first as a string."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "")


class LdapDirectory:
    """LDAP via ldap3. Works with any directory that has person entries under `ou=people,<base>` with `uid`, `mail`,
    `cn`/`displayName` and groups under `ou=groups,<base>` listing members by DN in `member` or `uniqueMember` (LLDAP,
    OpenLDAP groupOfNames/groupOfUniqueNames, 389-ds). Groups and lookups are cached for a few seconds."""

    def __init__(self, s: Settings, connect=None):
        self.url, self.bind_dn, self.password = s.ldap_url, s.ldap_bind_dn, s.ldap_bind_password
        self.people_dn = f"{s.ldap_people_ou},{s.ldap_base_dn}"
        self.groups_dn = f"{s.ldap_groups_ou},{s.ldap_base_dn}"
        self.admin_group, self.ta_group = s.ldap_admin_group, s.ldap_ta_group
        self.ttl = s.ldap_cache_seconds
        self._connect = connect or self._real_connect
        self._lock = threading.Lock()
        self._cache: dict = {}

    def _real_connect(self):
        import ldap3
        server = ldap3.Server(self.url, get_info=ldap3.NONE, connect_timeout=5)
        return ldap3.Connection(
            server, user=self.bind_dn or None, password=self.password or None, auto_bind=True,
            receive_timeout=10, raise_exceptions=True,
        )

    def _cached(self, key, fn):
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        value = fn()
        with self._lock:
            self._cache[key] = (now, value)
        return value

    def _search(self, base: str, flt: str, attrs: list[str]) -> list:
        try:
            conn = self._connect()
            try:
                conn.search(base, flt, attributes=attrs)
                return list(conn.entries)
            finally:
                conn.unbind()
        except Exception as e:  # ldap3 raises many LDAP*Exception types
            raise DirectoryError(f"LDAP error: {e}") from e

    def _group_members(self) -> dict[str, set[str]]:
        def load():
            from ldap3.utils.conv import escape_filter_chars as esc
            out: dict[str, set[str]] = {self.admin_group: set(), self.ta_group: set()}
            flt = f"(|(cn={esc(self.admin_group)})(cn={esc(self.ta_group)}))"
            for e in self._search(self.groups_dn, flt, ["cn", "member", "uniqueMember"]):
                cn = _first(e["cn"].value)
                dns = set()
                for attr in ("member", "uniqueMember"):
                    if attr in e.entry_attributes:
                        v = e[attr].value
                        dns |= {str(x).lower() for x in (v if isinstance(v, list) else [v] if v else [])}
                if cn in out:
                    out[cn] |= dns
            return out
        return self._cached("groups", load)

    def _role(self, dn: str) -> Role | None:
        groups = self._group_members()
        d = dn.lower()
        if d in groups[self.admin_group]:
            return Role.admin
        if d in groups[self.ta_group]:
            return Role.ta
        return None

    def _to_user(self, entry) -> DirUser | None:
        attrs = entry.entry_attributes
        mail = _first(entry["mail"].value) if "mail" in attrs else ""
        if not mail:
            return None
        name = _first(entry["displayName"].value) if "displayName" in attrs else ""
        if not name and "cn" in attrs:
            name = _first(entry["cn"].value)
        uid = _first(entry["uid"].value) if "uid" in attrs else ""
        return DirUser(uid=uid or mail.lower(), email=mail.lower(), name=name, role=self._role(entry.entry_dn))

    _ATTRS = ["uid", "mail", "cn", "displayName"]

    def find(self, email: str) -> DirUser | None:
        from ldap3.utils.conv import escape_filter_chars as esc

        def load():
            entries = self._search(self.people_dn, f"(&(objectClass=person)(mail={esc(email)}))", self._ATTRS)
            users = [u for u in (self._to_user(e) for e in entries) if u]
            return users[0] if len(users) == 1 else None  # two entries with one mail: refuse to guess
        return self._cached(("find", email.lower()), load)

    def members(self) -> list[DirUser]:
        def load():
            entries = self._search(self.people_dn, "(objectClass=person)", self._ATTRS)
            users = [u for u in (self._to_user(e) for e in entries) if u]
            seen: dict[str, int] = {}
            for u in users:
                seen[u.email] = seen.get(u.email, 0) + 1
            # Two entries on one email can't be told apart (find() refuses them too), so neither is listed.
            return sorted(
                (u for u in users if u.role is not None and seen[u.email] == 1), key=lambda u: (u.name or u.email).lower()
            )
        return self._cached("members", load)


_instance: Directory | None = None


def get_directory() -> Directory:
    """FastAPI dependency (tests override it with a fake)."""
    global _instance
    if _instance is None:
        _instance = LdapDirectory(get_settings())
    return _instance
