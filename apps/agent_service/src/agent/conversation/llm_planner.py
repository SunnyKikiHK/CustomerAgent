"""LLM-driven semantic role selection for conversation turns.

This runs after intent/entity extraction and asks an LLM to choose which
conversation specialist(s) should answer, from a fixed trusted allowlist. The LLM
returns only an ordered role list, a playbook boolean, a rationale, and a
confidence. It never chooses tools, task ids, dependencies, roles outside the
allowlist, signal-only agents, or policy. Trusted code (the planner) converts the
validated selection into the actual plan and owns all tools/deps/permissions.

Any malformed output, unknown/duplicate role, over-fan-out, low confidence,
timeout, or exception yields ``None`` so the caller falls back to the
deterministic router.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from packages.agent.src.subagent_types import AgentRole

from apps.agent_service.src.agent.conversation.capability_catalog import CONVERSATION_ROLES
from apps.agent_service.src.agent.conversation.intent import IntentResult
from apps.agent_service.src.agent.llm_client import LLMClient, LLMMessage

logger = logging.getLogger(__name__)

#: Maximum answering specialists the planner may select for one turn.
MAX_ANSWER_ROLES = 2

#: Minimum confidence below which the LLM selection is discarded (fallback).
MIN_CONFIDENCE = 0.5

#: Allowlisted role value -> AgentRole (conversation answering roles only).
_ALLOWED_ROLE_VALUES: dict[str, AgentRole] = {role.value: role for role in CONVERSATION_ROLES}


@dataclass(frozen=True)
class PlannerDecision:
    """Validated LLM planner output (roles already allowlisted)."""

    roles: list[AgentRole]
    needs_playbook: bool
    rationale: str
    confidence: float


def _build_prompt(
    *,
    message: str,
    history: list[dict[str, str]] | None,
    intent: IntentResult,
    catalog_text: str,
) -> str:
    history_text = ""
    if history:
        history_text = "\n".join(
            f"  {item.get('role', 'user')}: {item.get('content', '')}"
            for item in history[-3:]
        )
    allowed = ", ".join(_ALLOWED_ROLE_VALUES)
    return f"""You are the routing planner for a customer-support conversation.
    Choose which specialist role(s) should answer this turn, using the catalog below.

    [AVAILABLE ROLES]
    {catalog_text}

    [RULES]
    - Choose only from these roles: {allowed}.
    - Choose 1 role normally; choose 2 only for genuinely compound turns that need two
    distinct specialists (for example a technical fault AND a billing charge).
    - Never invent roles, tools, task ids, or dependencies. Never select any role not
    listed above.
    - Set needs_playbook=true when the answer depends on a documented policy or rule
    (refunds, cancellations, eligibility, warranty, returns).
    - Return confidence in [0,1] reflecting how sure you are of the routing.

    [SIGNALS]
    detected_intent: {intent.intent.value}
    urgency: {intent.urgency.value}
    entities: {json.dumps(intent.entities, default=str)}

    [RECENT HISTORY]
    {history_text}

    [MESSAGE]
    "{message}"

    [OUTPUT]
    Return RAW JSON only (no markdown fences), exactly:
    {{"roles": ["<role>", ...], "needs_playbook": <bool>, "rationale": "<short>", "confidence": <float>}}
    """


async def select_roles(
    *,
    message: str,
    history: list[dict[str, str]] | None,
    intent: IntentResult,
    catalog_text: str,
    llm_client: LLMClient,
    model: str,
    timeout: float = 30.0,
) -> PlannerDecision | None:
    """Ask the LLM to select roles; return None on any failure/invalid output."""
    prompt = _build_prompt(
        message=message,
        history=history,
        intent=intent,
        catalog_text=catalog_text,
    )
    try:
        response = await asyncio.wait_for(
            llm_client.complete(
                [LLMMessage(role="user", content=prompt)],
                model=model,
                temperature=0.0,
                max_tokens=256,
                name="conversation.planner.select_roles",
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("LLM planner timed out after %.1fs, falling back", timeout)
        return None
    except Exception as exc:  # noqa: BLE001 - fallback on any error
        logger.warning("LLM planner call failed (%s), falling back: %s", type(exc).__name__, exc)
        return None

    return _parse_decision(response.text)


def _parse_decision(text: str) -> PlannerDecision | None:
    """Defensively parse the model output into a validated PlannerDecision."""
    try:
        start, end = text.find("{"), text.rfind("}") + 1
        if start < 0 or end <= start:
            return None
        parsed = json.loads(text[start:end])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None

    raw_roles = parsed.get("roles", [])
    if not isinstance(raw_roles, list) or not raw_roles:
        return None

    roles: list[AgentRole] = []
    seen: set[str] = set()
    for item in raw_roles:
        value = str(item).strip().lower()
        if value not in _ALLOWED_ROLE_VALUES:
            return None  # unknown / signal-only / invented role
        if value in seen:
            return None  # duplicate
        seen.add(value)
        roles.append(_ALLOWED_ROLE_VALUES[value])

    if len(roles) > MAX_ANSWER_ROLES:
        return None

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    if confidence < MIN_CONFIDENCE:
        return None

    return PlannerDecision(
        roles=roles,
        needs_playbook=bool(parsed.get("needs_playbook", False)),
        rationale=str(parsed.get("rationale", "")),
        confidence=confidence,
    )


__all__ = [
    "PlannerDecision",
    "select_roles",
    "MAX_ANSWER_ROLES",
    "MIN_CONFIDENCE",
]
