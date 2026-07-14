"""Idempotency stores for preventing duplicate external actions."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from apps.tool_gateway.src.contracts import ActionResult
from packages.db.src import execute, fetch_one
from packages.redis.src import get_client, tenant_key


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class IdempotencyReservation:
    """Outcome of an atomic idempotency reservation attempt."""

    created: bool
    existing: ActionResult | None = None


class IdempotencyStore(Protocol):
    """Atomically reserve and later finalize tenant-scoped idempotency keys."""

    async def reserve(self, tenant_id: str, key: str) -> IdempotencyReservation: ...
    async def finalize(self, tenant_id: str, key: str, result: ActionResult) -> None: ...
    async def get(self, tenant_id: str, key: str) -> ActionResult | None: ...


class InMemoryIdempotencyStore:
    """Process-local store for tests and local development."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], ActionResult] = {}
        self._pending: set[tuple[str, str]] = set()
        self._lock = asyncio.Lock()

    async def reserve(self, tenant_id: str, key: str) -> IdempotencyReservation:
        async with self._lock:
            lookup = (tenant_id, key)
            if lookup in self._values:
                return IdempotencyReservation(created=False, existing=self._values[lookup])
            if lookup in self._pending:
                return IdempotencyReservation(
                    created=False,
                    existing=ActionResult(
                        success=False,
                        status="duplicate",
                        idempotency_key=key,
                        error="duplicate action already in progress",
                        retryable=True,
                    ),
                )
            self._pending.add(lookup)
            return IdempotencyReservation(created=True)

    async def finalize(self, tenant_id: str, key: str, result: ActionResult) -> None:
        async with self._lock:
            lookup = (tenant_id, key)
            self._pending.discard(lookup)
            self._values[lookup] = result

    async def get(self, tenant_id: str, key: str) -> ActionResult | None:
        async with self._lock:
            return self._values.get((tenant_id, key))


class RedisIdempotencyStore:
    """Redis-backed idempotency store using SET NX for atomic reservation."""

    def __init__(self, ttl_seconds: int = 86400) -> None:
        self.ttl_seconds = ttl_seconds

    async def reserve(self, tenant_id: str, key: str) -> IdempotencyReservation:
        client = get_client()
        redis_key = tenant_key("idem", tenant_id, key)
        if client.set(redis_key, "PENDING", ex=self.ttl_seconds, nx=True):
            return IdempotencyReservation(created=True)
        raw = client.get(redis_key)
        if not raw or raw == "PENDING":
            return IdempotencyReservation(
                created=False,
                existing=ActionResult(
                    success=False,
                    status="duplicate",
                    idempotency_key=key,
                    error="duplicate action already in progress",
                    retryable=True,
                ),
            )
        return IdempotencyReservation(created=False, existing=ActionResult.model_validate_json(raw))

    async def finalize(self, tenant_id: str, key: str, result: ActionResult) -> None:
        client = get_client()
        client.setex(
            tenant_key("idem", tenant_id, key),
            self.ttl_seconds,
            result.model_dump_json(),
        )

    async def get(self, tenant_id: str, key: str) -> ActionResult | None:
        raw = get_client().get(tenant_key("idem", tenant_id, key))
        if not raw or raw == "PENDING":
            return None
        return ActionResult.model_validate_json(raw)


class PostgresIdempotencyStore:
    """PostgreSQL-backed idempotency store using INSERT ... ON CONFLICT."""

    def __init__(self, ttl_seconds: int = 86400) -> None:
        self.ttl_seconds = ttl_seconds

    async def reserve(self, tenant_id: str, key: str) -> IdempotencyReservation:
        status = await execute(
            """
            insert into idempotency_keys (tenant_id, idempotency_key, result, expires_at)
            values ($1::uuid, $2, $3::jsonb, $4)
            on conflict (tenant_id, idempotency_key) do nothing
            """,
            tenant_id,
            key,
            json.dumps({"status": "PENDING"}),
            _utcnow() + timedelta(seconds=self.ttl_seconds),
            tenant_id=tenant_id,
        )
        if status.endswith("1"):
            return IdempotencyReservation(created=True)
        row = await fetch_one(
            "select result from idempotency_keys where idempotency_key = $1",
            key,
            tenant_id=tenant_id,
        )
        if row is None:
            return IdempotencyReservation(created=False)
        payload = row["result"]
        if payload.get("status") == "PENDING":
            return IdempotencyReservation(
                created=False,
                existing=ActionResult(
                    success=False,
                    status="duplicate",
                    idempotency_key=key,
                    error="duplicate action already in progress",
                    retryable=True,
                ),
            )
        return IdempotencyReservation(created=False, existing=ActionResult.model_validate(payload))

    async def finalize(self, tenant_id: str, key: str, result: ActionResult) -> None:
        await execute(
            """
            update idempotency_keys
            set result = $2::jsonb,
                expires_at = $3
            where idempotency_key = $1
            """,
            key,
            result.model_dump_json(),
            _utcnow() + timedelta(seconds=self.ttl_seconds),
            tenant_id=tenant_id,
        )

    async def get(self, tenant_id: str, key: str) -> ActionResult | None:
        row = await fetch_one(
            "select result from idempotency_keys where idempotency_key = $1",
            key,
            tenant_id=tenant_id,
        )
        if row is None or row["result"].get("status") == "PENDING":
            return None
        return ActionResult.model_validate(row["result"])


def get_idempotency_store() -> IdempotencyStore:
    """Select the configured idempotency backend."""
    backend = os.getenv("IDEMPOTENCY_BACKEND", "redis").strip().lower()
    ttl_seconds = int(os.getenv("IDEMPOTENCY_TTL_SECONDS", "86400"))
    if backend == "postgres":
        return PostgresIdempotencyStore(ttl_seconds=ttl_seconds)
    if backend == "memory":
        return InMemoryIdempotencyStore()
    return RedisIdempotencyStore(ttl_seconds=ttl_seconds)


__all__ = [
    "IdempotencyReservation",
    "IdempotencyStore",
    "InMemoryIdempotencyStore",
    "PostgresIdempotencyStore",
    "RedisIdempotencyStore",
    "get_idempotency_store",
]
