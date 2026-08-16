"""Temporal worker: registers signal workflows + activities and runs the poll loop.

Run this process instead of the old Redis-polling ``rq_worker``:

    python -m apps.temporal_worker.src.worker

It connects to the Temporal frontend (``TEMPORAL_ADDRESS``), registers the
workflows and activities on the shared task queue, and blocks serving them.
Optionally it can also (re)create a Temporal Schedule that runs the per-tenant
scan on an interval, replacing the old ad-hoc detector cron.
"""

from __future__ import annotations

import asyncio
import logging
import os

from apps.temporal_worker.src.activities import SIGNAL_ACTIVITIES
from apps.temporal_worker.src.client import (
    task_queue,
    temporal_namespace,
    temporal_target,
)
from apps.temporal_worker.src.nps_activities import NPS_ACTIVITIES
from apps.temporal_worker.src.nps_workflows import NpsCampaignWorkflow
from apps.temporal_worker.src.qbr_activities import QBR_ACTIVITIES
from apps.temporal_worker.src.qbr_workflows import GenerateTenantQbrWorkflow
from apps.temporal_worker.src.workflows import (
    ProcessSignalWorkflow,
    ProbeWorkflow,
    TenantSignalScanWorkflow,
)

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _ensure_scan_schedule(client, tenant_ids: list[str]) -> None:
    """Create/replace a Temporal Schedule that scans each tenant periodically.

    Best-effort: schedule creation failures (e.g. already exists) are logged and
    ignored so the worker still serves workflow/activity tasks.
    """
    from temporalio.client import (
        Schedule,
        ScheduleActionStartWorkflow,
        ScheduleIntervalSpec,
        ScheduleSpec,
    )
    from datetime import timedelta

    interval_minutes = int(os.getenv("SIGNAL_SCAN_INTERVAL_MINUTES", "60"))
    for tenant_id in tenant_ids:
        schedule_id = f"scan-schedule:{tenant_id}"
        try:
            await client.create_schedule(
                schedule_id,
                Schedule(
                    action=ScheduleActionStartWorkflow(
                        TenantSignalScanWorkflow.run,
                        tenant_id,
                        id=f"scan:{tenant_id}",
                        task_queue=task_queue(),
                    ),
                    spec=ScheduleSpec(
                        intervals=[ScheduleIntervalSpec(every=timedelta(minutes=interval_minutes))]
                    ),
                ),
            )
            logger.info("Created scan schedule for tenant %s", tenant_id)
        except Exception as exc:  # already exists or transient
            logger.info("Scan schedule for %s not created (%s)", tenant_id, type(exc).__name__)


async def main() -> None:
    """Connect to Temporal and run the worker until interrupted."""
    from temporalio.client import Client
    from temporalio.worker import Worker

    client = await Client.connect(temporal_target(), namespace=temporal_namespace())

    # Optional scheduled scans for a comma-separated tenant allowlist.
    scan_tenants = [t.strip() for t in os.getenv("SIGNAL_SCAN_TENANTS", "").split(",") if t.strip()]
    if scan_tenants:
        await _ensure_scan_schedule(client, scan_tenants)

    worker = Worker(
        client,
        task_queue=task_queue(),
        workflows=[
            ProcessSignalWorkflow,
            TenantSignalScanWorkflow,
            NpsCampaignWorkflow,
            GenerateTenantQbrWorkflow,
            ProbeWorkflow,
        ],
        activities=[*SIGNAL_ACTIVITIES, *NPS_ACTIVITIES, *QBR_ACTIVITIES],
    )
    logger.info(
        "Temporal worker started (target=%s ns=%s queue=%s)",
        temporal_target(),
        temporal_namespace(),
        task_queue(),
    )
    await worker.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Temporal worker stopped")
