"""Tool dispatch boundary for the agent runtime."""

from __future__ import annotations

import os
from typing import Any

import httpx

from packages.agent.src.types import SessionContext


async def dispatch_tool_call(
    tool_name: str,
    params: dict[str, Any],
    ctx: SessionContext,
) -> dict[str, Any]:
    """Dispatch a tool call to in-process execution or tool-gateway."""
    registry = _load_tool_registry()
    # look up the tool entry; None means the LM hallucinated a tool name
    entry = registry.TOOL_REGISTRY.get(tool_name)
    if entry is None:
        raise ValueError(f"Unknown tool: {tool_name}")

    # copy before mutating so the caller's dict is not modified
    safe_params = dict(params)
    # guarantee tenant_id is always present even if the caller omitted it
    safe_params.setdefault("tenant_id", ctx.tenant_id)

    # tools that touch external providers or need isolation run out-of-process
    if entry.requires_sandbox:
        return await _dispatch_to_tool_gateway(tool_name, safe_params, ctx)

    # in-process execution: call the executor function directly
    result = await entry.execute(safe_params, ctx)

    # normalize the return value to a plain dict regardless of what the
    # executor returned (Pydantic model, dict, or primitive)
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
    """Call tool-gateway for sandboxed or external-provider tools."""
    # fall back to localhost when running the stack locally without env vars
    tool_gateway_url = os.getenv("TOOL_GATEWAY_URL", "http://localhost:8002").rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{tool_gateway_url}/run",
            json={
                "tool": tool_name,
                "params": params,
                "tenant_id": ctx.tenant_id,
                # trace_id lets tool-gateway correlate its logs with the agent trace
                "trace_id": ctx.trace_id,
            },
        )
        # raise immediately on 4xx/5xx so the caller gets a clear exception
        response.raise_for_status()
        result = response.json()

    # tool-gateway wraps its response in {success, data, error}
    if not result.get("success"):
        raise RuntimeError(f"Tool {tool_name} failed: {result.get('error')}")
    data = result.get("data", {})
    # ensure we always return a dict even if the gateway returned a scalar
    return data if isinstance(data, dict) else {"result": data}


def _load_tool_registry() -> Any:
    # deferred import breaks the circular dependency between runtime and tool_system
    from packages.tool_system.src import registry

    return registry


__all__ = ["dispatch_tool_call"]
