"""Sign in with Google (OpenID Connect, authorization code flow with PKCE).

Only the pieces that talk to Google live here; the route in routes/auth.py decides which local user a
verified identity maps to.
"""

import base64
import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from jose import JWTError, jwt

from app.config import get_settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")

TX_COOKIE = "oauth_tx"
TX_MAX_AGE = 600
_ALG = "HS256"
_JWKS_TTL = 3600
_jwks_cache: tuple[float, dict] | None = None


class GoogleAuthError(Exception):
    """Any reason a Google sign-in cannot be accepted. The message is for logs, never shown to users."""


def enabled() -> bool:
    s = get_settings()
    return bool(s.google_client_id and s.google_client_secret and s.public_base_url)


def redirect_uri() -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/api/auth/google/callback"


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def start() -> tuple[str, str]:
    """Return (authorize_url, signed transaction cookie value)."""
    s = get_settings()
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    tx = jwt.encode(
        {"state": state, "nonce": nonce, "cv": verifier, "exp": datetime.now(timezone.utc) + timedelta(seconds=TX_MAX_AGE)},
        s.jwt_secret,
        algorithm=_ALG,
    )
    query = urlencode(
        {
            "client_id": s.google_client_id,
            "redirect_uri": redirect_uri(),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
    )
    return f"{AUTH_URL}?{query}", tx


def read_transaction(cookie: str | None) -> dict:
    if not cookie:
        raise GoogleAuthError("missing transaction cookie")
    try:
        return jwt.decode(cookie, get_settings().jwt_secret, algorithms=[_ALG])
    except JWTError as e:
        raise GoogleAuthError(f"bad transaction cookie: {e}")


def _jwks(client: httpx.Client, *, refresh: bool = False) -> dict:
    global _jwks_cache
    now = time.monotonic()
    if refresh or _jwks_cache is None or now - _jwks_cache[0] > _JWKS_TTL:
        r = client.get(JWKS_URL)
        r.raise_for_status()
        _jwks_cache = (now, r.json())
    return _jwks_cache[1]


def complete(code: str, tx: dict, client: httpx.Client | None = None) -> dict:
    """Exchange the code, verify the ID token, and return its claims (email is verified)."""
    s = get_settings()
    own = client is None
    client = client or httpx.Client(timeout=10)
    try:
        r = client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret,
                "redirect_uri": redirect_uri(),
                "grant_type": "authorization_code",
                "code_verifier": tx["cv"],
            },
        )
        if r.status_code != 200:
            raise GoogleAuthError(f"token exchange failed: {r.status_code}")
        id_token = r.json().get("id_token")
        if not id_token:
            raise GoogleAuthError("no id_token in response")

        claims = None
        for refresh in (False, True):  # a rotated signing key: refetch once
            try:
                claims = jwt.decode(
                    id_token,
                    _jwks(client, refresh=refresh),
                    algorithms=["RS256"],
                    audience=s.google_client_id,
                    options={"verify_at_hash": False},
                )
                break
            except JWTError as e:
                last = e
        if claims is None:
            raise GoogleAuthError(f"id_token invalid: {last}")
    except httpx.HTTPError as e:
        raise GoogleAuthError(f"network error: {e}")
    finally:
        if own:
            client.close()

    if claims.get("iss") not in ISSUERS:
        raise GoogleAuthError("wrong issuer")
    if not secrets.compare_digest(str(claims.get("nonce", "")), str(tx["nonce"])):
        raise GoogleAuthError("nonce mismatch")
    if claims.get("email_verified") is not True or not claims.get("email"):
        raise GoogleAuthError("email not verified")
    return claims
