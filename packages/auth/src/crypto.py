"""Symmetric encryption for at-rest secrets (OAuth refresh tokens).

A single key from ``INTEGRATION_ENCRYPTION_KEY`` (a urlsafe base64 Fernet key)
encrypts/decrypts credential blobs before they touch the database. Decrypted
values never leave this process boundary: they are used only to mint short-lived
access tokens at send time and are never returned through an API or a trace.

For local/dev without a configured key, a deterministic dev key is derived so
tests and offline runs work; production must set ``INTEGRATION_ENCRYPTION_KEY``.
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Any

#: Marks a stored value as ciphertext produced by this module.
_PREFIX = "enc:v1:"


class EncryptionError(RuntimeError):
    """Raised when encryption/decryption cannot be performed."""


def _fernet() -> Any:
    """Return a Fernet instance built from the configured (or dev) key."""
    from cryptography.fernet import Fernet

    raw = os.getenv("INTEGRATION_ENCRYPTION_KEY")
    if raw:
        key = raw.encode()
    else:
        # Deterministic dev key so local/offline works without configuration.
        # Not for production: set INTEGRATION_ENCRYPTION_KEY there.
        seed = os.getenv("AUTH_JWT_SECRET", "dev-insecure-secret-change-me")
        digest = hashlib.sha256(seed.encode()).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret string, returning a prefixed ciphertext token."""
    if plaintext is None:
        raise EncryptionError("cannot encrypt None")
    try:
        token = _fernet().encrypt(plaintext.encode())
    except Exception as exc:  # missing dep / bad key
        raise EncryptionError(f"encryption unavailable: {type(exc).__name__}") from exc
    return _PREFIX + token.decode()


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a value produced by :func:`encrypt_secret`."""
    if not ciphertext or not ciphertext.startswith(_PREFIX):
        raise EncryptionError("value is not encrypted by this module")
    body = ciphertext[len(_PREFIX):]
    try:
        return _fernet().decrypt(body.encode()).decode()
    except Exception as exc:
        raise EncryptionError(f"decryption failed: {type(exc).__name__}") from exc


def is_encrypted(value: str | None) -> bool:
    """Return whether a stored value is one of our ciphertext tokens."""
    return bool(value) and value.startswith(_PREFIX)


def generate_key() -> str:
    """Generate a fresh Fernet key (helper for provisioning)."""
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


__all__ = [
    "EncryptionError",
    "encrypt_secret",
    "decrypt_secret",
    "is_encrypted",
    "generate_key",
]
