"""
RQ worker entry for signal automation.

1. A producer calls enqueue_signal(...).
2. SignalQueue.enqueue() pushes the payload to the Redis list signal:queue using RPUSH.
3. The worker polls that queue using LPOP.
4. Once dequeued, it immediately runs the signal agent.

All three use enqueue_signal(), which:

deduplicates by signal ID for one hour
pushes new items to Redis list signal:queue
records the signal with status queued
"""

from __future__ import annotations

import asyncio
import logging

from apps.agent_service.src.agent.signal.signal_orchestrator import run_signal_agent
from apps.agent_service.src.signals.queue import get_signal_queue

logger = logging.getLogger(__name__)


def process_signal_job(payload: dict) -> dict:
    """RQ-compatible sync wrapper around the async signal orchestrator."""
    # Queue workers call regular functions, so create an event loop for this job.
    return asyncio.run(_process_signal_job_async(payload))


async def _process_signal_job_async(payload: dict) -> dict:
    """Run one signal end to end and update its durable status."""
    from apps.agent_service.src.signals.records import mark_signal_status

    queue = get_signal_queue()
    # Normalize the raw queue payload into the contracts used by the orchestrator.
    agent_input = queue.to_agent_input(payload)
    ctx = queue.to_session_context(payload)
    signal_key = agent_input.signal.id
    # Expose the in-progress state to dashboards before the agent starts.

    await mark_signal_status(
        tenant_id=ctx.tenant_id, signal_key=signal_key, status="processing"
    )
    try:
        # Run Planner -> subagents -> compliance critic for this signal.
        response = await run_signal_agent(agent_input, ctx)
    except Exception as exc:  # pragma: no cover - defensive worker guard
        # Store only the exception type; raw errors may contain credentials or PII.
        await mark_signal_status(
            tenant_id=ctx.tenant_id,
            signal_key=signal_key,
            status="failed",
            result={"error": f"{type(exc).__name__}"},
        )
        raise

    await mark_signal_status(
        tenant_id=ctx.tenant_id,
        signal_key=signal_key,
        status="done" if response.approved else "failed",
        result={"approved": response.approved, "text": response.text[:1000]},
    )
    logger.info(
        "signal_job_complete signal_id=%s approved=%s",
        ctx.signal_id,
        response.approved,
    )
    return {
        "approved": response.approved,
        "text": response.text,
        "signal_id": ctx.signal_id,
    }


def run_worker_loop(max_jobs: int = 1) -> int:
    """Process up to max_jobs queued signals in-process (test/local helper)."""
    queue = get_signal_queue()
    processed = 0
    # Stop early when no queued signal is available; otherwise honor max_jobs.
    for _ in range(max_jobs):
        payload = queue.dequeue()
        if payload is None:
            break
        process_signal_job(payload)
        processed += 1
    return processed


def run_forever(poll_interval_seconds: float = 2.0) -> None:
    """Continuously drain the signal queue (container worker entrypoint)."""
    import time

    queue = get_signal_queue()
    logger.info("signal worker started; polling every %ss", poll_interval_seconds)
    # Poll forever so the container remains ready for newly queued signals.
    while True:
        payload = queue.dequeue()
        if payload is None:
            time.sleep(poll_interval_seconds)
            continue
        try:
            process_signal_job(payload)
        except Exception:  # pragma: no cover - keep the worker alive
            logger.exception("signal job failed")


__all__ = ["process_signal_job", "run_worker_loop", "run_forever"]


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run_forever()
