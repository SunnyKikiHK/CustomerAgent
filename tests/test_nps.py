"""Offline tests for Phase 5 NPS: deterministic scoring, classification, schemas."""

from __future__ import annotations

import pytest


# ── Deterministic aggregate NPS (no DB) ────────────────────────────────────────

def test_classify_score_boundaries():
    from packages.knowledge_service.src.nps import classify_score

    assert classify_score(10) == "promoter"
    assert classify_score(9) == "promoter"
    assert classify_score(8) == "passive"
    assert classify_score(7) == "passive"
    assert classify_score(6) == "detractor"
    assert classify_score(0) == "detractor"


def test_calculate_nps_basic():
    from packages.knowledge_service.src.nps import calculate_nps

    # 3 promoters (9,10,10), 1 passive (7), 2 detractors (3,6) over 6 responses.
    result = calculate_nps([9, 10, 10, 7, 3, 6])
    assert result["responses"] == 6
    assert result["promoters"] == 3
    assert result["passives"] == 1
    assert result["detractors"] == 2
    # (3 - 2) / 6 * 100 = 16.67 -> round -> 17
    assert result["nps"] == 17


def test_calculate_nps_all_promoters_and_all_detractors():
    from packages.knowledge_service.src.nps import calculate_nps

    assert calculate_nps([9, 10, 9, 10])["nps"] == 100
    assert calculate_nps([0, 1, 2, 6])["nps"] == -100


def test_calculate_nps_empty_and_invalid():
    from packages.knowledge_service.src.nps import calculate_nps

    empty = calculate_nps([])
    assert empty["nps"] is None
    assert empty["responses"] == 0
    # Out-of-range scores are ignored, not clamped.
    filtered = calculate_nps([9, 10, 99, -5, 7])
    assert filtered["responses"] == 3  # 9, 10, 7


# ── Tool schemas (no DB) ────────────────────────────────────────────────────────

def test_record_nps_response_rejects_out_of_range():
    from pydantic import ValidationError

    from packages.tool_system.src.tools.nps_tools import RecordNpsResponseInput

    RecordNpsResponseInput(tenant_id="t", survey_id="s", score=0)
    RecordNpsResponseInput(tenant_id="t", survey_id="s", score=10)
    with pytest.raises(ValidationError):
        RecordNpsResponseInput(tenant_id="t", survey_id="s", score=11)
    with pytest.raises(ValidationError):
        RecordNpsResponseInput(tenant_id="t", survey_id="s", score=-1)


def test_nps_tools_registered_internal():
    from packages.tool_system.src.registry import ToolBoundary, require_tool_boundary

    for name in (
        "query_nps_history",
        "create_nps_survey",
        "record_nps_response",
        "calculate_tenant_nps",
    ):
        entry = require_tool_boundary(name, ToolBoundary.INTERNAL)
        assert entry is not None


def test_nps_response_route_validation():
    from pydantic import ValidationError

    from apps.api_gateway.src.routes.nps import NpsResponseInput

    NpsResponseInput(tenant_id="t", score=5)
    with pytest.raises(ValidationError):
        NpsResponseInput(tenant_id="t", score=11)


# ── NPS role + subagent wiring ──────────────────────────────────────────────────

def test_nps_outreach_role_is_signal_only():
    from apps.agent_service.src.agent.subagents import role_map_for_domain
    from packages.agent.src.subagent_types import AgentRole

    signal_roles = role_map_for_domain("signal")
    conversation_roles = role_map_for_domain("conversation")
    assert AgentRole.NPS_OUTREACH in signal_roles
    # NPS outreach must never be selectable by the conversation system.
    assert AgentRole.NPS_OUTREACH not in conversation_roles


def test_nps_outreach_agent_exposes_role_brief():
    from apps.agent_service.src.agent.subagents import nps_outreach

    assert hasattr(nps_outreach, "ROLE_BRIEF")
    assert isinstance(nps_outreach.ROLE_BRIEF, str) and nps_outreach.ROLE_BRIEF


def test_survey_url_uses_frontend_base(monkeypatch):
    from packages.tool_system.src.tools.nps_tools import _survey_url

    monkeypatch.setenv("FRONTEND_BASE_URL", "https://app.example.com/")
    assert _survey_url("abc-123") == "https://app.example.com/nps/abc-123"
