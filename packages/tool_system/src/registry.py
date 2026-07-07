"""Tool registry for LLM tool-calling and execution.

The Phase 1 tools execute in-process. Later phases can route sandboxed tools to
tool-gateway while preserving the same registry contract.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from packages.tool_system.src.tools.query_health import TOOL_DEFINITION as QUERY_HEALTH_DEFINITION
from packages.tool_system.src.tools.query_health import execute_query_health
from packages.tool_system.src.tools.query_playbooks import TOOL_DEFINITION as QUERY_PLAYBOOKS_DEFINITION
from packages.tool_system.src.tools.query_playbooks import execute_query_playbooks
from packages.tool_system.src.tools.send_email import TOOL_DEFINITION as SEND_EMAIL_DEFINITION
from packages.tool_system.src.tools.send_email import execute_send_email
from packages.tool_system.src.tools.send_slack import TOOL_DEFINITION as SEND_SLACK_DEFINITION
from packages.tool_system.src.tools.send_slack import execute_send_slack


ToolExecutor = Callable[..., Awaitable[Any]]


class TenantToolConfig(Protocol):
    """Minimal tenant config shape needed to resolve available tools."""

    tools: list[str]


@dataclass
class ToolEntry:
    """A registered tool: its LLM-facing schema and executor."""

    definition: dict[str, Any]
    execute: ToolExecutor
    requires_sandbox: bool = False
    tags: list[str] = field(default_factory=list)


TOOL_REGISTRY: dict[str, ToolEntry] = {}


class UnknownToolError(KeyError):
    """Raised when a requested tool name is not present in the registry."""


def register_tool(
    name: str,
    definition: dict[str, Any],
    execute: ToolExecutor,
    *,
    requires_sandbox: bool = False,
    tags: list[str] | None = None,
    overwrite: bool = False,
) -> ToolEntry:
    """Register a tool and return its registry entry."""
    if not overwrite and name in TOOL_REGISTRY:
        raise ValueError(f"tool '{name}' is already registered")

    entry = ToolEntry(
        definition=definition,
        execute=execute,
        requires_sandbox=requires_sandbox,
        tags=list(tags or []),
    )
    TOOL_REGISTRY[name] = entry
    return entry


def get_tool_entry(name: str) -> ToolEntry:
    """Return the full registry entry for a tool."""
    try:
        return TOOL_REGISTRY[name]
    except KeyError as exc:
        raise UnknownToolError(name) from exc


def get_tool_definition(name: str) -> dict[str, Any]:
    """Return the JSON Schema definition for a tool."""
    return get_tool_entry(name).definition


def get_tools_for_tenant(tenant_config: TenantToolConfig) -> list[dict[str, Any]]:
    """Return LLM-facing definitions for tools enabled on a tenant."""
    definitions: list[dict[str, Any]] = []
    for name in tenant_config.tools:
        entry = TOOL_REGISTRY.get(name)
        if entry is not None:
            definitions.append(entry.definition)
    return definitions


def register_builtin_tools() -> None:
    """Register Phase 1 customer-success tools."""
    register_tool(
        "query_health",
        QUERY_HEALTH_DEFINITION,
        execute_query_health,
        requires_sandbox=False,
        tags=["read", "health"],
        overwrite=True,
    )
    register_tool(
        "query_playbooks",
        QUERY_PLAYBOOKS_DEFINITION,
        execute_query_playbooks,
        requires_sandbox=False,
        tags=["read", "knowledge"],
        overwrite=True,
    )
    register_tool(
        "send_email",
        SEND_EMAIL_DEFINITION,
        execute_send_email,
        requires_sandbox=False,
        tags=["write", "outreach"],
        overwrite=True,
    )
    register_tool(
        "send_slack",
        SEND_SLACK_DEFINITION,
        execute_send_slack,
        requires_sandbox=False,
        tags=["write", "escalation"],
        overwrite=True,
    )


register_builtin_tools()


__all__ = [
    "ToolEntry",
    "ToolExecutor",
    "TOOL_REGISTRY",
    "TenantToolConfig",
    "UnknownToolError",
    "register_tool",
    "register_builtin_tools",
    "get_tool_entry",
    "get_tool_definition",
    "get_tools_for_tenant",
]
