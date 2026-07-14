"""FastAPI auth dependencies: JWT bearer -> user -> tenant authorization.

The rule these dependencies enforce: an ``X-Tenant-Id`` header (or a body/path
tenant) is only a *selection* of which tenant to act on. Authorization comes
from the authenticated user's tenant memberships, never from the header itself.
A user may only act on a tenant they are a member of (or any tenant if they are
a platform admin).
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from packages.auth.src.models import AuthContext, Membership, TenantRole, User, WRITE_ROLES
from packages.auth.src.security import decode_access_token
from packages.auth.src.store import get_membership, get_user_by_id

_bearer = HTTPBearer(auto_error=False)


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    """Resolve and validate the bearer token into an active user."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = decode_access_token(credentials.credentials)
    except Exception as exc:  # noqa: BLE001 - all decode failures are 401
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {type(exc).__name__}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing subject")

    user = await get_user_by_id(user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


async def tenant_context(
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    user: User = Depends(current_user),
) -> AuthContext:
    """Authorize the selected tenant against the user's memberships.

    Returns an AuthContext carrying the authenticated user, the resolved tenant,
    and the membership (None for a platform admin acting cross-tenant).
    """
    if not x_tenant_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="X-Tenant-Id header required")

    membership: Membership | None = await get_membership(user.id, x_tenant_id)
    if membership is None and not user.is_platform_admin:
        # Do not reveal whether the tenant exists; this is an access decision.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized for this tenant",
        )
    role = membership.role if membership else TenantRole.PLATFORM_ADMIN
    return AuthContext(user=user, tenant_id=x_tenant_id, role=role, membership=membership)


def require_role(*allowed: TenantRole):
    """Build a dependency that requires one of the given tenant roles."""

    async def _dep(auth: AuthContext = Depends(tenant_context)) -> AuthContext:
        if auth.user.is_platform_admin:
            return auth
        if auth.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role in {[r.value for r in allowed]}",
            )
        return auth

    return _dep


async def require_write(auth: AuthContext = Depends(tenant_context)) -> AuthContext:
    """Authorize a mutating request: caller must hold a write role in the tenant."""
    if auth.user.is_platform_admin or auth.role in WRITE_ROLES:
        return auth
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="This action requires a write role (tenant_admin or csm)",
    )


async def require_read(auth: AuthContext = Depends(tenant_context)) -> AuthContext:
    """Authorize a read request: any tenant membership (or platform admin) suffices."""
    return auth


__all__ = [
    "current_user",
    "tenant_context",
    "require_role",
    "require_write",
    "require_read",
]
