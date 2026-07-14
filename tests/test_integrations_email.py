"""Offline tests for Phase 7: credential encryption and email provider selection."""

from __future__ import annotations

import pytest


# ── Encryption round-trip ─────────────────────────────────────────────────────

def test_encrypt_decrypt_round_trip():
    from packages.auth.src.crypto import decrypt_secret, encrypt_secret, is_encrypted

    token = encrypt_secret("google-refresh-token-abc123")
    assert is_encrypted(token)
    assert token.startswith("enc:v1:")
    # Ciphertext must not leak the plaintext.
    assert "google-refresh-token-abc123" not in token
    assert decrypt_secret(token) == "google-refresh-token-abc123"


def test_decrypt_rejects_non_ciphertext():
    from packages.auth.src.crypto import EncryptionError, decrypt_secret

    with pytest.raises(EncryptionError):
        decrypt_secret("not-encrypted-plaintext")


def test_is_encrypted_predicate():
    from packages.auth.src.crypto import encrypt_secret, is_encrypted

    assert is_encrypted(encrypt_secret("x")) is True
    assert is_encrypted("plain") is False
    assert is_encrypted(None) is False


def test_generate_key_is_usable():
    import os
    from packages.auth.src import crypto

    key = crypto.generate_key()
    prev = os.environ.get("INTEGRATION_ENCRYPTION_KEY")
    os.environ["INTEGRATION_ENCRYPTION_KEY"] = key
    try:
        token = crypto.encrypt_secret("secret")
        assert crypto.decrypt_secret(token) == "secret"
    finally:
        if prev is None:
            os.environ.pop("INTEGRATION_ENCRYPTION_KEY", None)
        else:
            os.environ["INTEGRATION_ENCRYPTION_KEY"] = prev


# ── Email provider selection ──────────────────────────────────────────────────

def test_email_provider_selection(monkeypatch):
    from apps.tool_gateway.src import email_providers

    monkeypatch.setenv("EMAIL_PROVIDER", "mock")
    assert type(email_providers.select_email_provider()).__name__ == "MockEmailProvider"

    monkeypatch.setenv("EMAIL_PROVIDER", "console")
    assert type(email_providers.select_email_provider()).__name__ == "ConsoleEmailProvider"

    monkeypatch.setenv("EMAIL_PROVIDER", "google")
    assert type(email_providers.select_email_provider()).__name__ == "GoogleEmailProvider"

    # Unknown mode falls back to mock (fail-safe, no accidental real send).
    monkeypatch.setenv("EMAIL_PROVIDER", "banana")
    assert type(email_providers.select_email_provider()).__name__ == "MockEmailProvider"


def test_email_masking_keeps_domain_only():
    from apps.tool_gateway.src.email_providers import _mask_email

    assert _mask_email("alice@example.com") == "a***@example.com"
    assert _mask_email("bob@corp.io") == "b***@corp.io"
    assert _mask_email("not-an-email") == "***"


@pytest.mark.asyncio
async def test_google_provider_requires_sender():
    """Google send fails closed without a sender_user_id (no silent drop)."""
    from apps.tool_gateway.src.email_providers import GoogleEmailProvider

    provider = GoogleEmailProvider()
    with pytest.raises(RuntimeError):
        await provider.execute("tenant-1", {"recipient_email": "x@y.com", "subject": "s", "body": "b"})


@pytest.mark.asyncio
async def test_console_provider_returns_stable_id():
    from apps.tool_gateway.src.email_providers import ConsoleEmailProvider

    provider = ConsoleEmailProvider()
    mid = await provider.execute(
        "tenant-1",
        {"recipient_email": "a@b.com", "subject": "hi", "body": "hello", "customer_id": "c1"},
    )
    assert mid.startswith("mock-console-email-")
