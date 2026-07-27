"""GeneralAgent orchestrator loop (Orchestrator-Workers, streaming final answer).

The GeneralAgent is the single conversation orchestrator. It runs a bounded
ReAct loop: reason, call internal tools (``query_health`` / ``query_playbooks``)
and specialist delegate tools (``delegate_billing`` / ``delegate_technical`` /
``delegate_escalation``) as needed, then produce the final customer-facing reply
itself. It is its own critic — the compliance/safety rules live in its SKILL.md,
so there is no separate ComplianceCriticAgent LLM call on the conversation path.

Loop termination (see :meth:`ConversationLoop.run_stream`):

  A. Natural exit — the model returns a response with no tool calls. Its answer
     is produced by a dedicated streaming synthesis call.
  B. Forced Synthesis at ``MAX_REACT_LOOPS`` — tools are stripped and a system
     message commands an immediate final answer from information already
     gathered, then that answer is streamed. No exception, no hardcoded string.

Both exits converge on one real streaming call (``LLMClient.stream``), so the
customer always gets a genuine token stream and every turn ends with a real,
model-authored, bounded reply.

Tenant isolation, the resilient tool layer (circuit breaker / TTL cache /
timeout / JSONSchema validation / fallback / ToolStats), and the RAG pipeline
are all preserved: every tool call is dispatched through the same
``dispatch_tool_call`` boundary the previous subagents used, with the
session-authoritative ``tenant_id``/``customer_id`` injected here.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from packages.agent.src.config import AgentConfig
from packages.agent.src.models import orchestrator_model
from packages.agent.src.types import LLMUsage, SessionContext
from packages.observability.src.tracer import observe

from apps.agent_service.src.agent.conversation.delegates import (
    register_conversation_delegate_tools,
)
from apps.agent_service.src.agent.llm_client import LLMClient, LLMMessage, LLMResponse
from apps.agent_service.src.agent.runtime.prompts import collect_tool_docs
from apps.agent_service.src.agent.runtime.react_loop import ReActLoop
from apps.agent_service.src.agent.runtime.skills import get_skill_manager
from apps.agent_service.src.agent.runtime.tool_caller import dispatch_tool_call

#: Hard step budget for the GeneralAgent orchestrator loop (spec: suggest 6).
MAX_REACT_LOOPS = 6

#: Token budget for the streamed final answer.
_FINAL_ANSWER_MAX_TOKENS = 900

#: Token budget for one tool/reasoning step (bounded to keep tail latency low).
_STEP_MAX_TOKENS = 900

#: Token budget for one tool/reasoning step (bounded to keep step latency low).
_STEP_MAX_TOKENS = 900

#: Default tools available to the GeneralAgent orchestrator: read-only lookups
#: plus the three specialist delegate tools. process_refund et al. reach the
#: customer only via delegate_billing (the Billing specialist owns that tool).
DEFAULT_ORCHESTRATOR_TOOLS = [
    "query_health",
    "query_playbooks",
    "delegate_billing",
    "delegate_technical",
    "delegate_escalation",
]

#: General-support skill role (persona + embedded compliance rules).
_SKILL_ROLE = "general"

#: Tools whose params need the session customer id injected (tenant id is always
#: injected by the internal-dispatch boundary; these also key the cache/query on
#: the customer, so inject it here rather than trusting a model-supplied value).
_CUSTOMER_SCOPED_TOOLS = {"query_health"}

#: Delegate tools: inject customer id into params so the shared TTL cache key is
#: customer-scoped (the cache key is (tool_name, params) and does not otherwise
#: include the session customer).
_DELEGATE_TOOLS = {"delegate_billing", "delegate_technical", "delegate_escalation"}


@dataclass
class TurnMetrics:
    """Per-turn instrumentation for latency/eval reporting."""

    react_steps: int = 0          # tool-gathering loop iterations actually run
    llm_calls: int = 0            # complete() calls + the final stream() call
    tool_calls: int = 0           # individual tool invocations dispatched
    forced_synthesis: bool = False
    collapsed: bool = False       # final answer reused from the last step (no 2nd call)
    usage: LLMUsage = field(default_factory=LLMUsage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "react_steps": self.react_steps,
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "forced_synthesis": self.forced_synthesis,
            "collapsed": self.collapsed,
            "prompt_tokens": self.usage.prompt_tokens,
            "completion_tokens": self.usage.completion_tokens,
            "total_tokens": self.usage.total,
        }


#: Guidance appended to the injected skill so the orchestrator uses the tool
#: protocol correctly. The persona + compliance/safety rules come from the
#: general_support SKILL.md; this only documents the loop mechanics.
_ORCHESTRATOR_PROTOCOL = (
    "You are the customer-facing conversation orchestrator. Reason about what the "
    "turn needs and gather information with tools before answering.\n\n"
    "Delegation policy:\n"
    "- Billing/refund/invoice/subscription/payment → call delegate_billing.\n"
    "- Technical faults, errors, crashes, login/auth failures → call delegate_technical.\n"
    "- Explicit human/manager requests, legal/regulatory threats, urgent complaints "
    "→ call delegate_escalation.\n"
    "- Greetings, general questions, account info you can answer directly → do NOT "
    "delegate; answer yourself.\n\n"
    "When you delegate, pass a self-contained `task` string with all the context "
    "the specialist needs. Relay the specialist's grounded result; do not invent "
    "facts beyond it.\n\n"
    "Tool protocol: respond with exactly one JSON object: "
    '{"tool_calls": [{"name": <tool>, "arguments": {..}}], "markdown": "", "data": {}}. '
    "Request tools only while you still need evidence. When you have everything "
    "needed to answer, return tool_calls as []. Do not exceed the step budget; a "
    "concise grounded reply is better than more tool calls.\n\n"
    f"You have at most {MAX_REACT_LOOPS} tool-gathering steps before you must answer."
)

#: System message injected for the forced-synthesis final call (tools stripped).
_FORCED_SYNTHESIS_DIRECTIVE = (
    "You have reached the tool-use limit for this turn. Do NOT request any more "
    "tools. Using only the information already gathered in this conversation, write "
    "your final customer-facing reply now. If some detail is still missing, "
    "acknowledge it briefly and give the best grounded next step — never invent "
    "facts, account data, or outcomes."
)

#: System message for the natural-exit final call: turn gathered evidence into the
#: customer answer as plain streamed text (no JSON, no tools).
_NATURAL_SYNTHESIS_DIRECTIVE = (
    "Write your final customer-facing reply now as plain text (no JSON, no tool "
    "calls). Base it only on the information gathered in this conversation; keep it "
    "concise, grounded, professional, and compliant with your safety rules."
)


class ConversationLoop:
    """Bounded GeneralAgent ReAct loop with a streamed final answer."""

    def __init__(
        self,
        *,
        message: str,
        ctx: SessionContext,
        config: AgentConfig,
        memory_excerpt: str | None = None,
        history: list[dict[str, str]] | None = None,
        tenant_constraints: list[str] | None = None,
        llm_client: LLMClient | None = None,
        allowed_tools: list[str] | None = None,
        max_react_loops: int = MAX_REACT_LOOPS,
    ) -> None:
        register_conversation_delegate_tools()
        self.message = message
        self.ctx = ctx
        self.config = config
        self.memory_excerpt = memory_excerpt
        self.history = history or []
        self.tenant_constraints = tenant_constraints or []
        self.llm_client = llm_client or LLMClient(default_model=config.model)
        self.allowed_tools = list(allowed_tools or DEFAULT_ORCHESTRATOR_TOOLS)
        self.max_react_loops = max_react_loops
        self.messages: list[LLMMessage] = []
        self.metrics = TurnMetrics()
        self.final_text: str = ""

    async def run_stream(self) -> AsyncIterator[str]:
        """Run the loop and yield the final answer as streamed text deltas.

        Drives the tool/reasoning loop with non-streamed ``complete`` calls
        (intermediate steps are never streamed to the client). The final answer
        is emitted one of three ways:

        - **Collapsed natural exit** — the reasoning step that returns no tool
          calls *already contains* the answer in its ``markdown``. That text is
          the final answer, so it is chunked into token deltas directly and the
          separate synthesis call is skipped (saves one LLM round-trip/turn).
        - **Empty natural exit** — the model signalled done but wrote no
          markdown; fall back to a dedicated streaming synthesis call.
        - **Forced Synthesis** — the step budget was exhausted; strip tools and
          make one streaming synthesis call.
        """
        self.messages = [
            LLMMessage(role="system", content=self._system_prompt()),
            LLMMessage(role="user", content=self._user_context()),
        ]

        forced = True  # if the loop never breaks naturally, we force-synthesize
        collapsed_answer: str | None = None
        for step in range(self.max_react_loops):
            self.metrics.react_steps = step + 1
            response = await self._complete()
            parsed = ReActLoop._parse_model_response(response.text)
            tool_calls = parsed["tool_calls"]
            if not tool_calls:
                # Natural exit: the model signalled it is done (no tool calls).
                forced = False
                answer = (parsed.get("markdown") or "").strip()
                if answer:
                    # Double-call collapse: the model already wrote the final
                    # answer in this step, so reuse it instead of a second call.
                    collapsed_answer = answer
                break
            self.messages.append(LLMMessage(role="assistant", content=response.text))
            await self._execute_tool_calls(tool_calls)

        self.metrics.forced_synthesis = forced

        if collapsed_answer is not None:
            self.metrics.collapsed = True
            self.final_text = collapsed_answer
            for chunk in _chunk_text(collapsed_answer):
                yield chunk
            return

        async for delta in self._stream_final_answer(forced=forced):
            yield delta

    async def _complete(self) -> LLMResponse:
        """One non-streamed reasoning/tool step.

        Wrapped in a ``planner.*`` span so the evaluation metrics classifier
        attributes orchestrator reasoning time to the reasoning phase.
        """
        with observe(
            "planner.orchestrator_step",
            attributes={"tenant_id": self.ctx.tenant_id, "trace_id": self.ctx.trace_id},
        ):
            response = await self.llm_client.complete(
                self.messages,
                model=orchestrator_model(),
                max_tokens=_STEP_MAX_TOKENS,
                temperature=0.2,
                trace_id=self.ctx.trace_id,
                name="conversation.orchestrator.step",
                metadata={"phase": "reasoning"},
            )
        self.metrics.llm_calls += 1
        self._add_usage(response.usage)
        return response

    async def _execute_tool_calls(self, tool_calls: list[Any]) -> None:
        """Dispatch the step's tool calls concurrently and append results in order.

        Multiple independent tool calls issued in one reasoning step (e.g.
        ``query_health`` + ``delegate_billing``) run concurrently via
        ``asyncio.gather`` — the step's wall-clock is the slowest single call,
        not the sum. Result messages are still appended in the model's original
        call order so the transcript stays deterministic.

        Preserves the resilient tool layer: each dispatch goes through
        ``dispatch_tool_call`` (circuit breaker / cache / validation / fallback /
        ToolStats). Tenant + customer identity are injected per-call from the
        session context, never taken from model-supplied arguments.
        """
        valid = [tc for tc in tool_calls if isinstance(tc, dict)]
        if not valid:
            return
        results = await asyncio.gather(
            *(self._run_one_tool_call(tc) for tc in valid)
        )
        for tool_name, result in results:
            self._append_tool_result(tool_name, result)

    async def _run_one_tool_call(self, tool_call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Run a single tool call and return ``(tool_name, result)`` (never raises)."""
        tool_name = str(tool_call.get("name", ""))
        raw_args = tool_call.get("arguments", {})
        args = dict(raw_args) if isinstance(raw_args, dict) else {}

        if tool_name not in self.allowed_tools:
            return tool_name, {"error": f"Tool {tool_name} is not allowed"}

        params = self._inject_identity(tool_name, args)
        self.metrics.tool_calls += 1
        with observe(
            f"tool.{tool_name}",
            attributes={
                "tool": tool_name,
                "tenant_id": self.ctx.tenant_id,
                "trace_id": self.ctx.trace_id,
            },
        ) as span:
            try:
                result = await dispatch_tool_call(tool_name, params, self.ctx)
                success = True
            except Exception as exc:  # noqa: BLE001 - surface to the model, keep looping
                result = {"error": str(exc)}
                success = False
            span.set("success", success)
        return tool_name, result

    def _inject_identity(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Inject session-authoritative tenant/customer ids into tool params."""
        params = dict(args)
        params["tenant_id"] = self.ctx.tenant_id
        if tool_name in _CUSTOMER_SCOPED_TOOLS or tool_name in _DELEGATE_TOOLS:
            params["customer_id"] = self.ctx.user_id
        return params

    def _append_tool_result(self, tool_name: str, result: dict[str, Any]) -> None:
        self.messages.append(
            LLMMessage(
                role="tool",
                content=json.dumps({"tool": tool_name, "result": result}, default=str),
            )
        )

    async def _stream_final_answer(self, *, forced: bool) -> AsyncIterator[str]:
        """Stream the final customer answer (natural exit or forced synthesis)."""
        directive = _FORCED_SYNTHESIS_DIRECTIVE if forced else _NATURAL_SYNTHESIS_DIRECTIVE
        messages = [*self.messages, LLMMessage(role="system", content=directive)]

        captured: dict[str, LLMResponse] = {}

        def _capture(response: LLMResponse) -> None:
            captured["final"] = response

        self.metrics.llm_calls += 1
        with observe(
            "planner.orchestrator_synthesis",
            attributes={"tenant_id": self.ctx.tenant_id, "trace_id": self.ctx.trace_id, "forced": forced},
        ):
            async for delta in self.llm_client.stream(
                messages,
                model=orchestrator_model(),
                max_tokens=_FINAL_ANSWER_MAX_TOKENS,
                temperature=0.3,
                trace_id=self.ctx.trace_id,
                name="conversation.orchestrator.final",
                metadata={"phase": "synthesis", "forced": forced},
                on_complete=_capture,
            ):
                yield delta

        final = captured.get("final")
        if final is not None:
            self.final_text = final.text
            self._add_usage(final.usage)
        else:  # stream produced no completion callback (should not happen)
            self.final_text = self.final_text or ""

    def _add_usage(self, usage: LLMUsage) -> None:
        self.metrics.usage.prompt_tokens += usage.prompt_tokens
        self.metrics.usage.completion_tokens += usage.completion_tokens

    def _system_prompt(self) -> str:
        """Persona + embedded compliance rules (SKILL.md) + loop protocol."""
        skill_block = get_skill_manager(self.ctx.tenant_id).prompt_for(
            self.message, agent_role=_SKILL_ROLE
        )
        persona = skill_block or (
            "You are the General customer-support assistant. Answer grounded in "
            "context; never expose another tenant's data, PII, or secrets; never "
            "promise refunds or outcomes the context does not support."
        )
        tool_docs = collect_tool_docs(self.allowed_tools, _load_tool_registry())
        constraints = json.dumps(self.tenant_constraints) if self.tenant_constraints else "[]"
        return (
            f"{persona}\n\n"
            f"{_ORCHESTRATOR_PROTOCOL}\n\n"
            f"Tenant constraints: {constraints}\n"
            f"Available tools:\n{chr(10).join(tool_docs)}"
        )

    def _user_context(self) -> str:
        history_text = ""
        if self.history:
            history_text = "\n".join(
                f"  {item.get('role', 'user')}: {item.get('content', '')}"
                for item in self.history[-3:]
            )
        return json.dumps(
            {
                "message": self.message,
                "memory_excerpt": self.memory_excerpt or "",
                "recent_history": history_text,
            },
            default=str,
        )


def _load_tool_registry() -> Any:
    from packages.tool_system.src import registry

    return registry


def _chunk_text(text: str, size: int = 24) -> list[str]:
    """Split a complete answer into token-like chunks for the SSE stream.

    Used by the double-call collapse path: the reasoning step already produced
    the final answer, so there is no live provider stream to forward. Chunking
    keeps the client's token-stream contract (progressive `token` events) while
    saving the extra synthesis round-trip.
    """
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


__all__ = ["ConversationLoop", "TurnMetrics", "MAX_REACT_LOOPS", "DEFAULT_ORCHESTRATOR_TOOLS"]
