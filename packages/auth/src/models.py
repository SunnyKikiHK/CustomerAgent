"""Auth domain models: roles, users, memberships, and request context.

Authorization is derived from a user's tenant memberships, never from a client
-supplied ``X-Tenant-Id`` header. ``PLATFORM_ADMIN`` is a user-level flag that
grants cross-tenant access; the other roles are assigned per tenant membership.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TenantRole(str, Enum):
    """Role a user holds within a single tenant (or platform-wide)."""

    PLATFORM_ADMIN = "platform_admin"
    TENANT_ADMIN = "tenant_admin"
    CSM = "csm"
    VIEWER = "viewer"


#: Roles allowed to mutate tenant-scoped data (create/update/delete).
WRITE_ROLES = {TenantRole.PLATFORM_ADMIN, TenantRole.TENANT_ADMIN, TenantRole.CSM}

#: Roles allowed to read tenant-scoped data.
READ_ROLES = {
    TenantRole.PLATFORM_ADMIN,
    TenantRole.TENANT_ADMIN,
    TenantRole.CSM,
    TenantRole.VIEWER,
}


class User(BaseModel):
    """A platform user (typically a Customer Success Manager).

    ``password_hash`` is populated from the DB row but must never be serialized
    into an API response; routes return :class:`UserPublic` instead.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    email: str
    full_name: str | None = None
    password_hash: str | None = None
    is_platform_admin: bool = False
    is_active: bool = True


class UserPublic(BaseModel):
    """User fields safe to return over the API (no password hash)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    email: str
    full_name: str | None = None
    is_platform_admin: bool = False
    is_active: bool = True

    @classmethod
    def from_user(cls, user: User) -> "UserPublic":
        return cls(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_platform_admin=user.is_platform_admin,
            is_active=user.is_active,
        )


class Membership(BaseModel):
    """One user's role within one tenant, plus notification preferences."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    user_id: str
    role: TenantRole = TenantRole.CSM
    receives_qbr: bool = True
    notification_preferences: dict[str, Any] = Field(default_factory=dict)


class AuthContext(BaseModel):
    """Resolved caller context for one authorized tenant-scoped request."""

    model_config = ConfigDict(extra="forbid")

    user: User
    tenant_id: str
    role: TenantRole
    membership: Membership | None = None

    def can_write(self) -> bool:
        """Whether the caller may mutate tenant-scoped data."""
        return self.role in WRITE_ROLES


class TokenClaims(BaseModel):
    """Decoded JWT payload."""

    model_config = ConfigDict(extra="allow")

    sub: str
    email: str
    exp: int | None = None


__all__ = [
    "TenantRole",
    "WRITE_ROLES",
    "READ_ROLES",
    "User",
    "UserPublic",
    "Membership",
    "AuthContext",
    "TokenClaims",
]
