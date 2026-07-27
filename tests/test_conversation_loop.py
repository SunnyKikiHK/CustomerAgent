"""Focused tests for the GeneralAgent orchestrator loop (Orchestrator-Workers).

Covers the behaviours the redesign spec calls out:
- delegation to a specialist tool vs. handling a turn directly,
- natural ReAct exit (no tool calls) and Forced Synthesis at the step cap,
- streamed text equals the final model response text,
- delegate tools degrade through the circuit-breaker fallback,
- the bounded step budgets for the orchestrator (6) and delegates (4).

All tests are offline: a scripted fake LLM drives the loop, and tool dispatch is
monkeypatched, so nothing hits the network or the DB.
"""

from __future__ import annotations

import json

import pytest

from apps.agent_service.src.agent.llm_client import LLMResponse
from packages.agent.src.config import AgentConfig
from packages.agent.src.types import LLMUsage, SessionContext

from apps.agent_service.src.agent.conversation import conversation_loop as cl
from apps.agent_service.src.agent.conversation.conversation_loop import (
    MAX_REACT_LOOPS,
    ConversationLoop,
)
from apps.agent_service.src.agent.conversation.delegates import _DELEGATE_MAX_REACT_STEPS

_TOOLCALL = '{{"tool_calls": [{{"name": "{name}", "arguments": {args}}}], "markdown": "", "data": {{}}}}'
_DONE = '{"tool_calls": [], "markdown": "", "data": {}}'


def _done_with(answer: str) -> str:
    """A no-tool-call step whose markdown already holds the final answer."""
    return json.dumps({"tool_calls": [], "markdown": answer, "data": {}})


class FakeLLM:
    """Scripted LLM: a queue of complete() texts + a fixed stream() answer."""

    def __init__(self, complete_texts, stream_text="Here is your answer.", default=_DONE):
        self._queue = list(complete_texts)
        self._stream_text = stream_text
        self._default = default
        self.complete_calls = 0
        self.stream_calls = 0
        self.stream_messages = None

    async def complete(self, messages, **kwargs):
        self.complete_calls += 1
        text = self._queue.pop(0) if self._queue else self._default
        return LLMResponse(
            text=text, model="fake", usage=LLMUsage(prompt_tokens=1, completion_tokens=1)
        )

    async def stream(self, messages, *, on_complete=None, **kwargs):
        self.stream_calls += 1
        self.stream_messages = messages
        collected = []
        for i in range(0, len(self._stream_text), 5):
            piece = self._stream_text[i : i + 5]
            collected.append(piece)
            yield piece
        resp = LLMResponse(
            text="".join(collected),
            model="fake",
            usage=LLMUsage(prompt_tokens=2, completion_tokens=3),
        )
        if on_complete is not None:
            on_complete(resp)


def _ctx() -> SessionContext:
    return SessionContext(
        tenant_id="demo-tenant", user_id="cust-1", session_id="s1", trace_id="s1"
    )


def _config() -> AgentConfig:
    return AgentConfig(
        tenant_id="demo-tenant",
        name="conversation-agent",
        instructions="test",
        model="fake-model",
        planner_model="fake-planner",
        tools=[],
    )


def _make_loop(llm, message, *, max_react_loops=MAX_REACT_LOOPS) -> ConversationLoop:
    return ConversationLoop(
        message=message,
        ctx=_ctx(),
        config=_config(),
        llm_client=llm,
        max_react_loops=max_react_loops,
    )


async def _drain(loop) -> list[str]:
    return [delta async for delta in loop.run_stream()]


@pytest.mark.asyncio
async def test_greeting_handled_directly_without_delegation(monkeypatch):
    dispatched = []

    async def _spy(name, params, ctx):
        dispatched.append(name)
        return {}

    monkeypatch.setattr(cl, "dispatch_tool_call", _spy)

    llm = FakeLLM([_DONE], stream_text="Hi! How can I help?")
    loop = _make_loop(llm, "hello there")
    await _drain(loop)

    assert dispatched == []  # no tool/delegate call for a greeting
    assert loop.metrics.forced_synthesis is False
    assert loop.metrics.react_steps == 1
    assert loop.final_text == "Hi! How can I help?"


@pytest.mark.asyncio
async def test_double_call_collapse_reuses_step_answer(monkeypatch):
    """When the no-tool step already wrote the answer, skip the synthesis call."""
    async def _spy(name, params, ctx):
        return {}

    monkeypatch.setattr(cl, "dispatch_tool_call", _spy)

    answer = "Our hours are 9-5 ET, Monday to Friday."
    llm = FakeLLM([_done_with(answer)], stream_text="SHOULD-NOT-BE-USED")
    loop = _make_loop(llm, "what are your hours?")
    deltas = await _drain(loop)

    assert loop.metrics.collapsed is True
    assert llm.stream_calls == 0            # no second LLM call
    assert llm.complete_calls == 1          # only the single reasoning step
    assert loop.metrics.llm_calls == 1
    assert loop.final_text == answer
    assert "".join(deltas) == answer        # streamed (chunked) text == final text


@pytest.mark.asyncio
async def test_empty_natural_exit_falls_back_to_streaming(monkeypatch):
    """A done step with empty markdown still makes the dedicated stream call."""
    async def _spy(name, params, ctx):
        return {}

    monkeypatch.setattr(cl, "dispatch_tool_call", _spy)
    llm = FakeLLM([_DONE], stream_text="Streamed answer.")
    loop = _make_loop(llm, "hello")
    await _drain(loop)

    assert loop.metrics.collapsed is False
    assert llm.stream_calls == 1
    assert loop.final_text == "Streamed answer."


@pytest.mark.asyncio
async def test_billing_turn_delegates_to_delegate_billing(monkeypatch):
    dispatched = []

    async def _spy(name, params, ctx):
        dispatched.append((name, params))
        return {"role": "billing", "success": True, "summary": "refund initiated"}

    monkeypatch.setattr(cl, "dispatch_tool_call", _spy)

    llm = FakeLLM(
        [
            _TOOLCALL.format(name="delegate_billing", args='{"task": "refund invoice 42"}'),
            _DONE,
        ],
        stream_text="Your refund is on the way.",
    )
    loop = _make_loop(llm, "I need a refund for invoice 42")
    await _drain(loop)

    names = [name for name, _ in dispatched]
    assert "delegate_billing" in names
    # session identity is injected, never taken from model args
    _, params = dispatched[0]
    assert params["tenant_id"] == "demo-tenant"
    assert params["customer_id"] == "cust-1"


@pytest.mark.asyncio
async def test_multiple_tool_calls_run_concurrently_and_in_order(monkeypatch):
    """Two tool calls in one step run via gather; results append in call order."""
    import asyncio as _asyncio

    order_started = []

    async def _spy(name, params, ctx):
        order_started.append(name)
        # delegate is "slow"; if run sequentially it would finish last, but with
        # gather both are in flight — we assert append ORDER (not finish order).
        await _asyncio.sleep(0.05 if name == "query_health" else 0.0)
        return {"tool": name, "ok": True}

    monkeypatch.setattr(cl, "dispatch_tool_call", _spy)

    two_calls = (
        '{"tool_calls": [{"name": "query_health", "arguments": {}}, '
        '{"name": "delegate_billing", "arguments": {"task": "refund"}}], '
        '"markdown": "", "data": {}}'
    )
    llm = FakeLLM([two_calls, _DONE], stream_text="Handled both.")
    loop = _make_loop(llm, "health + refund")
    await _drain(loop)

    # both dispatched, and both concurrently in flight (started before either slept out)
    assert set(order_started) == {"query_health", "delegate_billing"}
    assert loop.metrics.tool_calls == 2
    # tool-result messages preserved in the model's original call order
    tool_msgs = [m.content for m in loop.messages if m.role == "tool"]
    assert '"tool": "query_health"' in tool_msgs[0]
    assert '"tool": "delegate_billing"' in tool_msgs[1]


@pytest.mark.asyncio
async def test_natural_exit_when_no_tool_calls(monkeypatch):
    monkeypatch.setattr(cl, "dispatch_tool_call", None)  # must not be called
    llm = FakeLLM([_DONE], stream_text="Done.")
    loop = _make_loop(llm, "what are your hours?")
    await _drain(loop)
    assert loop.metrics.forced_synthesis is False


@pytest.mark.asyncio
async def test_forced_synthesis_at_max_loops(monkeypatch):
    async def _spy(name, params, ctx):
        return {}

    monkeypatch.setattr(cl, "dispatch_tool_call", _spy)

    # Always returns a tool call → loop never exits naturally → Forced Synthesis.
    always_tool = _TOOLCALL.format(name="query_health", args="{}")
    llm = FakeLLM([], stream_text="Best-effort grounded answer.", default=always_tool)
    loop = _make_loop(llm, "keep looping", max_react_loops=3)

    deltas = await _drain(loop)  # must NOT raise

    assert loop.metrics.forced_synthesis is True
    assert loop.metrics.react_steps == 3  # capped
    assert loop.final_text == "Best-effort grounded answer."
    assert "".join(deltas) == loop.final_text
    # tools were stripped: the forced directive is present in the final call
    system_msgs = [m.content for m in llm.stream_messages if m.role == "system"]
    assert any("reached the tool-use limit" in c for c in system_msgs)


@pytest.mark.asyncio
async def test_streamed_text_equals_final_text_natural(monkeypatch):
    async def _spy(name, params, ctx):
        return {}

    monkeypatch.setattr(cl, "dispatch_tool_call", _spy)
    llm = FakeLLM([_DONE], stream_text="Streamed and final must match exactly.")
    loop = _make_loop(llm, "hello")
    deltas = await _drain(loop)
    assert "".join(deltas) == loop.final_text == "Streamed and final must match exactly."


@pytest.mark.asyncio
async def test_delegate_tool_falls_back_on_failure(monkeypatch):
    """A delegate whose worker raises degrades to the graceful fallback."""
    from apps.agent_service.src.agent.conversation import delegates
    from apps.agent_service.src.agent.runtime.mcp.tool_layer import get_mcp_tool_layer

    delegates.register_conversation_delegate_tools()

    async def _boom(tool_name, params, ctx):
        raise RuntimeError("specialist exploded")

    monkeypatch.setattr(delegates, "_run_delegate", _boom)

    layer = get_mcp_tool_layer()
    result = await layer.call(
        "delegate_billing",
        {"task": "refund", "tenant_id": "demo-tenant"},
        _ctx(),
        use_cache=False,
    )
    assert result.success is True  # fallback keeps the turn alive
    assert result.fallback_used is True
    assert result.data.get("fallback") is True


@pytest.mark.asyncio
async def test_step_budgets_are_bounded(monkeypatch):
    """Orchestrator cap is 6, delegate cap is 4, and ReActLoop honours the cap."""
    from apps.agent_service.src.agent.runtime import react_loop as rl
    from apps.agent_service.src.agent.runtime.react_loop import ReActLoop
    from packages.agent.src.subagent_types import (
        AgentRole,
        SubagentContextPacket,
        SubagentTask,
    )

    assert MAX_REACT_LOOPS == 6
    assert _DELEGATE_MAX_REACT_STEPS == 4

    async def _noop(tool_name, params, ctx):
        return {}

    monkeypatch.setattr(rl, "dispatch_tool_call", _noop)

    task = SubagentTask(
        id="answer",
        role=AgentRole.BILLING,
        objective="loop forever",
        skill="brief",
        input={"message": "x"},
        allowed_tools=["query_health"],
        max_react_steps=4,
    )
    packet = SubagentContextPacket(
        tenant_id="demo-tenant", customer_id="cust-1", trace_id="s1", task=task
    )
    always_tool = _TOOLCALL.format(name="query_health", args="{}")
    llm = FakeLLM([], default=always_tool)
    loop = ReActLoop(packet=packet, ctx=_ctx(), config=_config(), llm_client=llm)

    result = await loop.run()
    assert result.success is False
    assert "max_react_steps" in (result.error or "")
    assert llm.complete_calls == 4  # bounded by task.max_react_steps
