"""Small environment-backed config helpers shared across runtime packages."""

from __future__ import annotations

import os


def get_env(name: str, default: str | None = None) -> str | None:
    """Return an environment variable or default."""
    return os.getenv(name, default)


def get_bool_env(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable using common truthy values."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["get_bool_env", "get_env"]
