"""Offline tests for the Phase 2 customer simulator: detectors, usage_trend, RBAC."""

from __future__ import annotations

import pytest


# -- Pure usage-decline math (no DB) -------------------------------------------

def test_usage_decline_pct_basic():
    from apps.agent_service.src.signals.detectors import usage_decline_pct

    assert usage_decline_pct({"previous_active_users": 100, "current_active_users": 70}) == 30.0
    assert usage_decline_pct({"previous_active_users": 50, "current_active_users": 35}) == 30.0


def test_usage_decline_pct_edge_cases():
    from apps.agent_service.src.signals.detectors import usage_decline_pct

    # Growth or flat -> zero or negative percentage.
    assert usage_decline_pct({"previous_active_users": 100, "current_active_users": 100}) == 0.0
    assert usage_decline_pct({"previous_active_users": 100, "current_active_users": 120}) == -20.0
    # Not computable -> None.
    assert usage_decline_pct({}) is None
    assert usage_decline_pct({"previous_active_users": 0, "current_active_users": 0}) is None
    assert usage_decline_pct({"previous_active_users": 100}) is None


def test_merge_usage_trend_only_includes_provided():
    from packages.knowledge_service.src.customers import merge_usage_trend

    trend = merge_usage_trend(previous_active_users=100, current_active_users=60)
    assert trend == {"previous_active_users": 100, "current_active_users": 60}
    # None fields are omitted, keeping usage_trend compact.
    assert "period_start" not in trend


def test_merge_usage_trend_full():
    from packages.knowledge_service.src.customers import merge_usage_trend

    trend = merge_usage_trend(
        previous_active_users=100,
        current_active_users=60,
        previous_event_count=1000,
        current_event_count=400,
        previous_login_count=50,
        current_login_count=20,
        period_start="2026-06-01",
        period_end="2026-06-30",
    )
    assert trend["previous_event_count"] == 1000
    assert trend["period_end"] == "2026-06-30"


# -- Customer API models: field validation (no DB) -----------------------------

def test_customer_update_model_rejects_out_of_range():
    from apps.api_gateway.src.routes.customers import CustomerUpdate
    from pydantic import ValidationError

    # NPS as a single survey answer must be 0..10, not the -100..100 aggregate.
    with pytest.raises(ValidationError):
        CustomerUpdate(nps=50)
    with pytest.raises(ValidationError):
        CustomerUpdate(health_score=150)


def test_customer_create_defaults():
    from apps.api_gateway.src.routes.customers import CustomerCreate

    model = CustomerCreate(name="Acme")
    assert model.health_score is None
    assert model.usage_trend is None


def test_usage_trend_input_to_trend():
    from apps.api_gateway.src.routes.customers import UsageTrendInput

    payload = UsageTrendInput(previous_active_users=200, current_active_users=100)
    trend = payload.to_trend()
    assert trend == {"previous_active_users": 200, "current_active_users": 100}


# -- Detector signal builder shape (no DB) -------------------------------------

def test_detector_signal_shape():
    from apps.agent_service.src.signals.detectors import _signal

    sig = _signal(
        "tenant-1",
        "cust-1",
        "usage_decline",
        severity="high",
        payload={"decline_pct": 40.0},
    )
    assert sig["tenant_id"] == "tenant-1"
    assert sig["customer_id"] == "cust-1"
    assert sig["type"] == "usage_decline"
    assert sig["severity"] == "high"
    assert sig["source"] == "detector"
    assert sig["payload"]["decline_pct"] == 40.0


def test_run_all_detectors_is_offline_safe(monkeypatch):
    """When the DB is unavailable, detectors degrade to empty lists, not errors."""
    import asyncio

    from apps.agent_service.src.signals import detectors
    from packages.db.src import PostgresConfigError

    async def unavailable(*args, **kwargs):
        raise PostgresConfigError("no database")

    monkeypatch.setattr(detectors, "fetch_all", unavailable)

    result = asyncio.run(
        detectors.run_all_detectors(tenant_id="00000000-0000-0000-0000-000000000000")
    )
    assert result == []


# -- Customer update field filtering (no DB) -----------------------------------

def test_update_customer_ignores_unknown_fields(monkeypatch):
    """update_customer only applies whitelisted fields."""
    import asyncio

    from packages.knowledge_service.src import customers as customers_mod

    captured = {}

    async def fake_fetch_one(query, *args, tenant_id):
        captured["query"] = query
        captured["args"] = args
        return None

    monkeypatch.setattr(customers_mod, "fetch_one", fake_fetch_one)

    async def run():
        return await customers_mod.update_customer(
            tenant_id="t1",
            customer_id="c1",
            changes={"health_score": 42, "malicious_field": "drop", "mrr": 99},
        )

    asyncio.run(run())
    # Only health_score and mrr should appear in the SET clause.
    assert "health_score" in captured["query"]
    assert "mrr" in captured["query"]
    assert "malicious_field" not in captured["query"]
