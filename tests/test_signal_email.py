"""Tests for the gated email delivery wiring added in the signal-finalization milestone.

Covers the shared ``send_approved_email`` helper (approval persist -> MCP action
dispatch) and the QBR/NPS activities that route customer-facing emails through
it, so a delivery is never a silent ``mark delivered`` without a real send.
"""

from __future__ import annotations

import pytest


async def _fake_execute_mcp_action(action_name, params, ctx, *, approval_id, idempotency_key, actor="agent"):
    return {
        "success": True,
        "status": "executed",
        "provider_message_id": "console-123",
        "action": action_name,
        "params": params,
        "approval_id": approval_id,
        "idempotency_key": idempotency_key,
    }


@pytest.mark.asyncio
async def test_send_approved_email_routes_through_gated_path(monkeypatch):
    """Approval is persisted and the email is dispatched via the MCP action path."""
    from apps.agent_service.src.agent.runtime import tool_dispatch
    from apps.tool_gateway.src import approval as approval_mod
    from apps.temporal_worker.src.email_delivery import send_approved_email

    persisted: list[dict] = []
    calls: list[dict] = []

    async def _persist(**kwargs):
        persisted.append(kwargs)

    async def _execute(action_name, params, ctx, *, approval_id, idempotency_key, actor="agent"):
        calls.append(
            {
                "action_name": action_name,
                "params": params,
                "approval_id": approval_id,
                "idempotency_key": idempotency_key,
                "tenant_id": ctx.tenant_id,
            }
        )
        return {"success": True, "provider_message_id": "console-123"}

    monkeypatch.setattr(approval_mod, "persist_action_approval", _persist)
    monkeypatch.setattr(tool_dispatch, "execute_mcp_action", _execute)

    payload = {
        "tenant_id": "t-1",
        "customer_id": "c-1",
        "recipient_email": "customer@example.com",
        "subject": "Your quarterly review",
        "body": "# Report\n\nHello.",
    }
    result = await send_approved_email(payload, trace_id="qbr:t-1:r-1")

    assert result["provider_message_id"] == "console-123"
    assert len(persisted) == 1
    approval = persisted[0]
    assert approval["action_name"] == "send_email"
    assert approval["tenant_id"] == "t-1"
    assert approval["payload"]["recipient_email"] == "customer@example.com"
    # Session identity is authoritative: tenant matches the payload, not a caller.
    assert calls[0]["tenant_id"] == "t-1"
    assert calls[0]["action_name"] == "send_email"
    assert calls[0]["approval_id"] and calls[0]["idempotency_key"]


@pytest.mark.asyncio
async def test_send_approved_email_is_deterministic_for_retries(monkeypatch):
    """A Temporal retry with the same trace id produces the same idempotency key."""
    from apps.agent_service.src.agent.runtime import tool_dispatch
    from apps.tool_gateway.src import approval as approval_mod
    from apps.temporal_worker.src.email_delivery import send_approved_email

    async def _persist(**kwargs):
        return None

    keys: list[str] = []

    async def _execute(action_name, params, ctx, *, approval_id, idempotency_key, actor="agent"):
        keys.append(idempotency_key)
        return {"success": True, "provider_message_id": "console-123"}

    monkeypatch.setattr(approval_mod, "persist_action_approval", _persist)
    monkeypatch.setattr(tool_dispatch, "execute_mcp_action", _execute)

    payload = {
        "tenant_id": "t-1",
        "customer_id": "c-1",
        "recipient_email": "customer@example.com",
        "subject": "hi",
        "body": "hello",
    }
    await send_approved_email(payload, trace_id="nps:t-1:c-1:s-1")
    await send_approved_email(payload, trace_id="nps:t-1:c-1:s-1")

    assert len(keys) == 2
    assert keys[0] == keys[1]


@pytest.mark.asyncio
async def test_qbr_email_marks_failed_without_recipient(monkeypatch):
    """No recipient -> the report is marked failed, not silently delivered."""
    from apps.temporal_worker.src import qbr_activities
    from packages.knowledge_service.src import qbr

    statuses: list[dict] = []

    async def _mark(**kwargs):
        statuses.append(kwargs)
        return True

    monkeypatch.setattr(qbr, "mark_report_status", _mark)

    result = await qbr_activities.deliver_qbr_email(
        "t-1", "report-1", "# Report", None
    )

    assert result["status"] == "failed"
    assert statuses[0]["status"] == "failed"
    assert "no QBR recipient" in statuses[0]["error"]


@pytest.mark.asyncio
async def test_create_and_send_survey_skips_send_without_email(monkeypatch):
    """A customer without an email still gets a survey, but sent=False."""
    from apps.temporal_worker.src import nps_activities
    from packages.knowledge_service.src import nps

    async def _create(**kwargs):
        return {"id": "survey-1", "customer_id": "c-1", "status": "created"}

    monkeypatch.setattr(nps, "create_survey", _create)

    result = await nps_activities.create_and_send_survey("t-1", "c-1", None)

    assert result["created"] is True
    assert result["sent"] is False
    assert result["reason"] == "no recipient email"


@pytest.mark.asyncio
async def test_create_and_send_survey_sends_when_email_present(monkeypatch):
    """A customer with an email triggers the gated send and marks the survey sent."""
    from apps.temporal_worker.src import nps_activities
    from apps.temporal_worker.src import email_delivery
    from packages.knowledge_service.src import nps

    async def _create(**kwargs):
        return {"id": "survey-1", "customer_id": "c-1", "status": "created"}

    sent: list[str] = []

    async def _mark_sent(**kwargs):
        sent.append(kwargs["survey_id"])
        return True

    delivered: list[dict] = []

    async def _send(payload, *, trace_id, sender_name="Customer Success Agent"):
        delivered.append({"payload": payload, "trace_id": trace_id})
        return {"success": True, "provider_message_id": "console-1"}

    monkeypatch.setattr(nps, "create_survey", _create)
    monkeypatch.setattr(nps, "mark_survey_sent", _mark_sent)
    monkeypatch.setattr(email_delivery, "send_approved_email", _send)

    result = await nps_activities.create_and_send_survey(
        "t-1", "c-1", "customer@example.com"
    )

    assert result["sent"] is True
    assert sent == ["survey-1"]
    assert delivered[0]["payload"]["recipient_email"] == "customer@example.com"
    assert "survey-1" in delivered[0]["payload"]["body"]
