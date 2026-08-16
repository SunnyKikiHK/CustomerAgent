"""Unit tests for the runnable-milestone capabilities.

These are offline-safe: no live Postgres/Redis/LLM is required. Live-backend
integration is covered separately and skips when the services are down.
"""

from __future__ import annotations

import pytest

from packages.agent.src.config import AgentConfig
from packages.agent.src.subagent_types import AgentRole
from packages.agent.src.types import CustomerSignal


@pytest.fixture
def tenant_config() -> AgentConfig:
    return AgentConfig(
        tenant_id="demo-tenant",
        name="demo",
        instructions="test",
        model="deepseek/deepseek-v4-flash",
        planner_model="deepseek/deepseek-v4-flash",
        tools=["query_health", "query_playbooks", "send_email"],
    )


def _intent(category, urgency):
    from apps.agent_service.src.agent.conversation.intent import IntentResult

    return IntentResult(
        intent=category, confidence=0.9, urgency=urgency, entities={}, reasoning=""
    )


# ── Embeddings ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_embed_text_falls_back_to_local_1536(monkeypatch):
    from packages.knowledge_service.src import embed

    # Force the "no provider configured" path.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    embed.reset_embedding_client()

    vector = await embed.embed_text("renewal risk outreach")
    assert len(vector) == embed.EMBEDDING_DIM == 1536
    assert len(embed.local_embedding("x")) == 1536


# ── Ingest / chunking ─────────────────────────────────────────────────────────

def test_chunk_markdown_document_parses_front_matter_and_chunks():
    from packages.knowledge_service.src.ingest import chunk_markdown_document

    raw = (
        "---\n"
        "title: Renewal Save\n"
        "signal_type: renewal_risk\n"
        "---\n\n"
        "# Renewal Save\n\n"
        + ("Paragraph one about renewals. " * 20)
        + "\n\n"
        + ("Paragraph two about outreach. " * 20)
    )
    chunks = chunk_markdown_document(doc_id="renewal-save", raw=raw)
    assert len(chunks) >= 2
    assert chunks[0].metadata["title"] == "Renewal Save"
    assert chunks[0].metadata["signal_type"] == "renewal_risk"
    assert all(chunk.text for chunk in chunks)


# ── Domain-aware subagent factory ─────────────────────────────────────────────

def test_factory_partitions_specialists_by_domain():
    from apps.agent_service.src.agent.subagents import role_map_for_domain

    conversation = set(role_map_for_domain("conversation"))
    signal = set(role_map_for_domain("signal"))

    assert AgentRole.HEALTH_ANALYSIS in signal
    assert AgentRole.HEALTH_ANALYSIS not in conversation
    assert AgentRole.BILLING in conversation
    assert AgentRole.BILLING not in signal
    # playbook is the one shared specialist
    assert AgentRole.PLAYBOOK_RETRIEVAL in conversation
    assert AgentRole.PLAYBOOK_RETRIEVAL in signal


# ── Signal planner apology path ───────────────────────────────────────────────

def test_signal_planner_negative_sentiment_drafts_apology(tenant_config):
    from apps.agent_service.src.agent.signal.signal_planner import build_signal_plan

    signal = CustomerSignal(
        tenant_id="demo-tenant",
        customer_id="cust-1",
        type="negative_sentiment",
        payload={"reason": "angry about downtime"},
    )
    plan, _ = build_signal_plan(
        signal=signal, config=tenant_config, tenant_constraints=[], memory_excerpt=None
    )
    outreach = next(task for task in plan.tasks if task.role == AgentRole.OUTREACH_DRAFT)
    assert "apolog" in outreach.objective.lower()


# ── Signal text labels for new types ──────────────────────────────────────────

def test_signal_text_labels_new_types():
    renewal = CustomerSignal(
        tenant_id="t", customer_id="c", type="renewal_risk",
        payload={"days_to_renewal": 30, "renewal_date": "2026-08-01", "health_score": 40},
    )
    assert "Renewal" in renewal.signal_text
    low = CustomerSignal(
        tenant_id="t", customer_id="c", type="low_health",
        payload={"health_score": 30, "threshold": 50},
    )
    assert "Health score" in low.signal_text


# ── Conversation -> signal bridge decision ────────────────────────────────────

def test_bridge_fires_on_complaint_and_escalation():
    from apps.agent_service.src.agent.conversation.conversation_orchestrator import (
        _should_bridge_to_signal,
        _sentiment_label,
    )
    from apps.agent_service.src.agent.conversation.intent import IntentCategory, UrgencyLevel

    complaint = _intent(IntentCategory.COMPLAINT, UrgencyLevel.MEDIUM)
    greeting = _intent(IntentCategory.GREETING, UrgencyLevel.LOW)
    assert _should_bridge_to_signal(complaint) is True
    assert _should_bridge_to_signal(greeting) is False
    assert _sentiment_label(complaint) == "negative"
    assert _sentiment_label(_intent(IntentCategory.FEEDBACK, UrgencyLevel.LOW)) == "positive"


# ── Profile normalization helper ──────────────────────────────────────────────

def test_memory_prompt_skips_empty_profile():
    from packages.agent.src.memory import MemoryContext

    empty = MemoryContext(user_profile={"preferences": [], "entities": {"issue_types": []}})
    assert "[User profile]" not in empty.to_prompt_text()

    populated = MemoryContext(user_profile={"preferences": ["prefers email"]})
    assert "[User profile]" in populated.to_prompt_text()


# ── Embedding model default ───────────────────────────────────────────────────

def test_embed_default_model_is_qwen(monkeypatch):
    """With EMBEDDING_MODEL unset, embed.py resolves the qwen default."""
    import os

    from packages.knowledge_service.src import embed

    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    assert embed.DEFAULT_EMBEDDING_MODEL == "qwen/qwen3-embedding-8b"
    # Same fallback the embeddings call uses.
    assert (
        os.getenv("EMBEDDING_MODEL", embed.DEFAULT_EMBEDDING_MODEL)
        == "qwen/qwen3-embedding-8b"
    )


def test_model_helpers_read_env(monkeypatch):
    """worker/planner model helpers read OPENROUTER_MODEL / OPENROUTER_LARGE_MODEL."""
    from packages.agent.src import models

    monkeypatch.setenv("OPENROUTER_MODEL", "vendor/worker-x")
    monkeypatch.setenv("OPENROUTER_LARGE_MODEL", "vendor/planner-x")
    assert models.worker_model() == "vendor/worker-x"
    assert models.planner_model() == "vendor/planner-x"

    # planner falls back to the worker model when the large model is unset
    monkeypatch.delenv("OPENROUTER_LARGE_MODEL", raising=False)
    assert models.planner_model() == "vendor/worker-x"


# ── Skills layer (one persona per subagent role) ──────────────────────────────

#: Every subagent role and the skill folder that carries its persona.
_ROLE_TO_SKILL_DIR = {
    "general": "general_support",
    "technical": "technical_support",
    "billing": "billing_support",
    "escalation": "escalation_handling",
    "health_analysis": "health_analysis",
    "outreach_draft": "outreach_drafting",
    "playbook_retrieval": "playbook_retrieval",
}


def _demo_skills_root():
    from pathlib import Path

    # tests/ -> repo root -> skills/demo-tenant
    return Path(__file__).resolve().parents[1] / "skills" / "demo-tenant"


def test_every_subagent_role_has_a_skill_file():
    """Each role has a SKILL.md whose frontmatter targets that role."""
    from apps.agent_service.src.agent.runtime.skills import SkillManager

    manager = SkillManager(_demo_skills_root())
    manager.load()
    assert not manager.errors, manager.errors

    by_agent: dict[str, list[str]] = {}
    for skill in manager.skills:
        for agent in skill.agents:
            by_agent.setdefault(agent, []).append(skill.name)

    for role in _ROLE_TO_SKILL_DIR:
        assert role in by_agent, f"no SKILL.md targets role {role!r}"


def test_skill_manager_injects_role_matched_persona():
    """Role-matched (keyword-empty) personas inject for their role only."""
    from apps.agent_service.src.agent.runtime.skills import SkillManager

    manager = SkillManager(_demo_skills_root())
    manager.load()

    billing = manager.prompt_for("I was charged twice, I want a refund", "billing")
    technical = manager.prompt_for("I got a 500 error", "technical")

    assert "Billing" in billing
    assert "Technical" in technical
    # A billing turn must not pull in the technical persona and vice versa.
    assert "Technical Support Skill" not in billing
    assert "Billing Support Skill" not in technical


def test_subagents_expose_role_brief_not_skill():
    """Inline personas were relocated: subagents expose ROLE_BRIEF, not SKILL."""
    from apps.agent_service.src.agent.conversation.subagents import billing, general
    from apps.agent_service.src.agent.subagents import health_analysis

    for module in (general, billing, health_analysis):
        assert hasattr(module, "ROLE_BRIEF")
        assert not hasattr(module, "SKILL")
        assert isinstance(module.ROLE_BRIEF, str) and module.ROLE_BRIEF


# ── Per-turn profile-signal extraction ────────────────────────────────────────

class _EntityLLM:
    """Fake LLM that returns a full entity+signal extraction JSON object."""

    async def complete(self, messages, **kwargs):
        from apps.agent_service.src.agent.llm_client import LLMResponse
        from packages.agent.src.types import LLMUsage

        text = (
            '{"order_id": ["A-1"], "product": [], "date": [], "amount": [], '
            '"error_code": [], "preferences": ["fast responses", "clear next steps"], '
            '"risk_signals": ["considering not renewing"], '
            '"sentiment_signals": ["frustrated", "urgent"]}'
        )
        return LLMResponse(text=text, model="fake", usage=LLMUsage())


def test_entity_extraction_schema_shape():
    """Normalization always yields the full key set as string lists."""
    from apps.agent_service.src.agent.conversation.intent import IntentRecognizer

    empty = IntentRecognizer._empty_entities()
    assert set(empty) == set(IntentRecognizer._ENTITY_KEYS)
    assert {"preferences", "risk_signals", "sentiment_signals"} <= set(empty)

    normalized = IntentRecognizer._normalize_entities(
        {"preferences": "fast responses", "sentiment_signals": ["frustrated", " urgent "]}
    )
    assert normalized["preferences"] == ["fast responses"]  # scalar -> list
    assert normalized["sentiment_signals"] == ["frustrated", "urgent"]  # trimmed
    assert normalized["order_id"] == []  # missing -> empty
    # Non-dict input still returns the full empty schema.
    assert IntentRecognizer._normalize_entities("nope") == empty


@pytest.mark.asyncio
async def test_extract_entities_surfaces_profile_signals():
    from apps.agent_service.src.agent.conversation.intent import IntentRecognizer

    recognizer = IntentRecognizer(llm_client=_EntityLLM(), embedding_enabled=False)
    entities = await recognizer._extract_entities("I'm frustrated and may not renew")

    assert entities["preferences"] == ["fast responses", "clear next steps"]
    assert entities["risk_signals"] == ["considering not renewing"]
    assert entities["sentiment_signals"] == ["frustrated", "urgent"]
    assert entities["order_id"] == ["A-1"]  # transactional entities still extracted


def test_profile_data_lifts_signals_to_top_level():
    """on_approved's helper lifts signal lists out of the entities bucket."""
    from apps.agent_service.src.agent.conversation.conversation_orchestrator import (
        _profile_data_from_intent,
    )
    from apps.agent_service.src.agent.conversation.intent import IntentCategory, UrgencyLevel

    intent = _intent(IntentCategory.COMPLAINT, UrgencyLevel.MEDIUM)
    intent.entities = {
        "order_id": ["A-1"],
        "preferences": ["fast responses"],
        "risk_signals": ["considering not renewing"],
        "sentiment_signals": ["frustrated"],
    }
    profile_data = _profile_data_from_intent(intent, "negative")

    # Signals promoted to the top level (where the profile merger reads them).
    assert profile_data["preferences"] == ["fast responses"]
    assert profile_data["risk_signals"] == ["considering not renewing"]
    assert profile_data["sentiment_signals"] == ["frustrated"]
    # Transactional entities stay under `entities`; signals were removed from it.
    assert profile_data["entities"]["order_id"] == ["A-1"]
    assert "preferences" not in profile_data["entities"]
    assert profile_data["last_sentiment"] == "negative"


def test_profile_merge_persists_chat_signals():
    """End-to-end: lifted signals merge into the durable profile list fields."""
    from apps.agent_service.src.agent.conversation.conversation_orchestrator import (
        _profile_data_from_intent,
    )
    from apps.agent_service.src.agent.conversation.intent import IntentCategory, UrgencyLevel
    from packages.agent.src.memory import ConversationMemory

    intent = _intent(IntentCategory.COMPLAINT, UrgencyLevel.HIGH)
    intent.entities = {
        "preferences": ["clear next steps"],
        "risk_signals": ["repeated delay complaints"],
        "sentiment_signals": ["frustrated", "urgent"],
    }
    profile_data = _profile_data_from_intent(intent, "negative")

    merged = ConversationMemory._merge_profile(
        dict(ConversationMemory.EMPTY_PROFILE), profile_data
    )
    assert "frustrated" in merged["sentiment_signals"]
    assert "urgent" in merged["sentiment_signals"]
    assert "repeated delay complaints" in merged["risk_signals"]
    assert "clear next steps" in merged["preferences"]


# ── Item 1: check_human_availability prototype (read-only, inline) ─────────────

@pytest.mark.asyncio
async def test_check_human_availability_always_unavailable():
    from packages.tool_system.src.tools.check_human_availability import (
        execute_check_human_availability,
    )

    out = await execute_check_human_availability({"tenant_id": "t", "customer_id": "c"})
    assert out.available is False
    assert out.message == "No human support representative is currently available."


def test_check_human_availability_registered_internal_and_allowed():
    from packages.tool_system.src.registry import TOOL_REGISTRY, ToolBoundary
    from apps.agent_service.src.agent.conversation.subagents.escalation import DEFAULT_ALLOWED_TOOLS

    entry = TOOL_REGISTRY["check_human_availability"]
    assert entry.boundary is ToolBoundary.INTERNAL  # callable in-process this turn
    assert "check_human_availability" in DEFAULT_ALLOWED_TOOLS


@pytest.mark.asyncio
async def test_escalation_subagent_surfaces_no_human_message():
    """A fake-LLM escalation subagent uses the tool result in its reply this turn."""
    from apps.agent_service.src.agent.subagents.base import ReActSubagent
    from apps.agent_service.src.agent.llm_client import LLMResponse
    from packages.agent.src.config import AgentConfig
    from packages.agent.src.subagent_types import (
        AgentRole,
        SubagentContextPacket,
        SubagentTask,
    )
    from packages.agent.src.types import LLMUsage, SessionContext

    class _EscalationLLM:
        """Step 1: call the tool. Step 2: answer using the tool result."""

        def __init__(self):
            self._step = 0

        async def complete(self, messages, **kwargs):
            self._step += 1
            if self._step == 1:
                text = (
                    '{"markdown": "", "data": {}, "tool_calls": '
                    '[{"name": "check_human_availability", '
                    '"arguments": {"customer_id": "c", "reason": "wants human"}}]}'
                )
            else:
                # The tool result is now in the message history; the agent relays it.
                text = (
                    '{"markdown": "I am sorry, but no human support representative is '
                    'currently available. I have logged your issue for follow-up.", '
                    '"data": {}, "tool_calls": []}'
                )
            return LLMResponse(text=text, model="fake", usage=LLMUsage())

    task = SubagentTask(
        id="answer",
        role=AgentRole.ESCALATION,
        objective="Answer an escalation turn",
        skill="Escalation brief",
        input={"message": "I want to talk to a human"},
        allowed_tools=["check_human_availability"],
    )
    packet = SubagentContextPacket(
        task=task,
        tenant_id="demo-tenant",
        customer_id="c",
        tenant_constraints=[],
        memory_excerpt=None,
    )
    ctx = SessionContext(tenant_id="demo-tenant", user_id="c", session_id="s", trace_id="s")
    config = AgentConfig(
        tenant_id="demo-tenant", name="c", instructions="t",
        model="m", planner_model="pm", tools=["check_human_availability"],
    )
    agent = ReActSubagent(packet=packet, ctx=ctx, config=config)
    agent._loop.llm_client = _EscalationLLM()
    result = await agent.run()

    assert result.success
    assert "no human" in result.markdown.lower()
    assert any(call.tool_name == "check_human_availability" for call in result.tool_calls)


def test_escalate_to_human_is_mcp_action_not_conversation_tool():
    """The real (future) escalation action lives on the MCP boundary only."""
    from packages.tool_system.src.registry import TOOL_REGISTRY, ToolBoundary
    from apps.agent_service.src.agent.conversation.subagents.escalation import DEFAULT_ALLOWED_TOOLS

    assert TOOL_REGISTRY["escalate_to_human"].boundary is ToolBoundary.MCP_ACTION
    # It is NOT callable inline by the conversation escalation subagent.
    assert "escalate_to_human" not in DEFAULT_ALLOWED_TOOLS


def test_conversation_orchestrator_keeps_external_writes_disabled():
    from apps.agent_service.src.agent.conversation.conversation_orchestrator import (
        ConversationOrchestrator,
    )

    assert ConversationOrchestrator.supports_external_writes is False


# ── Item 2: process_refund prototype ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_process_refund_always_succeeds():
    from packages.tool_system.src.tools.process_refund import execute_process_refund

    out = await execute_process_refund(
        {"tenant_id": "t", "customer_id": "c", "order_id": "O-1"}
    )
    assert out.success is True
    assert out.status == "refund_initiated"
    assert out.refund_id.startswith("refund-")
    assert "refund has been initiated" in out.message.lower()


def test_process_refund_registered_internal_and_allowed():
    from packages.tool_system.src.registry import TOOL_REGISTRY, ToolBoundary
    from apps.agent_service.src.agent.conversation.subagents.billing import DEFAULT_ALLOWED_TOOLS

    assert TOOL_REGISTRY["process_refund"].boundary is ToolBoundary.INTERNAL
    assert "process_refund" in DEFAULT_ALLOWED_TOOLS


# ── Item 3: compliance critic skill ───────────────────────────────────────────

def test_compliance_critic_skill_targets_role():
    from apps.agent_service.src.agent.runtime.skills import SkillManager

    manager = SkillManager(_demo_skills_root())
    manager.load()
    persona = manager.persona_for("compliance_critic")
    assert "Compliance Critic Skill" in persona
    assert "tenant isolation" in persona.lower()


def test_critic_persona_falls_back_when_skill_missing(monkeypatch):
    from apps.agent_service.src.agent.subagents import compliance_critic
    from apps.agent_service.src.agent.subagents.compliance_critic import (
        COMPLIANCE_CRITIC_BRIEF,
        _critic_persona,
    )

    # When no compliance_critic skill persona is available (empty string), the
    # code-owned fallback brief is used. (get_skill_manager falls back to the
    # demo tenant's skills dir, so we force the empty-persona path directly
    # rather than relying on an unknown tenant name.)
    class _NoSkillManager:
        def persona_for(self, role: str) -> str:
            return ""

    monkeypatch.setattr(
        compliance_critic, "get_skill_manager", lambda tenant_id: _NoSkillManager()
    )
    assert _critic_persona("no-such-tenant-xyz") == COMPLIANCE_CRITIC_BRIEF


@pytest.mark.asyncio
async def test_run_compliance_critic_uses_skill_persona():
    from apps.agent_service.src.agent.subagents.compliance_critic import run_compliance_critic
    from apps.agent_service.src.agent.llm_client import LLMResponse
    from packages.agent.src.config import AgentConfig
    from packages.agent.src.orchestration_types import ConversationAgentInput, OrchestratorPlan
    from packages.agent.src.chat_types import ChatMessage, ChatMessageRole
    from packages.agent.src.subagent_types import AgentRole, SubagentTask
    from packages.agent.src.types import LLMUsage, SessionContext

    captured = {}

    class _CapturingLLM:
        async def complete(self, messages, **kwargs):
            captured["system"] = messages[0].content
            return LLMResponse(
                text='{"approved": true, "findings": [], "feedback": "ok"}',
                model="fake",
                usage=LLMUsage(),
            )

    ctx = SessionContext(tenant_id="demo-tenant", user_id="c", session_id="s", trace_id="s")
    agent_input = ConversationAgentInput(
        tenant_id="demo-tenant",
        customer_id="c",
        session_id="s",
        message=ChatMessage(
            tenant_id="demo-tenant", customer_id="c", session_id="s",
            role=ChatMessageRole.USER, content="hi",
        ),
    )
    config = AgentConfig(
        tenant_id="demo-tenant", name="c", instructions="t",
        model="m", planner_model="pm", tools=[],
    )
    dummy_task = SubagentTask(
        id="answer", role=AgentRole.GENERAL, objective="answer",
        skill="brief", input={"message": "hi"}, allowed_tools=[],
    )
    plan = OrchestratorPlan(
        goal="g", tasks=[dummy_task], global_constraints=[], reasoning_summary="",
    )

    review, _ = await run_compliance_critic(
        agent_input=agent_input, plan=plan, results=[], ctx=ctx,
        config=config, proposed_external_writes=[], llm_client=_CapturingLLM(),
    )
    assert review.approved is True
    # The system prompt came from the compliance_critic SKILL.md, not the old inline string.
    assert "Compliance Critic Skill" in captured["system"]


# ── Item 4: non-blocking profile update ───────────────────────────────────────

@pytest.mark.asyncio
async def test_background_profile_update_failure_is_swallowed():
    import asyncio

    from apps.agent_service.src.agent.conversation import conversation_orchestrator as co

    async def boom():
        raise RuntimeError("profile update failed")

    co._spawn_background(boom(), label="test")
    # Let the task run; a raised exception must not propagate here.
    await asyncio.sleep(0.01)
    assert len(co._BACKGROUND_TASKS) == 0



