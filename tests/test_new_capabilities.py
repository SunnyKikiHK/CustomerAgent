"""Unit tests for new agent capabilities and orchestrators."""

from __future__ import annotations

import pytest

from apps.agent_service.src.agent.conversation.intent import (
    IntentCategory,
    IntentRecognizer,
    UrgencyLevel,
)
from apps.agent_service.src.agent.conversation.conversation_planner import build_conversation_plan
from apps.agent_service.src.agent.runtime.monitor import get_performance_monitor
from apps.agent_service.src.agent.runtime.skills import SkillManager
from apps.agent_service.src.agent.signal.signal_planner import build_signal_plan
from apps.agent_service.src.signals.queue import SignalQueue
from packages.agent.src.config import AgentConfig
from packages.agent.src.memory import ConversationMemory
from packages.agent.src.types import CustomerSignal


@pytest.fixture
def tenant_config() -> AgentConfig:
    return AgentConfig(
        tenant_id="demo-tenant",
        name="demo",
        instructions="test",
        model="deepseek/deepseek-v4-flash",
        planner_model="deepseek/deepseek-v4-flash",
        tools=["query_health", "query_playbooks", "send_email", "send_slack"],
    )


def test_intent_pattern_and_vote():
    recognizer = IntentRecognizer(llm_client=_FakeLLM(), embedding_enabled=False)
    pattern = recognizer._pattern_recognize("I need a refund for my invoice")
    assert pattern["intent"] == IntentCategory.BILLING

    voted = recognizer._vote(
        {"intent": IntentCategory.BILLING, "confidence": 0.9},
        {"intent": IntentCategory.OTHER, "confidence": 0.0},
        pattern,
    )
    assert voted == IntentCategory.BILLING
    assert recognizer._urgency("urgent refund now", IntentCategory.BILLING) == UrgencyLevel.CRITICAL


def test_skill_manager_matches_keywords(tmp_path):
    skill_dir = tmp_path / "refund-handling"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: refund-handling\nkeywords: refund, billing\nagents: customer_chat\nenabled: true\n---\n\nRefund SOP\n",
        encoding="utf-8",
    )
    manager = SkillManager(tmp_path)
    manager.load()
    prompt = manager.prompt_for("I need a refund please", "customer_chat")
    assert "Refund SOP" in prompt


@pytest.mark.asyncio
async def test_memory_working_and_profile_excerpt():
    memory = ConversationMemory()
    from packages.agent.src.chat_types import ChatMessage, ChatMessageRole

    message = ChatMessage(
        tenant_id="t1",
        customer_id="c1",
        session_id="s1",
        role=ChatMessageRole.USER,
        content="Hello",
    )
    await memory.add_message(message)
    context = await memory.get_context(
        tenant_id="t1",
        customer_id="c1",
        session_id="s1",
        query="Hello",
    )
    assert len(context.recent_messages) == 1
    assert context.to_prompt_text().startswith("[Recent messages]")


def test_monitor_routing_penalty_loop():
    monitor = get_performance_monitor()
    for _ in range(5):
        monitor.record_role_result("health_analysis", success=False, latency_ms=5000)
    penalties = monitor.refresh_penalties({})
    assert penalties["health_analysis"] > 0.0


def test_signal_planner_builds_dependency_chain(tenant_config):
    signal = CustomerSignal(
        tenant_id="demo-tenant",
        customer_id="cust-1",
        type="usage_drop",
        payload={"pct": 25},
    )
    plan, _ = build_signal_plan(
        signal=signal,
        config=tenant_config,
        tenant_constraints=[],
        memory_excerpt="profile: prefers email",
    )
    assert len(plan.tasks) >= 2
    outreach = next(task for task in plan.tasks if task.role.value == "outreach_draft")
    assert outreach.depends_on


def test_conversation_fast_path_plan(tenant_config):
    from apps.agent_service.src.agent.conversation.intent import IntentResult

    intent = IntentResult(
        intent=IntentCategory.GREETING,
        confidence=0.9,
        urgency=UrgencyLevel.LOW,
        entities={},
        reasoning="greeting",
    )
    plan, _ = build_conversation_plan(
        message="Hello",
        intent=intent,
        config=tenant_config,
        tenant_constraints=[],
        memory_excerpt=None,
    )
    assert len(plan.tasks) == 1
    assert plan.tasks[0].role.value == "customer_chat"


def test_signal_queue_dedupes_payloads():
    queue = SignalQueue()
    payload = {
        "tenant_id": "demo-tenant",
        "customer_id": "cust-1",
        "type": "usage_drop",
        "payload": {"pct": 10},
    }
    first = queue.enqueue(payload)
    second = queue.enqueue(payload)
    assert first
    assert second == ""


@pytest.mark.asyncio
async def test_mcp_empty_playbook_fallback():
    from apps.agent_service.src.agent.runtime.mcp.retrieval import retrieve_with_optimization
    from packages.agent.src.types import SessionContext

    ctx = SessionContext(
        tenant_id="demo-tenant",
        user_id="cust-1",
        session_id="s1",
    )
    result = await retrieve_with_optimization(
        tool_name="query_playbooks",
        query="renewal risk playbook",
        ctx=ctx,
        params={"tenant_id": "demo-tenant", "customer_id": "cust-1", "use_retrieval_optimizer": False},
        top_k=3,
        llm_client=_FakeLLM(),
    )
    assert result.success is True
    assert result.data.get("matches") == []


@pytest.mark.asyncio
async def test_target_phase_imports_and_base_orchestrator_contract():
    from apps.agent_service.src.agent.orchestrator.base import BaseOrchestrator, EMITTED_ACTION
    from apps.agent_service.src.agent.signal.signal_orchestrator import SignalOrchestrator
    from apps.agent_service.src.agent.conversation.conversation_orchestrator import ConversationOrchestrator

    assert issubclass(SignalOrchestrator, BaseOrchestrator)
    assert issubclass(ConversationOrchestrator, BaseOrchestrator)
    assert SignalOrchestrator.supports_external_writes is True
    assert ConversationOrchestrator.supports_external_writes is False
    assert EMITTED_ACTION == "emit_or_execute_approved_payload"


class _FakeLLM:
    async def complete(self, messages, **kwargs):
        from apps.agent_service.src.agent.llm_client import LLMResponse
        from packages.agent.src.types import LLMUsage

        content = messages[-1].content if messages else ""
        if "rewrite" in str(kwargs.get("name", "")):
            text = '["renewal outreach", "churn prevention"]'
        elif "rerank" in str(kwargs.get("name", "")):
            text = "[0]"
        else:
            text = '["renewal outreach"]'
        return LLMResponse(text=text, model="fake", usage=LLMUsage())
