"""AUTH_MODE=sso: the gateway sends Remote-Email, LDAP decides who is enrolled and as what."""

import ldap3
import pytest

from app.auth.directory import DirectoryError, DirUser, LdapDirectory, get_directory
from app.config import Settings, get_settings
from app.main import app
from app.models import Role, User
from tests.test_auth import TestSession, client, login  # noqa: F401  (fixture reuse)
from tests.test_exams_scoping import make_exam
from tests.test_import import make_pdf, storage, upload  # noqa: F401  (fixture reuse)


class FakeDirectory:
    def __init__(self):
        self.users: dict[str, DirUser] = {}
        self.down = False

    def add(self, email, role, name="", uid=None):
        self.users[email.lower()] = DirUser(uid or email.split("@")[0], email.lower(), name, role)

    def find(self, email):
        if self.down:
            raise DirectoryError("down")
        return self.users.get(email.lower())

    def members(self):
        if self.down:
            raise DirectoryError("down")
        return [u for u in self.users.values() if u.role is not None]


@pytest.fixture()
def sso(client, monkeypatch):
    """The usual two users exist (as before the migration); then switch to SSO with a fake directory."""
    d = FakeDirectory()
    d.add("admin@x.io", Role.admin, "Admin One")
    d.add("ta@x.io", Role.ta, "Tia")
    monkeypatch.setattr(get_settings(), "auth_mode", "sso")
    app.dependency_overrides[get_directory] = lambda: d
    client.cookies.clear()
    yield client, d
    app.dependency_overrides.pop(get_directory, None)


def as_(client, email, **kw):
    return {"Remote-Email": email, **kw}


def test_no_header_is_unauthenticated(sso):
    client, _ = sso
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Remote-Email": "  "}).status_code == 401


def test_existing_user_keeps_their_id_and_is_linked(sso):
    client, _ = sso
    with TestSession() as s:
        old = s.query(User).filter_by(email="admin@x.io").one().id
    r = client.get("/auth/me", headers=as_(client, "ADMIN@x.io"))  # case-insensitive match
    assert r.status_code == 200 and r.json()["id"] == old and r.json()["role"] == "admin"
    with TestSession() as s:
        u = s.get(User, old)
        assert u.ldap_uid == "admin" and u.name == "Admin One" and s.query(User).count() == 2


def test_new_directory_user_is_created_as_cache_row(sso):
    client, d = sso
    d.add("new@x.io", Role.ta, "Newbie")
    a = client.get("/auth/me", headers=as_(client, "new@x.io")).json()
    b = client.get("/auth/me", headers=as_(client, "new@x.io")).json()
    assert a["id"] == b["id"] and a["role"] == "ta" and a["name"] == "Newbie"
    with TestSession() as s:
        u = s.get(User, a["id"])
        assert u.password_hash == "" and u.active and u.ldap_uid == "new"


def test_remote_name_is_used_only_when_ldap_has_no_name(sso):
    client, d = sso
    d.add("nn@x.io", Role.ta, "")
    assert client.get("/auth/me", headers=as_(client, "nn@x.io", **{"Remote-Name": "From Gateway"})).json()["name"] == "From Gateway"
    d.add("nn@x.io", Role.ta, "Directory Name")
    assert client.get("/auth/me", headers=as_(client, "nn@x.io", **{"Remote-Name": "Other"})).json()["name"] == "Directory Name"


def test_role_follows_the_directory(sso):
    client, d = sso
    assert client.get("/auth/me", headers=as_(client, "ta@x.io")).json()["role"] == "ta"
    assert client.get("/auth/users", headers=as_(client, "ta@x.io")).status_code == 403
    d.add("ta@x.io", Role.admin, "Tia")
    assert client.get("/auth/me", headers=as_(client, "ta@x.io")).json()["role"] == "admin"
    assert client.get("/auth/users", headers=as_(client, "ta@x.io")).status_code == 200


def test_not_enrolled_is_403_and_deactivates_the_cache_row(sso):
    client, d = sso
    client.get("/auth/me", headers=as_(client, "ta@x.io"))
    d.add("ta@x.io", None, "Tia")  # still in the directory, but in neither group
    r = client.get("/auth/me", headers=as_(client, "ta@x.io"))
    assert r.status_code == 403 and "not enrolled" in r.json()["detail"]
    assert client.get("/auth/me", headers=as_(client, "stranger@x.io")).status_code == 403
    with TestSession() as s:
        assert s.query(User).filter_by(email="ta@x.io").one().active is False
    d.add("ta@x.io", Role.ta, "Tia")  # re-enrolled: active again
    assert client.get("/auth/me", headers=as_(client, "ta@x.io")).status_code == 200
    with TestSession() as s:
        assert s.query(User).filter_by(email="ta@x.io").one().active is True


def test_directory_down_is_503(sso):
    client, d = sso
    d.down = True
    assert client.get("/auth/me", headers=as_(client, "ta@x.io")).status_code == 503


def test_password_and_user_management_endpoints_are_off(sso):
    client, _ = sso
    h = as_(client, "admin@x.io")
    assert client.post("/auth/login", json={"email": "admin@x.io", "password": "pw"}).status_code == 409
    assert client.put("/auth/me/password", json={"current_password": "pw", "new_password": "newpassword"}, headers=h).status_code == 409
    assert client.post("/auth/users", json={"email": "a@b.c", "password": ""}, headers=h).status_code == 409
    assert client.patch("/auth/users/1", json={"role": "ta"}, headers=h).status_code == 409
    assert client.delete("/auth/users/2", headers=h).status_code == 409
    assert client.get("/auth/google/login", follow_redirects=False).status_code == 409
    assert client.get("/auth/providers").json() == {"google": False, "mode": "sso"}


def test_listing_users_syncs_the_directory_into_the_cache(sso):
    client, d = sso
    d.add("never@x.io", Role.ta, "Never Logged In")
    h = as_(client, "admin@x.io")
    users = {u["email"]: u for u in client.get("/auth/users", headers=h).json()}
    assert users["never@x.io"]["id"] and users["never@x.io"]["active"] and users["never@x.io"]["role"] == "ta"
    d.users.pop("ta@x.io")  # left the course
    users = {u["email"]: u for u in client.get("/auth/users", headers=h).json()}
    assert users["ta@x.io"]["active"] is False and users["admin@x.io"]["active"] is True


def test_import_assigns_a_ta_who_never_signed_in(client, storage, monkeypatch):
    exam_id = make_exam(client)  # made with local sign-in, as before the switch
    d = FakeDirectory()
    d.add("admin@x.io", Role.admin)
    d.add("fresh@x.io", Role.ta, "Fresh TA")
    monkeypatch.setattr(get_settings(), "auth_mode", "sso")
    app.dependency_overrides[get_directory] = lambda: d
    try:
        client.cookies.clear()
        client.headers["Remote-Email"] = "admin@x.io"
        r = upload(client, exam_id, [("s1.pdf", make_pdf())], "student_id,name,ta_emails\ns1,Ann,fresh@x.io\n")
        assert r.status_code == 200 and r.json()["created"] == 1 and r.json()["items"][0]["ta_emails"] == ["fresh@x.io"]
        sub = client.get("/submissions").json()[0]
        fresh = next(u for u in client.get("/auth/users").json() if u["email"] == "fresh@x.io")
        assert sub["ta_ids"] == [fresh["id"]]
    finally:
        app.dependency_overrides.pop(get_directory, None)
        client.headers.pop("Remote-Email", None)


# ---- LdapDirectory against an in-memory LDAP server ------------------------------------------------------

BASE = "dc=example,dc=com"
PEOPLE = {
    "ann": ("ann@x.io", "Ann Admin"),
    "tia": ("tia@x.io", "Tia TA"),
    "both": ("both@x.io", "Both"),
    "nobody": ("nobody@x.io", "No Group"),
}


def mock_connect():
    server = ldap3.Server("mock")
    conn = ldap3.Connection(server, user="uid=obs,ou=people," + BASE, password="pw", client_strategy=ldap3.MOCK_SYNC)
    dn = lambda uid: f"uid={uid},ou=people,{BASE}"  # noqa: E731
    for uid, (mail, cn) in PEOPLE.items():
        conn.strategy.add_entry(dn(uid), {"objectClass": ["person"], "uid": uid, "mail": mail, "cn": cn, "userPassword": "x"})
    conn.strategy.add_entry(f"cn=papergrader_admins,ou=groups,{BASE}", {"objectClass": ["groupOfUniqueNames"], "cn": "papergrader_admins", "member": [dn("ann"), dn("both")]})
    conn.strategy.add_entry(f"cn=papergrader_tas,ou=groups,{BASE}", {"objectClass": ["groupOfUniqueNames"], "cn": "papergrader_tas", "member": [dn("tia"), dn("both")]})
    conn.bind()
    return conn


@pytest.fixture()
def ldap_dir():
    return LdapDirectory(Settings(ldap_base_dn=BASE, ldap_cache_seconds=0), connect=mock_connect)


def test_ldap_roles_from_groups(ldap_dir):
    assert ldap_dir.find("ann@x.io").role == Role.admin
    assert ldap_dir.find("TIA@x.io").role == Role.ta and ldap_dir.find("tia@x.io").name == "Tia TA"
    assert ldap_dir.find("both@x.io").role == Role.admin  # in both groups: admin wins
    assert ldap_dir.find("nobody@x.io").role is None
    assert ldap_dir.find("ghost@x.io") is None


def test_ldap_filter_special_characters_are_escaped(ldap_dir):
    assert ldap_dir.find("*") is None
    assert ldap_dir.find("*)(uid=*") is None
    assert ldap_dir.find("a*@x.io") is None


def test_ldap_members_are_the_enrolled_people_sorted(ldap_dir):
    assert [u.email for u in ldap_dir.members()] == ["ann@x.io", "both@x.io", "tia@x.io"]


def test_ldap_errors_become_directory_errors():
    def boom():
        raise OSError("connection refused")
    d = LdapDirectory(Settings(ldap_base_dn=BASE, ldap_cache_seconds=0), connect=boom)
    with pytest.raises(DirectoryError):
        d.find("ann@x.io")


def test_export_users_for_lldap(tmp_path):
    import json
    from app.export_ldap_users import ldap_id, user_docs
    users = [
        User(id=1, email="Ann.Lee@x.io", name="Ann Lee", password_hash="h", role=Role.admin),
        User(id=2, email="ann.lee@y.io", name="", password_hash="h", role=Role.ta),
        User(id=3, email="admin@x.io", name="Root", password_hash="h", role=Role.ta),
    ]
    docs = user_docs(users)
    assert [d["id"] for d in docs] == ["ann.lee", "ann.lee2", "admin2"]  # unique, and never LLDAP's own "admin"
    assert docs[0] == {"id": "ann.lee", "email": "ann.lee@x.io", "displayName": "Ann Lee", "groups": ["papergrader_admins"]}
    assert docs[1]["groups"] == ["papergrader_tas"] and docs[1]["displayName"] == "ann.lee2"
    assert ldap_id("???@x.io", set()) == "user"


def test_export_reads_only_old_columns_and_survives_a_missing_table(tmp_path):
    """The export runs before the api has migrated the database, so it must work on the PRE-0009 schema."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from app.export_ldap_users import load_users, user_docs

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255), name VARCHAR(255), password_hash VARCHAR(255),"
                       " role VARCHAR(10), active BOOLEAN, token_version INTEGER)"))  # no ldap_uid column
        c.execute(text("INSERT INTO users VALUES (1,'ann@x.io','Ann','h','admin',1,0), (2,'old@x.io','Old','h','ta',0,0)"))
    with sessionmaker(bind=eng)() as db:
        users = load_users(db)
    assert [u.email for u in users] == ["ann@x.io"]  # inactive users are not exported
    assert [d["id"] for d in user_docs(users)] == ["ann"]

    with sessionmaker(bind=create_engine(f"sqlite:///{tmp_path / 'empty.db'}"))() as db:
        assert load_users(db) == []  # a brand-new database: no table, nothing to export


def test_export_skips_the_user_who_is_the_builtin_admin(monkeypatch):
    from app.export_ldap_users import user_docs
    monkeypatch.setattr(get_settings(), "bootstrap_admin_email", "Boss@x.io")
    users = [User(id=1, email="boss@x.io", name="Boss", password_hash="h", role=Role.admin),
             User(id=2, email="ta@x.io", name="T", password_hash="h", role=Role.ta)]
    assert [d["email"] for d in user_docs(users)] == ["ta@x.io"]


def test_a_directory_email_that_equals_another_cache_row_merges_instead_of_failing(sso):
    """The bug: LDAP's `admin` got the email of a stale migrated row, and the unique email made /auth/users a 500."""
    client, d = sso
    from app.models import Grade, GradeStatus, Question, Rubric, Student, Submission, submission_tas
    with TestSession() as s:
        old = User(email="boss@localhost", name="Old Boss", password_hash="", role=Role.admin, ldap_uid="admin2")
        cur = User(email="boss@example.com", name="Boss", password_hash="", role=Role.admin, ldap_uid="boss")
        s.add_all([old, cur]); s.flush()
        from app.models import Exam
        ex = Exam(name="E"); s.add(ex); s.flush()
        q = Question(rubric=Rubric(exam_id=ex.id, version=1), number="1", max_points=1)
        st = Student(external_id="s1"); s.add_all([q, st]); s.flush()
        sub = Submission(exam_id=ex.id, student_id=st.id, pdf_path="p"); s.add(sub); s.flush()
        s.execute(submission_tas.insert().values(submission_id=sub.id, user_id=old.id))
        s.add(Grade(submission_id=sub.id, question_id=q.id, ai_score=1, status=GradeStatus.reviewed, reviewed_by=old.id))
        s.commit()
        old_id, cur_id, sub_id = old.id, cur.id, sub.id
    d.add("boss@localhost", Role.admin, "Boss", uid="boss")  # the directory now says: uid boss has the OLD row's email
    r = client.get("/auth/me", headers=as_(client, "boss@localhost"))
    assert r.status_code == 200 and r.json()["id"] == cur_id
    with TestSession() as s:
        assert s.get(User, old_id) is None
        assert s.get(User, cur_id).email == "boss@localhost"
        assert s.execute(submission_tas.select()).all()[0].user_id == cur_id  # assignment moved
        assert s.query(Grade).one().reviewed_by == cur_id  # review attribution moved
    assert client.get("/auth/users", headers=as_(client, "boss@localhost")).status_code == 200


def test_directory_does_not_list_two_people_with_one_email(ldap_dir):
    import ldap3
    base = ldap_dir._connect

    def with_twin():
        conn = base()
        conn.strategy.add_entry(f"uid=twin,ou=people,{BASE}", {"objectClass": ["person"], "uid": "twin", "mail": "tia@x.io", "cn": "Twin"})
        return conn
    ldap_dir._connect = with_twin
    emails = [u.email for u in ldap_dir.members()]
    assert "tia@x.io" not in emails and "ann@x.io" in emails
