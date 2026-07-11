"""Validate/cache/circuit/fallback wrapper for subagent tool calls."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from packages.llm_gateway.src.cache import get_tool_cache
from packages.llm_gateway.src.circuit import CircuitBreaker, CircuitState
from packages.observability.src.tracer import trace_event

from apps.agent_service.src.agent.runtime.tool_dispatch import execute_tool_call
from packages.agent.src.types import SessionContext


@dataclass
class ToolStats:
    """Runtime counters for one tool."""

    total: int = 0
    success: int = 0
    failed: int = 0
    total_latency_ms: float = 0.0
    consecutive_fails: int = 0

    @property
    def success_rate(self) -> float:
        return self.success / self.total if self.total else 1.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.total if self.total else 0.0


@dataclass
class ToolCallResult:
    """Normalized MCP tool call result."""

    success: bool
    data: dict[str, Any]
    tool_name: str
    error: str | None = None
    cached: bool = False
    latency_ms: float = 0.0
    fallback_used: bool = False


_TYPE_MAP = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


class MCPToolLayer:
    """Thin auditable wrapper around in-process tool execution."""

    # Read tools are safe to cache for 5 min; write tools should pass cache_ttl=0 to bypass.
    DEFAULT_CACHE_TTL = 300.0
    # Hard ceiling per tool call so a slow upstream can't block the ReAct loop indefinitely.
    DEFAULT_TIMEOUT = 30.0

    def __init__(self) -> None:
        self._cache = get_tool_cache()
        # Breakers are keyed per tool so a degraded tool doesn't affect healthy ones.
        self._breakers: dict[str, CircuitBreaker] = {}
        # Stats are keyed per tool; PerformanceMonitor reads these via get_stats().
        self._stats: dict[str, ToolStats] = {}
        # Degraded responses let subagents keep reasoning even when a tool is unavailable.
        # Write tools share _write_fallback because their degraded shape is identical.
        self._fallbacks: dict[str, Any] = {
            "query_health": _health_fallback,
            "query_playbooks": _playbook_fallback,
            "send_email": _write_fallback,
            "send_slack": _write_fallback,
        }

    async def call(
        self,
        tool_name: str,
        params: dict[str, Any],
        ctx: SessionContext,
        *,
        use_cache: bool = True,
        cache_ttl: float | None = None,
    ) -> ToolCallResult:
        """Run validate -> cache -> circuit -> execute -> fallback."""
        stats = self._stats.setdefault(tool_name, ToolStats())
        breaker = self._breakers.setdefault(tool_name, CircuitBreaker())
        ttl = cache_ttl if cache_ttl is not None else self.DEFAULT_CACHE_TTL

        # 1) Cache check: avoids redundant downstream calls for identical inputs.
        #    cache_ttl=0 or use_cache=False lets callers opt out for write/non-idempotent tools.
        if use_cache and ttl > 0:
            cache_key = self._cache.make_key(tool_name, params)
            cached = self._cache.get(cache_key)
            if cached is not None:
                stats.total += 1
                stats.success += 1
                return ToolCallResult(
                    success=True,
                    # Normalize plain values to a dict so callers always get a consistent shape.
                    data=cached if isinstance(cached, dict) else {"result": cached},
                    tool_name=tool_name,
                    cached=True,
                )

        # 2) Circuit check: if the breaker is open (too many recent failures) skip execution
        #    entirely and go straight to fallback to avoid hammering a broken upstream.
        if not breaker.allow():
            return await self._fallback(tool_name, params, stats, breaker, "circuit open")

        started = time.monotonic()
        stats.total += 1
        try:
            # 3) Schema validation against the tool_system registry before any I/O.
            #    Catches missing/wrong-type params early so errors are actionable.
            self._validate_params(tool_name, params)
            # execute_tool_call is the raw dispatcher; cache, circuit, and stats live here only.
            data = await asyncio.wait_for(
                execute_tool_call(tool_name, params, ctx),
                timeout=self.DEFAULT_TIMEOUT,
            )
            latency = (time.monotonic() - started) * 1000
            stats.success += 1
            # Reset streak so the breaker doesn't trip on stale consecutive-fail counts.
            stats.consecutive_fails = 0
            stats.total_latency_ms += latency
            # Notify the breaker of a clean result; may transition HALF_OPEN -> CLOSED.
            breaker.record_success()

            # Populate cache only on success so stale error payloads are never cached.
            if use_cache and ttl > 0:
                self._cache.set(self._cache.make_key(tool_name, params), data, ttl)

            trace_event(
                "mcp.tool.success",
                {"tool": tool_name, "latency_ms": round(latency, 1), "cached": False},
            )
            return ToolCallResult(
                success=True,
                data=data,
                tool_name=tool_name,
                latency_ms=latency,
            )
        except asyncio.TimeoutError:
            stats.failed += 1
            stats.consecutive_fails += 1
            breaker.record_failure()
            return await self._fallback(tool_name, params, stats, breaker, "timeout")
        except Exception as exc:
            stats.failed += 1
            stats.consecutive_fails += 1
            breaker.record_failure()
            return await self._fallback(tool_name, params, stats, breaker, str(exc))

    def get_stats(self) -> dict[str, dict[str, Any]]:
        """Return tool stats for the monitor."""
        return {
            name: {
                "total": stats.total,
                "success_rate": round(stats.success_rate, 3),
                "avg_latency_ms": round(stats.avg_latency_ms, 1),
                "consecutive_fails": stats.consecutive_fails,
                "circuit_state": self._breakers.get(name, CircuitBreaker()).state.value,
            }
            for name, stats in self._stats.items()
        }

    def _validate_params(self, tool_name: str, params: dict[str, Any]) -> None:
        # Lazy import avoids a circular dependency at module load time.
        registry = _load_tool_registry()
        entry = registry.TOOL_REGISTRY.get(tool_name)
        if entry is None:
            raise ValueError(f"Unknown tool: {tool_name}")

        # The schema follows the OpenAI function-calling JSON Schema shape.
        schema = entry.definition.get("function", {}).get("parameters", {})
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        # Presence check first so the error message names the missing field.
        for field in required:
            if field not in params:
                raise ValueError(f"Tool {tool_name} missing required parameter: {field}")
        # Type check only for params that appear in the schema; extra keys are ignored.
        for key, value in params.items():
            if key in properties:
                expected = properties[key].get("type")
                if expected and expected in _TYPE_MAP and not isinstance(value, _TYPE_MAP[expected]):
                    raise ValueError(
                        f"Tool {tool_name} parameter {key} expected {expected}, "
                        f"got {type(value).__name__}"
                    )

    async def _fallback(
        self,
        tool_name: str,
        params: dict[str, Any],
        stats: ToolStats,
        breaker: CircuitBreaker,
        error: str,
    ) -> ToolCallResult:
        handler = self._fallbacks.get(tool_name)
        # Tools without a registered fallback fail hard so callers aren't misled
        # by a silently empty response for an unknown tool.
        if handler is None:
            trace_event("mcp.tool.failure", {"tool": tool_name, "error": error})
            return ToolCallResult(success=False, data={}, tool_name=tool_name, error=error)

        # success=True lets the ReAct loop continue reasoning with degraded data
        # rather than aborting the agent turn entirely.
        data = handler(params, error)
        # Support async fallback handlers in case a future handler needs I/O.
        if asyncio.iscoroutine(data):
            data = await data
        trace_event(
            "mcp.tool.fallback",
            {"tool": tool_name, "error": error, "circuit": breaker.state.value},
        )
        return ToolCallResult(
            success=True,
            data=data if isinstance(data, dict) else {"result": data},
            tool_name=tool_name,
            error=error,
            fallback_used=True,
        )


_LAYER: MCPToolLayer | None = None


def get_mcp_tool_layer() -> MCPToolLayer:
    """Return the process-wide MCP tool layer."""
    global _LAYER
    if _LAYER is None:
        _LAYER = MCPToolLayer()
    return _LAYER


def _health_fallback(params: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "found": False,
        "health_score": None,
        "fallback": True,
        "reason": error,
        "tenant_id": params.get("tenant_id"),
        "customer_id": params.get("customer_id"),
    }


def _playbook_fallback(params: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "matches": [],
        "fallback": True,
        "reason": error,
        "query": params.get("query", ""),
    }


def _write_fallback(params: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "accepted": False,
        "fallback": True,
        "reason": error,
        "tool_params": {key: value for key, value in params.items() if key != "api_key"},
    }


def _load_tool_registry() -> Any:
    from packages.tool_system.src import registry

    return registry


__all__ = ["MCPToolLayer", "ToolCallResult", "ToolStats", "get_mcp_tool_layer"]
