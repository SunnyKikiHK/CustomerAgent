"""Prompt builders for the ReAct subagent loop."""

from __future__ import annotations

import json
from typing import Any

from packages.agent.src.subagent_types import SubagentContextPacket

from apps.agent_service.src.agent.runtime.skills import get_skill_manager


def build_system_prompt(
    packet: SubagentContextPacket,
    tool_docs: list[str],
    *,
    message_for_skills: str = "",
) -> str:
    task = packet.task
    skill_block = get_skill_manager(packet.tenant_id).prompt_for(
        message_for_skills or task.objective,
        agent_role=task.role.value,
    )
    injected_skills = f"\n\n{skill_block}" if skill_block else ""
    return (
        f"{task.skill}{injected_skills}\n\n"
        "You are an ephemeral subagent. Complete only the assigned task. "
        "Do not write long-term memory, do not emit final customer-visible output, "
        "and do not call tools outside the allowed list.\n\n"
        f"Objective: {task.objective}\n"
        f"Tenant constraints: {json.dumps(packet.tenant_constraints)}\n"
        f"Allowed tools:\n{chr(10).join(tool_docs)}\n\n"
        "Respond with JSON containing markdown, data, and optional tool_calls. "
        'Each tool call must be {"name": string, "arguments": object}. '
        "When finished, set tool_calls to an empty list."
    )


def collect_tool_docs(allowed_tools: list[str], registry: Any) -> list[str]:
    docs: list[str] = []
    for tool_name in allowed_tools:
        try:
            tool_def = registry.get_tool_definition(tool_name)
        except Exception:
            continue
        fn = tool_def.get("function", {})
        docs.append(f"- {fn.get('name', tool_name)}: {fn.get('description', '')}")
    return docs


__all__ = ["build_system_prompt", "collect_tool_docs"]
