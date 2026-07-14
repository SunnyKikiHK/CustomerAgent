"""Tool registry for LLM schemas and boundary-enforced execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from packages.tool_system.src.tools.check_human_availability import (
    TOOL_DEFINITION as CHECK_HUMAN_AVAILABILITY_DEFINITION,
)
from packages.tool_system.src.tools.check_human_availability import execute_check_human_availability
from packages.tool_system.src.tools.escalate_to_human import (
    TOOL_DEFINITION as ESCALATE_TO_HUMAN_DEFINITION,
)
from packages.tool_system.src.tools.escalate_to_human import execute_escalate_to_human
from packages.tool_system.src.tools.process_refund import TOOL_DEFINITION as PROCESS_REFUND_DEFINITION
from packages.tool_system.src.tools.process_refund import execute_process_refund
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


class ToolBoundary(str, Enum):
    """Trusted execution boundary for a registered tool."""

    INTERNAL = "internal"
    MCP_ACTION = "mcp_action"


@dataclass
class ToolEntry:
    """A registered tool with its schema, executor, and execution boundary."""

    definition: dict[str, Any]
    execute: ToolExecutor
    boundary: ToolBoundary = ToolBoundary.INTERNAL
    side_effecting: bool = False
    tags: list[str] = field(default_factory=list)

    @property
    def requires_sandbox(self) -> bool:
        """Compatibility alias while callers migrate to explicit boundaries."""
        return self.boundary is ToolBoundary.MCP_ACTION


TOOL_REGISTRY: dict[str, ToolEntry] = {}


class UnknownToolError(KeyError):
    """Raised when a requested tool name is not present in the registry."""


class ToolBoundaryError(ValueError):
    """Raised when a tool is invoked through the wrong execution boundary."""


def register_tool(
    name: str,
    definition: dict[str, Any],
    execute: ToolExecutor,
    *,
    boundary: ToolBoundary = ToolBoundary.INTERNAL,
    side_effecting: bool = False,
    requires_sandbox: bool | None = None,
    tags: list[str] | None = None,
    overwrite: bool = False,
) -> ToolEntry:
    """Register a tool and return its registry entry."""
    if not overwrite and name in TOOL_REGISTRY:
        raise ValueError(f"tool '{name}' is already registered")

    # Preserve the old registration argument for downstream extensions while
    # making the explicit boundary authoritative for new code.
    if requires_sandbox is not None:
        boundary = ToolBoundary.MCP_ACTION if requires_sandbox else ToolBoundary.INTERNAL

    entry = ToolEntry(
        definition=definition,
        execute=execute,
        boundary=boundary,
        side_effecting=side_effecting,
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


def require_tool_boundary(name: str, boundary: ToolBoundary) -> ToolEntry:
    """Return a tool only when it belongs to the requested boundary."""
    entry = get_tool_entry(name)
    if entry.boundary is not boundary:
        raise ToolBoundaryError(
            f"tool '{name}' belongs to {entry.boundary.value}, not {boundary.value}"
        )
    return entry


def get_tool_definition(name: str) -> dict[str, Any]:
    """Return the JSON Schema definition for a tool."""
    return get_tool_entry(name).definition


def get_tools_for_tenant(tenant_config: TenantToolConfig) -> list[dict[str, Any]]:
    """Return LLM-facing definitions for tools enabled on a tenant."""
    return [
        entry.definition
        for name in tenant_config.tools
        if (entry := TOOL_REGISTRY.get(name)) is not None
    ]


def get_tools_by_boundary(boundary: ToolBoundary) -> dict[str, ToolEntry]:
    """Return a copy of entries assigned to one execution boundary."""
    return {name: entry for name, entry in TOOL_REGISTRY.items() if entry.boundary is boundary}


def register_builtin_tools() -> None:
    """Register internal analysis and external action tools."""
    register_tool(
        "query_health",
        QUERY_HEALTH_DEFINITION,
        execute_query_health,
        boundary=ToolBoundary.INTERNAL,
        tags=["read", "health"],
        overwrite=True,
    )
    register_tool(
        "query_playbooks",
        QUERY_PLAYBOOKS_DEFINITION,
        execute_query_playbooks,
        boundary=ToolBoundary.INTERNAL,
        tags=["read", "knowledge"],
        overwrite=True,
    )
    register_tool(
        "check_human_availability",
        CHECK_HUMAN_AVAILABILITY_DEFINITION,
        execute_check_human_availability,
        boundary=ToolBoundary.INTERNAL,
        tags=["read", "escalation", "availability"],
        overwrite=True,
    )
    register_tool(
        "process_refund",
        PROCESS_REFUND_DEFINITION,
        execute_process_refund,
        boundary=ToolBoundary.INTERNAL,
        tags=["billing", "refund"],
        overwrite=True,
    )
    register_tool(
        "send_email",
        SEND_EMAIL_DEFINITION,
        execute_send_email,
        boundary=ToolBoundary.MCP_ACTION,
        side_effecting=True,
        tags=["write", "outreach"],
        overwrite=True,
    )
    register_tool(
        "send_slack",
        SEND_SLACK_DEFINITION,
        execute_send_slack,
        boundary=ToolBoundary.MCP_ACTION,
        side_effecting=True,
        tags=["write", "escalation"],
        overwrite=True,
    )
    register_tool(
        "escalate_to_human",
        ESCALATE_TO_HUMAN_DEFINITION,
        execute_escalate_to_human,
        boundary=ToolBoundary.MCP_ACTION,
        side_effecting=True,
        tags=["write", "escalation", "handoff"],
        overwrite=True,
    )


register_builtin_tools()


__all__ = [
    "ToolBoundary",
    "ToolBoundaryError",
    "ToolEntry",
    "ToolExecutor",
    "TOOL_REGISTRY",
    "TenantToolConfig",
    "UnknownToolError",
    "register_tool",
    "register_builtin_tools",
    "get_tool_entry",
    "require_tool_boundary",
    "get_tool_definition",
    "get_tools_for_tenant",
    "get_tools_by_boundary",
]
