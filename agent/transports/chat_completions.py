"""OpenAI Chat Completions transport.

Handles the default api_mode ('chat_completions') used by ~16 OpenAI-compatible
providers (OpenRouter, Nous, NVIDIA, Qwen, Ollama, DeepSeek, xAI, custom, etc.).

Messages and tools are already in OpenAI format — convert_messages and
convert_tools are near-identity.  The complexity lives in build_kwargs
which has provider-specific conditionals for max_tokens defaults,
reasoning configuration, temperature handling, and extra_body assembly.
"""

import copy
import uuid
from typing import Any, Dict, List, Optional

from agent.transports.base import ProviderTransport
from agent.transports.types import NormalizedResponse, ToolCall, Usage


from agent.prompt_builder import DEVELOPER_ROLE_MODELS


class ChatCompletionsTransport(ProviderTransport):
    """Transport for api_mode='chat_completions'.

    The default path for OpenAI-compatible providers.
    """

    @property
    def api_mode(self) -> str:
        return "chat_completions"

    def convert_messages(self, messages: List[Dict[str, Any]], **kwargs) -> List[Dict[str, Any]]:
        """Messages are already in OpenAI format — sanitize codex leaks only."""
        sanitized = messages
        needs_sanitize = False
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            if "codex_reasoning_items" in msg:
                needs_sanitize = True
                break
            tool_calls = msg.get("tool_calls")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if isinstance(tc, dict) and ("call_id" in tc or "response_item_id" in tc):
                        needs_sanitize = True
                        break
                if needs_sanitize:
                    break

        if needs_sanitize:
            sanitized = copy.deepcopy(messages)
            for msg in sanitized:
                if not isinstance(msg, dict):
                    continue
                msg.pop("codex_reasoning_items", None)
                tool_calls = msg.get("tool_calls")
                if isinstance(tool_calls, list):
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            tc.pop("call_id", None)
                            tc.pop("response_item_id", None)

        return sanitized

    def convert_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Tools are already in OpenAI format — identity."""
        return tools

    def build_kwargs(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **params,
    ) -> Dict[str, Any]:
        """Build chat.completions.create() kwargs.

        This is the most complex transport method — it handles ~16 providers
        via params rather than subclasses.

        params:
            timeout: float — API call timeout
            max_tokens: int | None — user-configured max tokens
            ephemeral_max_output_tokens: int | None — one-shot override (error recovery)
            max_tokens_param_fn: callable — returns {max_tokens: N} or {max_completion_tokens: N}
            reasoning_config: dict | None
            request_overrides: dict | None
            session_id: str | None
            model_lower: str — lowercase model name for pattern matching
            base_url_lower: str — lowercase base URL
            # Provider detection flags
            is_openrouter: bool
            is_nous: bool
            is_qwen_portal: bool
            is_github_models: bool
            is_nvidia_nim: bool
            is_custom_provider: bool
            ollama_num_ctx: int | None
            # Provider routing
            provider_preferences: dict | None
            # Qwen-specific
            qwen_prepare_fn: callable | None — prep fn applied AFTER codex sanitization
            qwen_metadata: dict | None
            # Temperature
            fixed_temperature: Any — from _fixed_temperature_for_model()
            omit_temperature: bool
            # Reasoning
            supports_reasoning: bool
            github_reasoning_extra: dict | None
            # Claude on OpenRouter max output
            anthropic_max_output: int | None
            # Extra
            extra_body_additions: dict | None — pre-built extra_body entries
        """
        # Start with sanitized messages (codex field stripping)
        sanitized = self.convert_messages(messages)

        # Qwen portal prep AFTER codex sanitization (must transform sanitized messages)
        qwen_fn = params.get("qwen_prepare_fn")
        if qwen_fn is not None:
            sanitized = qwen_fn(sanitized)

        # Developer role swap for GPT-5/Codex models
        model_lower = params.get("model_lower", (model or "").lower())
        if (
            sanitized
            and isinstance(sanitized[0], dict)
            and sanitized[0].get("role") == "system"
            and any(p in model_lower for p in DEVELOPER_ROLE_MODELS)
        ):
            sanitized = list(sanitized)
            sanitized[0] = {**sanitized[0], "role": "developer"}

        api_kwargs: Dict[str, Any] = {
            "model": model,
            "messages": sanitized,
        }

        timeout = params.get("timeout")
        if timeout is not None:
            api_kwargs["timeout"] = timeout

        # Temperature
        fixed_temp = params.get("fixed_temperature")
        omit_temp = params.get("omit_temperature", False)
        if omit_temp:
            api_kwargs.pop("temperature", None)
        elif fixed_temp is not None:
            api_kwargs["temperature"] = fixed_temp

        # Qwen metadata
        qwen_meta = params.get("qwen_metadata")
        if qwen_meta:
            api_kwargs["metadata"] = qwen_meta

        # Tools
        if tools:
            api_kwargs["tools"] = tools

        # max_tokens resolution
        max_tokens_fn = params.get("max_tokens_param_fn")
        ephemeral = params.get("ephemeral_max_output_tokens")
        max_tokens = params.get("max_tokens")
        anthropic_max_out = params.get("anthropic_max_output")

        if ephemeral is not None and max_tokens_fn:
            api_kwargs.update(max_tokens_fn(ephemeral))
        elif max_tokens is not None and max_tokens_fn:
            api_kwargs.update(max_tokens_fn(max_tokens))
        elif params.get("is_nvidia_nim") and max_tokens_fn:
            api_kwargs.update(max_tokens_fn(16384))
        elif params.get("is_qwen_portal") and max_tokens_fn:
            api_kwargs.update(max_tokens_fn(65536))
        elif anthropic_max_out is not None:
            api_kwargs["max_tokens"] = anthropic_max_out

        # extra_body assembly
        extra_body: Dict[str, Any] = {}

        is_openrouter = params.get("is_openrouter", False)
        is_nous = params.get("is_nous", False)
        is_github_models = params.get("is_github_models", False)

        provider_prefs = params.get("provider_preferences")
        if provider_prefs and is_openrouter:
            extra_body["provider"] = provider_prefs

        # Reasoning
        if params.get("supports_reasoning", False):
            if is_github_models:
                gh_reasoning = params.get("github_reasoning_extra")
                if gh_reasoning is not None:
                    extra_body["reasoning"] = gh_reasoning
            else:
                reasoning_config = params.get("reasoning_config")
                if reasoning_config is not None:
                    rc = dict(reasoning_config)
                    if is_nous and rc.get("enabled") is False:
                        pass  # omit for Nous when disabled
                    else:
                        extra_body["reasoning"] = rc
                else:
                    extra_body["reasoning"] = {"enabled": True, "effort": "medium"}

        if is_nous:
            extra_body["tags"] = ["product=hermes-agent"]

        # Ollama num_ctx
        ollama_ctx = params.get("ollama_num_ctx")
        if ollama_ctx:
            options = extra_body.get("options", {})
            options["num_ctx"] = ollama_ctx
            extra_body["options"] = options

        # Ollama/custom think=false
        if params.get("is_custom_provider", False):
            reasoning_config = params.get("reasoning_config")
            if reasoning_config and isinstance(reasoning_config, dict):
                _effort = (reasoning_config.get("effort") or "").strip().lower()
                _enabled = reasoning_config.get("enabled", True)
                if _effort == "none" or _enabled is False:
                    extra_body["think"] = False

        if params.get("is_qwen_portal"):
            extra_body["vl_high_resolution_images"] = True

        # Merge any pre-built extra_body additions
        additions = params.get("extra_body_additions")
        if additions:
            extra_body.update(additions)

        if extra_body:
            api_kwargs["extra_body"] = extra_body

        # Request overrides last
        overrides = params.get("request_overrides")
        if overrides:
            api_kwargs.update(overrides)

        return api_kwargs

    def normalize_response(self, response: Any, **kwargs) -> NormalizedResponse:
        """Normalize OpenAI ChatCompletion to NormalizedResponse.

        For chat_completions, this is near-identity — the response is
        already in OpenAI format.
        """
        choice = response.choices[0]
        msg = choice.message
        finish_reason = choice.finish_reason or "stop"

        tool_calls = None
        if msg.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=tc.function.arguments,
                )
                for tc in msg.tool_calls
            ]

        usage = None
        if hasattr(response, "usage") and response.usage:
            u = response.usage
            usage = Usage(
                prompt_tokens=getattr(u, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(u, "completion_tokens", 0) or 0,
                total_tokens=getattr(u, "total_tokens", 0) or 0,
            )

        # reasoning_content is used by some providers (DeepSeek, etc.)
        reasoning = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)

        # reasoning_details carries encrypted reasoning blocks for cross-turn replay
        provider_data = None
        rd = getattr(msg, "reasoning_details", None)
        if rd:
            provider_data = {"reasoning_details": rd}

        return NormalizedResponse(
            content=msg.content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            reasoning=reasoning,
            usage=usage,
            provider_data=provider_data,
        )

    def validate_response(self, response: Any) -> bool:
        """Check that response has valid choices."""
        if response is None:
            return False
        if not hasattr(response, "choices") or response.choices is None:
            return False
        if not response.choices:
            return False
        return True

    def extract_cache_stats(self, response: Any) -> Optional[Dict[str, int]]:
        """Extract OpenRouter/OpenAI cache stats from prompt_tokens_details."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        details = getattr(usage, "prompt_tokens_details", None)
        if details is None:
            return None
        cached = getattr(details, "cached_tokens", 0) or 0
        written = getattr(details, "cache_write_tokens", 0) or 0
        if cached or written:
            return {"cached_tokens": cached, "creation_tokens": written}
        return None


# Auto-register on import
from agent.transports import register_transport  # noqa: E402

register_transport("chat_completions", ChatCompletionsTransport)
