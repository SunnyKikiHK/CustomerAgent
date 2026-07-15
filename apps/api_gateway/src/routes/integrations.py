"""Google (Gmail) OAuth connect flow and integration status for a CSM.

The platform holds a single OAuth client (CS_MANAGER_GOOGLE_CLOUD_*). A CSM
connects their own Google account; the resulting refresh token is stored
encrypted per user (never returned through an API or a trace). The QBR/outreach
email path resolves this integration at send time.

Routes require an authenticated user. The connect flow is two steps:
  1. GET  /integrations/google/authorize -> returns the Google consent URL.
  2. GET  /integrations/google/callback  -> exchanges the code, stores the
     encrypted refresh token, and marks the integration connected.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from packages.auth.src.models import User
from packages.auth.src.integrations import (
    connect_integration,
    disconnect_integration,
    get_integration_status,
)

from apps.api_gateway.src.security import current_user

router = APIRouter(tags=["integrations"])

_SCOPES = "https://www.googleapis.com/auth/gmail.send"


def _oauth_config() -> dict[str, str]:
    """Return the platform Google OAuth client config (from deployment env)."""
    client_id = os.getenv("CS_MANAGER_GOOGLE_CLOUD_CLIENT_ID")
    client_secret = os.getenv("CS_MANAGER_GOOGLE_CLOUD_CLIENT_SECRET")
    if not (client_id and client_secret):
        raise HTTPException(status_code=503, detail="Google OAuth client not configured")
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "auth_uri": os.getenv(
            "CS_MANAGER_GOOGLE_CLOUD_AUTH_URI", "https://accounts.google.com/o/oauth2/auth"
        ),
        "token_uri": os.getenv(
            "CS_MANAGER_GOOGLE_CLOUD_TOKEN_URI", "https://oauth2.googleapis.com/token"
        ),
        "redirect_uri": os.getenv(
            "CS_MANAGER_GOOGLE_CLOUD_REDIRECT_URIS",
            "http://localhost:8000/integrations/google/callback",
        ),
    }


@router.get("/integrations/google/authorize")
async def google_authorize(user: User = Depends(current_user)) -> dict[str, Any]:
    """Return the Google consent URL for this CSM to connect Gmail."""
    from urllib.parse import urlencode

    cfg = _oauth_config()
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "response_type": "code",
        "scope": _SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": user.id,  # correlate the callback to this user
    }
    return {"authorize_url": f"{cfg['auth_uri']}?{urlencode(params)}"}


@router.get("/integrations/google/callback")
async def google_callback(
    code: str = Query(...),
    state: str = Query(...),
) -> dict[str, Any]:
    """Exchange the authorization code and store the encrypted refresh token.

    ``state`` carries the user id from the authorize step. The refresh token is
    encrypted at rest; it is never returned in the response.
    """
    import httpx

    cfg = _oauth_config()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            cfg["token_uri"],
            data={
                "code": code,
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "redirect_uri": cfg["redirect_uri"],
                "grant_type": "authorization_code",
            },
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Google token exchange failed")
    data = resp.json()
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        raise HTTPException(
            status_code=400,
            detail="No refresh token returned (re-consent with prompt=consent required)",
        )

    await connect_integration(
        user_id=state,
        provider="google",
        refresh_token=refresh_token,
        scopes=_SCOPES,
    )
    # Never return the token; only confirm the connection.
    return {"connected": True, "provider": "google"}


@router.get("/integrations/status")
async def integration_status(user: User = Depends(current_user)) -> dict[str, Any]:
    """Return the caller's integration status (no secrets).

    When the user has never connected Google, return an explicit disconnected
    payload instead of ``None`` so response validation does not 500.
    """
    status = await get_integration_status(user_id=user.id, provider="google")
    if status is None:
        return {
            "provider": "google",
            "status": "disconnected",
            "has_token": False,
            "account_email": None,
        }
    return status


@router.delete("/integrations/google")
async def google_disconnect(user: User = Depends(current_user)) -> dict[str, Any]:
    """Revoke/disconnect the caller's Google integration."""
    removed = await disconnect_integration(user_id=user.id, provider="google")
    return {"disconnected": removed}


__all__ = ["router"]
