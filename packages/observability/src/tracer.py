"""Lightweight tracing helpers for monitor and orchestrator events."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def trace_event(name: str, metadata: dict[str, Any] | None = None) -> None:
    """Record a structured trace event."""
    logger.info("trace_event name=%s metadata=%s", name, metadata or {})


__all__ = ["trace_event"]
