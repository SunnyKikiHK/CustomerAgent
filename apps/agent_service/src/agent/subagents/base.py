"""BaseSubagent protocol and role-to-implementation factory.

Specialist subagents are ephemeral, single-turn workers. Most role subagents
run a scoped :class:`ReActLoop`, but this protocol lets non-ReAct subagents
(for example the compliance critic) share one delegation contract.
"""

from __future__ import annotations

from typing import ClassVar, Protocol, runtime_checkable

from packages.agent.src.config import AgentConfig
from packages.agent.src.subagent_types import AgentRole, SubagentContextPacket, SubagentResult
from packages.agent.src.types import SessionContext

from apps.agent_service.src.agent.runtime.react_loop import ReActLoop


@runtime_checkable
class BaseSubagent(Protocol): # No inheritance needed, checked BEFORE runtime
    """Contract for an ephemeral subagent that returns one structured result."""

    async def run(self) -> SubagentResult:
        """Execute the bounded task and return a structured result."""
        ...


class ReActSubagent:
    """Default subagent that delegates to a scoped ReAct loop.

    Specialist subclasses declare their `role`, `default_allowed_tools`, and
    `skill_prompt` as class attributes. The concrete per-task values still come
    from the planner-produced `SubagentTask` inside the packet; the class-level
    attributes document the role contract and drive the role factory below.
    """

    role: ClassVar[AgentRole | None] = None
    default_allowed_tools: ClassVar[list[str]] = []
    skill_prompt: ClassVar[str] = ""

    def __init__(
        self,
        *,
        packet: SubagentContextPacket,
        ctx: SessionContext,
        config: AgentConfig,
    ) -> None:
        self._loop = ReActLoop(packet=packet, ctx=ctx, config=config)

    async def run(self) -> SubagentResult:
        return await self._loop.run()


__all__ = ["BaseSubagent", "ReActSubagent"]
