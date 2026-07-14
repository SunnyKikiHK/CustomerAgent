"""Signal queue helpers with idempotency keys."""

from __future__ import annotations

import json
import os
from typing import Any

from packages.agent.src.orchestration_types import SignalAgentInput
from packages.agent.src.types import CustomerSignal, SessionContext

from apps.agent_service.src.signals.normalizer import normalize_signal_payload

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None


class SignalQueue:
    """Enqueue and deduplicate customer signals."""

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or os.getenv("REDIS_URL")
        self._redis: Any | None = None
        self._memory_seen: set[str] = set()
        self._memory_queue: list[dict[str, Any]] = []

    def _client(self) -> Any | None:
        if self._redis is not None:
            return self._redis
        if redis is None or not self._redis_url:
            return None
        try:
            self._redis = redis.from_url(self._redis_url, decode_responses=True)
            self._redis.ping()
            return self._redis
        except Exception:
            self._redis = None
            return None

    def enqueue(self, payload: dict[str, Any], *, ttl_seconds: int = 3600) -> str:
        """Enqueue a signal if its dedupe key has not been seen recently."""
        signal = normalize_signal_payload(payload)
        key = f"signal:dedupe:{signal.id}"
        client = self._client()
        if client is not None:
            if client.setnx(key, "1"):
                client.expire(key, ttl_seconds)
                client.rpush("signal:queue", json.dumps(payload, default=str))
                return signal.id
            return ""

        if key in self._memory_seen:
            return ""
        self._memory_seen.add(key)
        self._memory_queue.append(payload)
        return signal.id

    def dequeue(self) -> dict[str, Any] | None:
        """Dequeue the next signal payload."""
        client = self._client()
        if client is not None:
            raw = client.lpop("signal:queue")
            return json.loads(raw) if raw else None
        if self._memory_queue:
            return self._memory_queue.pop(0)
        return None

    def to_agent_input(self, payload: dict[str, Any]) -> SignalAgentInput:
        """Convert a queued payload into a SignalAgentInput."""
        signal = normalize_signal_payload(payload)
        return SignalAgentInput(
            tenant_id=signal.tenant_id,
            customer_id=signal.customer_id,
            signal=signal,
            requested_by_user_id=payload.get("requested_by_user_id"),
        )

    def to_session_context(self, payload: dict[str, Any]) -> SessionContext:
        signal = normalize_signal_payload(payload)
        return SessionContext(
            tenant_id=signal.tenant_id,
            user_id=str(payload.get("user_id", signal.customer_id)),
            session_id=str(payload.get("session_id", f"signal:{signal.id}")),
            signal_id=signal.id,
            trace_id=str(payload.get("trace_id") or signal.id),
        )


_QUEUE: SignalQueue | None = None


def get_signal_queue() -> SignalQueue:
    global _QUEUE
    if _QUEUE is None:
        _QUEUE = SignalQueue()
    return _QUEUE


async def enqueue_signal(payload: dict[str, Any]) -> str:
    """Enqueue a signal and record it in the durable `signals` table.

    Returns the signal id when newly enqueued, or "" when it was a duplicate.
    Recording is best-effort (skipped when the DB is unavailable).
    """
    from apps.agent_service.src.signals.normalizer import normalize_signal_payload
    from apps.agent_service.src.signals.records import record_signal

    signal_id = get_signal_queue().enqueue(payload)
    if signal_id:
        signal = normalize_signal_payload(payload)
        await record_signal(
            signal,
            source=str(payload.get("source", "manual")),
            severity=str(payload.get("severity", "normal")),
            status="queued",
        )
    return signal_id


__all__ = ["SignalQueue", "get_signal_queue", "enqueue_signal"]
