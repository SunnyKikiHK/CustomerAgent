"""Temporal workflows coordinating signal detection and processing.

Workflows are deterministic: they contain no DB/LLM/clock/uuid access, only
calls to activities (via ``workflow.execute_activity``) and deterministic
control flow. All nondeterministic work lives in ``activities.py``.

Two workflows:

- ``ProcessSignalWorkflow`` — the durable replacement for one iteration of the
  old Redis-polling loop: record -> processing -> run agent -> done/failed, with
  automatic retries on transient failures. Idempotency for external actions is
  owned by the orchestrator/gateway, so retries never double-send.
- ``TenantSignalScanWorkflow`` — runs detectors for a tenant and starts one
  ProcessSignalWorkflow per detected signal, using a deterministic child
  workflow id (``signal:{tenant}:{signal_key}``) so the same signal firing in
  overlapping scans does not process twice.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.workflow import ParentClosePolicy

# Activities are referenced by name inside execute_activity calls, but importing
# the functions lets us pass typed references and keeps refactors safe. The
# sandbox allows this import because activities are not invoked at import time.
with workflow.unsafe.imports_passed_through():
    from apps.temporal_worker.src import activities


#: Retry policy for the heavy processing activity: retry transient failures a
#: few times with backoff. Non-retryable business outcomes (e.g. compliance
#: block) are returned as data, not raised, so they do not trigger retries.
_PROCESS_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=4,
)

#: Short retry for the quick status/record activities.
_QUICK_RETRY = RetryPolicy(maximum_attempts=5)


@workflow.defn
class ProcessSignalWorkflow:
    """Durably process one customer signal end to end."""

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = str(payload.get("tenant_id", ""))

        signal_key = await workflow.execute_activity(
            activities.record_signal_queued,
            payload,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_QUICK_RETRY,
        )

        await workflow.execute_activity(
            activities.mark_signal_processing,
            args=[tenant_id, signal_key],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_QUICK_RETRY,
        )

        try:
            result = await workflow.execute_activity(
                activities.process_signal,
                payload,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=_PROCESS_RETRY,
            )
        except Exception as exc:  # activity exhausted retries
            await workflow.execute_activity(
                activities.mark_signal_failed,
                args=[tenant_id, signal_key, f"{type(exc).__name__}"],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_QUICK_RETRY,
            )
            raise

        await workflow.execute_activity(
            activities.mark_signal_done,
            args=[tenant_id, signal_key, result],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_QUICK_RETRY,
        )
        return {"signal_key": signal_key, **result}


@workflow.defn
class TenantSignalScanWorkflow:
    """Scan a tenant for signals and fan out one child workflow per signal."""

    @workflow.run
    async def run(self, tenant_id: str) -> dict[str, Any]:
        detected = await workflow.execute_activity(
            activities.scan_tenant_signals,
            tenant_id,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_QUICK_RETRY,
        )

        started: list[str] = []
        for payload in detected:
            signal_key = _signal_key(payload)
            child_id = f"signal:{tenant_id}:{signal_key}"
            # Deterministic child id + reject-duplicate semantics: if a signal is
            # already being processed from an overlapping scan, starting it again
            # is a no-op rather than duplicate work.
            await workflow.start_child_workflow(
                ProcessSignalWorkflow.run,
                payload,
                id=child_id,
                parent_close_policy=ParentClosePolicy.ABANDON,
            )
            started.append(child_id)

        return {"tenant_id": tenant_id, "detected": len(detected), "started": started}


def _signal_key(payload: dict[str, Any]) -> str:
    """Deterministic signal key from a detector payload (mirrors normalizer).

    Kept dependency-free and deterministic so it is safe to call inside the
    workflow sandbox. Uses the same (type, customer) shape the normalizer uses
    for dedupe; the authoritative key is recomputed in the record activity.
    """
    signal_type = str(payload.get("type", "signal"))
    customer_id = str(payload.get("customer_id", ""))
    return f"{signal_type}:{customer_id}"


__all__ = ["ProcessSignalWorkflow", "TenantSignalScanWorkflow"]
