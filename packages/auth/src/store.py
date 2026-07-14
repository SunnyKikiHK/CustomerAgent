"""Postgres access for users and tenant memberships.

Users are global (not tenant-scoped), so these helpers use the raw pool rather
than the tenant-scoped ``fetch_one``/``execute`` in ``packages.db``. Tenant
membership rows carry the tenant_id and are how a user's authorized tenants and
role are derived — never trust an ``X-Tenant-Id`` header as proof of access.
"""

from __future__ import annotations

from typing import Any

from packages.auth.src.models import Membership, TenantRole, User
from packages.auth.src.security import hash_password
from packages.db.src import get_pool


async def get_user_by_email(email: str) -> User | None:
    """Return a user by email (case-insensitive), or None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "select id, email, full_name, password_hash, is_platform_admin, is_active"
            " from users where lower(email) = lower($1)",
            email,
        )
    return _user_from_row(row)


async def get_user_by_id(user_id: str) -> User | None:
    """Return a user by id, or None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "select id, email, full_name, password_hash, is_platform_admin, is_active"
            " from users where id = $1::uuid",
            user_id,
        )
    return _user_from_row(row)


async def create_user(
    *,
    email: str,
    password: str,
    full_name: str | None = None,
    is_platform_admin: bool = False,
) -> User:
    """Create a user with a hashed password and return it."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            insert into users (email, full_name, password_hash, is_platform_admin)
            values ($1, $2, $3, $4)
            on conflict (email) do update set
                full_name = excluded.full_name
            returning id, email, full_name, password_hash, is_platform_admin, is_active
            """,
            email,
            full_name,
            hash_password(password),
            is_platform_admin,
        )
    user = _user_from_row(row)
    assert user is not None
    return user


async def list_memberships(user_id: str) -> list[Membership]:
    """Return every tenant membership for a user."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "select tenant_id, user_id, role, receives_qbr, notification_preferences"
            " from tenant_memberships where user_id = $1::uuid",
            user_id,
        )
    return [_membership_from_row(row) for row in rows]


async def get_membership(user_id: str, tenant_id: str) -> Membership | None:
    """Return a user's membership for one tenant, or None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "select tenant_id, user_id, role, receives_qbr, notification_preferences"
            " from tenant_memberships where user_id = $1::uuid and tenant_id = $2::uuid",
            user_id,
            tenant_id,
        )
    return _membership_from_row(row) if row else None


async def upsert_membership(
    *,
    user_id: str,
    tenant_id: str,
    role: TenantRole = TenantRole.CSM,
    receives_qbr: bool = True,
    notification_preferences: dict[str, Any] | None = None,
) -> Membership:
    """Create or update a tenant membership."""
    import json

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            insert into tenant_memberships
                (tenant_id, user_id, role, receives_qbr, notification_preferences)
            values ($1::uuid, $2::uuid, $3, $4, $5::jsonb)
            on conflict (tenant_id, user_id) do update set
                role = excluded.role,
                receives_qbr = excluded.receives_qbr,
                notification_preferences = excluded.notification_preferences
            returning tenant_id, user_id, role, receives_qbr, notification_preferences
            """,
            tenant_id,
            user_id,
            role.value if isinstance(role, TenantRole) else str(role),
            receives_qbr,
            json.dumps(notification_preferences or {}),
        )
    return _membership_from_row(row)


async def list_qbr_recipients(tenant_id: str) -> list[User]:
    """Return users who should receive QBR reports for a tenant."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            select u.id, u.email, u.full_name, u.password_hash,
                   u.is_platform_admin, u.is_active
            from tenant_memberships m
            join users u on u.id = m.user_id
            where m.tenant_id = $1::uuid and m.receives_qbr = true and u.is_active = true
            """,
            tenant_id,
        )
    return [user for row in rows if (user := _user_from_row(row)) is not None]


def _user_from_row(row: Any) -> User | None:
    if row is None:
        return None
    return User(
        id=str(row["id"]),
        email=row["email"],
        full_name=row["full_name"],
        password_hash=row["password_hash"],
        is_platform_admin=row["is_platform_admin"],
        is_active=row["is_active"],
    )


def _membership_from_row(row: Any) -> Membership:
    prefs = row["notification_preferences"]
    if isinstance(prefs, str):
        import json

        prefs = json.loads(prefs) if prefs else {}
    return Membership(
        tenant_id=str(row["tenant_id"]),
        user_id=str(row["user_id"]),
        role=TenantRole(row["role"]),
        receives_qbr=row["receives_qbr"],
        notification_preferences=prefs or {},
    )


__all__ = [
    "get_user_by_email",
    "get_user_by_id",
    "create_user",
    "list_memberships",
    "get_membership",
    "upsert_membership",
    "list_qbr_recipients",
]
