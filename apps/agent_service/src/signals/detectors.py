"""Signal detectors over the customers table.

Detectors are pure DB scans that yield signal payloads. They are triggered on
demand (``POST /signals/scan``) and by the Temporal tenant-scan workflow. Each
yielded payload is a plain dict suitable for ``normalize_signal_payload`` and
the signal-processing path.

Every detector routes its query through ``_safe_fetch_all`` so a scan degrades
to "no signals" on any DB failure (unconfigured, unreachable, or auth error)
rather than crashing the caller.
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any

from packages.db.src import PostgresConfigError, fetch_all

#: Defaults; overridable via env for tuning without a code change.
RENEWAL_WINDOW_DAYS = int(os.getenv("SIGNAL_RENEWAL_WINDOW_DAYS", "60"))
LOW_HEALTH_THRESHOLD = float(os.getenv("SIGNAL_LOW_HEALTH_THRESHOLD", "50"))

#: Additional tuning thresholds for the simulator-driven detectors.
USAGE_DECLINE_PCT = float(os.getenv("SIGNAL_USAGE_DECLINE_PCT", "30"))
SUPPORT_TICKET_THRESHOLD = int(os.getenv("SIGNAL_SUPPORT_TICKET_THRESHOLD", "5"))


async def _safe_fetch_all(query: str, *args: Any, tenant_id: str) -> list[Any]:
    """Run a tenant-scoped query, degrading to an empty list on any DB failure.

    A detector scan must never crash the caller: an unconfigured DB
    (``PostgresConfigError``) or an unreachable/misconfigured one (asyncpg
    connection errors) both mean "no signals right now", not an exception.
    """
    try:
        return list(await fetch_all(query, *args, tenant_id=tenant_id))
    except PostgresConfigError:
        return []
    except Exception:
        # Connection refused, auth failure, timeout: treat as no data.
        return []


async def detect_renewal_risk(
    *, tenant_id: str, window_days: int | None = None
) -> list[dict[str, Any]]:
    """Customers whose renewal_date falls within the risk window."""
    window = window_days if window_days is not None else RENEWAL_WINDOW_DAYS
    rows = await _safe_fetch_all(
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
    rows = await _safe_fetch_all(
        """
        select id::text as customer_id, name, health_score
        from customers
        where health_score is not null
          and health_score < $1
        """,
        limit_value,
        tenant_id=tenant_id,
    )
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


def usage_decline_pct(usage_trend: dict[str, Any]) -> float | None:
    """Return the percent drop in active users, or None when not computable.

    Pure and deterministic so it can be unit-tested without a database. Uses the
    flat ``previous_active_users`` / ``current_active_users`` shape produced by
    the simulator (see ``customers.merge_usage_trend``). A positive result means
    usage fell by that percentage; zero or negative means flat or growing.
    """
    if not isinstance(usage_trend, dict):
        return None
    previous = usage_trend.get("previous_active_users")
    current = usage_trend.get("current_active_users")
    if previous is None or current is None:
        return None
    try:
        previous_f = float(previous)
        current_f = float(current)
    except (TypeError, ValueError):
        return None
    if previous_f <= 0:
        return None
    return (previous_f - current_f) / previous_f * 100.0


async def detect_usage_decline(
    *, tenant_id: str, pct_threshold: float | None = None
) -> list[dict[str, Any]]:
    """Customers whose active-user count dropped beyond the decline threshold."""
    threshold = pct_threshold if pct_threshold is not None else USAGE_DECLINE_PCT
    rows = await _safe_fetch_all(
        "select id::text as customer_id, name, health_score, renewal_date, usage_trend"
        " from customers where usage_trend is not null",
        tenant_id=tenant_id,
    )
    signals: list[dict[str, Any]] = []
    for row in rows:
        trend = _as_dict(row["usage_trend"])
        drop = usage_decline_pct(trend)
        if drop is None or drop < threshold:
            continue
        renewal_date = row["renewal_date"]
        days_to_renewal = (renewal_date - date.today()).days if renewal_date else None
        # The base usage_decline signal is always emitted so the dashboard sees
        # the drop; a decline coinciding with an imminent renewal also emits a
        # more urgent composite signal below.
        signals.append(
            _signal(
                tenant_id,
                row["customer_id"],
                "usage_decline",
                severity="high" if drop >= threshold * 1.5 else "normal",
                payload={
                    "decline_pct": round(drop, 1),
                    "threshold_pct": threshold,
                    "previous_active_users": trend.get("previous_active_users"),
                    "current_active_users": trend.get("current_active_users"),
                    "customer_name": row["name"],
                },
            )
        )
        if days_to_renewal is not None and 0 <= days_to_renewal <= RENEWAL_WINDOW_DAYS:
            signals.append(
                _signal(
                    tenant_id,
                    row["customer_id"],
                    "renewal_usage_risk",
                    severity="critical",
                    payload={
                        "decline_pct": round(drop, 1),
                        "days_to_renewal": days_to_renewal,
                        "customer_name": row["name"],
                    },
                )
            )
    return signals


async def detect_support_ticket_spike(
    *, tenant_id: str, threshold: int | None = None
) -> list[dict[str, Any]]:
    """Customers whose recent support-ticket count exceeds the threshold.

    Reads the ``support_ticket_count`` recorded in ``usage_trend`` by the
    simulator (kept there to avoid a schema change on ``customers``).
    """
    limit_value = threshold if threshold is not None else SUPPORT_TICKET_THRESHOLD
    rows = await _safe_fetch_all(
        "select id::text as customer_id, name, usage_trend"
        " from customers where usage_trend is not null",
        tenant_id=tenant_id,
    )
    signals: list[dict[str, Any]] = []
    for row in rows:
        trend = _as_dict(row["usage_trend"])
        count = trend.get("support_ticket_count")
        try:
            count_int = int(count) if count is not None else 0
        except (TypeError, ValueError):
            count_int = 0
        if count_int >= limit_value:
            signals.append(
                _signal(
                    tenant_id,
                    row["customer_id"],
                    "support_ticket_spike",
                    severity="high",
                    payload={
                        "support_ticket_count": count_int,
                        "threshold": limit_value,
                        "customer_name": row["name"],
                    },
                )
            )
    return signals


async def detect_negative_sentiment(*, tenant_id: str) -> list[dict[str, Any]]:
    """Customers whose distilled profile carries risk or negative-sentiment cues.

    Reads ``customer_profiles`` (populated by the conversation system) so that
    chat-learned dissatisfaction becomes a proactive signal even absent a metric
    change.
    """
    rows = await _safe_fetch_all(
        """
        select customer_id::text as customer_id, risk_signals, sentiment_signals,
               last_sentiment
        from customer_profiles
        where jsonb_array_length(coalesce(risk_signals, '[]'::jsonb)) > 0
           or last_sentiment in ('negative', 'frustrated', 'angry')
        """,
        tenant_id=tenant_id,
    )
    signals: list[dict[str, Any]] = []
    for row in rows:
        risk = _as_list(row["risk_signals"])
        sentiment = _as_list(row["sentiment_signals"])
        signals.append(
            _signal(
                tenant_id,
                row["customer_id"],
                "negative_sentiment",
                severity="high" if risk else "normal",
                payload={
                    "risk_signals": risk,
                    "sentiment_signals": sentiment,
                    "last_sentiment": row["last_sentiment"],
                    "source_kind": "profile",
                },
            )
        )
    return signals


async def run_all_detectors(*, tenant_id: str) -> list[dict[str, Any]]:
    """Run every detector and return the combined list of signal payloads."""
    renewal = await detect_renewal_risk(tenant_id=tenant_id)
    low_health = await detect_low_health(tenant_id=tenant_id)
    usage = await detect_usage_decline(tenant_id=tenant_id)
    tickets = await detect_support_ticket_spike(tenant_id=tenant_id)
    sentiment = await detect_negative_sentiment(tenant_id=tenant_id)
    return [*renewal, *low_health, *usage, *tickets, *sentiment]


def _as_dict(value: Any) -> dict[str, Any]:
    """Coerce a jsonb column (dict or JSON string) into a dict."""
    if isinstance(value, str):
        try:
            value = json.loads(value) if value else {}
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    """Coerce a jsonb column (list or JSON string) into a list."""
    if isinstance(value, str):
        try:
            value = json.loads(value) if value else []
        except json.JSONDecodeError:
            return []
    return value if isinstance(value, list) else []


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
    "detect_usage_decline",
    "detect_support_ticket_spike",
    "detect_negative_sentiment",
    "usage_decline_pct",
    "run_all_detectors",
    "RENEWAL_WINDOW_DAYS",
    "LOW_HEALTH_THRESHOLD",
    "USAGE_DECLINE_PCT",
    "SUPPORT_TICKET_THRESHOLD",
]
