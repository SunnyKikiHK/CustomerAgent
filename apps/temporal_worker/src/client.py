"""Temporal client helpers for starting signal workflows from the API.

The API/detectors no longer push to a Redis list; instead they ask Temporal to
run a ``ProcessSignalWorkflow`` (or a ``TenantSignalScanWorkflow``). Temporal
owns durability, retries, and scheduling.

To keep the API usable when Temporal is not running (local dev, offline tests),
``start_signal_workflow`` degrades gracefully: if the Temporal server is
unreachable, it falls back to running the signal in-process through the same
activity code path. The return value reports which path was taken.
"""

from __future__ import annotations

import os
from typing import Any

_TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "customer-agent-signals")


def temporal_target() -> str:
    """Return the Temporal frontend address (host:port)."""
    return os.getenv("TEMPORAL_ADDRESS", "localhost:7233")


def temporal_namespace() -> str:
    """Return the Temporal namespace."""
    return os.getenv("TEMPORAL_NAMESPACE", "default")


def task_queue() -> str:
    """Return the task queue workflows and the worker share."""
    return _TASK_QUEUE


async def _connect() -> Any:
    """Connect to Temporal, or raise if the SDK/server is unavailable."""
    from temporalio.client import Client

    return await Client.connect(temporal_target(), namespace=temporal_namespace())


def _signal_key(payload: dict[str, Any]) -> str:
    """Deterministic child/workflow id component (mirrors the workflow)."""
    signal_type = str(payload.get("type", "signal"))
    customer_id = str(payload.get("customer_id", ""))
    return f"{signal_type}:{customer_id}"


async def start_signal_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    """Start a ProcessSignalWorkflow for one signal.

    Returns a dict describing the outcome:
      - {"started": True, "mode": "temporal", "workflow_id": ...} on success
      - {"started": True, "mode": "inprocess", ...} when Temporal is down and the
        signal was processed inline as a fallback
      - {"started": False, "mode": "failed", "error": ...} if both paths fail
    """
    tenant_id = str(payload.get("tenant_id", ""))
    workflow_id = f"signal:{tenant_id}:{_signal_key(payload)}"
    try:
        client = await _connect()
    except Exception as exc:
        return await _run_inprocess(payload, reason=f"temporal unavailable: {type(exc).__name__}")

    from apps.temporal_worker.src.workflows import ProcessSignalWorkflow

    try:
        handle = await client.start_workflow(
            ProcessSignalWorkflow.run,
            payload,
            id=workflow_id,
            task_queue=task_queue(),
        )
        return {"started": True, "mode": "temporal", "workflow_id": handle.id}
    except Exception as exc:
        # Includes WorkflowAlreadyStartedError: a duplicate signal is already
        # being processed, which is the intended dedupe behavior.
        name = type(exc).__name__
        if "AlreadyStarted" in name:
            return {"started": False, "mode": "duplicate", "workflow_id": workflow_id}
        return await _run_inprocess(payload, reason=f"start failed: {name}")


async def _run_inprocess(payload: dict[str, Any], *, reason: str) -> dict[str, Any]:
    """Fallback: process the signal inline using the same activity logic."""
    from apps.temporal_worker.src import activities

    tenant_id = str(payload.get("tenant_id", ""))
    signal_key = await activities.record_signal_queued(payload)
    await activities.mark_signal_processing(tenant_id, signal_key)
    try:
        result = await activities.process_signal(payload)
    except Exception as exc:
        await activities.mark_signal_failed(tenant_id, signal_key, type(exc).__name__)
        return {"started": False, "mode": "failed", "error": str(exc)[:200], "reason": reason}
    await activities.mark_signal_done(tenant_id, signal_key, result)
    return {"started": True, "mode": "inprocess", "signal_key": signal_key, "reason": reason}


async def start_tenant_scan(tenant_id: str) -> dict[str, Any]:
    """Start a TenantSignalScanWorkflow, falling back to inline scan+process."""
    try:
        client = await _connect()
    except Exception:
        return await _scan_inprocess(tenant_id)

    from apps.temporal_worker.src.workflows import TenantSignalScanWorkflow

    try:
        handle = await client.start_workflow(
            TenantSignalScanWorkflow.run,
            tenant_id,
            id=f"scan:{tenant_id}",
            task_queue=task_queue(),
        )
        return {"started": True, "mode": "temporal", "workflow_id": handle.id}
    except Exception:
        return await _scan_inprocess(tenant_id)


async def _scan_inprocess(tenant_id: str) -> dict[str, Any]:
    """Fallback: run detectors and process each signal inline."""
    from apps.temporal_worker.src import activities

    detected = await activities.scan_tenant_signals(tenant_id)
    results = [await _run_inprocess(payload, reason="scan inprocess") for payload in detected]
    started = sum(1 for r in results if r.get("started"))
    return {"started": True, "mode": "inprocess", "detected": len(detected), "processed": started}


__all__ = [
    "temporal_target",
    "temporal_namespace",
    "task_queue",
    "start_signal_workflow",
    "start_tenant_scan",
]
