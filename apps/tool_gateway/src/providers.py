"""Provider interfaces and safe mock implementations for MCP actions."""

from __future__ import annotations

import hashlib
from typing import Any, Protocol


class ActionProvider(Protocol):
    """Execute one external action without exposing provider credentials."""

    async def execute(self, tenant_id: str, payload: dict[str, Any]) -> str: ...


class MockEmailProvider:
    """Development adapter that records no external side effect."""

    async def execute(self, tenant_id: str, payload: dict[str, Any]) -> str:
        return _mock_id("email", tenant_id, payload)


class MockSlackProvider:
    """Development adapter that records no external side effect."""

    async def execute(self, tenant_id: str, payload: dict[str, Any]) -> str:
        return _mock_id("slack", tenant_id, payload)


class MockHumanEscalationProvider:
    """Development adapter for human escalation; records no external side effect."""

    async def execute(self, tenant_id: str, payload: dict[str, Any]) -> str:
        return _mock_id("escalation", tenant_id, payload)


def _mock_id(kind: str, tenant_id: str, payload: dict[str, Any]) -> str:
    """Return a stable non-sensitive provider identifier for local tests."""
    material = f"{tenant_id}:{payload.get('customer_id', '')}:{kind}"
    return f"mock-{kind}-{hashlib.sha256(material.encode()).hexdigest()[:16]}"


__all__ = [
    "ActionProvider",
    "MockEmailProvider",
    "MockSlackProvider",
    "MockHumanEscalationProvider",
]
