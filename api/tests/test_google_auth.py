import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from jose import jwt as jose_jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import google
from app.config import get_settings
from app.models import User
from tests.test_auth import TestSession, client  # noqa: F401

CID = "cid.apps.googleusercontent.com"
_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_pem = _key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()


def _jwks():
    from jose import jwk

    pub = _key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    d = jwk.construct(pub, "RS256").to_dict()
    d.update(kid="k1", use="sig", alg="RS256")
    return {"keys": [d]}


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "google_client_id", CID)
    monkeypatch.setattr(s, "google_client_secret", "secret")
    monkeypatch.setattr(s, "public_base_url", "http://localhost:4321")
    monkeypatch.setattr(google, "_jwks_cache", None)


def _id_token(real_nonce, **over):
    claims = {
        "iss": "https://accounts.google.com", "aud": CID, "sub": "1", "email": "ta@x.io",
        "email_verified": True, "name": "Tia", "nonce": real_nonce, "exp": int(time.time()) + 300,
    }
    claims.update(over)
    return jose_jwt.encode(claims, _pem, algorithm="RS256", headers={"kid": "k1"})


def _mock_google(monkeypatch, **claim_over):
    def handler(request: httpx.Request):
        if request.url.host == "oauth2.googleapis.com":
            nonce = google.read_transaction(_cookie["tx"])["nonce"]
            return httpx.Response(200, json={"id_token": _id_token(nonce, **claim_over)})
        return httpx.Response(200, json=_jwks())

    real = httpx.Client
    monkeypatch.setattr(google.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


_cookie = {}


def _start(client):
    r = client.get("/auth/google/login", follow_redirects=False)
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == [CID]
    _cookie["tx"] = client.cookies.get(google.TX_COOKIE)
    return q["state"][0]


def _callback(client, state, code="abc"):
    return client.get(f"/auth/google/callback?code={code}&state={state}", follow_redirects=False)


def test_success_signs_in_existing_user(client, monkeypatch):
    _mock_google(monkeypatch)
    r = _callback(client, _start(client))
    assert r.status_code == 302 and r.headers["location"] == "/"
    assert client.get("/auth/me").json()["email"] == "ta@x.io"


def test_email_match_is_case_insensitive(client, monkeypatch):
    _mock_google(monkeypatch, email="TA@X.io")
    assert _callback(client, _start(client)).headers["location"] == "/"


@pytest.mark.parametrize(
    "over",
    [{"email": "stranger@x.io"}, {"email_verified": False}, {"aud": "other"}, {"nonce": "bad"},
     {"iss": "https://evil.example"}, {"exp": int(time.time()) - 10}],
)
def test_rejected_identities_get_no_session(client, monkeypatch, over):
    _mock_google(monkeypatch, **over)
    r = _callback(client, _start(client))
    assert r.headers["location"].startswith("/login?error=google_")
    assert client.get("/auth/me").status_code == 401


def test_inactive_user_rejected(client, monkeypatch):
    with TestSession() as s:
        u = s.query(User).filter_by(email="ta@x.io").one()
        u.active = False
        s.commit()
    _mock_google(monkeypatch)
    r = _callback(client, _start(client))
    assert r.headers["location"] == "/login?error=google_not_allowed"
    assert client.get("/auth/me").status_code == 401


def test_bad_state_and_missing_cookie(client, monkeypatch):
    _mock_google(monkeypatch)
    _start(client)
    assert _callback(client, "wrong").headers["location"] == "/login?error=google_failed"
    client.cookies.clear()
    assert _callback(client, "x").headers["location"] == "/login?error=google_failed"


def test_user_cancelled(client):
    r = client.get("/auth/google/callback?error=access_denied", follow_redirects=False)
    assert r.headers["location"] == "/login?error=google_cancelled"


def test_providers_and_unconfigured(client, monkeypatch):
    assert client.get("/auth/providers").json() == {"google": True}
    monkeypatch.setattr(get_settings(), "google_client_secret", "")
    assert client.get("/auth/providers").json() == {"google": False}
    assert client.get("/auth/google/login", follow_redirects=False).status_code == 404
    assert client.get("/auth/google/callback?code=a&state=b", follow_redirects=False).status_code == 404
