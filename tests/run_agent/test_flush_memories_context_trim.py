"""Tests for flush_memories context-overflow prevention.

1. _check_compression_model_feasibility now also resolves the
   flush_memories auxiliary model and uses min(compression, flush) as the
   effective aux context.
2. Headroom is always deducted before comparing aux_context vs threshold
   (not only when aux_context < threshold).
3. flush_memories() trims oversized conversations before the LLM call as
   defence-in-depth for paths that bypass preflight compression.
"""

import sys
import types
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

sys.modules.setdefault("fire", types.SimpleNamespace(Fire=lambda *a, **k: None))
sys.modules.setdefault("firecrawl", types.SimpleNamespace(Firecrawl=object))
sys.modules.setdefault("fal_client", types.SimpleNamespace())

import run_agent


# ── Helpers ──────────────────────────────────────────────────────────────


class _FakeOpenAI:
    def __init__(self, **kw):
        self.api_key = kw.get("api_key", "test")
        self.base_url = kw.get("base_url", "http://test")

    def close(self):
        pass


def _make_agent(monkeypatch, **kw):
    monkeypatch.setattr(run_agent, "get_tool_definitions", lambda **k: [
        {"type": "function", "function": {
            "name": "memory", "description": "m",
            "parameters": {"type": "object", "properties": {
                "action": {"type": "string"},
                "target": {"type": "string"},
                "content": {"type": "string"},
            }},
        }},
    ])
    monkeypatch.setattr(run_agent, "check_toolset_requirements", lambda: {})
    monkeypatch.setattr(run_agent, "OpenAI", _FakeOpenAI)
    agent = run_agent.AIAgent(
        api_key="test-key", base_url="https://test.example.com/v1",
        provider=kw.get("provider", "openrouter"),
        api_mode=kw.get("api_mode", "chat_completions"),
        max_iterations=4, quiet_mode=True,
        skip_context_files=True, skip_memory=True,
    )
    agent._memory_store = MagicMock()
    agent._memory_flush_min_turns = 1
    agent._user_turn_count = 5
    return agent


def _make_msgs(n, chars=400):
    return [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"M{i}: " + "x" * max(0, chars - 6)}
            for i in range(n)]


def _noop_response():
    return SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason="stop",
            message=SimpleNamespace(content="Nothing.", tool_calls=None),
        )],
        usage=SimpleNamespace(prompt_tokens=50, completion_tokens=10, total_tokens=60),
    )


# ── Feasibility: flush model + always-deduct headroom ────────────────────


class TestFeasibilityFixes:

    def test_smaller_flush_model_lowers_effective_context(self, monkeypatch):
        """flush_memories model with smaller context drives the threshold."""
        agent = _make_agent(monkeypatch)
        agent.context_compressor.context_length = 200_000
        agent.context_compressor.threshold_tokens = 100_000

        fc = SimpleNamespace(base_url="http://test", api_key="k")

        def _aux(task, **kw):
            if task == "compression":
                return fc, "big-model"
            return fc, "small-flush-model"

        def _ctx(model, **kw):
            return 200_000 if model == "big-model" else 80_000

        with patch("agent.auxiliary_client.get_text_auxiliary_client", side_effect=_aux), \
             patch("agent.model_metadata.get_model_context_length", side_effect=_ctx):
            agent._check_compression_model_feasibility()

        assert agent.context_compressor.threshold_tokens < 100_000

    def test_same_model_overhead_still_triggers_correction(self, monkeypatch):
        """The primary bug: aux == main model, aux_context > threshold, but
        threshold + overhead > aux_context.  Headroom must fire even when
        aux_context >= threshold."""
        agent = _make_agent(monkeypatch)
        agent.context_compressor.context_length = 128_000
        agent.context_compressor.threshold_tokens = 120_000

        fc = SimpleNamespace(base_url="http://test", api_key="k")

        with patch("agent.auxiliary_client.get_text_auxiliary_client",
                    return_value=(fc, "same-model")), \
             patch("agent.model_metadata.get_model_context_length",
                    return_value=128_000):
            agent._check_compression_model_feasibility()

        # 128K - headroom (~12.1K) ≈ 115.9K < 120K → threshold lowered
        assert agent.context_compressor.threshold_tokens < 120_000

    def test_flush_resolution_failure_is_non_fatal(self, monkeypatch):
        """If flush model resolution raises, check proceeds with compression model."""
        agent = _make_agent(monkeypatch)
        agent.context_compressor.context_length = 200_000
        agent.context_compressor.threshold_tokens = 100_000

        fc = SimpleNamespace(base_url="http://test", api_key="k")
        n = [0]

        def _aux(task, **kw):
            n[0] += 1
            if task == "flush_memories":
                raise RuntimeError("boom")
            return fc, "model"

        with patch("agent.auxiliary_client.get_text_auxiliary_client", side_effect=_aux), \
             patch("agent.model_metadata.get_model_context_length", return_value=200_000):
            agent._check_compression_model_feasibility()

        assert n[0] == 2  # both tasks attempted


# ── flush_memories trimming ──────────────────────────────────────────────


class TestFlushMemoriesTrimming:

    def test_oversized_conversation_trimmed(self, monkeypatch):
        agent = _make_agent(monkeypatch)
        agent._cached_system_prompt = "System."
        messages = _make_msgs(200, chars=500)

        fc = SimpleNamespace(base_url="http://test", api_key="k")
        with patch("agent.auxiliary_client.get_text_auxiliary_client",
                    return_value=(fc, "small")), \
             patch("agent.model_metadata.get_model_context_length",
                    return_value=8_000), \
             patch("agent.auxiliary_client.call_llm",
                    return_value=_noop_response()) as mock:
            agent.flush_memories(messages)

        sent = mock.call_args.kwargs.get("messages", [])
        assert len(sent) < 100

    def test_small_conversation_untouched(self, monkeypatch):
        agent = _make_agent(monkeypatch)
        agent._cached_system_prompt = "System."
        messages = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hey"},
            {"role": "user", "content": "Save"},
        ]

        fc = SimpleNamespace(base_url="http://test", api_key="k")
        with patch("agent.auxiliary_client.get_text_auxiliary_client",
                    return_value=(fc, "big")), \
             patch("agent.model_metadata.get_model_context_length",
                    return_value=200_000), \
             patch("agent.auxiliary_client.call_llm",
                    return_value=_noop_response()) as mock:
            agent.flush_memories(messages)

        sent = mock.call_args.kwargs.get("messages", [])
        assert len(sent) == 5  # sys + 3 conv + flush

    def test_trim_failure_does_not_block_flush(self, monkeypatch):
        agent = _make_agent(monkeypatch)
        messages = _make_msgs(10, chars=100)

        with patch("agent.auxiliary_client.get_text_auxiliary_client",
                    side_effect=RuntimeError("no provider")), \
             patch("agent.auxiliary_client.call_llm",
                    return_value=_noop_response()) as mock:
            agent.flush_memories(messages)
            assert mock.called

    def test_sentinel_cleaned_after_trim(self, monkeypatch):
        agent = _make_agent(monkeypatch)
        messages = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hey"},
            {"role": "user", "content": "Save"},
        ]
        n = len(messages)

        fc = SimpleNamespace(base_url="http://test", api_key="k")
        with patch("agent.auxiliary_client.get_text_auxiliary_client",
                    return_value=(fc, "m")), \
             patch("agent.model_metadata.get_model_context_length",
                    return_value=128_000), \
             patch("agent.auxiliary_client.call_llm",
                    return_value=_noop_response()):
            agent.flush_memories(messages)

        assert len(messages) == n
        assert not any(m.get("_flush_sentinel") for m in messages)
