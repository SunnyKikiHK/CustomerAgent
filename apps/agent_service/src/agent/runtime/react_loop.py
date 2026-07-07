"""Isolated ReAct loop primitive used by ephemeral subagents."""

from __future__ import annotations

import json
from typing import Any

from apps.agent_service.src.agent.llm_client import LLMClient, LLMMessage
from apps.agent_service.src.agent.runtime.prompts import build_system_prompt, collect_tool_docs
from apps.agent_service.src.agent.runtime.tool_caller import dispatch_tool_call
from packages.agent.src.config import AgentConfig
from packages.agent.src.subagent_types import SubagentContextPacket, SubagentResult, ToolCallRecord
from packages.agent.src.types import LLMUsage, SessionContext


class ReActLoop:
    """Per-task subagent loop with bounded tool access and token tracking."""

    def __init__(
        self,
        *,
        packet: SubagentContextPacket, # task payload + prior results 
        ctx: SessionContext, # auth identity 
        config: AgentConfig,
        llm_client: LLMClient | None = None,
    ) -> None:
        self.packet = packet
        self.ctx = ctx
        self.config = config
        self.llm_client = llm_client or LLMClient(default_model=config.model)
        self.messages: list[LLMMessage] = []
        self.tool_calls: list[ToolCallRecord] = []
        self.usage = LLMUsage()

    async def run(self) -> SubagentResult:
        """
        Execute the subagent loop and return one structured result.
        For example:
            step 0: LM thinks → wants query_health → continue
            step 1: LM sees health result → wants query_playbooks → continue
            step 2: LM sees playbook result → no more tool calls → return
        """
        task = self.packet.task
        self.messages = [
            LLMMessage(role="system", content=self._system_prompt()),
            LLMMessage(role="user", content=self._user_context()),
        ]

        last_markdown = ""
        last_data: dict[str, Any] = {}
        for step in range(task.max_react_steps):
            response = await self.llm_client.complete(
                self.messages,
                model=self.config.model,
                max_tokens=task.max_tokens,
                trace_id=self.ctx.trace_id,
                name=f"subagent_{task.role.value}_react_step",
                metadata={"task_id": task.id, "step": step, "phase": "executor"},
            )
            self.usage.prompt_tokens += response.usage.prompt_tokens
            self.usage.completion_tokens += response.usage.completion_tokens
            parsed = self._parse_model_response(response.text)

            if parsed["tool_calls"]:
                self.messages.append(LLMMessage(role="assistant", content=response.text))
                await self._execute_tool_calls(parsed["tool_calls"])
                continue

            last_markdown = parsed["markdown"] or response.text
            last_data = parsed["data"]
            return SubagentResult(
                task_id=task.id,
                role=task.role,
                success=True,
                markdown=last_markdown,
                data=last_data or {"final_markdown": last_markdown},
                tool_calls=self.tool_calls,
                tokens_used=self.usage.total,
            )

        return SubagentResult(
            task_id=task.id,
            role=task.role,
            success=False,
            markdown=last_markdown,
            data=last_data,
            tool_calls=self.tool_calls,
            tokens_used=self.usage.total,
            error="Subagent reached max_react_steps",
        )

    def _system_prompt(self) -> str:
        registry = _load_tool_registry()
        docs = collect_tool_docs(self.packet.task.allowed_tools, registry)
        return build_system_prompt(self.packet, docs)

    def _user_context(self) -> str:
        dependency_markdown = "\n\n".join(
            f"## Prior result: {task_id}\n{markdown}"
            for task_id, markdown in self.packet.dependency_markdown.items()
        )
        return json.dumps(
            {
                "tenant_id": self.packet.tenant_id,
                "customer_id": self.packet.customer_id,
                "task_input": self.packet.task.input,
                "memory_excerpt": self.packet.memory_excerpt,
                "previous_subagent_markdown": dependency_markdown,
                "previous_subagent_data": self.packet.dependency_data,
            },
            default=str,
        )

    @staticmethod
    def _parse_model_response(text: str) -> dict[str, Any]:
        """
        Parse the model response into a dictionary containing markdown, data, and tool calls.
        For example:
            {
                "tool_calls": [],
                "markdown": "## Health Summary\nCustomer 42 has a health score of **34/100**. Renewal in 14 days.",
                "data": {
                    "health_score": 34,
                    "renewal_days": 14,
                    "risk_tier": "critical"
                }
            }
        """
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {"markdown": text, "data": {}, "tool_calls": []}

        tool_calls = payload.get("tool_calls", [])
        if not isinstance(tool_calls, list):
            tool_calls = []
        data = payload.get("data", {})
        if not isinstance(data, dict):
            data = {"value": data}
        markdown = payload.get("markdown", "")
        return {
            "markdown": markdown if isinstance(markdown, str) else str(markdown),
            "data": data,
            "tool_calls": tool_calls,
        }

    async def _execute_tool_calls(self, tool_calls: list[Any]) -> None:
        for tool_call in tool_calls:
            await self._execute_tool_call(tool_call)

    async def _execute_tool_call(self, tool_call: Any) -> None:
        task = self.packet.task
        if not isinstance(tool_call, dict):
            return

        tool_name = str(tool_call.get("name", ""))
        raw_arguments = tool_call.get("arguments", {})
        original_params = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}

        if tool_name not in task.allowed_tools:
            result = {"error": f"Tool {tool_name} is not allowed for {task.role.value}"}
            self.messages.append(LLMMessage(role="tool", content=json.dumps(result)))
            self.tool_calls.append(ToolCallRecord(tool_name=tool_name, success=False))
            return

        params = {**original_params, "tenant_id": self.ctx.tenant_id}
        try:
            result = await dispatch_tool_call(tool_name, params, self.ctx)
            success = True
        except Exception as exc:
            result = {"error": str(exc)}
            success = False

        self.messages.append(LLMMessage(role="tool", content=json.dumps(result, default=str)))
        self.tool_calls.append(
            ToolCallRecord(
                tool_name=tool_name,
                success=success,
                arguments_redacted=_redact_for_audit(original_params),
                result_redacted=_redact_for_audit(result),
            )
        )


def _redact_for_audit(value: dict[str, Any]) -> dict[str, Any]:
    """Mask common PII keys in audit-visible tool payloads."""
    sensitive_keys = {"email", "recipient_email", "phone", "phone_number", "token", "api_key"}
    return {
        key: "[REDACTED]" if key.lower() in sensitive_keys else item
        for key, item in value.items()
    }


def _load_tool_registry() -> Any:
    from packages.tool_system.src import registry

    return registry


__all__ = ["ReActLoop"]
