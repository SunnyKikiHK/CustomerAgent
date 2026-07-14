"""Unit tests for new agent capabilities and orchestrators."""

from __future__ import annotations

import os
import uuid

import pytest

from apps.agent_service.src.agent.conversation.intent import IntentCategory, IntentRecognizer, UrgencyLevel
from apps.agent_service.src.agent.conversation.conversation_planner import build_conversation_plan
from apps.agent_service.src.agent.runtime.monitor import get_performance_monitor
from apps.agent_service.src.agent.runtime.skills import (
    SkillManager,
    _resolve_skills_root,
)
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


def test_missing_tenant_uses_demo_skills():
    root = _resolve_skills_root(f"missing-{uuid.uuid4()}", None)
    manager = SkillManager(root)
    manager.load()

    assert root.name == "demo-tenant"
    assert manager.skills
    assert manager.prompt_for("I need help", "general")


def test_skill_manager_matches_keywords(tmp_path):
    skill_dir = tmp_path / "refund-handling"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: refund-handling\nkeywords: refund, billing\nagents: billing\nenabled: true\n---\n\nRefund SOP\n",
        encoding="utf-8",
    )
    manager = SkillManager(tmp_path)
    manager.load()
    assert "Refund SOP" in manager.prompt_for("I need a refund please", "billing")


@pytest.mark.asyncio
async def test_memory_working_and_profile_excerpt():
    memory = ConversationMemory()
    from packages.agent.src.chat_types import ChatMessage, ChatMessageRole

    # Unique session id so the test is isolated even against a live Redis that
    # persists working-memory keys across runs.
    session_id = f"s-{uuid.uuid4()}"
    message = ChatMessage(
        tenant_id="t1", customer_id="c1", session_id=session_id, role=ChatMessageRole.USER, content="Hello"
    )
    await memory.add_message(message)
    context = await memory.get_context(
        tenant_id="t1", customer_id="c1", session_id=session_id, query="Hello"
    )
    assert len(context.recent_messages) == 1
    assert context.to_prompt_text().startswith("[Recent messages]")


def test_monitor_routing_penalty_loop():
    monitor = get_performance_monitor()
    for _ in range(5):
        monitor.record_role_result("health_analysis", success=False, latency_ms=5000)
    assert monitor.refresh_penalties({})["health_analysis"] > 0.0


def test_signal_planner_builds_dependency_chain(tenant_config):
    signal = CustomerSignal(
        tenant_id="demo-tenant", customer_id="cust-1", type="usage_drop", payload={"pct": 25}
    )
    plan, _ = build_signal_plan(
        signal=signal,
        config=tenant_config,
        tenant_constraints=[],
        memory_excerpt="profile: prefers email",
    )
    outreach = next(task for task in plan.tasks if task.role.value == "outreach_draft")
    assert outreach.depends_on


@pytest.mark.asyncio
async def test_conversation_fast_path_plan(tenant_config):
    from apps.agent_service.src.agent.conversation.intent import IntentResult

    intent = IntentResult(
        intent=IntentCategory.GREETING,
        confidence=0.9,
        urgency=UrgencyLevel.LOW,
        entities={},
        reasoning="greeting",
    )
    # Fast path is deterministic and never calls the LLM planner.
    plan, _ = await build_conversation_plan(
        message="Hello",
        intent=intent,
        config=tenant_config,
        tenant_constraints=[],
        memory_excerpt=None,
    )
    assert len(plan.tasks) == 1
    assert plan.tasks[0].role.value == "general"


def test_signal_queue_dedupes_payloads():
    queue = SignalQueue()
    payload = {
        "tenant_id": f"demo-tenant-{uuid.uuid4()}",
        "customer_id": "cust-1",
        "type": "usage_drop",
        "payload": {"pct": 10},
    }
    assert queue.enqueue(payload)
    assert queue.enqueue(payload) == ""

pytest.mark
@pytest.mark.asyncio
async def test_mcp_empty_playbook_fallback():
    from apps.agent_service.src.agent.runtime.mcp.retrieval import retrieve_with_optimization
    from packages.agent.src.types import SessionContext

    tenant_id = str(uuid.uuid4())
    ctx = SessionContext(tenant_id=tenant_id, user_id="cust-1", session_id="s1")
    result = await retrieve_with_optimization(
        tool_name="query_playbooks",
        query="renewal risk playbook",
        ctx=ctx,
        params={"tenant_id": tenant_id, "customer_id": "cust-1"},
        top_k=3,
        llm_client=_FakeLLM(),
    )
    assert result.success is True
    assert isinstance(result.data.get("matches"), list)


def test_tool_registry_enforces_internal_and_mcp_boundaries():
    from packages.tool_system.src.registry import (
        ToolBoundary,
        ToolBoundaryError,
        get_tools_by_boundary,
        require_tool_boundary,
    )

    assert require_tool_boundary("query_health", ToolBoundary.INTERNAL)
    assert require_tool_boundary("send_email", ToolBoundary.MCP_ACTION)
    with pytest.raises(ToolBoundaryError):
        require_tool_boundary("send_email", ToolBoundary.INTERNAL)
    with pytest.raises(ToolBoundaryError):
        require_tool_boundary("query_health", ToolBoundary.MCP_ACTION)
    assert set(get_tools_by_boundary(ToolBoundary.MCP_ACTION)) == {
        "send_email",
        "send_slack",
        "escalate_to_human",
    }


def test_outreach_draft_cannot_execute_actions():
    from apps.agent_service.src.agent.subagents.outreach_draft import DEFAULT_ALLOWED_TOOLS

    assert DEFAULT_ALLOWED_TOOLS == []


@pytest.mark.asyncio
async def test_action_service_is_tenant_authoritative_and_idempotent():
    from apps.tool_gateway.src.approval import LocalApprovalVerifier
    from apps.tool_gateway.src.contracts import ActionContext
    from apps.tool_gateway.src.idempotency import InMemoryIdempotencyStore
    from apps.tool_gateway.src.service import ActionService

    service = ActionService(
        verifier=LocalApprovalVerifier(),
        idempotency=InMemoryIdempotencyStore(),
    )
    context = ActionContext(
        tenant_id="trusted-tenant",
        approval_id="approval-1",
        idempotency_key="idem-1",
        actor="agent",
    )
    payload = {
        "tenant_id": "attacker-tenant",
        "customer_id": "cust-1",
        "recipient_email": "user@example.com",
        "subject": "Hello",
        "body": "Safe message",
        "sender_name": "CSM",
    }
    first = await service.execute("send_email", payload, context)
    second = await service.execute("send_email", payload, context)
    assert first.success is True and first.status == "executed"
    assert second.success is True and second.status == "duplicate"
    assert first.provider_message_id == second.provider_message_id


@pytest.mark.asyncio
async def test_action_service_rejects_missing_approval_and_unknown_action():
    from pydantic import ValidationError
    from apps.tool_gateway.src.contracts import ActionContext
    from apps.tool_gateway.src.service import ActionService

    with pytest.raises(ValidationError):
        ActionContext(
            tenant_id="tenant", approval_id="", idempotency_key="idem", actor="agent"
        )
    context = ActionContext(
        tenant_id="tenant", approval_id="approved", idempotency_key="idem", actor="agent"
    )
    result = await ActionService().execute("query_health", {}, context)
    assert result.success is False
    assert result.status == "rejected"


@pytest.mark.asyncio
async def test_internal_dispatch_rejects_mcp_action_without_gateway():
    from apps.agent_service.src.agent.runtime.tool_dispatch import execute_internal_analysis
    from packages.agent.src.types import SessionContext
    from packages.tool_system.src.registry import ToolBoundaryError

    ctx = SessionContext(tenant_id="tenant", user_id="user", session_id="session")
    with pytest.raises(ToolBoundaryError):
        await execute_internal_analysis("send_email", {}, ctx)


def test_gateway_exposes_only_action_tools():
    pytest.importorskip("mcp")
    from apps.tool_gateway.src.index import mcp

    tool_manager = mcp._tool_manager
    assert set(tool_manager._tools) == {"send_email", "send_slack", "escalate_to_human"}


def test_target_phase_imports_and_base_orchestrator_contract():
    from apps.agent_service.src.agent.orchestrator.base import (
        BaseOrchestrator,
        EMITTED_ACTION,
        SAFE_COMPLIANCE_FALLBACK,
    )
    from apps.agent_service.src.agent.signal.signal_orchestrator import SignalOrchestrator
    from apps.agent_service.src.agent.conversation.conversation_orchestrator import ConversationOrchestrator

    assert issubclass(SignalOrchestrator, BaseOrchestrator)
    assert issubclass(ConversationOrchestrator, BaseOrchestrator)
    assert SignalOrchestrator.supports_external_writes is True
    assert ConversationOrchestrator.supports_external_writes is False
    assert EMITTED_ACTION == "emit_or_execute_approved_payload"
    assert SAFE_COMPLIANCE_FALLBACK == (
        "I’m unable to safely complete that request here based on your request. "
        "Please contact support through the approved channel."
    )


@pytest.fixture
def live_tenant_ids():
    return str(uuid.uuid4()), str(uuid.uuid4())


async def _seed_live_tenant(tenant_id: str, customer_id: str) -> None:
    from packages.db.src import execute

    await execute(
        "insert into tenants (id, name) values ($1::uuid, $2)",
        tenant_id,
        "pytest-tenant",
        tenant_id=tenant_id,
    )
    await execute(
        """
        insert into customers (id, tenant_id, name, email, health_score, mrr, renewal_date, nps, usage_trend)
        values ($1::uuid, $2::uuid, $3, $4, $5, $6, current_date + interval '30 day', $7, $8::jsonb)
        """,
        customer_id,
        tenant_id,
        "Pytest Customer",
        "customer@example.com",
        72.5,
        499.0,
        40,
        '{"weekly_active_users": 12, "trend": "down"}',
        tenant_id=tenant_id,
    )


async def _cleanup_live_tenant(tenant_id: str) -> None:
    from packages.db.src import close_pool
    from packages.redis.src import get_client, reset_client

    try:
        client = get_client()
        for key in client.scan_iter(f"*:{tenant_id}:*"):
            client.delete(key)
    except Exception:
        pass
    reset_client()
    await close_pool()


async def _require_live_backends() -> None:
    from packages.db.src import fetch_one
    from packages.redis.src import get_client

    try:
        row = await fetch_one("select 1 as ok", tenant_id=str(uuid.uuid4()))
        assert row is not None
        assert get_client().ping() is True
    except Exception as exc:  # pragma: no cover - environment-dependent skip
        pytest.skip(f"Live Postgres/Redis unavailable: {exc}")


@pytest.mark.asyncio
async def test_live_postgres_redis_capabilities(live_tenant_ids, monkeypatch):
    """Verify database, retrieval, approval, and Redis idempotency together."""
    await _require_live_backends()
    tenant_id, customer_id = live_tenant_ids
    await _seed_live_tenant(tenant_id, customer_id)

    from apps.tool_gateway.src.approval import persist_action_approval
    from apps.tool_gateway.src.contracts import ActionContext
    from apps.tool_gateway.src.service import ActionService
    from packages.knowledge_service.src.retrieve import retrieve_documents, store_document
    from packages.tool_system.src.tools.query_health import execute_query_health
    from packages.tool_system.src.tools.query_playbooks import execute_query_playbooks

    try:
        health = await execute_query_health(
            {"tenant_id": tenant_id, "customer_id": customer_id}
        )
        assert health.found is True
        assert health.customer_id == customer_id
        assert health.health_score == pytest.approx(72.5)

        stored = await store_document(
            tenant_id=tenant_id,
            collection="playbooks",
            doc_id="renewal-save-playbook",
            text="Use a renewal risk outreach playbook with a personalized check-in.",
            metadata={"customer_id": customer_id, "signal_type": "renewal_due", "title": "Renewal Save"},
        )
        assert stored is True
        docs = await retrieve_documents(
            tenant_id=tenant_id,
            query="renewal risk outreach",
            collection="playbooks",
            limit=3,
            metadata_filter={"customer_id": customer_id, "signal_type": "renewal_due"},
        )
        assert docs
        playbooks = await execute_query_playbooks(
            {
                "tenant_id": tenant_id,
                "customer_id": customer_id,
                "signal_type": "renewal_due",
                "query": "renewal risk outreach",
                "limit": 3,
            }
        )
        assert playbooks.matches
        assert playbooks.matches[0].metadata.get("title") == "Renewal Save"

        monkeypatch.setenv("APPROVAL_BACKEND", "postgres")
        monkeypatch.setenv("IDEMPOTENCY_BACKEND", "redis")
        payload = {
            "tenant_id": tenant_id,
            "customer_id": customer_id,
            "recipient_email": "user@example.com",
            "subject": "Hello",
            "body": "Safe message",
            "sender_name": "CSM",
        }
        await persist_action_approval(
            approval_id="approval-live-1",
            tenant_id=tenant_id,
            action_name="send_email",
            trace_id="trace-live-1",
            payload=payload,
        )
        context = ActionContext(
            tenant_id=tenant_id,
            approval_id="approval-live-1",
            idempotency_key="idem-live-1",
            actor="agent",
            trace_id="trace-live-1",
        )
        from apps.tool_gateway.src.approval import PostgresApprovalVerifier
        from packages.db.src import fetch_one

        approval = await fetch_one(
            "select approval_id, action_name, payload_hash, consumed_at from action_approvals "
            "where approval_id = $1 and action_name = $2 and tenant_id = $3::uuid",
            "approval-live-1",
            "send_email",
            tenant_id,
            tenant_id=tenant_id,
        )
        assert approval is not None
        assert approval["consumed_at"] is None
        from apps.tool_gateway.src.approval import stable_payload_hash

        assert approval["payload_hash"] == stable_payload_hash(payload)
        assert await PostgresApprovalVerifier().verify("send_email", context, payload)
        service = ActionService()
        first = await service.execute("send_email", payload, context)
        second = await service.execute("send_email", payload, context)
        assert first.success is True
        assert first.status == "executed"
        assert second.success is True
        assert second.status == "duplicate"
    finally:
        await _cleanup_live_tenant(tenant_id)


class _FakeLLM:
    async def complete(self, messages, **kwargs):
        from apps.agent_service.src.agent.llm_client import LLMResponse
        from packages.agent.src.types import LLMUsage

        if "rewrite" in str(kwargs.get("name", "")):
            text = '["renewal outreach", "churn prevention"]'
        elif "rerank" in str(kwargs.get("name", "")):
            text = "[0]"
        else:
            text = '["renewal outreach"]'
        return LLMResponse(text=text, model="fake", usage=LLMUsage())
