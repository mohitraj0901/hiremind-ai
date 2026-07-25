"""
Security primitives: password hashing, JWT access tokens, and opaque
refresh tokens.

Design decision (see Module 2 architecture notes): access tokens are
JWTs (stateless, fast to verify on every request); refresh tokens are
cryptographically random opaque strings that we hash before storing —
identical treatment to passwords — because a refresh token grants the
same power as a password until it expires, and JWTs would only add
attack surface without adding revocability we don't already need to
build ourselves.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Refresh tokens are high-entropy random strings, generated with
# `secrets` (CSPRNG), never a JWT. 32 bytes -> 43 url-safe base64 chars.
_REFRESH_TOKEN_BYTES = 32


class TokenType(StrEnum):
    """Distinguishes access vs. refresh JWTs isn't needed since refresh
    tokens aren't JWTs — this enum exists solely to tag the `type` claim
    inside access-token JWTs, so a stolen access token can never be
    replayed against endpoints expecting a different token category."""

    ACCESS = "access"


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(plain_password: str) -> str:
    """Hashes a plaintext password with bcrypt (via passlib)."""
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plaintext password against its bcrypt hash."""
    return _pwd_context.verify(plain_password, hashed_password)


# ---------------------------------------------------------------------------
# Access tokens (JWT, stateless, short-lived)
# ---------------------------------------------------------------------------


def create_access_token(*, user_id: str) -> tuple[str, datetime]:
    """
    Issues a short-lived signed JWT access token.

    Returns the encoded token plus its expiry timestamp so callers
    (the auth service) can include `expires_in` in the API response
    without re-decoding the token they just created.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.access_token_expire_minutes)

    claims: dict[str, Any] = {
        "sub": user_id,
        "type": TokenType.ACCESS.value,
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, expires_at


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Decodes and validates an access token JWT (signature + expiry).

    Raises `jose.JWTError` on any failure (expired, bad signature,
    malformed) — the caller (the `get_current_user` dependency) is
    responsible for translating that into an `UnauthorizedException`.
    """
    settings = get_settings()
    payload: dict[str, Any] = jwt.decode(
        token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
    )
    if payload.get("type") != TokenType.ACCESS.value:
        raise JWTError("Token is not an access token")
    return payload


# ---------------------------------------------------------------------------
# Refresh tokens (opaque, random, hashed-at-rest)
# ---------------------------------------------------------------------------


def generate_refresh_token() -> str:
    """
    Generates a new opaque refresh token. This is the value returned
    to the client — it is NEVER stored in the database in this form,
    only its hash (see `hash_refresh_token`), mirroring how we treat
    passwords: the database should never hold a value that alone is
    sufficient to compromise an account, even in the event of a
    database leak.
    """
    return secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    """
    Hashes a refresh token for storage/lookup.

    SHA-256 (not bcrypt) is deliberate here: refresh tokens are already
    high-entropy random values (unlike human passwords), so there's no
    dictionary/brute-force risk to defend against with a slow hash —
    we only need a fast, deterministic, collision-resistant digest to
    use as a database lookup key.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
