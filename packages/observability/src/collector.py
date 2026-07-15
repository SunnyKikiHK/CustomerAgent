"""In-process span collector for evaluation/metrics.

The observability facade (``observe``) normally only logs. For evaluation we want
to capture per-phase timing (planner/reasoning vs tool execution vs LLM calls)
for one pipeline run without standing up a full OTel backend.

``collect_spans()`` is a context manager that activates a contextvar-scoped
buffer; every ``observe`` block that finishes while a collector is active appends
a ``SpanRecord`` (name, duration_ms, status). It is opt-in and zero-cost when no
collector is active, so production paths are unaffected.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

_ACTIVE: contextvars.ContextVar[list["SpanRecord"] | None] = contextvars.ContextVar(
    "eval_span_collector", default=None
)


@dataclass
class SpanRecord:
    """One completed span captured during a collected run."""

    name: str
    duration_ms: float
    status: str


def record_span(name: str, duration_ms: float, status: str) -> None:
    """Append a span to the active collector, if any (no-op otherwise)."""
    buffer = _ACTIVE.get()
    if buffer is not None:
        buffer.append(SpanRecord(name=name, duration_ms=duration_ms, status=status))


@contextmanager
def collect_spans() -> Iterator[list[SpanRecord]]:
    """Activate span collection for the duration of the block.

    Yields the list that will be populated with spans that complete inside the
    block. Nested collectors are supported; each restores the previous buffer.
    """
    buffer: list[SpanRecord] = []
    token = _ACTIVE.set(buffer)
    try:
        yield buffer
    finally:
        _ACTIVE.reset(token)


__all__ = ["SpanRecord", "collect_spans", "record_span"]
