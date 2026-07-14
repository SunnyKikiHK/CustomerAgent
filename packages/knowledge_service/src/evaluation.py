"""Deterministic evaluation dataset for the customer-success platform.

Eight canonical scenarios (healthy, renewal-risk, usage-collapse, detractor,
negative-sentiment, high-value-at-risk, empty-tenant, conflicting-indicators)
plus the *expected* detector activations for each. The dataset is pure data:
tests feed each scenario's ``usage_trend`` / profile through the pure detector
and scoring helpers and assert the expected signals fire, with no database.

This is the "small deterministic evaluation dataset" the project plan calls for.
It exercises detector activation, NPS classification, and QBR health banding
without any live infra, so it runs fully offline in CI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvalCustomer:
    """One evaluation customer: inputs plus the signals it should produce."""

    key: str
    description: str
    health_score: float | None
    days_to_renewal: int | None
    usage_trend: dict[str, Any]
    profile: dict[str, Any] = field(default_factory=dict)
    #: Detector signal types expected to fire for this customer.
    expected_signals: frozenset[str] = frozenset()


#: Health thresholds mirror the detector/QBR defaults so expectations line up.
_HEALTHY = 82.0
_AT_RISK = 45.0
_CRITICAL = 20.0


EVAL_CUSTOMERS: tuple[EvalCustomer, ...] = (
    EvalCustomer(
        key="healthy",
        description="Healthy customer: high health, growing usage, far renewal.",
        health_score=_HEALTHY,
        days_to_renewal=200,
        usage_trend={"previous_active_users": 100, "current_active_users": 110},
        expected_signals=frozenset(),
    ),
    EvalCustomer(
        key="renewal_risk",
        description="Renewal within 60 days, otherwise stable.",
        health_score=68.0,
        days_to_renewal=30,
        usage_trend={"previous_active_users": 100, "current_active_users": 98},
        expected_signals=frozenset({"renewal_risk"}),
    ),
    EvalCustomer(
        key="usage_collapse",
        description="Active users collapsed well beyond the decline threshold.",
        health_score=60.0,
        days_to_renewal=None,
        usage_trend={"previous_active_users": 200, "current_active_users": 60},
        expected_signals=frozenset({"usage_decline"}),
    ),
    EvalCustomer(
        key="detractor",
        description="NPS detractor from a low survey score (profile-recorded).",
        health_score=55.0,
        days_to_renewal=None,
        usage_trend={"previous_active_users": 50, "current_active_users": 49},
        profile={"last_nps_score": 3},
        expected_signals=frozenset(),  # detractor is raised on response, not scan
    ),
    EvalCustomer(
        key="negative_sentiment",
        description="Chat-derived risk/sentiment cues in the profile.",
        health_score=64.0,
        days_to_renewal=None,
        usage_trend={"previous_active_users": 30, "current_active_users": 30},
        profile={"risk_signals": ["considering not renewing"], "last_sentiment": "negative"},
        expected_signals=frozenset({"negative_sentiment"}),
    ),
    EvalCustomer(
        key="high_value_at_risk",
        description="High MRR, low health, imminent renewal, usage falling.",
        health_score=_CRITICAL,
        days_to_renewal=20,
        usage_trend={"previous_active_users": 300, "current_active_users": 120},
        expected_signals=frozenset({"low_health", "renewal_risk", "usage_decline", "renewal_usage_risk"}),
    ),
    EvalCustomer(
        key="conflicting",
        description="Healthy score but collapsing usage and a near renewal.",
        health_score=_HEALTHY,
        days_to_renewal=25,
        usage_trend={"previous_active_users": 500, "current_active_users": 150},
        expected_signals=frozenset({"renewal_risk", "usage_decline", "renewal_usage_risk"}),
    ),
)


#: A tenant with no customers/data at all (empty-portfolio scenario).
EMPTY_TENANT_CUSTOMERS: tuple[EvalCustomer, ...] = ()


def scenario(key: str) -> EvalCustomer:
    """Return one evaluation customer by key."""
    for customer in EVAL_CUSTOMERS:
        if customer.key == key:
            return customer
    raise KeyError(key)


__all__ = [
    "EvalCustomer",
    "EVAL_CUSTOMERS",
    "EMPTY_TENANT_CUSTOMERS",
    "scenario",
]
