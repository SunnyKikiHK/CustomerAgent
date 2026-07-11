"""Redis-backed cache with in-process fallback for tool and LLM results."""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any


class ToolCache:
    """TTL cache shared by the MCP tool layer."""

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or os.getenv("REDIS_URL")
        self._memory: dict[str, tuple[Any, float]] = {}
        self._redis: Any | None = None

    def get(self, key: str) -> Any | None:
        """Return a cached value when present and not expired."""
        if self._redis is not None:
            try:
                raw = self._redis.get(key)
                if raw:
                    return json.loads(raw)
            except Exception:
                # Redis is unavailable; fall through to the in-process store.
                pass

        item = self._memory.get(key)
        if item is None:
            return None
        value, expire_at = item
        # Lazy expiry: delete on first access after TTL rather than on a background sweep.
        if time.monotonic() >= expire_at:
            del self._memory[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        """Store a value with a TTL."""
        if self._redis is not None:
            try:
                # setex expects a whole-second TTL; clamp to 1 to avoid a zero/negative value.
                self._redis.setex(key, int(max(ttl_seconds, 1)), json.dumps(value, default=str))
                return
            except Exception:
                # Redis write failed; fall through so the in-process store still caches the value.
                pass

        # Evict the oldest 25 % of entries when the in-process store hits its size cap.
        # Insertion-order eviction is a cheap approximation of LRU for this use-case.
        if len(self._memory) >= 5000:
            for stale_key in list(self._memory)[:1250]:
                self._memory.pop(stale_key, None)
        self._memory[key] = (value, time.monotonic() + ttl_seconds)

    @staticmethod
    def make_key(tool_name: str, params: dict[str, Any]) -> str:
        """Build a stable cache key for a tool invocation."""
        # sort_keys ensures param order doesn't produce different keys for the same call.
        payload = json.dumps(params, sort_keys=True, default=str)
        # MD5 is used for speed and compactness, not security.
        digest = hashlib.md5(payload.encode()).hexdigest()
        return f"tool_cache:{tool_name}:{digest}"


_CACHE: ToolCache | None = None


def get_tool_cache() -> ToolCache:
    """Return the process-wide tool cache singleton."""
    global _CACHE
    if _CACHE is None:
        _CACHE = ToolCache()
    return _CACHE


__all__ = ["ToolCache", "get_tool_cache"]
