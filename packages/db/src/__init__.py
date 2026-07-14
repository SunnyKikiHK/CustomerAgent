"""Async PostgreSQL helpers shared across agent, gateway, and retrieval code."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import json

import asyncpg


class PostgresConfigError(RuntimeError):
    """Raised when PostgreSQL access is requested without configuration."""


_POOL: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Decode json/jsonb columns to Python objects on every pooled connection."""
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    await conn.set_type_codec(
        "json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


def get_database_url() -> str:
    """Return the configured PostgreSQL URL or a sensible local default."""
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB", "agent")
    user = os.getenv("POSTGRES_USER", "sunny")
    password = os.getenv("POSTGRES_PASSWORD", "sunny")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def _pool_loop_is_closed(pool: asyncpg.Pool) -> bool:
    """Return whether a cached pool belongs to an event loop that has closed."""
    loop = getattr(pool, "_loop", None)
    return bool(loop and loop.is_closed())


async def get_pool() -> asyncpg.Pool:
    """Return the process-wide asyncpg pool."""
    global _POOL
    if _POOL is not None and _pool_loop_is_closed(_POOL):
        _POOL = None
    if _POOL is None:
        url = get_database_url()
        if not url:
            raise PostgresConfigError("DATABASE_URL is not configured")
        _POOL = await asyncpg.create_pool(
            url, min_size=1, max_size=5, init=_init_connection
        )
    return _POOL


async def close_pool() -> None:
    """Close the shared pool when tests or workers shut down."""
    global _POOL
    if _POOL is not None:
        await _POOL.close()
        _POOL = None


@asynccontextmanager
async def tenant_connection(tenant_id: str) -> AsyncIterator[asyncpg.Connection]:
    """Yield a connection with tenant context set for the transaction."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("select set_config('app.current_tenant_id', $1, true)", tenant_id)
            yield conn


async def fetch_all(query: str, *args: Any, tenant_id: str) -> list[asyncpg.Record]:
    """Run a tenant-scoped query and return all rows."""
    async with tenant_connection(tenant_id) as conn:
        return list(await conn.fetch(query, *args))


async def fetch_one(query: str, *args: Any, tenant_id: str) -> asyncpg.Record | None:
    """Run a tenant-scoped query and return one row when present."""
    async with tenant_connection(tenant_id) as conn:
        return await conn.fetchrow(query, *args)


async def execute(query: str, *args: Any, tenant_id: str) -> str:
    """Run a tenant-scoped write statement and return asyncpg status text."""
    async with tenant_connection(tenant_id) as conn:
        return await conn.execute(query, *args)


__all__ = [
    "PostgresConfigError",
    "close_pool",
    "execute",
    "fetch_all",
    "fetch_one",
    "get_database_url",
    "get_pool",
    "tenant_connection",
]
