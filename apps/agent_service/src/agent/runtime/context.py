"""Context packing and subagent memory slicing helpers.

The orchestrator owns tenant-safe context construction. This module builds the
scoped :class:`SubagentContextPacket` handed to each ephemeral subagent so no
subagent sees unrelated tenant/global context.
"""

from __future__ import annotations

from packages.agent.src.subagent_types import SubagentContextPacket, SubagentResult, SubagentTask
from packages.agent.src.types import SessionContext


def build_context_packet(
    *,
    task: SubagentTask,
    ctx: SessionContext,
    customer_id: str,
    tenant_constraints: list[str],
    memory_excerpt: str | None,
    dependency_results: dict[str, SubagentResult],
) -> SubagentContextPacket:
    """Assemble a tenant-safe context packet for one subagent task."""
    return SubagentContextPacket(
        tenant_id=ctx.tenant_id,
        customer_id=customer_id,
        trace_id=ctx.trace_id,
        task=task,
        tenant_constraints=tenant_constraints,
        memory_excerpt=memory_excerpt,
        dependency_markdown={
            dep_id: result.markdown
            for dep_id, result in dependency_results.items()
        },
        dependency_data={
            dep_id: result.data
            for dep_id, result in dependency_results.items()
        },
    )


def select_dependency_results(
    task: SubagentTask,
    result_map: dict[str, SubagentResult],
) -> dict[str, SubagentResult]:
    """Return only the completed dependency results a task is allowed to see."""
    return {
        dep_id: result_map[dep_id]
        for dep_id in task.depends_on
        if dep_id in result_map
    }


__all__ = ["build_context_packet", "select_dependency_results"]
