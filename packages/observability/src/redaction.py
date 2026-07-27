"""Redaction helpers for observability attributes.

Traces and spans must never carry secrets or raw PII. These helpers filter and
truncate attribute dicts before they reach any backend (logs, Langfuse, OTel).
The rule: allow small, non-sensitive scalars by key; drop or mask anything that
looks like a credential, an email body, a raw customer message, or a token.
"""

from __future__ import annotations

from typing import Any

#: Attribute keys that must never be recorded, even truncated.
_BLOCKED_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "api_key",
        "authorization",
        "secret",
        "client_secret",
        "credentials",
    }
)

#: Substrings that mark a key as sensitive regardless of exact name.
_SENSITIVE_SUBSTRINGS = ("secret", "token", "password", "credential", "api_key")

#: Maximum length for any string attribute value.
_MAX_STR_LEN = 256


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _BLOCKED_KEYS:
        return True
    return any(sub in lowered for sub in _SENSITIVE_SUBSTRINGS)


def redact_attributes(attributes: dict[str, Any] | None) -> dict[str, Any]:
    """Return a copy of ``attributes`` safe to attach to a trace/span.

    Sensitive keys are dropped entirely; long strings are truncated; nested
    dicts are redacted recursively; lists are summarized by length to avoid
    leaking element content.
    """
    if not attributes:
        return {}
    safe: dict[str, Any] = {}
    for key, value in attributes.items():
        if _is_sensitive_key(str(key)):
            continue
        safe[str(key)] = _redact_value(value)
    return safe


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= _MAX_STR_LEN else value[:_MAX_STR_LEN] + "…"
    if isinstance(value, dict):
        return redact_attributes(value)
    if isinstance(value, (list, tuple)):
        # Do not record element content; a count is enough for observability.
        return {"_count": len(value)}
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    # Unknown types: record only the type name.
    return {"_type": type(value).__name__}


__all__ = ["redact_attributes"]
