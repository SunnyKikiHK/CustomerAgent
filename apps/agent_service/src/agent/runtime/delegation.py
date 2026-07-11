"""Delegation manager for executing orchestrator plans with subagents."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable

from packages.agent.src.config import AgentConfig
from packages.agent.src.memory import MemoryContext
from packages.agent.src.orchestration_types import OrchestratorPlan
from packages.agent.src.subagent_types import SubagentResult, SubagentTask
from packages.agent.src.types import SessionContext

from apps.agent_service.src.agent.runtime.context import (
    build_context_packet,
    fuse_memory_excerpt,
    select_dependency_results,
)
from apps.agent_service.src.agent.runtime.monitor import get_performance_monitor
from apps.agent_service.src.agent.runtime.mcp.tool_layer import get_mcp_tool_layer
from apps.agent_service.src.agent.subagents import build_subagent


async def execute_tasks(
    *,
    plan: OrchestratorPlan,
    ctx: SessionContext,
    config: AgentConfig,
    customer_id: str,
    tenant_constraints: list[str],
    memory_excerpt: str | None,
    memory_context: MemoryContext | None = None,
) -> list[SubagentResult]:
    """Execute ready subagent tasks in dependency-aware batches."""
    # fail fast if the plan itself is malformed before running anything
    _validate_plan_dependencies(plan.tasks)

    # task_id -> result, used to pass prior outputs to dependent tasks
    result_map: dict[str, SubagentResult] = {}
    # task IDs that finished with success=True
    completed: set[str] = set()
    pending = list(plan.tasks)
    all_results: list[SubagentResult] = []

    while pending:
        # tasks whose every dependency is already in `completed`
        ready = [task for task in pending if task.is_ready(completed)]

        if not ready:
            # remaining tasks have unsatisfied dependencies that will never complete
            all_results.extend(_blocked_results(pending, completed))
            break

        # run all ready tasks concurrently; return_exceptions prevents one
        # failure from cancelling the other coroutines in the batch
        batch = await asyncio.gather(
            *(
                _run_subagent(
                    task=task,
                    result_map=result_map,
                    ctx=ctx,
                    config=config,
                    customer_id=customer_id,
                    tenant_constraints=tenant_constraints,
                    memory_excerpt=memory_excerpt,
                    memory_context=memory_context,
                )
                for task in ready
            ),
            return_exceptions=True,
        )

        for task, item in zip(ready, batch):
            # convert unexpected exceptions into a failed SubagentResult so the
            # rest of the pipeline can continue instead of crashing
            result = item if isinstance(item, SubagentResult) else SubagentResult(
                task_id=task.id,
                role=task.role,
                success=False,
                markdown="",
                error=str(item),
            )
            result_map[task.id] = result
            all_results.append(result)
            # only successful tasks unblock downstream dependents
            if result.success:
                completed.add(task.id)

        # remove every task we just attempted regardless of outcome
        attempted = {task.id for task in ready}
        pending = [task for task in pending if task.id not in attempted]

    return all_results


async def _run_subagent(
    *,
    task: SubagentTask,
    result_map: dict[str, SubagentResult],
    ctx: SessionContext,
    config: AgentConfig,
    customer_id: str,
    tenant_constraints: list[str],
    memory_excerpt: str | None,
    memory_context: MemoryContext | None = None,
) -> SubagentResult:
    monitor = get_performance_monitor()
    penalty = monitor.get_routing_penalty(task.role.value)
    if penalty >= 0.9:
        return SubagentResult(
            task_id=task.id,
            role=task.role,
            success=False,
            markdown="",
            error=f"Role {task.role.value} is temporarily downgraded by monitor",
        )

    started = time.monotonic()
    dependency_results = select_dependency_results(task, result_map)
    role_memory = fuse_memory_excerpt(
        memory_context=memory_context,
        task_role=task.role,
    )
    packet = build_context_packet(
        task=task,
        ctx=ctx,
        customer_id=customer_id,
        tenant_constraints=tenant_constraints,
        memory_excerpt=role_memory or memory_excerpt,
        dependency_results=dependency_results,
    )
    subagent = build_subagent(packet=packet, ctx=ctx, config=config)
    result = await subagent.run()
    monitor.record_role_result(
        task.role.value,
        success=result.success,
        latency_ms=(time.monotonic() - started) * 1000,
    )
    monitor.refresh_penalties(get_mcp_tool_layer().get_stats())
    return result


def _validate_plan_dependencies(tasks: Iterable[SubagentTask]) -> None:
    task_list = list(tasks)
    task_ids = {task.id for task in task_list}

    # two tasks sharing the same ID would cause result_map collisions
    duplicate_ids = {task.id for task in task_list if sum(item.id == task.id for item in task_list) > 1}
    if duplicate_ids:
        raise ValueError(f"Duplicate subagent task IDs: {sorted(duplicate_ids)}")

    # a dependency pointing at a non-existent task ID would deadlock the loop
    missing = {
        dep_id
        for task in task_list
        for dep_id in task.depends_on
        if dep_id not in task_ids
    }
    if missing:
        raise ValueError(f"Unknown subagent dependencies: {sorted(missing)}")


def _blocked_results(
    pending: list[SubagentTask],
    completed: set[str],
) -> list[SubagentResult]:
    results: list[SubagentResult] = []
    for task in pending:
        # record exactly which dependencies prevented this task from running
        missing = [dep_id for dep_id in task.depends_on if dep_id not in completed]
        results.append(
            SubagentResult(
                task_id=task.id,
                role=task.role,
                success=False,
                markdown="",
                data={"missing_dependencies": missing},
                error="Task dependencies were not satisfied",
            )
        )
    return results


__all__ = ["execute_tasks"]
