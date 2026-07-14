"""Signal payload helpers: normalization, converters, and idempotent recording.

Temporal (``apps/temporal_worker``) now owns durable signal processing, retries,
and scheduling. This module no longer maintains a Redis work-list or a polling
consumer; it keeps only the pieces the rest of the system still needs:

- ``SignalQueue`` — payload -> ``SignalAgentInput`` / ``SessionContext`` converters
  plus a lightweight, TTL-style dedupe used to avoid recording the same signal
  twice in quick succession (Redis-backed when available, in-memory otherwise).
- ``enqueue_signal`` — normalize + best-effort record a signal row in status
  ``queued`` and hand it to Temporal via ``start_signal_workflow``. The workflow
  (or its in-process fallback) drives the queued -> processing -> done lifecycle.
"""

from __future__ import annotations

import os
from typing import Any

from packages.agent.src.orchestration_types import SignalAgentInput
from packages.agent.src.types import SessionContext

from apps.agent_service.src.signals.normalizer import normalize_signal_payload

try:  # Redis is optional; dedupe degrades to in-memory when it is absent.
    import redis
except ImportError:  # pragma: no cover
    redis = None


class SignalQueue:
    """Signal payload converters plus a best-effort dedupe.

    The dedupe prevents the same (tenant, customer, type) signal from being
    recorded repeatedly within a TTL window. It is not a work queue: Temporal is
    the system of record for processing, retries, and scheduling.
    """

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or os.getenv("REDIS_URL")
        self._redis: Any | None = None
        self._memory_seen: set[str] = set()

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
        """Return the signal id when first seen in the TTL window, else "".

        Name kept for backward compatibility with callers/tests; this only does
        dedupe now (no work-list push). A return of "" means "already seen".
        """
        signal = normalize_signal_payload(payload)
        key = f"signal:dedupe:{signal.id}"
        client = self._client()
        if client is not None:
            if client.setnx(key, "1"):
                client.expire(key, ttl_seconds)
                return signal.id
            return ""

        if key in self._memory_seen:
            return ""
        self._memory_seen.add(key)
        return signal.id

    def to_agent_input(self, payload: dict[str, Any]) -> SignalAgentInput:
        """Convert a payload into a SignalAgentInput."""
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
    """Record a signal and hand it to Temporal for durable processing.

    Returns the signal id when the workflow was started (or processed in the
    fallback path), or "" when it was a duplicate already in flight. Recording
    happens inside the workflow/fallback, so this function no longer writes the
    row itself.
    """
    from apps.temporal_worker.src.client import start_signal_workflow

    outcome = await start_signal_workflow(payload)
    if not outcome.get("started"):
        return ""
    signal = normalize_signal_payload(payload)
    return signal.id


__all__ = ["SignalQueue", "get_signal_queue", "enqueue_signal"]
