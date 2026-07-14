"""Signal detectors over the customers table.

Detectors are pure DB scans that yield signal payloads. They are triggered on
demand (``POST /signals/scan``); there is no hidden background scheduler in this
milestone (Temporal is deferred). Each yielded payload is a plain dict suitable
for ``SignalQueue.enqueue`` / ``normalize_signal_payload``.
"""

from __future__ import annotations

import os
from typing import Any

from packages.db.src import PostgresConfigError, fetch_all

#: Defaults; overridable via env for tuning without a code change.
RENEWAL_WINDOW_DAYS = int(os.getenv("SIGNAL_RENEWAL_WINDOW_DAYS", "60"))
LOW_HEALTH_THRESHOLD = float(os.getenv("SIGNAL_LOW_HEALTH_THRESHOLD", "50"))


async def detect_renewal_risk(
    *, tenant_id: str, window_days: int | None = None
) -> list[dict[str, Any]]:
    """Customers whose renewal_date falls within the risk window."""
    window = window_days if window_days is not None else RENEWAL_WINDOW_DAYS
    try:
        rows = await fetch_all(
            """
            select id::text as customer_id, name, health_score, renewal_date,
                   (renewal_date - current_date) as days_to_renewal
            from customers
            where renewal_date is not null
              and renewal_date >= current_date
              and renewal_date <= current_date + ($1 || ' days')::interval
            """,
            str(window),
            tenant_id=tenant_id,
        )
    except PostgresConfigError:
        return []
    signals: list[dict[str, Any]] = []
    for row in rows:
        days = row["days_to_renewal"]
        days_int = days.days if hasattr(days, "days") else int(days) if days is not None else None
        signals.append(
            _signal(
                tenant_id,
                row["customer_id"],
                "renewal_risk",
                severity="high",
                payload={
                    "days_to_renewal": days_int,
                    "renewal_date": row["renewal_date"].isoformat() if row["renewal_date"] else None,
                    "health_score": float(row["health_score"]) if row["health_score"] is not None else None,
                    "customer_name": row["name"],
                },
            )
        )
    return signals


async def detect_low_health(
    *, tenant_id: str, threshold: float | None = None
) -> list[dict[str, Any]]:
    """Customers whose health_score is below the at-risk threshold."""
    limit_value = threshold if threshold is not None else LOW_HEALTH_THRESHOLD
    try:
        rows = await fetch_all(
            """
            select id::text as customer_id, name, health_score
            from customers
            where health_score is not null
              and health_score < $1
            """,
            limit_value,
            tenant_id=tenant_id,
        )
    except PostgresConfigError:
        return []
    return [
        _signal(
            tenant_id,
            row["customer_id"],
            "low_health",
            severity="high",
            payload={
                "health_score": float(row["health_score"]) if row["health_score"] is not None else None,
                "threshold": limit_value,
                "customer_name": row["name"],
            },
        )
        for row in rows
    ]


async def run_all_detectors(*, tenant_id: str) -> list[dict[str, Any]]:
    """Run every detector and return the combined list of signal payloads."""
    renewal = await detect_renewal_risk(tenant_id=tenant_id)
    low_health = await detect_low_health(tenant_id=tenant_id)
    return [*renewal, *low_health]


def _signal(
    tenant_id: str,
    customer_id: str,
    signal_type: str,
    *,
    severity: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "customer_id": customer_id,
        "type": signal_type,
        "severity": severity,
        "source": "detector",
        "payload": payload,
    }


__all__ = [
    "detect_renewal_risk",
    "detect_low_health",
    "run_all_detectors",
    "RENEWAL_WINDOW_DAYS",
    "LOW_HEALTH_THRESHOLD",
]
