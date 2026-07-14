"""Redis helpers shared across memory, cache, and gateway idempotency code."""

from __future__ import annotations

import os
from typing import Any

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None


class RedisConfigError(RuntimeError):
    """Raised when Redis access is requested without a usable client."""


_CLIENT: Any | None = None


def get_redis_url() -> str:
    """Return the configured Redis URL or a sensible local default."""
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


def get_client() -> Any:
    """Return the process-wide Redis client with a connectivity check."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    if redis is None:
        raise RedisConfigError("redis package is not installed")
    client = redis.Redis.from_url(get_redis_url(), decode_responses=True)
    client.ping()
    _CLIENT = client
    return _CLIENT


def reset_client() -> None:
    """Clear the cached Redis client for tests."""
    global _CLIENT
    _CLIENT = None


def tenant_key(namespace: str, tenant_id: str, *parts: str) -> str:
    """Build a tenant-scoped Redis key."""
    suffix = ":".join(part for part in parts if part)
    return f"{namespace}:{tenant_id}:{suffix}" if suffix else f"{namespace}:{tenant_id}"


__all__ = ["RedisConfigError", "get_client", "get_redis_url", "reset_client", "tenant_key"]
