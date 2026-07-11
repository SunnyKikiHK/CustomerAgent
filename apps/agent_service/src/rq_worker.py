"""RQ worker entry for signal automation."""

from __future__ import annotations

import asyncio
import logging

from apps.agent_service.src.agent.signal.signal_orchestrator import run_signal_agent
from apps.agent_service.src.signals.queue import get_signal_queue

logger = logging.getLogger(__name__)


def process_signal_job(payload: dict) -> dict:
    """RQ-compatible sync wrapper around the async signal orchestrator."""
    queue = get_signal_queue()
    agent_input = queue.to_agent_input(payload)
    ctx = queue.to_session_context(payload)
    response = asyncio.run(run_signal_agent(agent_input, ctx))
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
    for _ in range(max_jobs):
        payload = queue.dequeue()
        if payload is None:
            break
        process_signal_job(payload)
        processed += 1
    return processed


__all__ = ["process_signal_job", "run_worker_loop"]
