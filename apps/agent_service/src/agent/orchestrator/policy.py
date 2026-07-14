"""Policy helpers shared by top-level orchestrators."""

from __future__ import annotations

from packages.agent.src.config import AgentConfig
from packages.agent.src.orchestration_types import OrchestratorPlan
from packages.agent.src.subagent_types import AgentRole


DEFAULT_TENANT_CONSTRAINTS = [
    "Never access or disclose data from another tenant.",
    "Do not expose raw secrets, credentials, or payment information.",
    "Customer-visible output must be factually grounded in provided context.",
    "External writes require ComplianceCriticAgent approval.",
]


def build_global_constraints(
    config: AgentConfig,
    tenant_constraints: list[str] | None = None,
) -> list[str]:
    """Merge default constraints with tenant-specific instructions."""
    constraints = list(DEFAULT_TENANT_CONSTRAINTS)
    if config.pii_masking_enabled:
        constraints.append("Mask PII before sending content to LLMs or external tools.")
    constraints.extend(tenant_constraints or [])
    return constraints


def plan_requires_critic(plan: OrchestratorPlan) -> bool:
    """Return whether a plan should pass through the reflector phase."""
    if plan.requires_critic:
        return True
    return any(task.role in _WRITE_OR_VISIBLE_ROLES for task in plan.tasks)


_WRITE_OR_VISIBLE_ROLES = {
    AgentRole.OUTREACH_DRAFT,
    AgentRole.GENERAL,
    AgentRole.TECHNICAL,
    AgentRole.BILLING,
    AgentRole.ESCALATION,
}


#: Payload field names whose content is delivered to the customer verbatim.
#: Redaction must never silently alter these; a flagged value here forces a
#: replan/block instead of masking, so the customer never receives "[REDACTED]".
CUSTOMER_VISIBLE_FIELDS = frozenset(
    {
        "body",
        "subject",
        "message",
        "content",
        "reply",
        "response_text",
        "text",
        "html",
    }
)


def is_customer_visible_field(field_name: str) -> bool:
    """Return True when a payload field is delivered to the customer verbatim."""
    return field_name.lower() in CUSTOMER_VISIBLE_FIELDS


__all__ = [
    "DEFAULT_TENANT_CONSTRAINTS",
    "build_global_constraints",
    "plan_requires_critic",
    "CUSTOMER_VISIBLE_FIELDS",
    "is_customer_visible_field",
]
