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


@pytest.mark.asyncio
async def test_complete_records_langfuse_v4_generation(monkeypatch):
    """Prompt, response, model, metadata, and usage reach one v4 generation."""
    from apps.agent_service.src.agent import llm_client as llm_mod
    from apps.agent_service.src.agent.llm_client import LLMClient, LLMMessage

    class _FakeUsage:
        prompt_tokens = 7
        completion_tokens = 3

    class _FakeMessage:
        content = "recorded response"

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeCompletion:
        choices = [_FakeChoice()]
        usage = _FakeUsage()
        id = "provider-1"

    class _FakeCompletions:
        async def create(self, **kwargs):
            return _FakeCompletion()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = _FakeChat()

    class _Observation:
        def __init__(self):
            self.updated = None

        def update(self, **kwargs):
            self.updated = kwargs

    class _Context:
        def __init__(self, observation):
            self.observation = observation
            self.closed = False

        def __enter__(self):
            return self.observation

        def __exit__(self, exc_type, exc, traceback):
            self.closed = True

    class _Langfuse:
        def __init__(self):
            self.observation = _Observation()
            self.context = _Context(self.observation)
            self.started = None

        def create_trace_id(self, *, seed):
            return f"lf-{seed}"

        def start_as_current_observation(self, **kwargs):
            self.started = kwargs
            return self.context

    monkeypatch.setattr(llm_mod, "AsyncOpenAI", _FakeOpenAI)
    langfuse = _Langfuse()
    client = LLMClient(
        api_key="test",
        default_model="test-model",
        langfuse_client=langfuse,
    )

    response = await client.complete(
        [LLMMessage(role="user", content="record this prompt")],
        trace_id="session-1",
        name="test.generation",
        metadata={"phase": "planner", "api_key": "must-drop"},
    )

    assert response.text == "recorded response"
    assert langfuse.started["as_type"] == "generation"
    assert langfuse.started["trace_context"] == {"trace_id": "lf-session-1"}
    assert langfuse.started["input"] == [
        {"role": "user", "content": "record this prompt"}
    ]
    assert langfuse.started["model"] == "test-model"
    assert langfuse.started["metadata"] == {"phase": "planner"}
    assert langfuse.observation.updated["output"] == "recorded response"
    assert langfuse.observation.updated["usage_details"] == {
        "input": 7,
        "output": 3,
        "total": 10,
    }
    assert langfuse.context.closed is True
