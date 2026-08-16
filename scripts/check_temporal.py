"""Probe whether Temporal is actually working end to end.

Layers checked (in order):
  1. Server reachable   -- ``Client.connect`` to ``TEMPORAL_ADDRESS``.
  2. Workflow started   -- submit a deterministic ``ProbeWorkflow``.
  3. Worker consuming   -- await the workflow's ``result()``; this only returns
                           when a worker executes the workflow to completion.

A workflow that starts but never completes means the server is up but no worker
is polling the queue (wrong ``TEMPORAL_TASK_QUEUE``, worker not running, or a
stale worker that predates ``ProbeWorkflow``).

Exit codes:
  0  fully working (server reachable + worker consumed the probe)
  1  server unreachable (Temporal not up / wrong TEMPORAL_ADDRESS)
  2  workflow started but no worker completed it within the timeout
  3  other error (missing SDK, start failure, ...)

Why this matters: the signal API silently falls back to in-process execution
(``start_signal_workflow`` returns ``mode: "inprocess"``) when Temporal is down,
so "the app runs and signals get processed" does NOT prove Temporal is in use.
This probe talks to Temporal directly and cannot be masked by that fallback.

Usage (repo root, inside the WSL venv, stack up via ``./start.sh``):

    python scripts/check_temporal.py
    python scripts/check_temporal.py --timeout 30
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _config() -> tuple[str, str, str]:
    """Return (target, namespace, task_queue) from the client's own resolution."""
    from apps.temporal_worker.src.client import (
        task_queue,
        temporal_namespace,
        temporal_target,
    )

    return temporal_target(), temporal_namespace(), task_queue()


async def _run(timeout_seconds: float) -> int:
    target, namespace, queue = _config()
    print(f"Temporal config: address={target}  namespace={namespace}  task_queue={queue}")

    try:
        from temporalio.client import Client
    except ImportError:  # pragma: no cover - only if run outside the venv
        print("FAIL: temporalio SDK not importable. Activate the WSL venv first.")
        return 3

    # Layer 1: server reachable.
    try:
        client = await Client.connect(target, namespace=namespace)
    except Exception as exc:  # noqa: BLE001 - report any connect failure
        print(f"[1/3] SERVER UNREACHABLE  {type(exc).__name__}: {exc}")
        print("       => Temporal server is not up, or TEMPORAL_ADDRESS is wrong.")
        print("          Start it with: ./start.sh   (service: agent_temporal, port 7233)")
        return 1
    print(f"[1/3] server reachable    OK  (connected to {target}, namespace '{namespace}')")

    # Layer 2: start a probe workflow.
    from apps.temporal_worker.src.workflows import ProbeWorkflow

    workflow_id = f"check-temporal:{uuid.uuid4().hex[:8]}"
    try:
        handle = await client.start_workflow(
            ProbeWorkflow.run,
            {"ping": "pong"},
            id=workflow_id,
            task_queue=queue,
        )
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        if "AlreadyStarted" in name:
            print(f"[2/3] workflow already started  (id collision: {workflow_id})")
            return 3
        print(f"[2/3] START FAILED          {name}: {exc}")
        return 3
    print(f"[2/3] workflow started     OK  (id={handle.id})")

    # Layer 3: worker consumes to completion.
    try:
        result = await handle.result(timeout=timedelta(seconds=timeout_seconds))
    except Exception as exc:  # noqa: BLE001 - TimeoutError and any workflow failure
        print(
            f"[3/3] WORKER NOT CONSUMING  no result within {timeout_seconds}s "
            f"({type(exc).__name__}: {exc})"
        )
        print("       => The workflow started but no worker completed it. Check:")
        print("          - a worker is running: python -m apps.temporal_worker.src.worker")
        print("            (or the agent-worker container is up and not crash-looping)")
        print("          - the worker and client use the SAME TEMPORAL_TASK_QUEUE")
        print(f"            (this probe targeted '{queue}')")
        print("          - the worker runs CURRENT code that registers ProbeWorkflow")
        print("            (rebuild/restart the worker if it predates this probe)")
        return 2

    print("[3/3] worker consumed      OK")
    print(f"       result = {result}")
    print("Temporal is working end to end: server reachable AND worker consuming the queue.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Temporal end to end.")
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for the worker to complete the probe (default 30).",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.timeout))


if __name__ == "__main__":
    sys.exit(main())
