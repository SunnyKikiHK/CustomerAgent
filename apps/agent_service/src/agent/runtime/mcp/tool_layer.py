"""Validate/cache/circuit/fallback wrapper for internal and MCP action tool calls."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from packages.llm_gateway.src.cache import get_tool_cache
from packages.llm_gateway.src.circuit import CircuitBreaker
from packages.observability.src.tracer import trace_event

from apps.agent_service.src.agent.runtime.tool_dispatch import execute_internal_analysis
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
    """Normalized tool call result used by internal analysis and MCP actions."""

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
    """Resilience layer for internal analysis and MCP action calls."""

    DEFAULT_CACHE_TTL = 300.0
    DEFAULT_TIMEOUT = 30.0

    def __init__(self) -> None:
        self._cache = get_tool_cache()
        self._breakers: dict[str, CircuitBreaker] = {}
        self._stats: dict[str, ToolStats] = {}
        self._fallbacks: dict[str, Any] = {
            "query_health": _health_fallback,
            "query_playbooks": _playbook_fallback,
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
        """Run validate -> cache -> circuit -> internal execute -> fallback."""
        stats = self._stats.setdefault(tool_name, ToolStats())
        breaker = self._breakers.setdefault(tool_name, CircuitBreaker())
        ttl = cache_ttl if cache_ttl is not None else self.DEFAULT_CACHE_TTL

        if use_cache and ttl > 0:
            cache_key = self._cache.make_key(tool_name, params)
            cached = self._cache.get(cache_key)
            if cached is not None:
                stats.total += 1
                stats.success += 1
                return ToolCallResult(
                    success=True,
                    data=cached if isinstance(cached, dict) else {"result": cached},
                    tool_name=tool_name,
                    cached=True,
                )

        if not breaker.allow():
            return await self._fallback(tool_name, params, stats, breaker, "circuit open")

        started = time.monotonic()
        stats.total += 1
        try:
            self._validate_params(tool_name, params)
            data = await asyncio.wait_for(
                execute_internal_analysis(tool_name, params, ctx),
                timeout=self.DEFAULT_TIMEOUT,
            )
            return self._success(tool_name, params, data, stats, breaker, started, use_cache, ttl)
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

    async def call_mcp_action(
        self,
        tool_name: str,
        params: dict[str, Any],
        *,
        executor: Any,
    ) -> ToolCallResult:
        """Run circuit/stats/timeout around an MCP action call, with cache disabled."""
        stats = self._stats.setdefault(tool_name, ToolStats())
        breaker = self._breakers.setdefault(tool_name, CircuitBreaker())
        if not breaker.allow():
            trace_event("mcp.tool.failure", {"tool": tool_name, "error": "circuit open"})
            return ToolCallResult(success=False, data={}, tool_name=tool_name, error="circuit open")

        started = time.monotonic()
        stats.total += 1
        try:
            # Skip local validation: the MCP gateway is the authoritative verifier.
            data = await asyncio.wait_for(executor(), timeout=self.DEFAULT_TIMEOUT)
            return self._success(tool_name, params, data, stats, breaker, started, use_cache=False, ttl=0)
        except asyncio.TimeoutError:
            stats.failed += 1
            stats.consecutive_fails += 1
            breaker.record_failure()
            trace_event("mcp.tool.failure", {"tool": tool_name, "error": "timeout"})
            return ToolCallResult(success=False, data={}, tool_name=tool_name, error="timeout")
        except Exception as exc:
            stats.failed += 1
            stats.consecutive_fails += 1
            breaker.record_failure()
            trace_event("mcp.tool.failure", {"tool": tool_name, "error": str(exc)})
            return ToolCallResult(success=False, data={}, tool_name=tool_name, error=str(exc))

    def _success(
        self,
        tool_name: str,
        params: dict[str, Any],
        data: dict[str, Any],
        stats: ToolStats,
        breaker: CircuitBreaker,
        started: float,
        use_cache: bool,
        ttl: float,
    ) -> ToolCallResult:
        latency = (time.monotonic() - started) * 1000
        stats.success += 1
        stats.consecutive_fails = 0
        stats.total_latency_ms += latency
        breaker.record_success()
        if use_cache and ttl > 0:
            self._cache.set(self._cache.make_key(tool_name, params), data, ttl)
        trace_event(
            "mcp.tool.success",
            {"tool": tool_name, "latency_ms": round(latency, 1), "cached": use_cache and ttl > 0},
        )
        return ToolCallResult(success=True, data=data, tool_name=tool_name, latency_ms=latency)

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
        registry = _load_tool_registry()
        entry = registry.require_tool_boundary(tool_name, registry.ToolBoundary.INTERNAL)
        schema = entry.definition.get("function", {}).get("parameters", {})
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        for field in required:
            if field not in params:
                raise ValueError(f"Tool {tool_name} missing required parameter: {field}")
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
        _stats: ToolStats,
        breaker: CircuitBreaker,
        error: str,
    ) -> ToolCallResult:
        handler = self._fallbacks.get(tool_name)
        if handler is None:
            trace_event("mcp.tool.failure", {"tool": tool_name, "error": error})
            return ToolCallResult(success=False, data={}, tool_name=tool_name, error=error)
        data = handler(params, error)
        if asyncio.iscoroutine(data):
            data = await data
        trace_event("mcp.tool.fallback", {"tool": tool_name, "error": error, "circuit": breaker.state.value})
        return ToolCallResult(
            success=True,
            data=data if isinstance(data, dict) else {"result": data},
            tool_name=tool_name,
            error=error,
            fallback_used=True,
        )


_LAYER: MCPToolLayer | None = None


def get_mcp_tool_layer() -> MCPToolLayer:
    """Return the process-wide tool resilience layer."""
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


def _load_tool_registry() -> Any:
    from packages.tool_system.src import registry

    return registry


__all__ = ["MCPToolLayer", "ToolCallResult", "ToolStats", "get_mcp_tool_layer"]
