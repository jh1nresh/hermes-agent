"""Tests for Mistral Magistral structured content handling.

Mistral's Magistral reasoning models return ``content`` as a list of typed
blocks instead of a plain string (both in streaming deltas and non-streaming
responses).  This test suite verifies that:

1. _normalize_structured_content() correctly extracts text and thinking parts.
2. The streaming path handles list-valued delta.content without crashing.
3. The non-streaming path normalizes list content and extracts thinking.
4. _build_assistant_message handles list content correctly.
"""

import os
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ── Ensure HERMES_HOME is set before importing run_agent ──────────────
if not os.environ.get("HERMES_HOME"):
    import tempfile

    _tmp = tempfile.mkdtemp(prefix="hermes_test_")
    os.environ["HERMES_HOME"] = _tmp

from run_agent import AIAgent, _normalize_structured_content


# ── Fixtures ──────────────────────────────────────────────────────────

def _make_tool_defs(*names):
    """Build minimal tool definitions matching get_tool_definitions output."""
    return [
        {"type": "function", "function": {"name": n, "description": n, "parameters": {}}}
        for n in names
    ]


@pytest.fixture
def agent():
    """Minimal AIAgent for testing _build_assistant_message."""
    with (
        patch("run_agent.get_tool_definitions", return_value=_make_tool_defs("web_search")),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        ag = AIAgent(
            api_key="test-key-1234567890",
            model="mistral/magistral-medium-latest",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    ag.client = MagicMock()
    ag.verbose_logging = False
    ag.reasoning_callback = None
    ag.stream_delta_callback = None
    return ag


# ── Sample data matching Mistral's API format ─────────────────────────

MAGISTRAL_CONTENT_BLOCKS = [
    {
        "type": "thinking",
        "thinking": [
            {"type": "text", "text": "Let me think about this step by step."},
            {"type": "text", "text": "The capital of France is Paris."},
        ],
    },
    {"type": "text", "text": "The capital of France is Paris."},
]

MAGISTRAL_TEXT_ONLY_BLOCKS = [
    {"type": "text", "text": "Hello, how can I help?"},
]

MAGISTRAL_WITH_REFERENCE = [
    {"type": "thinking", "thinking": [{"type": "text", "text": "Checking references."}]},
    {"type": "text", "text": "Here is the answer."},
    {"type": "reference", "url": "https://example.com"},
]

STREAMING_THINKING_DELTA = [
    {"type": "thinking", "thinking": [{"type": "text", "text": "Okay"}]},
]

STREAMING_TEXT_DELTA = [
    {"type": "text", "text": "Hello"},
]


# ── Tests: _normalize_structured_content ──────────────────────────────

class TestNormalizeStructuredContent:
    """Tests for the _normalize_structured_content helper."""

    def test_string_passthrough(self):
        text, thinking = _normalize_structured_content("Hello world")
        assert text == "Hello world"
        assert thinking is None

    def test_none_returns_empty_string(self):
        text, thinking = _normalize_structured_content(None)
        assert text == ""
        assert thinking is None

    def test_non_list_non_string_coerced(self):
        text, thinking = _normalize_structured_content(42)
        assert text == "42"
        assert thinking is None

    def test_magistral_full_response(self):
        text, thinking = _normalize_structured_content(MAGISTRAL_CONTENT_BLOCKS)
        assert text == "The capital of France is Paris."
        assert "step by step" in thinking
        assert "capital of France is Paris" in thinking

    def test_text_only_blocks(self):
        text, thinking = _normalize_structured_content(MAGISTRAL_TEXT_ONLY_BLOCKS)
        assert text == "Hello, how can I help?"
        assert thinking is None

    def test_with_reference_blocks(self):
        """Reference blocks should be skipped, not cause errors."""
        text, thinking = _normalize_structured_content(MAGISTRAL_WITH_REFERENCE)
        assert text == "Here is the answer."
        assert thinking == "Checking references."

    def test_streaming_thinking_delta(self):
        text, thinking = _normalize_structured_content(STREAMING_THINKING_DELTA)
        assert text == ""
        assert thinking == "Okay"

    def test_streaming_text_delta(self):
        text, thinking = _normalize_structured_content(STREAMING_TEXT_DELTA)
        assert text == "Hello"
        assert thinking is None

    def test_empty_list(self):
        text, thinking = _normalize_structured_content([])
        assert text == ""
        assert thinking is None

    def test_mixed_string_and_dict_blocks(self):
        """Some providers might mix raw strings with typed blocks."""
        content = ["raw text", {"type": "text", "text": "typed text"}]
        text, thinking = _normalize_structured_content(content)
        assert "raw text" in text
        assert "typed text" in text

    def test_thinking_as_plain_string(self):
        """Handle edge case where thinking value is a string not a list."""
        content = [{"type": "thinking", "thinking": "I'm thinking..."}]
        text, thinking = _normalize_structured_content(content)
        assert text == ""
        assert thinking == "I'm thinking..."

    def test_multiple_text_blocks_joined(self):
        content = [
            {"type": "text", "text": "First paragraph."},
            {"type": "text", "text": "Second paragraph."},
        ]
        text, thinking = _normalize_structured_content(content)
        assert "First paragraph." in text
        assert "Second paragraph." in text
        assert "\n" in text  # joined with newline

    def test_empty_thinking_block(self):
        """Thinking block with no text should result in thinking=None."""
        content = [
            {"type": "thinking", "thinking": []},
            {"type": "text", "text": "Answer"},
        ]
        text, thinking = _normalize_structured_content(content)
        assert text == "Answer"
        assert thinking is None


# ── Tests: _build_assistant_message with structured content ────────────

class TestBuildAssistantMessageStructuredContent:
    """Tests that _build_assistant_message correctly handles Mistral list content."""

    def test_list_content_normalized_to_string(self, agent):
        msg = SimpleNamespace(
            content=MAGISTRAL_CONTENT_BLOCKS,
            tool_calls=None,
        )
        result = agent._build_assistant_message(msg, "stop")
        assert isinstance(result["content"], str)
        assert "The capital of France is Paris." in result["content"]

    def test_list_content_thinking_extracted(self, agent):
        msg = SimpleNamespace(
            content=MAGISTRAL_CONTENT_BLOCKS,
            tool_calls=None,
        )
        result = agent._build_assistant_message(msg, "stop")
        assert result["reasoning"] is not None
        assert "step by step" in result["reasoning"]

    def test_string_content_unchanged(self, agent):
        msg = SimpleNamespace(
            content="Normal string response",
            tool_calls=None,
        )
        result = agent._build_assistant_message(msg, "stop")
        assert result["content"] == "Normal string response"

    def test_list_content_with_tool_calls(self, agent):
        tool_call = SimpleNamespace(
            id="call_123",
            type="function",
            function=SimpleNamespace(name="web_search", arguments='{"query": "test"}'),
        )
        msg = SimpleNamespace(
            content=MAGISTRAL_CONTENT_BLOCKS,
            tool_calls=[tool_call],
        )
        result = agent._build_assistant_message(msg, "tool_calls")
        assert isinstance(result["content"], str)
        assert "tool_calls" in result

    def test_text_only_blocks_no_reasoning(self, agent):
        msg = SimpleNamespace(
            content=MAGISTRAL_TEXT_ONLY_BLOCKS,
            tool_calls=None,
        )
        result = agent._build_assistant_message(msg, "stop")
        assert result["content"] == "Hello, how can I help?"
        assert result["reasoning"] is None

    def test_structured_thinking_not_duplicated_with_reasoning_content(self, agent):
        """When reasoning_content is set AND content has thinking blocks,
        don't duplicate the reasoning."""
        msg = SimpleNamespace(
            content=MAGISTRAL_CONTENT_BLOCKS,
            tool_calls=None,
            reasoning_content="Already extracted reasoning",
        )
        result = agent._build_assistant_message(msg, "stop")
        # Should use the already-set reasoning_content, not duplicate
        assert result["reasoning"] == "Already extracted reasoning"


# ── Tests: Non-streaming content normalization ─────────────────────────

class TestNonStreamingContentNormalization:
    """Tests for the non-streaming content normalization block in the agent loop."""

    def test_list_content_normalized(self, agent):
        """Simulate the normalization block that runs after getting the
        assistant_message from response.choices[0].message."""
        msg = SimpleNamespace(content=MAGISTRAL_CONTENT_BLOCKS, tool_calls=None)

        # Simulate the normalization block from run_agent.py
        if msg.content is not None and not isinstance(msg.content, str):
            raw = msg.content
            if isinstance(raw, list):
                text, thinking = _normalize_structured_content(raw)
                msg.content = text
                if thinking and not getattr(msg, "reasoning_content", None):
                    msg.reasoning_content = thinking

        assert isinstance(msg.content, str)
        assert "The capital of France is Paris." in msg.content
        assert hasattr(msg, "reasoning_content")
        assert "step by step" in msg.reasoning_content

    def test_dict_content_handled(self, agent):
        """Dict content (from llama-server etc.) should still work."""
        msg = SimpleNamespace(content={"text": "Hello from dict"}, tool_calls=None)

        if msg.content is not None and not isinstance(msg.content, str):
            raw = msg.content
            if isinstance(raw, dict):
                msg.content = raw.get("text", "") or raw.get("content", "") or str(raw)

        assert msg.content == "Hello from dict"


# ── Tests: Streaming delta normalization ───────────────────────────────

class TestStreamingDeltaNormalization:
    """Tests for the streaming delta content normalization."""

    def test_list_delta_content_split(self):
        """When delta.content is a list, text goes to content_parts
        and thinking goes to reasoning_parts."""
        content_parts = []
        reasoning_parts = []

        # Simulate the streaming normalization block
        delta_content = MAGISTRAL_CONTENT_BLOCKS
        if isinstance(delta_content, list):
            text, thinking = _normalize_structured_content(delta_content)
            if thinking:
                reasoning_parts.append(thinking)
        else:
            text = delta_content

        if text:
            content_parts.append(text)

        # Verify text and thinking are separated
        assert len(content_parts) == 1
        assert "The capital of France is Paris." in content_parts[0]
        assert len(reasoning_parts) == 1
        assert "step by step" in reasoning_parts[0]

        # Verify join succeeds (this was the original crash)
        full_content = "".join(content_parts)
        assert isinstance(full_content, str)

    def test_string_delta_passthrough(self):
        """Normal string deltas should work unchanged."""
        content_parts = []
        delta_content = "Hello"

        if isinstance(delta_content, list):
            text, _ = _normalize_structured_content(delta_content)
        else:
            text = delta_content

        if text:
            content_parts.append(text)

        full_content = "".join(content_parts)
        assert full_content == "Hello"

    def test_thinking_only_delta(self):
        """Streaming delta with only thinking and no text."""
        content_parts = []
        reasoning_parts = []

        delta_content = STREAMING_THINKING_DELTA
        if isinstance(delta_content, list):
            text, thinking = _normalize_structured_content(delta_content)
            if thinking:
                reasoning_parts.append(thinking)
        else:
            text = delta_content

        if text:
            content_parts.append(text)

        # No text content, only reasoning
        assert len(content_parts) == 0
        assert len(reasoning_parts) == 1
        assert reasoning_parts[0] == "Okay"

        # Join should succeed (empty list)
        full_content = "".join(content_parts) or None
        assert full_content is None

    def test_multiple_streaming_chunks_joined(self):
        """Multiple streaming chunks with mixed list and string content."""
        content_parts = []
        reasoning_parts = []

        chunks = [
            STREAMING_THINKING_DELTA,  # list: thinking only
            STREAMING_TEXT_DELTA,  # list: text only
            "more text",  # string
        ]

        for delta_content in chunks:
            if isinstance(delta_content, list):
                text, thinking = _normalize_structured_content(delta_content)
                if thinking:
                    reasoning_parts.append(thinking)
            else:
                text = delta_content

            if text:
                content_parts.append(text)

        full_content = "".join(content_parts)
        full_reasoning = "".join(reasoning_parts) or None

        assert full_content == "Hellomore text"
        assert full_reasoning == "Okay"
