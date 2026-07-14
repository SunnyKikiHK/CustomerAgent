"""Boundary-enforced tool execution for internal analysis and MCP actions."""

from __future__ import annotations

from typing import Any

from packages.agent.src.types import SessionContext
from packages.tool_system.src.registry import ToolBoundary, require_tool_boundary


async def execute_internal_analysis(
    tool_name: str,
    params: dict[str, Any],
    ctx: SessionContext,
) -> dict[str, Any]:
    """Execute a trusted internal analysis tool directly in-process."""
    entry = require_tool_boundary(tool_name, ToolBoundary.INTERNAL)
    safe_params = dict(params)
    # Session identity is authoritative; model-supplied tenant IDs are ignored.
    safe_params["tenant_id"] = ctx.tenant_id
    result = await entry.execute(safe_params, ctx)
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    return result if isinstance(result, dict) else {"result": result}


async def execute_mcp_action(
    tool_name: str,
    params: dict[str, Any],
    ctx: SessionContext,
    *,
    approval_id: str,
    idempotency_key: str,
    actor: str = "agent",
) -> dict[str, Any]:
    """Execute an approved external action through the MCP gateway."""
    require_tool_boundary(tool_name, ToolBoundary.MCP_ACTION)
    from apps.agent_service.src.agent.runtime.mcp.client import get_mcp_action_client
    from apps.agent_service.src.agent.runtime.mcp.tool_layer import get_mcp_tool_layer

    safe_params = dict(params)
    safe_params["tenant_id"] = ctx.tenant_id
    result = await get_mcp_tool_layer().call_mcp_action(
        tool_name,
        safe_params,
        executor=lambda: get_mcp_action_client().call_action(
            tool_name,
            safe_params,
            tenant_id=ctx.tenant_id,
            trace_id=ctx.trace_id,
            approval_id=approval_id,
            idempotency_key=idempotency_key,
            actor=actor,
        ),
    )
    if not result.success:
        raise RuntimeError(result.error or f"MCP action {tool_name} failed")
    return result.data


async def execute_tool_call(
    tool_name: str,
    params: dict[str, Any],
    ctx: SessionContext,
) -> dict[str, Any]:
    """Compatibility entry point restricted to internal analysis tools."""
    return await execute_internal_analysis(tool_name, params, ctx)


__all__ = ["execute_internal_analysis", "execute_mcp_action", "execute_tool_call"]
