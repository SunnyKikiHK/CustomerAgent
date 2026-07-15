"""Offline tests for OpenRouter reasoning payload construction."""

from __future__ import annotations

import pytest


def test_reasoning_payload_enabled_uses_budget_tokens(monkeypatch):
    from apps.agent_service.src.agent.llm_client import reasoning_payload

    monkeypatch.setenv("BUDGET_TOKENS", "1500")
    assert reasoning_payload(True) == {"enabled": True, "budget_tokens": 1500}


def test_reasoning_payload_disabled_is_false(monkeypatch):
    from apps.agent_service.src.agent.llm_client import reasoning_payload

    monkeypatch.setenv("BUDGET_TOKENS", "999")
    assert reasoning_payload(False) == {"enabled": False}


def test_budget_tokens_defaults_and_rejects_garbage(monkeypatch):
    from apps.agent_service.src.agent.llm_client import budget_tokens

    monkeypatch.delenv("BUDGET_TOKENS", raising=False)
    assert budget_tokens() == 512

    monkeypatch.setenv("BUDGET_TOKENS", "not-a-number")
    assert budget_tokens() == 512

    monkeypatch.setenv("BUDGET_TOKENS", "-5")
    assert budget_tokens() == 0


def test_reasoning_default_env(monkeypatch):
    from apps.agent_service.src.agent.llm_client import reasoning_default

    monkeypatch.delenv("LLM_REASONING_DEFAULT", raising=False)
    assert reasoning_default() is False
    monkeypatch.setenv("LLM_REASONING_DEFAULT", "1")
    assert reasoning_default() is True
    monkeypatch.setenv("LLM_REASONING_DEFAULT", "0")
    assert reasoning_default() is False


@pytest.mark.asyncio
async def test_complete_sends_reasoning_object(monkeypatch):
    """LLMClient.complete must put the OpenRouter reasoning object on the request."""
    from apps.agent_service.src.agent import llm_client as llm_mod
    from apps.agent_service.src.agent.llm_client import LLMClient, LLMMessage

    monkeypatch.setenv("BUDGET_TOKENS", "1800")
    monkeypatch.setenv("LLM_REASONING_DEFAULT", "0")
    captured: dict = {}

    class _FakeUsage:
        prompt_tokens = 1
        completion_tokens = 2

    class _FakeMessage:
        content = "ok"

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeCompletion:
        choices = [_FakeChoice()]
        usage = _FakeUsage()
        id = "fake-1"

    class _FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeCompletion()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = _FakeChat()

    monkeypatch.setattr(llm_mod, "AsyncOpenAI", _FakeOpenAI)

    client = LLMClient(api_key="test", default_model="deepseek/deepseek-v4-flash")
    await client.complete(
        [LLMMessage(role="user", content="hi")],
        reasoning=True,
        name="test.reasoning.on",
    )
    # `reasoning` is an OpenRouter extension carried via extra_body, not a
    # top-level OpenAI SDK kwarg (the SDK rejects unknown kwargs).
    assert captured["extra_body"]["reasoning"] == {"enabled": True, "budget_tokens": 1800}
    assert "reasoning" not in captured

    captured.clear()
    await client.complete(
        [LLMMessage(role="user", content="hi")],
        reasoning=False,
        name="test.reasoning.off",
    )
    assert captured["extra_body"]["reasoning"] == {"enabled": False}

    # Omitted reasoning follows LLM_REASONING_DEFAULT (off).
    captured.clear()
    await client.complete(
        [LLMMessage(role="user", content="hi")],
        name="test.reasoning.default",
    )
    assert captured["extra_body"]["reasoning"] == {"enabled": False}
