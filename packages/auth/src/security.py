"""Password hashing and JWT encode/decode helpers.

Kept dependency-light and import-safe: passlib/jose are optional at import time
so unit tests that never touch auth can run without them, but the functions
raise clearly if used without the dependency installed.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any

_JWT_ALGORITHM = "HS256"
_DEFAULT_TOKEN_TTL_SECONDS = 60 * 60 * 12  # 12 hours


def _jwt_secret() -> str:
    """Return the signing secret, falling back to a dev-only default."""
    return os.getenv("AUTH_JWT_SECRET") or "dev-insecure-secret-change-me"


# ── Password hashing ──────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash a plaintext password for storage.

    Uses passlib/bcrypt when available; otherwise a salted PBKDF2-SHA256 hash so
    local/dev and tests work without the native bcrypt wheel. The stored string
    is self-describing (scheme prefix) so verification picks the right path.
    """
    try:
        from passlib.hash import bcrypt

        return bcrypt.hash(password)
    except Exception:
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
        return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify a plaintext password against a stored hash."""
    if hashed.startswith("pbkdf2_sha256$"):
        try:
            _, salt_hex, digest_hex = hashed.split("$", 2)
            salt = bytes.fromhex(salt_hex)
            expected = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
            return hmac.compare_digest(expected.hex(), digest_hex)
        except Exception:
            return False
    try:
        from passlib.hash import bcrypt

        return bcrypt.verify(password, hashed)
    except Exception:
        return False


# ── JWT ─────────────────────────────────────────────────────────────────────

def create_access_token(
    *,
    subject: str,
    email: str,
    extra_claims: dict[str, Any] | None = None,
    ttl_seconds: int = _DEFAULT_TOKEN_TTL_SECONDS,
) -> str:
    """Create a signed JWT for a user."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": subject,
        "email": email,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    if extra_claims:
        payload.update(extra_claims)

    from jose import jwt

    return jwt.encode(payload, _jwt_secret(), algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT, returning its claims.

    Raises ``ValueError`` on any invalid/expired token so callers can map it to
    a 401 without importing jose's exception types.
    """
    from jose import jwt
    from jose.exceptions import JWTError

    try:
        return jwt.decode(token, _jwt_secret(), algorithms=[_JWT_ALGORITHM])
    except JWTError as exc:
        raise ValueError(f"invalid token: {exc}") from exc


__all__ = [
    "hash_password",
    "verify_password",
    "create_access_token",
    "decode_access_token",
]
