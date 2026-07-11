"""Low-level in-process and gateway tool execution."""

from __future__ import annotations

import os
from typing import Any

import httpx

from packages.agent.src.types import SessionContext


async def execute_tool_call(
    tool_name: str,
    params: dict[str, Any],
    ctx: SessionContext,
) -> dict[str, Any]:
    """Execute a tool directly without MCP wrapping."""
    registry = _load_tool_registry()
    entry = registry.TOOL_REGISTRY.get(tool_name)
    if entry is None:
        raise ValueError(f"Unknown tool: {tool_name}")

    safe_params = dict(params)
    safe_params.setdefault("tenant_id", ctx.tenant_id)

    if entry.requires_sandbox:
        return await _dispatch_to_tool_gateway(tool_name, safe_params, ctx)

    result = await entry.execute(safe_params, ctx)
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if isinstance(result, dict):
        return result
    return {"result": result}


async def _dispatch_to_tool_gateway(
    tool_name: str,
    params: dict[str, Any],
    ctx: SessionContext,
) -> dict[str, Any]:
    tool_gateway_url = os.getenv("TOOL_GATEWAY_URL", "http://localhost:8002").rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{tool_gateway_url}/run",
            json={
                "tool": tool_name,
                "params": params,
                "tenant_id": ctx.tenant_id,
                "trace_id": ctx.trace_id,
            },
        )
        response.raise_for_status()
        result = response.json()

    if not result.get("success"):
        raise RuntimeError(f"Tool {tool_name} failed: {result.get('error')}")
    data = result.get("data", {})
    return data if isinstance(data, dict) else {"result": data}


def _load_tool_registry() -> Any:
    from packages.tool_system.src import registry

    return registry


__all__ = ["execute_tool_call"]
