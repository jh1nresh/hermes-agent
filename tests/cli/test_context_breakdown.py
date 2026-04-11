"""Tests for /context command — live context window breakdown.

Inspired by Claude Code's /context feature.
"""

import os
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cli(tmp_path):
    """Build a minimal HermesCLI stub with enough state for _show_context_breakdown."""
    from cli import HermesCLI

    cli_obj = object.__new__(HermesCLI)
    # Minimal attrs expected by _show_context_breakdown
    cli_obj.agent = None
    cli_obj.conversation_history = []
    return cli_obj


def _make_agent_stub(model="anthropic/claude-sonnet-4.6", system_prompt="You are Hermes.",
                     context_length=200000, compression_count=0, threshold_tokens=160000,
                     last_prompt_tokens=50000):
    """Return a mock agent with attributes used by _show_context_breakdown."""
    agent = MagicMock()
    agent.model = model
    agent._cached_system_prompt = system_prompt
    agent.session_input_tokens = 1000
    agent.session_output_tokens = 500

    compressor = MagicMock()
    compressor.context_length = context_length
    compressor.compression_count = compression_count
    compressor.threshold_tokens = threshold_tokens
    compressor.last_prompt_tokens = last_prompt_tokens
    agent.context_compressor = compressor

    agent._memory_store = None
    agent._cached_tool_schemas = None
    return agent


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestContextBreakdown:
    """Tests for _show_context_breakdown method."""

    def test_no_agent(self, tmp_path, capsys):
        """When no agent is active, prints a helpful message."""
        cli_obj = _make_cli(tmp_path)
        cli_obj._show_context_breakdown()
        out = capsys.readouterr().out
        assert "No active agent" in out

    def test_basic_breakdown(self, tmp_path, capsys):
        """Basic breakdown shows model, context bar, and section headers."""
        cli_obj = _make_cli(tmp_path)
        cli_obj.agent = _make_agent_stub()
        cli_obj.conversation_history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        cli_obj._show_context_breakdown()
        out = capsys.readouterr().out

        # Model name should appear
        assert "claude-sonnet-4.6" in out
        # Section headers
        assert "System Prompt" in out
        assert "Conversation" in out
        # Token counts appear
        assert "tokens" in out

    def test_shows_context_percentage(self, tmp_path, capsys):
        """The context usage percentage is displayed."""
        cli_obj = _make_cli(tmp_path)
        cli_obj.agent = _make_agent_stub()
        cli_obj.conversation_history = []

        cli_obj._show_context_breakdown()
        out = capsys.readouterr().out
        assert "%" in out

    def test_shows_tool_schemas_when_present(self, tmp_path, capsys):
        """When tool schemas are cached, their token count is shown."""
        cli_obj = _make_cli(tmp_path)
        agent = _make_agent_stub()
        agent._cached_tool_schemas = [
            {"name": "tool1", "description": "Does something", "parameters": {}},
            {"name": "tool2", "description": "Does another thing", "parameters": {}},
        ]
        cli_obj.agent = agent
        cli_obj.conversation_history = []

        cli_obj._show_context_breakdown()
        out = capsys.readouterr().out
        assert "Tool Schemas" in out
        assert "2 tools" in out

    def test_shows_message_role_breakdown(self, tmp_path, capsys):
        """Individual message role counts are shown."""
        cli_obj = _make_cli(tmp_path)
        cli_obj.agent = _make_agent_stub()
        cli_obj.conversation_history = [
            {"role": "user", "content": "Do something"},
            {"role": "assistant", "content": "OK", "tool_calls": [
                {"id": "call_1", "function": {"name": "terminal", "arguments": '{"command":"ls"}'}}
            ]},
            {"role": "tool", "content": '{"output": "file1.py\\nfile2.py"}', "tool_call_id": "call_1"},
            {"role": "assistant", "content": "Found 2 files."},
            {"role": "user", "content": "Good"},
        ]

        cli_obj._show_context_breakdown()
        out = capsys.readouterr().out
        assert "User messages (2)" in out
        assert "Assistant messages (2)" in out
        assert "Tool results (1)" in out

    def test_shows_compression_info(self, tmp_path, capsys):
        """When compressions have occurred, that info is shown."""
        cli_obj = _make_cli(tmp_path)
        cli_obj.agent = _make_agent_stub(compression_count=2)
        cli_obj.conversation_history = []

        cli_obj._show_context_breakdown()
        out = capsys.readouterr().out
        assert "Compressions this session: 2" in out

    def test_shows_auto_compress_threshold(self, tmp_path, capsys):
        """Auto-compress threshold and remaining tokens are shown."""
        cli_obj = _make_cli(tmp_path)
        cli_obj.agent = _make_agent_stub(threshold_tokens=160000)
        cli_obj.conversation_history = []

        cli_obj._show_context_breakdown()
        out = capsys.readouterr().out
        assert "Auto-compress at" in out
        assert "remaining" in out

    def test_detects_compaction_summaries(self, tmp_path, capsys):
        """Messages containing compaction summary markers are identified."""
        from agent.context_compressor import SUMMARY_PREFIX

        cli_obj = _make_cli(tmp_path)
        cli_obj.agent = _make_agent_stub()
        cli_obj.conversation_history = [
            {"role": "assistant", "content": f"{SUMMARY_PREFIX}\n## Goal\nBuild a feature."},
            {"role": "user", "content": "Continue from the summary."},
        ]

        cli_obj._show_context_breakdown()
        out = capsys.readouterr().out
        assert "Compaction summaries" in out

    def test_bar_rendering(self, tmp_path, capsys):
        """The progress bar renders block characters."""
        cli_obj = _make_cli(tmp_path)
        cli_obj.agent = _make_agent_stub()
        cli_obj.conversation_history = [
            {"role": "user", "content": "x" * 1000},
        ]

        cli_obj._show_context_breakdown()
        out = capsys.readouterr().out
        # Should contain block characters from the bar
        assert "█" in out or "░" in out

    def test_identifies_skills_section(self, tmp_path, capsys):
        """When system prompt contains skills marker, it's broken out."""
        system_prompt = (
            "You are Hermes.\n\n"
            "## Skills (mandatory)\n"
            "Before replying, scan the skills below.\n"
            "<available_skills>\n  skill1: does something\n</available_skills>\n\n"
            "Conversation started: Friday, April 10, 2026"
        )
        cli_obj = _make_cli(tmp_path)
        cli_obj.agent = _make_agent_stub(system_prompt=system_prompt)
        cli_obj.conversation_history = []

        cli_obj._show_context_breakdown()
        out = capsys.readouterr().out
        assert "Skills index" in out

    def test_identifies_context_files_section(self, tmp_path, capsys):
        """When system prompt contains context files marker, it's broken out."""
        system_prompt = (
            "You are Hermes.\n\n"
            "# Project Context\n\n"
            "## AGENTS.md\nDevelopment guide content here...\n\n"
            "Conversation started: Friday, April 10, 2026"
        )
        cli_obj = _make_cli(tmp_path)
        cli_obj.agent = _make_agent_stub(system_prompt=system_prompt)
        cli_obj.conversation_history = []

        cli_obj._show_context_breakdown()
        out = capsys.readouterr().out
        assert "Context files" in out


class TestCompressFocusTopic:
    """Tests for /compress <focus> — guided compression."""

    def test_focus_topic_extracted(self, tmp_path, capsys):
        """Focus topic is extracted from the command string."""
        cli_obj = _make_cli(tmp_path)
        agent = _make_agent_stub()
        agent.compression_enabled = True
        agent._cached_system_prompt = "You are Hermes."
        # Make compress return the messages unchanged for testing
        agent._compress_context = MagicMock(return_value=(
            [{"role": "user", "content": "test"}],
            "system prompt",
        ))
        cli_obj.agent = agent
        cli_obj.conversation_history = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
            {"role": "assistant", "content": "d"},
        ]

        cli_obj._manual_compress("/compress database schema")
        out = capsys.readouterr().out
        assert 'focus: "database schema"' in out

        # Verify the focus_topic was passed through
        agent._compress_context.assert_called_once()
        call_kwargs = agent._compress_context.call_args
        assert call_kwargs.kwargs.get("focus_topic") == "database schema"

    def test_no_focus_topic_when_bare_command(self, tmp_path, capsys):
        """When no focus topic is provided, None is passed."""
        cli_obj = _make_cli(tmp_path)
        agent = _make_agent_stub()
        agent.compression_enabled = True
        agent._cached_system_prompt = "You are Hermes."
        agent._compress_context = MagicMock(return_value=(
            [{"role": "user", "content": "test"}],
            "system prompt",
        ))
        cli_obj.agent = agent
        cli_obj.conversation_history = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
            {"role": "assistant", "content": "d"},
        ]

        cli_obj._manual_compress("/compress")
        agent._compress_context.assert_called_once()
        call_kwargs = agent._compress_context.call_args
        assert call_kwargs.kwargs.get("focus_topic") is None

    def test_focus_topic_in_generate_summary_prompt(self):
        """Focus topic is injected into the LLM prompt for summarization."""
        from agent.context_compressor import ContextCompressor

        compressor = ContextCompressor.__new__(ContextCompressor)
        compressor.protect_first_n = 2
        compressor.protect_last_n = 5
        compressor.tail_token_budget = 20000
        compressor.context_length = 200000
        compressor.threshold_percent = 0.80
        compressor.threshold_tokens = 160000
        compressor.max_summary_tokens = 10000
        compressor.quiet_mode = True
        compressor.compression_count = 0
        compressor.last_prompt_tokens = 0
        compressor._previous_summary = None
        compressor._summary_failure_cooldown_until = 0.0
        compressor.summary_model = None

        turns = [
            {"role": "user", "content": "Tell me about the database schema"},
            {"role": "assistant", "content": "The schema has tables: users, orders, products."},
        ]

        # Mock call_llm to capture the prompt
        captured_prompt = {}

        def mock_call_llm(**kwargs):
            captured_prompt["messages"] = kwargs["messages"]
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = "## Goal\nUnderstand DB schema."
            return resp

        with patch("agent.context_compressor.call_llm", mock_call_llm):
            result = compressor._generate_summary(turns, focus_topic="database schema")

        assert result is not None
        prompt_text = captured_prompt["messages"][0]["content"]
        assert 'FOCUS TOPIC: "database schema"' in prompt_text
        assert "PRIORITISE" in prompt_text

    def test_no_focus_topic_no_injection(self):
        """Without focus_topic, the prompt doesn't contain focus guidance."""
        from agent.context_compressor import ContextCompressor

        compressor = ContextCompressor.__new__(ContextCompressor)
        compressor.protect_first_n = 2
        compressor.protect_last_n = 5
        compressor.tail_token_budget = 20000
        compressor.context_length = 200000
        compressor.threshold_percent = 0.80
        compressor.threshold_tokens = 160000
        compressor.max_summary_tokens = 10000
        compressor.quiet_mode = True
        compressor.compression_count = 0
        compressor.last_prompt_tokens = 0
        compressor._previous_summary = None
        compressor._summary_failure_cooldown_until = 0.0
        compressor.summary_model = None

        turns = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]

        captured_prompt = {}

        def mock_call_llm(**kwargs):
            captured_prompt["messages"] = kwargs["messages"]
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = "## Goal\nGreeting."
            return resp

        with patch("agent.context_compressor.call_llm", mock_call_llm):
            result = compressor._generate_summary(turns)

        prompt_text = captured_prompt["messages"][0]["content"]
        assert "FOCUS TOPIC" not in prompt_text
