"""Per-user OAuth integration persistence (``user_integrations``).

Stores one integration row per (user, provider). The refresh token is encrypted
at rest via ``packages.auth.src.crypto`` and is never returned through the public
helpers: ``get_integration_public`` omits it, while ``get_refresh_token`` decrypts
in-process only for minting a short-lived access token at send time.
"""

from __future__ import annotations

from typing import Any

from packages.auth.src.crypto import decrypt_secret, encrypt_secret
from packages.db.src import get_pool


async def upsert_integration(
    *,
    user_id: str,
    provider: str = "google",
    account_email: str | None = None,
    refresh_token: str | None = None,
    scopes: str | None = None,
    status: str = "connected",
) -> dict[str, Any]:
    """Create/update a user's integration, encrypting the refresh token."""
    token_enc = encrypt_secret(refresh_token) if refresh_token else None
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            insert into user_integrations
                (user_id, provider, account_email, refresh_token_enc, scopes, status, updated_at)
            values ($1::uuid, $2, $3, $4, $5, $6, now())
            on conflict (user_id, provider) do update set
                account_email = excluded.account_email,
                refresh_token_enc = coalesce(excluded.refresh_token_enc, user_integrations.refresh_token_enc),
                scopes = excluded.scopes,
                status = excluded.status,
                updated_at = now()
            returning id::text, user_id::text, provider, account_email, status
            """,
            user_id,
            provider,
            account_email,
            token_enc,
            scopes,
            status,
        )
    return _public_row(row)


async def get_integration_public(*, user_id: str, provider: str = "google") -> dict[str, Any] | None:
    """Return a user's integration WITHOUT the token (safe for API responses)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "select id::text, user_id::text, provider, account_email, status,"
            " (refresh_token_enc is not null) as has_token, connected_at, updated_at"
            " from user_integrations where user_id = $1::uuid and provider = $2",
            user_id,
            provider,
        )
    if row is None:
        return None
    data = _public_row(row)
    data["has_token"] = bool(row["has_token"])
    return data


async def get_refresh_token(*, user_id: str, provider: str = "google") -> str | None:
    """Decrypt and return the refresh token (in-process use only, never an API)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "select refresh_token_enc, status from user_integrations"
            " where user_id = $1::uuid and provider = $2",
            user_id,
            provider,
        )
    if row is None or row["status"] != "connected" or not row["refresh_token_enc"]:
        return None
    try:
        return decrypt_secret(row["refresh_token_enc"])
    except Exception:
        return None


async def revoke_integration(*, user_id: str, provider: str = "google") -> bool:
    """Mark an integration revoked and clear the stored token."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        status = await conn.execute(
            "update user_integrations set status = 'revoked', refresh_token_enc = null,"
            " updated_at = now() where user_id = $1::uuid and provider = $2",
            user_id,
            provider,
        )
    return status.rsplit(" ", 1)[-1] != "0"


#: Route-facing aliases (intuitive names used by the API layer).
connect_integration = upsert_integration
disconnect_integration = revoke_integration
get_integration_status = get_integration_public


def _public_row(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "provider": row["provider"],
        "account_email": row["account_email"],
        "status": row["status"],
    }


__all__ = [
    "upsert_integration",
    "get_integration_public",
    "get_refresh_token",
    "revoke_integration",
    "connect_integration",
    "disconnect_integration",
    "get_integration_status",
]
