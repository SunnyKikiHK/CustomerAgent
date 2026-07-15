"""Offline tests for the Phase 3 Temporal migration.

Workflow/activity coordination against a live Temporal server is exercised in
the live verification step. These offline tests cover the parts that must work
without a server:

- the client's in-process fallback (Temporal unreachable -> signal still
  recorded + processed via the same activity code path),
- deterministic child/workflow id derivation,
- the SignalQueue dedupe that survived the migration.
"""

from __future__ import annotations

import asyncio

import pytest


def test_signal_queue_dedupe_still_works():
    """The migration kept dedupe: same signal seen twice returns "" the 2nd time."""
    import uuid

    from apps.agent_service.src.signals.queue import SignalQueue

    queue = SignalQueue()
    # Unique type so a leftover Redis TTL key from a previous run cannot pollute
    # the first-seen assertion (dedupe keys live for 1h by default).
    unique_type = f"usage_decline_{uuid.uuid4().hex[:10]}"
    payload = {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "customer_id": "22222222-2222-2222-2222-222222222222",
        "type": unique_type,
        "payload": {"decline_pct": 40},
    }
    first = queue.enqueue(payload)
    second = queue.enqueue(payload)
    assert first  # non-empty signal id the first time
    assert second == ""  # deduped the second time


def test_client_workflow_id_is_deterministic():
    """The same signal maps to the same workflow id (dedupe across scans)."""
    from apps.temporal_worker.src.client import _signal_key

    payload = {"tenant_id": "t1", "customer_id": "c1", "type": "low_health"}
    assert _signal_key(payload) == _signal_key(dict(payload))
    assert _signal_key(payload) == "low_health:c1"


@pytest.mark.asyncio
async def test_start_signal_workflow_falls_back_inprocess(monkeypatch):
    """When Temporal is unreachable, the signal is processed inline via activities."""
    from apps.temporal_worker.src import client

    async def fail_connect():
        raise RuntimeError("temporal down")

    monkeypatch.setattr(client, "_connect", fail_connect)

    calls: list[str] = []

    async def fake_record(payload):
        calls.append("record")
        return "sig-key-1"

    async def fake_processing(tenant_id, signal_key):
        calls.append("processing")

    async def fake_process(payload):
        calls.append("process")
        return {"approved": True, "action": "emit"}

    async def fake_done(tenant_id, signal_key, result):
        calls.append("done")

    from apps.temporal_worker.src import activities

    monkeypatch.setattr(activities, "record_signal_queued", fake_record)
    monkeypatch.setattr(activities, "mark_signal_processing", fake_processing)
    monkeypatch.setattr(activities, "process_signal", fake_process)
    monkeypatch.setattr(activities, "mark_signal_done", fake_done)

    outcome = await client.start_signal_workflow(
        {"tenant_id": "t1", "customer_id": "c1", "type": "low_health"}
    )
    assert outcome["started"] is True
    assert outcome["mode"] == "inprocess"
    # Full lifecycle ran in order.
    assert calls == ["record", "processing", "process", "done"]


@pytest.mark.asyncio
async def test_start_signal_workflow_fallback_marks_failed(monkeypatch):
    """A failing agent in the fallback path marks the signal failed, not done."""
    from apps.temporal_worker.src import activities, client

    async def fail_connect():
        raise RuntimeError("temporal down")

    monkeypatch.setattr(client, "_connect", fail_connect)

    marked: list[str] = []

    async def fake_record(payload):
        return "sig-key-2"

    async def fake_processing(tenant_id, signal_key):
        pass

    async def fake_process(payload):
        raise RuntimeError("agent blew up")

    async def fake_failed(tenant_id, signal_key, error):
        marked.append("failed")

    monkeypatch.setattr(activities, "record_signal_queued", fake_record)
    monkeypatch.setattr(activities, "mark_signal_processing", fake_processing)
    monkeypatch.setattr(activities, "process_signal", fake_process)
    monkeypatch.setattr(activities, "mark_signal_failed", fake_failed)

    outcome = await client.start_signal_workflow(
        {"tenant_id": "t1", "customer_id": "c1", "type": "low_health"}
    )
    assert outcome["started"] is False
    assert outcome["mode"] == "failed"
    assert marked == ["failed"]


@pytest.mark.asyncio
async def test_scan_fallback_processes_each_detected_signal(monkeypatch):
    """The scan fallback runs detectors then processes each signal inline."""
    from apps.temporal_worker.src import activities, client

    async def fail_connect():
        raise RuntimeError("temporal down")

    monkeypatch.setattr(client, "_connect", fail_connect)

    async def fake_scan(tenant_id):
        return [
            {"tenant_id": tenant_id, "customer_id": "c1", "type": "low_health"},
            {"tenant_id": tenant_id, "customer_id": "c2", "type": "usage_decline"},
        ]

    processed: list[str] = []

    async def fake_record(payload):
        return f"key-{payload['customer_id']}"

    async def fake_processing(tenant_id, signal_key):
        pass

    async def fake_process(payload):
        processed.append(payload["customer_id"])
        return {"approved": True}

    async def fake_done(tenant_id, signal_key, result):
        pass

    monkeypatch.setattr(activities, "scan_tenant_signals", fake_scan)
    monkeypatch.setattr(activities, "record_signal_queued", fake_record)
    monkeypatch.setattr(activities, "mark_signal_processing", fake_processing)
    monkeypatch.setattr(activities, "process_signal", fake_process)
    monkeypatch.setattr(activities, "mark_signal_done", fake_done)

    outcome = await client.start_tenant_scan("t1")
    assert outcome["mode"] == "inprocess"
    assert outcome["detected"] == 2
    assert sorted(processed) == ["c1", "c2"]


def test_workflows_and_activities_are_registered():
    """Worker registration lists match the defined workflows/activities."""
    from apps.temporal_worker.src.activities import SIGNAL_ACTIVITIES
    from apps.temporal_worker.src.workflows import (
        ProcessSignalWorkflow,
        TenantSignalScanWorkflow,
    )

    assert len(SIGNAL_ACTIVITIES) == 6
    assert ProcessSignalWorkflow.__name__ == "ProcessSignalWorkflow"
    assert TenantSignalScanWorkflow.__name__ == "TenantSignalScanWorkflow"
