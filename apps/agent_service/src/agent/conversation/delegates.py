"""Specialist subagents exposed as callable tools for the GeneralAgent.

In the Orchestrator-Workers design the GeneralAgent is the single conversation
orchestrator. Domain specialists (Billing / Technical / Escalation) are no
longer selected by a planner; instead the GeneralAgent *calls them as tools*
when a turn needs domain work:

    delegate_billing(task)     -> structured result summary
    delegate_technical(task)   -> structured result summary
    delegate_escalation(task)  -> structured result summary

Each delegate is a thin wrapper around the *existing* conversation ReAct
subagent for that role: same allowed tools, same skill/persona, same
circuit-protected tool layer. It runs a bounded, single-turn worker
(``max_react_steps`` = ``_DELEGATE_MAX_REACT_STEPS``) and returns a structured
summary. It does not stream and performs no external writes.

The delegates register on the INTERNAL tool boundary, so calls made by the
GeneralAgent go through the same resilience layer as any other internal tool
(circuit breaker, TTL cache, timeout, JSONSchema validation, ToolStats,
fallback). Registration lives here (agent_service), not in
``packages/tool_system``, to avoid a layering cycle: the executor imports the
subagent runtime, which sits above the tool package.
"""

from __future__ import annotations

from typing import Any

from packages.agent.src.config import AgentConfig
from packages.agent.src.models import orchestrator_model, planner_model
from packages.agent.src.subagent_types import AgentRole, SubagentTask
from packages.agent.src.types import SessionContext

from apps.agent_service.src.agent.runtime.context import build_context_packet
from apps.agent_service.src.agent.subagents import build_subagent

#: Bounded step budget for a delegated specialist worker (spec: suggest 4).
_DELEGATE_MAX_REACT_STEPS = 4
_DELEGATE_MAX_TOKENS = 900

#: Role -> (tool name, allowed tools, one-line objective prefix). Allowed tools
#: mirror each conversation specialist's existing DEFAULT_ALLOWED_TOOLS so the
#: delegate keeps the exact same tool surface it has today.
_DELEGATE_TOOLS = ("delegate_billing", "delegate_technical", "delegate_escalation")


def _task_param_schema(role_label: str) -> dict[str, Any]:
    """JSON Schema for a delegate tool: a single ``task`` string."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["task"],
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    f"A self-contained {role_label} task for the specialist to "
                    "handle, phrased with all the customer context it needs (the "
                    "specialist does not see the raw chat history)."
                ),
            }
        },
    }


def _delegate_definition(name: str, role_label: str, description: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": _task_param_schema(role_label),
        },
    }


DELEGATE_BILLING_DEFINITION = _delegate_definition(
    "delegate_billing",
    "billing",
    "Delegate a billing/refund/invoice/subscription question to the Billing "
    "specialist. Use for charges, refunds, invoices, payments, or subscription "
    "changes. Returns the specialist's grounded result for you to relay.",
)
DELEGATE_TECHNICAL_DEFINITION = _delegate_definition(
    "delegate_technical",
    "technical",
    "Delegate a technical fault to the Technical specialist. Use for errors, "
    "crashes, bugs, API/login/authentication failures, or troubleshooting. "
    "Returns the specialist's grounded result for you to relay.",
)
DELEGATE_ESCALATION_DEFINITION = _delegate_definition(
    "delegate_escalation",
    "escalation",
    "Delegate to the Escalation specialist. Use for explicit human/manager "
    "requests, legal or regulatory threats, or urgent/critical complaints. "
    "Returns the specialist's grounded result for you to relay.",
)

#: tool name -> (role, allowed tools, skill brief, objective template).
_DELEGATE_SPECS: dict[str, tuple[AgentRole, str]] = {}


def _build_delegate_specs() -> dict[str, tuple[AgentRole, list[str], str]]:
    """Resolve each delegate's role + allowed tools + skill brief lazily.

    Imported lazily (inside the function) for the same reason the subagent
    factory defers conversation-specialist imports: eager import here would form
    a package-init cycle through ``subagents.base``.
    """
    from apps.agent_service.src.agent.conversation.subagents.billing import (
        DEFAULT_ALLOWED_TOOLS as BILLING_TOOLS,
        ROLE_BRIEF as BILLING_BRIEF,
    )
    from apps.agent_service.src.agent.conversation.subagents.escalation import (
        DEFAULT_ALLOWED_TOOLS as ESCALATION_TOOLS,
        ROLE_BRIEF as ESCALATION_BRIEF,
    )
    from apps.agent_service.src.agent.conversation.subagents.technical import (
        DEFAULT_ALLOWED_TOOLS as TECHNICAL_TOOLS,
        ROLE_BRIEF as TECHNICAL_BRIEF,
    )

    return {
        "delegate_billing": (AgentRole.BILLING, list(BILLING_TOOLS), BILLING_BRIEF),
        "delegate_technical": (AgentRole.TECHNICAL, list(TECHNICAL_TOOLS), TECHNICAL_BRIEF),
        "delegate_escalation": (AgentRole.ESCALATION, list(ESCALATION_TOOLS), ESCALATION_BRIEF),
    }


_SPECS_CACHE: dict[str, tuple[AgentRole, list[str], str]] | None = None


def _specs() -> dict[str, tuple[AgentRole, list[str], str]]:
    global _SPECS_CACHE
    if _SPECS_CACHE is None:
        _SPECS_CACHE = _build_delegate_specs()
    return _SPECS_CACHE


def _delegate_config(tenant_id: str) -> AgentConfig:
    """Minimal per-call config for a delegated specialist run."""
    return AgentConfig(
        tenant_id=tenant_id,
        name="conversation-delegate",
        instructions="Delegated conversation specialist worker",
        model=orchestrator_model(),
        planner_model=planner_model(),
        tools=[],
    )


async def _run_delegate(tool_name: str, params: dict[str, Any], ctx: SessionContext) -> dict[str, Any]:
    """Run the matching conversation specialist as a bounded worker.

    This is the shared executor for all three delegate tools. It builds a
    tenant-safe context packet (``customer_id`` comes from ``ctx.user_id`` — the
    session identity is authoritative, never a model-supplied value), runs the
    existing conversation ReAct subagent with its existing allowed tools/skill,
    and returns a structured summary for the GeneralAgent to relay.

    Any exception propagates so the tool layer's circuit breaker / fallback can
    engage (see :func:`_delegate_fallback`).
    """
    role, allowed_tools, brief = _specs()[tool_name]
    task_text = str(params.get("task", "")).strip()

    task = SubagentTask(
        id=tool_name,
        role=role,
        objective=task_text or f"Handle the {role.value} request",
        skill=brief,
        input={"message": task_text},
        allowed_tools=allowed_tools,
        max_react_steps=_DELEGATE_MAX_REACT_STEPS,
        max_tokens=_DELEGATE_MAX_TOKENS,
    )
    packet = build_context_packet(
        task=task,
        ctx=ctx,
        customer_id=ctx.user_id,
        tenant_constraints=[],
        memory_excerpt=None,
        dependency_results={},
    )
    config = _delegate_config(ctx.tenant_id)
    subagent = build_subagent(packet=packet, ctx=ctx, config=config, domain="conversation")
    result = await subagent.run()
    return {
        "role": role.value,
        "success": result.success,
        "summary": result.markdown,
        "data": result.data,
        "tool_calls": [record.model_dump(mode="json") for record in result.tool_calls],
        "tokens_used": result.tokens_used,
        "error": result.error,
    }


async def execute_delegate_billing(params: dict[str, Any], ctx: SessionContext) -> dict[str, Any]:
    return await _run_delegate("delegate_billing", params, ctx)


async def execute_delegate_technical(params: dict[str, Any], ctx: SessionContext) -> dict[str, Any]:
    return await _run_delegate("delegate_technical", params, ctx)


async def execute_delegate_escalation(params: dict[str, Any], ctx: SessionContext) -> dict[str, Any]:
    return await _run_delegate("delegate_escalation", params, ctx)


def delegate_fallback(params: dict[str, Any], error: str) -> dict[str, Any]:
    """Graceful degradation when a delegate's circuit is open or it times out.

    Returns a safe, non-committal summary so the GeneralAgent can still compose a
    grounded reply (ask for detail / promise follow-up) instead of crashing.
    """
    return {
        "role": "delegate",
        "success": False,
        "summary": (
            "The specialist could not be reached right now. Acknowledge the "
            "request, avoid guessing specifics, and offer to follow up."
        ),
        "data": {},
        "tool_calls": [],
        "tokens_used": 0,
        "fallback": True,
        "error": error,
    }


_DELEGATE_DEFINITIONS = {
    "delegate_billing": (DELEGATE_BILLING_DEFINITION, execute_delegate_billing),
    "delegate_technical": (DELEGATE_TECHNICAL_DEFINITION, execute_delegate_technical),
    "delegate_escalation": (DELEGATE_ESCALATION_DEFINITION, execute_delegate_escalation),
}


def register_conversation_delegate_tools() -> None:
    """Register the three delegate tools on the INTERNAL boundary (idempotent)."""
    from packages.tool_system.src.registry import ToolBoundary, register_tool
    from apps.agent_service.src.agent.runtime.mcp.tool_layer import get_mcp_tool_layer

    for name, (definition, executor) in _DELEGATE_DEFINITIONS.items():
        register_tool(
            name,
            definition,
            executor,
            boundary=ToolBoundary.INTERNAL,
            tags=["conversation", "delegate"],
            overwrite=True,
        )

    tool_layer = get_mcp_tool_layer()
    for name in _DELEGATE_DEFINITIONS:
        tool_layer.register_fallback(name, delegate_fallback)


__all__ = [
    "DELEGATE_BILLING_DEFINITION",
    "DELEGATE_TECHNICAL_DEFINITION",
    "DELEGATE_ESCALATION_DEFINITION",
    "execute_delegate_billing",
    "execute_delegate_technical",
    "execute_delegate_escalation",
    "delegate_fallback",
    "register_conversation_delegate_tools",
    "_DELEGATE_MAX_REACT_STEPS",
]
