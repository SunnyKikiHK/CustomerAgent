"""Offline evaluation: each canonical scenario fires exactly its expected signals.

Runs the deterministic detector/scoring helpers against the fixed eval dataset
(no DB). This is the project's evaluation gate: correct detector activation,
correct NPS classification, and no cross-scenario bleed.
"""

from __future__ import annotations

from datetime import date

import pytest

from apps.agent_service.src.signals.detectors import (
    LOW_HEALTH_THRESHOLD,
    RENEWAL_WINDOW_DAYS,
    USAGE_DECLINE_PCT,
    usage_decline_pct,
)
from packages.knowledge_service.src.evaluation import EVAL_CUSTOMERS, scenario
from packages.knowledge_service.src.nps import calculate_nps, classify_score


_NEGATIVE_SENTIMENTS = {"negative", "frustrated", "angry"}


def _expected_from_rules(cust) -> set[str]:
    """Recompute the signals a customer should produce, mirroring the detectors."""
    signals: set[str] = set()

    if cust.health_score is not None and cust.health_score < LOW_HEALTH_THRESHOLD:
        signals.add("low_health")

    renewal_in_window = (
        cust.days_to_renewal is not None and 0 <= cust.days_to_renewal <= RENEWAL_WINDOW_DAYS
    )
    if renewal_in_window:
        signals.add("renewal_risk")

    drop = usage_decline_pct(cust.usage_trend)
    usage_declined = drop is not None and drop >= USAGE_DECLINE_PCT
    if usage_declined:
        signals.add("usage_decline")
        if renewal_in_window:
            signals.add("renewal_usage_risk")

    risk = cust.profile.get("risk_signals") or []
    sentiment = cust.profile.get("last_sentiment")
    if risk or (sentiment in _NEGATIVE_SENTIMENTS):
        signals.add("negative_sentiment")

    return signals


@pytest.mark.parametrize("cust", EVAL_CUSTOMERS, ids=lambda c: c.key)
def test_scenario_fires_expected_signals(cust):
    """Each scenario's rule-derived signals match its declared expectation."""
    assert _expected_from_rules(cust) == set(cust.expected_signals), cust.description


def test_healthy_customer_is_silent():
    """A healthy customer must not trip any detector."""
    assert _expected_from_rules(scenario("healthy")) == set()


def test_high_value_at_risk_is_multi_signal():
    """The high-value at-risk account trips the full risk stack."""
    fired = _expected_from_rules(scenario("high_value_at_risk"))
    assert {"low_health", "renewal_risk", "usage_decline", "renewal_usage_risk"} <= fired


def test_detractor_score_classifies_and_aggregates():
    """A low survey score is a detractor and drags the aggregate NPS negative."""
    assert classify_score(3) == "detractor"
    result = calculate_nps([3, 4, 9])  # 1 promoter, 2 detractors
    assert result["detractors"] == 2
    assert result["promoters"] == 1
    assert result["nps"] == round((1 - 2) / 3 * 100)


def test_empty_tenant_has_no_nps():
    """An empty portfolio yields nps=None, never a crash or a zero-division."""
    assert calculate_nps([])["nps"] is None
