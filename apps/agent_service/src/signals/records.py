"""Durable `signals` table persistence for the dashboard and audit trail.

Every enqueued signal is recorded here so the API/dashboard can list signals and
show their processing status. Best-effort: a DB outage must not stop a signal
from being queued and processed in memory.
"""

from __future__ import annotations

import json
from typing import Any

from packages.agent.src.types import CustomerSignal
from packages.db.src import PostgresConfigError, execute, fetch_all


async def record_signal(
    signal: CustomerSignal,
    *,
    source: str = "manual",
    severity: str = "normal",
    status: str = "queued",
) -> bool:
    """Insert a signal row (idempotent on signal_key). Returns success."""
    try:
        status_text = await execute(
            """
            insert into signals (
                tenant_id, customer_id, signal_key, type, severity,
                source, status, payload
            )
            values ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8::jsonb)
            """,
            signal.tenant_id,
            signal.customer_id,
            signal.id,
            signal.type,
            severity,
            source,
            status,
            signal.payload,
            tenant_id=signal.tenant_id,
        )
        return status_text.startswith("INSERT")
    except PostgresConfigError:
        return False
    except Exception:
        return False


async def mark_signal_status(
    *,
    tenant_id: str,
    signal_key: str,
    status: str,
    result: dict[str, Any] | None = None,
) -> bool:
    """Update a signal row's status (and result) after processing."""
    try:
        status_text = await execute(
            """
            update signals
            set status = $2,
                result = coalesce($3::jsonb, result),
                processed_at = case when $2 in ('done', 'failed') then now() else processed_at end
            where signal_key = $1
            """,
            signal_key,
            status,
            result if result is not None else None,
            tenant_id=tenant_id,
        )
        return status_text.startswith("UPDATE")
    except PostgresConfigError:
        return False
    except Exception:
        return False


async def list_signals(*, tenant_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Return recent signals for a tenant (newest first)."""
    try:
        rows = await fetch_all(
            """
            select id::text, customer_id::text, signal_key, type, severity,
                   source, status, payload, result, created_at, processed_at
            from signals
            order by created_at desc
            limit $1
            """,
            limit,
            tenant_id=tenant_id,
        )
    except PostgresConfigError:
        return []
    except Exception:
        return []
    return [
        {
            "id": row["id"],
            "customer_id": row["customer_id"],
            "signal_key": row["signal_key"],
            "type": row["type"],
            "severity": row["severity"],
            "source": row["source"],
            "status": row["status"],
            "payload": _as_dict(row["payload"]),
            "result": _as_dict(row["result"]),
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "processed_at": row["processed_at"].isoformat() if row["processed_at"] else None,
        }
        for row in rows
    ]


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


__all__ = ["record_signal", "mark_signal_status", "list_signals"]
