"""Authentication routes: login and identity introspection."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from packages.auth.src.models import User
from packages.auth.src.security import create_access_token, verify_password
from packages.auth.src.store import get_user_by_email, list_memberships

from apps.api_gateway.src.security import current_user

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class TenantMembershipView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    role: str
    receives_qbr: bool


class LoginResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
    is_platform_admin: bool
    memberships: list[TenantMembershipView]


class MeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    email: str
    full_name: str | None = None
    is_platform_admin: bool
    memberships: list[TenantMembershipView]


def _membership_views(memberships) -> list[TenantMembershipView]:
    return [
        TenantMembershipView(
            tenant_id=m.tenant_id,
            role=m.role.value,
            receives_qbr=m.receives_qbr,
        )
        for m in memberships
    ]


@router.post("/auth/login")
async def login(body: LoginRequest) -> LoginResponse:
    """Exchange email + password for a signed JWT and the user's tenant access."""
    record = await get_user_by_email(body.email)
    if record is None or not record.is_active or record.password_hash is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(body.password, record.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    memberships = await list_memberships(record.id)
    token = create_access_token(
        subject=record.id,
        email=record.email,
        extra_claims={"is_platform_admin": record.is_platform_admin},
    )
    return LoginResponse(
        access_token=token,
        user_id=record.id,
        email=record.email,
        is_platform_admin=record.is_platform_admin,
        memberships=_membership_views(memberships),
    )


@router.get("/auth/me")
async def me(user: User = Depends(current_user)) -> MeResponse:
    """Return the authenticated user and the tenants they can access."""
    memberships = await list_memberships(user.id)
    return MeResponse(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_platform_admin=user.is_platform_admin,
        memberships=_membership_views(memberships),
    )


__all__ = ["router"]
