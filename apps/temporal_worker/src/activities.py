"""Temporal activities: all nondeterministic work for signal/QBR/NPS workflows.

Workflows must stay deterministic, so every DB query, LLM call, email send, and
clock/uuid access lives here in activities. Each activity is a thin async
wrapper over existing business logic (detectors, the SignalOrchestrator, the
durable signal records) so the Temporal migration reuses, rather than
duplicates, the runtime.

Activities are registered on the worker in ``worker.py``. They are plain async
functions decorated with ``@activity.defn``; importing this module does not
require a running Temporal server.
"""

from __future__ import annotations

from typing import Any

from temporalio import activity

from apps.agent_service.src.signals.detectors import run_all_detectors
from apps.agent_service.src.signals.normalizer import normalize_signal_payload
from apps.agent_service.src.signals.records import mark_signal_status, record_signal


@activity.defn
async def scan_tenant_signals(tenant_id: str) -> list[dict[str, Any]]:
    """Run every detector for a tenant and return the signal payloads.

    Pure read scan; the workflow decides what to do with the results (start a
    ProcessSignalWorkflow per signal). Degrades to an empty list when the DB is
    unavailable (detectors already swallow DB failures).
    """
    return await run_all_detectors(tenant_id=tenant_id)


@activity.defn
async def record_signal_queued(payload: dict[str, Any]) -> str:
    """Persist a signal row in status='queued'; return its stable signal_key.

    Idempotent per signal_key: a Temporal retry that re-runs this activity will
    not create a duplicate row (the record layer inserts on signal_key).
    """
    signal = normalize_signal_payload(payload)
    await record_signal(
        signal,
        source=str(payload.get("source", "detector")),
        severity=str(payload.get("severity", "normal")),
        status="queued",
    )
    return signal.id


@activity.defn
async def mark_signal_processing(tenant_id: str, signal_key: str) -> None:
    """Transition a signal row to status='processing'."""
    await mark_signal_status(tenant_id=tenant_id, signal_key=signal_key, status="processing")


@activity.defn
async def process_signal(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the SignalOrchestrator for one signal and return a compact result.

    This is the heavy activity: it triggers the planner, subagents, compliance
    critic, and (post-approval) any external writes through the MCP gateway. The
    orchestrator itself owns idempotency for external actions, so a Temporal
    retry of this activity will not double-send an approved email.
    """
    # Imported lazily so importing activities (e.g. on the API side) does not pull
    # the full agent runtime graph.
    from apps.agent_service.src.agent.signal.signal_orchestrator import run_signal_agent
    from apps.agent_service.src.signals.queue import get_signal_queue

    queue = get_signal_queue()
    agent_input = queue.to_agent_input(payload)
    ctx = queue.to_session_context(payload)
    response = await run_signal_agent(agent_input, ctx)

    decision = getattr(response, "final_decision", None)
    return {
        "approved": bool(getattr(response, "approved", False)),
        "action": getattr(decision, "action", None) if decision else None,
        "response_text": getattr(response, "text", "") or "",
        "external_action_results": getattr(decision, "external_action_results", [])
        if decision
        else [],
    }


@activity.defn
async def mark_signal_done(
    tenant_id: str, signal_key: str, result: dict[str, Any]
) -> None:
    """Transition a signal row to status='done' with its result payload."""
    await mark_signal_status(
        tenant_id=tenant_id, signal_key=signal_key, status="done", result=result
    )


@activity.defn
async def mark_signal_failed(
    tenant_id: str, signal_key: str, error: str
) -> None:
    """Transition a signal row to status='failed' with a short error note."""
    await mark_signal_status(
        tenant_id=tenant_id,
        signal_key=signal_key,
        status="failed",
        result={"error": error},
    )


#: Activity function objects registered on the worker (kept in one place so the
#: worker and tests share a single source of truth).
SIGNAL_ACTIVITIES = [
    scan_tenant_signals,
    record_signal_queued,
    mark_signal_processing,
    process_signal,
    mark_signal_done,
    mark_signal_failed,
]


__all__ = [
    "scan_tenant_signals",
    "record_signal_queued",
    "mark_signal_processing",
    "process_signal",
    "mark_signal_done",
    "mark_signal_failed",
    "SIGNAL_ACTIVITIES",
]
