"""Langfuse client accessor for the observability facade.

Langfuse is optional. This module builds a process-wide client only when
credentials are present, and returns None otherwise so callers (the tracer
facade) can no-op cleanly. Importing this module never requires the langfuse
package to be installed.
"""

from __future__ import annotations

import os
from typing import Any

_CLIENT: Any | None = None
_ATTEMPTED = False


def get_langfuse_client() -> Any | None:
    """Return a cached Langfuse client, or None when unavailable.

    Requires ``LANGFUSE_PUBLIC_KEY`` and ``LANGFUSE_SECRET_KEY``. The client is
    built once and cached; any construction/import failure yields None so
    instrumentation degrades to logs-only.
    """
    global _CLIENT, _ATTEMPTED
    if _ATTEMPTED:
        return _CLIENT
    _ATTEMPTED = True

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    if not (public_key and secret_key):
        _CLIENT = None
        return None
    try:
        from langfuse import Langfuse

        _CLIENT = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=os.getenv("LANGFUSE_HOST", "http://localhost:3000"),
        )
    except Exception:
        _CLIENT = None
    return _CLIENT


def reset_langfuse_client() -> None:
    """Drop the cached client (used by tests that toggle credentials)."""
    global _CLIENT, _ATTEMPTED
    _CLIENT = None
    _ATTEMPTED = False


__all__ = ["get_langfuse_client", "reset_langfuse_client"]
