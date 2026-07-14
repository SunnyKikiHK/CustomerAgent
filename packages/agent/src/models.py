"""Central resolution of default LLM model names from the environment.

One source of truth so every orchestrator, planner, and runtime helper reads the
same env vars instead of hardcoding model ids:

- ``OPENROUTER_MODEL`` — the worker/subagent model (fast, cheap).
- ``OPENROUTER_LARGE_MODEL`` — the planner/critic model (larger, more capable);
  falls back to the worker model, then to a sane built-in default.
"""

from __future__ import annotations

import os

#: Built-in fallbacks used only when the corresponding env var is unset.
DEFAULT_WORKER_MODEL = "deepseek/deepseek-v4-flash"
DEFAULT_PLANNER_MODEL = "deepseek/deepseek-v4-pro"


def worker_model() -> str:
    """Return the worker/subagent model id (``OPENROUTER_MODEL``)."""
    return os.getenv("OPENROUTER_MODEL", DEFAULT_WORKER_MODEL)


def planner_model() -> str:
    """Return the planner/critic model id.

    Prefers ``OPENROUTER_LARGE_MODEL``, then the worker model, then the built-in
    default — so a tenant that sets only ``OPENROUTER_MODEL`` still gets a
    consistent (single-model) deployment.
    """
    return (
        os.getenv("OPENROUTER_LARGE_MODEL")
        or os.getenv("OPENROUTER_MODEL")
        or DEFAULT_PLANNER_MODEL
    )


__all__ = [
    "DEFAULT_WORKER_MODEL",
    "DEFAULT_PLANNER_MODEL",
    "worker_model",
    "planner_model",
]
