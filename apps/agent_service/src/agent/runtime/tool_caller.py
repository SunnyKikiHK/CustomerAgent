"""Tool dispatch boundary for the agent runtime."""

from __future__ import annotations

from typing import Any

from packages.agent.src.types import SessionContext

from apps.agent_service.src.agent.runtime.mcp.retrieval import retrieve_with_optimization
from apps.agent_service.src.agent.runtime.mcp.tool_layer import get_mcp_tool_layer


async def dispatch_tool_call(
    tool_name: str,
    params: dict[str, Any],
    ctx: SessionContext,
) -> dict[str, Any]:
    """Dispatch a tool call through the MCP layer or retrieval optimizer."""
    if tool_name == "query_playbooks" and params.get("use_retrieval_optimizer", True):
        result = await retrieve_with_optimization(
            tool_name=tool_name,
            query=str(params.get("query", "")),
            ctx=ctx,
            params=params,
            top_k=int(params.get("limit", 5)),
        )
        return result.data

    layer = get_mcp_tool_layer()
    result = await layer.call(tool_name, params, ctx)
    if not result.success:
        raise RuntimeError(result.error or f"Tool {tool_name} failed")
    return result.data


__all__ = ["dispatch_tool_call"]
