"""Tests for the ChatCompletionsTransport."""

import pytest
from types import SimpleNamespace

from agent.transports import get_transport
from agent.transports.types import NormalizedResponse, ToolCall


@pytest.fixture
def transport():
    import agent.transports.chat_completions  # noqa: F401
    return get_transport("chat_completions")


class TestChatCompletionsBasic:

    def test_api_mode(self, transport):
        assert transport.api_mode == "chat_completions"

    def test_registered(self, transport):
        assert transport is not None

    def test_convert_tools_identity(self, transport):
        tools = [{"type": "function", "function": {"name": "test", "parameters": {}}}]
        assert transport.convert_tools(tools) is tools

    def test_convert_messages_no_codex_leaks(self, transport):
        msgs = [{"role": "user", "content": "hi"}]
        result = transport.convert_messages(msgs)
        assert result is msgs  # no copy needed

    def test_convert_messages_strips_codex_fields(self, transport):
        msgs = [
            {"role": "assistant", "content": "ok", "codex_reasoning_items": [{"id": "rs_1"}],
             "tool_calls": [{"id": "call_1", "call_id": "call_1", "response_item_id": "fc_1",
                            "type": "function", "function": {"name": "t", "arguments": "{}"}}]},
        ]
        result = transport.convert_messages(msgs)
        assert "codex_reasoning_items" not in result[0]
        assert "call_id" not in result[0]["tool_calls"][0]
        assert "response_item_id" not in result[0]["tool_calls"][0]


class TestChatCompletionsBuildKwargs:

    def test_basic_kwargs(self, transport):
        msgs = [{"role": "user", "content": "Hello"}]
        kw = transport.build_kwargs(model="gpt-4o", messages=msgs, timeout=30.0)
        assert kw["model"] == "gpt-4o"
        assert kw["messages"][0]["content"] == "Hello"
        assert kw["timeout"] == 30.0

    def test_developer_role_swap(self, transport):
        msgs = [{"role": "system", "content": "You are helpful"}, {"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(model="gpt-5.4", messages=msgs, model_lower="gpt-5.4")
        assert kw["messages"][0]["role"] == "developer"

    def test_no_developer_swap_for_non_gpt5(self, transport):
        msgs = [{"role": "system", "content": "You are helpful"}, {"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(model="claude-sonnet-4", messages=msgs, model_lower="claude-sonnet-4")
        assert kw["messages"][0]["role"] == "system"

    def test_tools_included(self, transport):
        msgs = [{"role": "user", "content": "Hi"}]
        tools = [{"type": "function", "function": {"name": "test", "parameters": {}}}]
        kw = transport.build_kwargs(model="gpt-4o", messages=msgs, tools=tools)
        assert kw["tools"] == tools

    def test_openrouter_provider_prefs(self, transport):
        msgs = [{"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(
            model="gpt-4o", messages=msgs,
            is_openrouter=True,
            provider_preferences={"only": ["openai"]},
        )
        assert kw["extra_body"]["provider"] == {"only": ["openai"]}

    def test_nous_tags(self, transport):
        msgs = [{"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(model="gpt-4o", messages=msgs, is_nous=True)
        assert kw["extra_body"]["tags"] == ["product=hermes-agent"]

    def test_reasoning_default(self, transport):
        msgs = [{"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(
            model="gpt-4o", messages=msgs,
            supports_reasoning=True,
        )
        assert kw["extra_body"]["reasoning"] == {"enabled": True, "effort": "medium"}

    def test_ollama_num_ctx(self, transport):
        msgs = [{"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(
            model="llama3", messages=msgs,
            ollama_num_ctx=32768,
        )
        assert kw["extra_body"]["options"]["num_ctx"] == 32768

    def test_custom_think_false(self, transport):
        msgs = [{"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(
            model="qwen3", messages=msgs,
            is_custom_provider=True,
            reasoning_config={"effort": "none"},
        )
        assert kw["extra_body"]["think"] is False

    def test_max_tokens_with_fn(self, transport):
        msgs = [{"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(
            model="gpt-4o", messages=msgs,
            max_tokens=4096,
            max_tokens_param_fn=lambda n: {"max_tokens": n},
        )
        assert kw["max_tokens"] == 4096

    def test_ephemeral_overrides_max_tokens(self, transport):
        msgs = [{"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(
            model="gpt-4o", messages=msgs,
            max_tokens=4096,
            ephemeral_max_output_tokens=2048,
            max_tokens_param_fn=lambda n: {"max_tokens": n},
        )
        assert kw["max_tokens"] == 2048

    def test_request_overrides_last(self, transport):
        msgs = [{"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(
            model="gpt-4o", messages=msgs,
            request_overrides={"service_tier": "priority"},
        )
        assert kw["service_tier"] == "priority"


class TestChatCompletionsValidate:

    def test_none(self, transport):
        assert transport.validate_response(None) is False

    def test_no_choices(self, transport):
        r = SimpleNamespace(choices=None)
        assert transport.validate_response(r) is False

    def test_empty_choices(self, transport):
        r = SimpleNamespace(choices=[])
        assert transport.validate_response(r) is False

    def test_valid(self, transport):
        r = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))])
        assert transport.validate_response(r) is True


class TestChatCompletionsNormalize:

    def test_text_response(self, transport):
        r = SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="Hello", tool_calls=None, reasoning_content=None),
                finish_reason="stop",
            )],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
        nr = transport.normalize_response(r)
        assert isinstance(nr, NormalizedResponse)
        assert nr.content == "Hello"
        assert nr.finish_reason == "stop"
        assert nr.tool_calls is None

    def test_tool_call_response(self, transport):
        tc = SimpleNamespace(
            id="call_123",
            function=SimpleNamespace(name="terminal", arguments='{"command": "ls"}'),
        )
        r = SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=None, tool_calls=[tc], reasoning_content=None),
                finish_reason="tool_calls",
            )],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )
        nr = transport.normalize_response(r)
        assert len(nr.tool_calls) == 1
        assert nr.tool_calls[0].name == "terminal"
        assert nr.tool_calls[0].id == "call_123"


class TestChatCompletionsCacheStats:

    def test_no_usage(self, transport):
        r = SimpleNamespace(usage=None)
        assert transport.extract_cache_stats(r) is None

    def test_no_details(self, transport):
        r = SimpleNamespace(usage=SimpleNamespace(prompt_tokens_details=None))
        assert transport.extract_cache_stats(r) is None

    def test_with_cache(self, transport):
        details = SimpleNamespace(cached_tokens=500, cache_write_tokens=100)
        r = SimpleNamespace(usage=SimpleNamespace(prompt_tokens_details=details))
        result = transport.extract_cache_stats(r)
        assert result == {"cached_tokens": 500, "creation_tokens": 100}
