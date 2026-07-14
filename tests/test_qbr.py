"""Offline tests for Phase 6 QBR: health bands, renewal pipeline, aggregation, tools."""

from __future__ import annotations

import pytest


# ── Deterministic health-band classification (no DB) ──────────────────────────

def test_health_band_thresholds():
    from packages.knowledge_service.src.qbr import _health_band

    assert _health_band(90) == "healthy"
    assert _health_band(70) == "healthy"  # boundary
    assert _health_band(69.9) == "at_risk"
    assert _health_band(50) == "at_risk"  # boundary
    assert _health_band(49.9) == "critical"
    assert _health_band(0) == "critical"
    assert _health_band(None) == "unknown"


# ── Deterministic renewal pipeline bucketing (no DB) ──────────────────────────

def test_renewal_pipeline_buckets():
    from datetime import date, timedelta

    from packages.knowledge_service.src.qbr import _renewal_pipeline

    today = date.today()

    class _Row(dict):
        def __getitem__(self, k):
            return super().__getitem__(k)

    rows = [
        _Row(renewal_date=today + timedelta(days=10)),   # next_30
        _Row(renewal_date=today + timedelta(days=45)),   # next_60
        _Row(renewal_date=today + timedelta(days=80)),   # next_90
        _Row(renewal_date=today + timedelta(days=200)),  # beyond
        _Row(renewal_date=today - timedelta(days=5)),    # past (ignored)
        _Row(renewal_date=None),                          # none (ignored)
    ]
    buckets = _renewal_pipeline(rows)
    assert buckets == {"next_30": 1, "next_60": 1, "next_90": 1}


# ── QBR tool schemas + narrative role wiring (no DB) ──────────────────────────

def test_qbr_tools_registered_internal():
    from packages.tool_system.src.registry import ToolBoundary, require_tool_boundary

    for name in (
        "query_tenant_portfolio",
        "query_tenant_nps",
        "query_renewal_pipeline",
        "query_signal_summary",
    ):
        entry = require_tool_boundary(name, ToolBoundary.INTERNAL)
        assert entry is not None


def test_qbr_agent_is_read_only_reporting_role():
    from apps.agent_service.src.agent.subagents.qbr_report import (
        DEFAULT_ALLOWED_TOOLS,
        ROLE,
    )
    from packages.agent.src.subagent_types import AgentRole

    assert ROLE == AgentRole.QBR_REPORT
    # QBR reads facts only; it must not carry any external-write tool.
    assert "send_email" not in DEFAULT_ALLOWED_TOOLS
    assert set(DEFAULT_ALLOWED_TOOLS) == {
        "query_tenant_portfolio",
        "query_tenant_nps",
        "query_renewal_pipeline",
        "query_signal_summary",
    }


def test_reporting_domain_isolates_qbr_role():
    """The QBR role is only instantiable in the reporting domain, not signal/conversation."""
    from apps.agent_service.src.agent.subagents import role_map_for_domain
    from packages.agent.src.subagent_types import AgentRole

    reporting = role_map_for_domain("reporting")
    assert AgentRole.QBR_REPORT in reporting
    # A signal or conversation run can never spin up the QBR reporting agent.
    assert AgentRole.QBR_REPORT not in role_map_for_domain("signal")
    assert AgentRole.QBR_REPORT not in role_map_for_domain("conversation")


# ── Portfolio aggregation degrades safely offline ─────────────────────────────

def test_aggregate_portfolio_offline_safe(monkeypatch):
    """With DB reads stubbed empty, aggregation returns a well-formed zero snapshot."""
    import asyncio

    from packages.knowledge_service.src import qbr

    async def fake_safe_rows(query, tenant_id):
        return []

    monkeypatch.setattr(qbr, "_safe_rows", fake_safe_rows)

    snapshot = asyncio.run(qbr.aggregate_portfolio(tenant_id="t1"))
    assert snapshot["customers"] == 0
    assert snapshot["mrr_total"] == 0
    assert snapshot["arr_total"] == 0
    assert snapshot["health_distribution"] == {
        "healthy": 0,
        "at_risk": 0,
        "critical": 0,
        "unknown": 0,
    }
    assert snapshot["renewal_pipeline"] == {"next_30": 0, "next_60": 0, "next_90": 0}
    assert snapshot["nps"]["nps"] is None
    assert snapshot["open_signals"]["total"] == 0


def test_aggregate_portfolio_computes_facts(monkeypatch):
    """Aggregation buckets health, sums MRR, and computes ARR from stubbed rows."""
    import asyncio

    from packages.knowledge_service.src import qbr

    async def fake_safe_rows(query, tenant_id):
        if "from customers" in query:
            return [
                {"health_score": 90, "mrr": 100, "renewal_date": None},
                {"health_score": 60, "mrr": 200, "renewal_date": None},
                {"health_score": 30, "mrr": 50, "renewal_date": None},
                {"health_score": None, "mrr": None, "renewal_date": None},
            ]
        return []

    monkeypatch.setattr(qbr, "_safe_rows", fake_safe_rows)

    snapshot = asyncio.run(qbr.aggregate_portfolio(tenant_id="t1"))
    assert snapshot["customers"] == 4
    assert snapshot["mrr_total"] == 350.0
    assert snapshot["arr_total"] == 4200.0
    assert snapshot["health_distribution"] == {
        "healthy": 1,
        "at_risk": 1,
        "critical": 1,
        "unknown": 1,
    }
