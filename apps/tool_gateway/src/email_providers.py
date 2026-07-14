"""Email provider adapters selected by the ``EMAIL_PROVIDER`` env var.

Modes:
  - ``mock``    -> MockEmailProvider (no side effect; stable fake id). Default.
  - ``console`` -> log the email to stdout/logs; return a console id. For local dev.
  - ``google``  -> send via a CSM's Gmail OAuth integration (real delivery).

The Google provider resolves the sender CSM's encrypted refresh token at send
time, mints a short-lived access token, and calls the Gmail API. It never logs
or returns the token or the message body. If Google libraries or credentials are
unavailable, it fails closed (raises) so the action is marked failed and can be
retried, rather than silently dropping a customer email.
"""

from __future__ import annotations

import base64
import logging
import os
from email.mime.text import MIMEText
from typing import Any

from apps.tool_gateway.src.providers import ActionProvider, MockEmailProvider, _mock_id

logger = logging.getLogger("tool_gateway.email")


class ConsoleEmailProvider:
    """Log the email locally instead of sending. Useful for demos/dev."""

    async def execute(self, tenant_id: str, payload: dict[str, Any]) -> str:
        # Log only non-sensitive envelope fields (never the raw body verbatim).
        logger.info(
            "console_email tenant=%s to=%s subject=%s body_len=%s",
            tenant_id,
            _mask_email(str(payload.get("recipient_email", ""))),
            payload.get("subject", ""),
            len(str(payload.get("body", ""))),
        )
        return _mock_id("console-email", tenant_id, payload)


class GoogleEmailProvider:
    """Send email via a CSM's Gmail OAuth integration (real delivery)."""

    async def execute(self, tenant_id: str, payload: dict[str, Any]) -> str:
        sender_user_id = payload.get("sender_user_id")
        if not sender_user_id:
            raise RuntimeError("google email requires a sender_user_id with a connected integration")

        from packages.auth.src.integrations import get_refresh_token

        refresh_token = await get_refresh_token(user_id=str(sender_user_id), provider="google")
        if not refresh_token:
            raise RuntimeError("no connected Google integration for sender")

        access_token = await _mint_access_token(refresh_token)
        message_id = await _gmail_send(
            access_token=access_token,
            to=str(payload.get("recipient_email", "")),
            subject=str(payload.get("subject", "")),
            body=str(payload.get("body", "")),
        )
        return message_id


async def _mint_access_token(refresh_token: str) -> str:
    """Exchange a refresh token for a short-lived Gmail access token."""
    import httpx

    token_uri = os.getenv("CS_MANAGER_GOOGLE_CLOUD_TOKEN_URI", "https://oauth2.googleapis.com/token")
    client_id = os.getenv("CS_MANAGER_GOOGLE_CLOUD_CLIENT_ID")
    client_secret = os.getenv("CS_MANAGER_GOOGLE_CLOUD_CLIENT_SECRET")
    if not (client_id and client_secret):
        raise RuntimeError("Google OAuth client is not configured")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            token_uri,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        token = resp.json().get("access_token")
    if not token:
        raise RuntimeError("token endpoint returned no access_token")
    return token


async def _gmail_send(*, access_token: str, to: str, subject: str, body: str) -> str:
    """Send one message via the Gmail REST API; return the provider message id."""
    import httpx

    mime = MIMEText(body)
    mime["to"] = to
    mime["subject"] = subject
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"raw": raw},
        )
        resp.raise_for_status()
        return str(resp.json().get("id", "gmail-sent"))


def _mask_email(email: str) -> str:
    """Mask an email for logs: keep the domain, obscure the local part."""
    if "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    head = local[:1] if local else ""
    return f"{head}***@{domain}"


def select_email_provider() -> ActionProvider:
    """Return the email provider adapter for the configured EMAIL_PROVIDER mode."""
    mode = os.getenv("EMAIL_PROVIDER", "mock").strip().lower()
    if mode == "google":
        return GoogleEmailProvider()
    if mode == "console":
        return ConsoleEmailProvider()
    return MockEmailProvider()


__all__ = [
    "ConsoleEmailProvider",
    "GoogleEmailProvider",
    "select_email_provider",
]
