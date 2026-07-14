"""Conversation package exports.

Exports are resolved lazily via ``__getattr__`` so that importing a leaf module
(for example ``conversation.subagents.billing``) does not eagerly pull in the
whole orchestrator chain. That chain imports the shared delegation factory,
which in turn imports the conversation subagents — eager exports here would
create an import cycle.
"""

from __future__ import annotations

from typing import Any

_EXPORTS = {
    "handle_chat_turn": "apps.agent_service.src.agent.conversation.chat_handler",
    "ConversationOrchestrator": "apps.agent_service.src.agent.conversation.conversation_orchestrator",
    "run_conversation_agent": "apps.agent_service.src.agent.conversation.conversation_orchestrator",
    "build_conversation_plan": "apps.agent_service.src.agent.conversation.conversation_planner",
    "IntentRecognizer": "apps.agent_service.src.agent.conversation.intent",
    "get_intent_recognizer": "apps.agent_service.src.agent.conversation.intent",
    "stream_approved_response": "apps.agent_service.src.agent.conversation.streaming",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, name)
