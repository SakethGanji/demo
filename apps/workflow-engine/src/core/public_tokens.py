"""Short-lived, HMAC-signed tokens for public-app → engine API calls.

Issued in the HTML returned by GET /a/{slug} and embedded in the page so the
running app can call /a/{slug}/api/... without the visitor having any other
form of auth. The token is scoped to a specific app + published version and
expires quickly; if the visitor leaves the tab open past `exp`, the page
re-fetches automatically (the token is short-lived by design).

Format:  base64url(payload).base64url(hmac-sha256)
Payload: JSON { "app_id", "version_id", "exp" }   (exp = unix seconds)

We hand-roll this because the only thing we need is "is this payload signed
by us and not expired" — pulling in PyJWT for that is overkill.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from functools import lru_cache

from .config import settings


_DEFAULT_TTL_SECONDS = 60 * 60  # 1 hour


@dataclass(frozen=True)
class PublicTokenClaims:
    app_id: str
    version_id: int
    exp: int  # unix seconds


class PublicTokenError(Exception):
    """Token failed to verify (bad signature, malformed, or expired)."""


@lru_cache
def _signing_secret() -> bytes:
    """Return the HMAC key. Prefers WORKFLOW_PUBLIC_TOKEN_SECRET; otherwise
    a per-process random value. The fallback is fine for a single-pod POC
    (visitors get a fresh token on every page load anyway), but production
    should always set the env var so tokens survive restarts and rolling
    deploys."""
    if settings.public_token_secret:
        return settings.public_token_secret.encode("utf-8")
    return secrets.token_bytes(32)


def _b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def issue_token(app_id: str, version_id: int, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> str:
    payload = {
        "app_id": app_id,
        "version_id": version_id,
        "exp": int(time.time()) + ttl_seconds,
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body_b64 = _b64u_encode(body)
    sig = hmac.new(_signing_secret(), body_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{body_b64}.{_b64u_encode(sig)}"


def verify_token(token: str) -> PublicTokenClaims:
    try:
        body_b64, sig_b64 = token.split(".", 1)
    except ValueError as e:
        raise PublicTokenError("malformed token") from e

    expected_sig = hmac.new(
        _signing_secret(), body_b64.encode("ascii"), hashlib.sha256
    ).digest()
    try:
        provided_sig = _b64u_decode(sig_b64)
    except Exception as e:
        raise PublicTokenError("malformed signature") from e
    if not hmac.compare_digest(expected_sig, provided_sig):
        raise PublicTokenError("invalid signature")

    try:
        payload = json.loads(_b64u_decode(body_b64))
    except Exception as e:
        raise PublicTokenError("malformed payload") from e

    if not isinstance(payload, dict) or {"app_id", "version_id", "exp"} - set(payload):
        raise PublicTokenError("payload missing required claims")

    if int(payload["exp"]) < int(time.time()):
        raise PublicTokenError("token expired")

    return PublicTokenClaims(
        app_id=str(payload["app_id"]),
        version_id=int(payload["version_id"]),
        exp=int(payload["exp"]),
    )
