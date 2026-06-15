"""Regression guard: preserve thinking blocks on Xiaomi MiMo /anthropic.

Xiaomi MiMo's Anthropic-compatible route requires unsigned thinking blocks
synthesised from ``reasoning_content`` to round-trip on replayed assistant
tool-call messages.  The generic third-party Anthropic path strips thinking
blocks, which breaks multi-turn tool-use replay on MiMo.
"""

from __future__ import annotations

import pytest


class TestXiaomiAnthropicPreservesThinking:
    @pytest.mark.parametrize(
        "base_url",
        [
            "https://token-plan-cn.xiaomimimo.com/anthropic",
            "https://token-plan-sgp.xiaomimimo.com/anthropic/",
            "https://api.xiaomimimo.com/anthropic/v1",
            "https://API.XiaomiMiMo.com/anthropic",
        ],
    )
    def test_unsigned_thinking_block_survives_replay(self, base_url: str) -> None:
        from agent.anthropic_adapter import convert_messages_to_anthropic

        messages = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "reasoning_content": "planning the tool call",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "terminal", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ]
        _system, converted = convert_messages_to_anthropic(messages, base_url=base_url)

        assistant_msg = next(m for m in converted if m["role"] == "assistant")
        thinking_blocks = [
            b for b in assistant_msg["content"]
            if isinstance(b, dict) and b.get("type") == "thinking"
        ]
        assert len(thinking_blocks) == 1
        assert thinking_blocks[0]["thinking"] == "planning the tool call"
        assert "signature" not in thinking_blocks[0]

    def test_signed_anthropic_thinking_block_is_stripped(self) -> None:
        from agent.anthropic_adapter import convert_messages_to_anthropic

        messages = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "anthropic-signed payload",
                        "signature": "anthropic-sig-xyz",
                    },
                    {"type": "text", "text": "hello"},
                ],
            },
            {"role": "user", "content": "again"},
        ]
        _system, converted = convert_messages_to_anthropic(
            messages,
            base_url="https://token-plan-cn.xiaomimimo.com/anthropic",
        )

        assistant_msg = next(m for m in converted if m["role"] == "assistant")
        thinking_blocks = [
            b for b in assistant_msg["content"]
            if isinstance(b, dict) and b.get("type") == "thinking"
        ]
        assert thinking_blocks == []

    def test_openai_compat_xiaomi_base_is_not_matched(self) -> None:
        from agent.anthropic_adapter import _is_xiaomi_anthropic_endpoint

        assert _is_xiaomi_anthropic_endpoint("https://api.xiaomimimo.com") is False
        assert _is_xiaomi_anthropic_endpoint("https://api.xiaomimimo.com/v1") is False
        assert _is_xiaomi_anthropic_endpoint("https://api.xiaomimimo.com/anthropic") is True
        assert _is_xiaomi_anthropic_endpoint("https://token-plan-sgp.xiaomimimo.com/anthropic/v1") is True

    def test_lookalike_host_is_not_matched(self) -> None:
        from agent.anthropic_adapter import _is_xiaomi_anthropic_endpoint

        assert _is_xiaomi_anthropic_endpoint("https://xiaomimimo.com.evil/anthropic") is False
        assert _is_xiaomi_anthropic_endpoint("https://evil.example.com/xiaomimimo.com/anthropic") is False

    def test_non_xiaomi_third_party_still_strips_all_thinking(self) -> None:
        from agent.anthropic_adapter import convert_messages_to_anthropic

        messages = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "reasoning_content": "r1",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "terminal", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ]
        _system, converted = convert_messages_to_anthropic(
            messages,
            base_url="https://api.minimax.io/anthropic",
        )
        assistant_msg = next(m for m in converted if m["role"] == "assistant")
        thinking_blocks = [
            b for b in assistant_msg["content"]
            if isinstance(b, dict) and b.get("type") == "thinking"
        ]
        assert thinking_blocks == []
