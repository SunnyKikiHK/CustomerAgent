"""auth: JWT validation and tenant-scoped RBAC.

Authorization is derived from a user's tenant memberships, never from a
client-supplied ``X-Tenant-Id`` header. See :mod:`packages.auth.src.models`.
"""

from packages.auth.src.models import (
    AuthContext,
    Membership,
    READ_ROLES,
    TenantRole,
    TokenClaims,
    User,
    UserPublic,
    WRITE_ROLES,
)
from packages.auth.src.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

__all__ = [
    "AuthContext",
    "Membership",
    "READ_ROLES",
    "TenantRole",
    "TokenClaims",
    "User",
    "UserPublic",
    "WRITE_ROLES",
    "create_access_token",
    "decode_access_token",
    "hash_password",
    "verify_password",
]
