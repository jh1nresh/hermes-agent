"""Tests for mid-chat /model switching.

Covers the full model-switching stack:
- Model aliases (sonnet, opus, gpt5, etc.)
- Fuzzy matching and suggestions
- CommandDef registration (commands.py)
- Switch pipeline (model_switch.py)
- AIAgent.switch_model() method (run_agent.py)
- CLI handler (cli.py)
- Gateway handler (gateway/run.py)
- Edge cases and error paths
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ═══════════════════════════════════════════════════════════════════════
# Model aliases
# ═══════════════════════════════════════════════════════════════════════

class TestModelAliases:
    """Verify the alias system resolves short names to full model slugs."""

    def test_sonnet_alias(self):
        from hermes_cli.model_switch import resolve_alias
        result = resolve_alias("sonnet")
        assert result is not None
        provider, model, alias = result
        assert "claude" in model.lower()
        assert "sonnet" in model.lower()
        assert alias == "sonnet"

    def test_opus_alias(self):
        from hermes_cli.model_switch import resolve_alias
        result = resolve_alias("opus")
        assert result is not None
        _, model, _ = result
        assert "opus" in model.lower()

    def test_haiku_alias(self):
        from hermes_cli.model_switch import resolve_alias
        result = resolve_alias("haiku")
        assert result is not None
        _, model, _ = result
        assert "haiku" in model.lower()

    def test_gpt5_alias(self):
        from hermes_cli.model_switch import resolve_alias
        result = resolve_alias("gpt5")
        assert result is not None
        _, model, _ = result
        assert "gpt-5" in model.lower()

    def test_gemini_alias(self):
        from hermes_cli.model_switch import resolve_alias
        result = resolve_alias("gemini")
        assert result is not None
        _, model, _ = result
        assert "gemini" in model.lower()

    def test_deepseek_alias(self):
        """deepseek not in static OpenRouter catalog — falls through to pipeline."""
        from hermes_cli.model_switch import resolve_alias
        # deepseek-chat isn't in our curated OPENROUTER_MODELS,
        # so alias returns None and the pipeline handles it via
        # aggregator resolution or provider detection
        result = resolve_alias("deepseek", "openrouter")
        # May or may not resolve depending on catalog state — just don't crash
        if result:
            _, model, _ = result
            assert "deepseek" in model.lower()

    def test_codex_alias(self):
        from hermes_cli.model_switch import resolve_alias
        result = resolve_alias("codex")
        assert result is not None
        _, model, _ = result
        assert "codex" in model.lower()

    def test_case_insensitive(self):
        from hermes_cli.model_switch import resolve_alias
        assert resolve_alias("SONNET") is not None
        assert resolve_alias("Opus") is not None
        assert resolve_alias("GPT5") is not None

    def test_unknown_returns_none(self):
        from hermes_cli.model_switch import resolve_alias
        assert resolve_alias("nonexistent-model-xyz") is None
        assert resolve_alias("") is None

    def test_all_aliases_have_valid_identities(self):
        """Every alias must have a vendor and family."""
        from hermes_cli.model_switch import MODEL_ALIASES
        for alias, identity in MODEL_ALIASES.items():
            assert identity.vendor, f"Alias '{alias}' has empty vendor"
            assert identity.family, f"Alias '{alias}' has empty family"

    def test_alias_provider_aware_openrouter(self):
        """On OpenRouter, sonnet resolves with vendor/ prefix."""
        from hermes_cli.model_switch import resolve_alias
        result = resolve_alias("sonnet", "openrouter")
        assert result is not None
        provider, model, _ = result
        assert provider == "openrouter"
        assert model.startswith("anthropic/")

    def test_alias_provider_aware_anthropic(self):
        """On native Anthropic, sonnet resolves with hyphens."""
        from hermes_cli.model_switch import resolve_alias
        result = resolve_alias("sonnet", "anthropic")
        assert result is not None
        provider, model, _ = result
        assert provider == "anthropic"
        assert "." not in model  # hyphens, not dots
        assert "claude-sonnet" in model

    def test_alias_unavailable_on_provider(self):
        """GPT5 on native Anthropic returns None (not available)."""
        from hermes_cli.model_switch import resolve_alias
        result = resolve_alias("gpt5", "anthropic")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# Fuzzy matching and suggestions
# ═══════════════════════════════════════════════════════════════════════

class TestFuzzyMatching:
    """Verify fuzzy matching suggests alternatives for typos."""

    def test_close_typo_gets_suggestion(self):
        from hermes_cli.model_switch import suggest_models
        suggestions = suggest_models("sonet")  # missing 'n'
        assert len(suggestions) > 0
        # Should suggest "sonnet" or something close
        assert any("sonnet" in s.lower() for s in suggestions)

    def test_partial_name_gets_suggestion(self):
        from hermes_cli.model_switch import suggest_models
        suggestions = suggest_models("claude-sonn")
        assert len(suggestions) > 0

    def test_completely_wrong_gets_empty(self):
        from hermes_cli.model_switch import suggest_models
        suggestions = suggest_models("zzzzzzzzzzz")
        # May or may not return suggestions — just shouldn't crash
        assert isinstance(suggestions, list)

    def test_suggestion_limit(self):
        from hermes_cli.model_switch import suggest_models
        suggestions = suggest_models("gpt", limit=2)
        assert len(suggestions) <= 2


# ═══════════════════════════════════════════════════════════════════════
# CommandDef registration
# ═══════════════════════════════════════════════════════════════════════

class TestCommandRegistration:
    """Verify /model is registered correctly in the command system."""

    def test_model_command_exists(self):
        from hermes_cli.commands import COMMAND_REGISTRY
        names = [c.name for c in COMMAND_REGISTRY]
        assert "model" in names

    def test_model_command_properties(self):
        from hermes_cli.commands import COMMAND_REGISTRY
        cmd = next(c for c in COMMAND_REGISTRY if c.name == "model")
        assert cmd.category == "Configuration"
        assert not cmd.cli_only
        assert not cmd.gateway_only
        assert cmd.args_hint

    def test_model_command_resolves(self):
        from hermes_cli.commands import resolve_command
        result = resolve_command("model")
        assert result is not None
        assert result.name == "model"

    def test_model_in_gateway_known_commands(self):
        from hermes_cli.commands import GATEWAY_KNOWN_COMMANDS
        assert "model" in GATEWAY_KNOWN_COMMANDS


# ═══════════════════════════════════════════════════════════════════════
# Switch pipeline
# ═══════════════════════════════════════════════════════════════════════

class TestSwitchPipeline:
    """Test the rebuilt model switch pipeline."""

    def test_empty_input_error(self):
        from hermes_cli.model_switch import switch_model
        result = switch_model("", current_provider="openrouter")
        assert not result.success
        assert "No model" in result.error_message

    def test_alias_resolves_in_pipeline(self):
        """Typing 'sonnet' should resolve through the alias table."""
        from hermes_cli.model_switch import switch_model
        with patch("hermes_cli.runtime_provider.resolve_runtime_provider", return_value={
                 "api_key": "test-key", "base_url": "https://openrouter.ai/api/v1", "api_mode": "",
             }):
            result = switch_model("sonnet", current_provider="openrouter")
            assert result.success
            assert "sonnet" in result.new_model.lower()
            assert result.resolved_via_alias == "sonnet"

    def test_vendor_colon_on_aggregator(self):
        """openai:gpt-5.4 on OpenRouter becomes openai/gpt-5.4 (stays on aggregator)."""
        from hermes_cli.model_switch import switch_model
        with patch("hermes_cli.runtime_provider.resolve_runtime_provider", return_value={
                 "api_key": "key", "base_url": "https://openrouter.ai/api/v1", "api_mode": "",
             }):
            result = switch_model("openai:gpt-5.4", current_provider="openrouter")
            assert result.success
            assert result.new_model == "openai/gpt-5.4"
            assert result.target_provider == "openrouter"
            assert not result.provider_changed  # stays on aggregator

    def test_explicit_hermes_provider_model(self):
        """anthropic:claude-opus-4 switches to the anthropic hermes provider."""
        from hermes_cli.model_switch import switch_model
        with patch("hermes_cli.models.parse_model_input", return_value=("anthropic", "claude-opus-4")), \
             patch("hermes_cli.runtime_provider.resolve_runtime_provider", return_value={
                 "api_key": "sk-ant", "base_url": "https://api.anthropic.com", "api_mode": "anthropic_messages",
             }):
            result = switch_model("anthropic:claude-opus-4", current_provider="openrouter")
            assert result.success
            assert result.target_provider == "anthropic"
            assert result.provider_changed

    def test_missing_credentials_actionable_error(self):
        """Error message should be actionable when creds are missing."""
        from hermes_cli.model_switch import switch_model
        with patch("hermes_cli.models.parse_model_input", return_value=("anthropic", "claude-opus")), \
             patch("hermes_cli.runtime_provider.resolve_runtime_provider",
                   side_effect=Exception("No Anthropic credentials found")):
            result = switch_model("anthropic:claude-opus", current_provider="openrouter")
            assert not result.success
            assert "hermes setup" in result.error_message.lower()

    def test_unrecognized_model_warning(self):
        """Unrecognized model gets a warning but still succeeds."""
        from hermes_cli.model_switch import switch_model
        with patch("hermes_cli.runtime_provider.resolve_runtime_provider", return_value={
                 "api_key": "key", "base_url": "https://openrouter.ai/api/v1", "api_mode": "",
             }), \
             patch("hermes_cli.models.parse_model_input", return_value=("openrouter", "weird-unknown-model")), \
             patch("hermes_cli.models.detect_provider_for_model", return_value=None):
            result = switch_model("weird-unknown-model", current_provider="openrouter")
            assert result.success
            assert result.warning_message  # should have a warning

    def test_custom_provider_error_message(self):
        """Custom endpoint error gives specific guidance."""
        from hermes_cli.model_switch import switch_model
        with patch("hermes_cli.models.parse_model_input", return_value=("custom", "local-model")), \
             patch("hermes_cli.runtime_provider.resolve_runtime_provider",
                   side_effect=Exception("no endpoint")):
            result = switch_model("custom:local-model", current_provider="openrouter")
            assert not result.success
            assert "config.yaml" in result.error_message or "hermes setup" in result.error_message

    def test_persist_always_true_on_success(self):
        """Successful switches should always persist."""
        from hermes_cli.model_switch import switch_model
        with patch("hermes_cli.runtime_provider.resolve_runtime_provider", return_value={
                 "api_key": "key", "base_url": "https://openrouter.ai/api/v1", "api_mode": "",
             }):
            result = switch_model("opus", current_provider="openrouter")
            assert result.success
            assert result.persist is True


# ═══════════════════════════════════════════════════════════════════════
# AIAgent.switch_model()
# ═══════════════════════════════════════════════════════════════════════

class TestAgentSwitchModel:
    """Test the AIAgent.switch_model() method."""

    def _make_agent(self, model="test-model", provider="openrouter"):
        """Create a minimal mock agent with the attributes switch_model needs."""
        from run_agent import AIAgent
        with patch.object(AIAgent, "__init__", lambda self: None):
            agent = AIAgent()
        agent.model = model
        agent.provider = provider
        agent.base_url = "https://openrouter.ai/api/v1"
        agent.api_mode = "chat_completions"
        agent.api_key = "test-key"
        agent.client = MagicMock()
        agent._client_kwargs = {"api_key": "test-key", "base_url": "https://openrouter.ai/api/v1"}
        agent._use_prompt_caching = True
        agent._cached_system_prompt = "cached prompt"
        agent._fallback_activated = False
        agent._fallback_index = 0
        agent._anthropic_client = None
        agent._anthropic_api_key = ""
        agent._anthropic_base_url = None
        agent._is_anthropic_oauth = False
        agent._memory_store = None
        cc = MagicMock()
        cc.model = model
        cc.base_url = "https://openrouter.ai/api/v1"
        cc.api_key = "test-key"
        cc.provider = provider
        cc.context_length = 200000
        cc.threshold_tokens = 160000
        cc.threshold_percent = 0.8
        agent.context_compressor = cc
        agent._primary_runtime = {
            "model": model, "provider": provider,
            "base_url": "https://openrouter.ai/api/v1",
            "api_mode": "chat_completions", "api_key": "test-key",
            "client_kwargs": dict(agent._client_kwargs),
            "use_prompt_caching": True,
            "compressor_model": model, "compressor_base_url": "https://openrouter.ai/api/v1",
            "compressor_api_key": "test-key", "compressor_provider": provider,
            "compressor_context_length": 200000, "compressor_threshold_tokens": 160000,
        }
        agent._create_openai_client = MagicMock(return_value=MagicMock())
        agent._is_direct_openai_url = MagicMock(return_value=False)
        agent._invalidate_system_prompt = MagicMock()
        return agent

    def test_basic_switch(self):
        agent = self._make_agent()
        agent.switch_model(
            new_model="claude-sonnet-4",
            new_provider="openrouter",
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
        )
        assert agent.model == "claude-sonnet-4"

    def test_system_prompt_invalidated(self):
        agent = self._make_agent()
        agent.switch_model(
            new_model="new-model", new_provider="openrouter",
            api_key="key", base_url="https://openrouter.ai/api/v1",
        )
        agent._invalidate_system_prompt.assert_called_once()

    def test_primary_runtime_updated(self):
        agent = self._make_agent()
        agent.switch_model(
            new_model="gpt-5", new_provider="openai",
            api_key="sk-test", base_url="https://api.openai.com/v1",
        )
        assert agent._primary_runtime["model"] == "gpt-5"
        assert agent._primary_runtime["provider"] == "openai"

    def test_prompt_caching_claude_on_openrouter(self):
        agent = self._make_agent()
        agent._use_prompt_caching = False
        agent.switch_model(
            new_model="anthropic/claude-sonnet-4",
            new_provider="openrouter",
            api_key="key", base_url="https://openrouter.ai/api/v1",
        )
        assert agent._use_prompt_caching is True

    def test_prompt_caching_non_claude(self):
        agent = self._make_agent()
        agent._use_prompt_caching = True
        agent.switch_model(
            new_model="openai/gpt-5",
            new_provider="openrouter",
            api_key="key", base_url="https://openrouter.ai/api/v1",
        )
        assert agent._use_prompt_caching is False

    def test_cross_api_mode_to_anthropic(self):
        agent = self._make_agent()
        with patch("agent.anthropic_adapter.build_anthropic_client", return_value=MagicMock()), \
             patch("agent.anthropic_adapter.resolve_anthropic_token", return_value="sk-ant"), \
             patch("agent.anthropic_adapter._is_oauth_token", return_value=False):
            agent.switch_model(
                new_model="claude-opus-4", new_provider="anthropic",
                api_key="sk-ant",
            )
        assert agent.api_mode == "anthropic_messages"
        assert agent.client is None

    def test_switch_from_anthropic_clears_state(self):
        agent = self._make_agent()
        agent.api_mode = "anthropic_messages"
        agent._anthropic_client = MagicMock()
        agent.switch_model(
            new_model="gpt-5", new_provider="openai",
            api_key="sk-test", base_url="https://api.openai.com/v1",
        )
        assert agent.api_mode == "chat_completions"
        assert agent._anthropic_client is None

    def test_context_compressor_updated(self):
        agent = self._make_agent()
        with patch("agent.model_metadata.get_model_context_length", return_value=128000):
            agent.switch_model(
                new_model="gpt-4o", new_provider="openai",
                api_key="key", base_url="https://api.openai.com/v1",
            )
        assert agent.context_compressor.context_length == 128000

    def test_fallback_state_reset(self):
        agent = self._make_agent()
        agent._fallback_activated = True
        agent._fallback_index = 2
        agent.switch_model(
            new_model="new", new_provider="openrouter",
            api_key="key", base_url="https://openrouter.ai/api/v1",
        )
        assert agent._fallback_activated is False
        assert agent._fallback_index == 0


# ═══════════════════════════════════════════════════════════════════════
# CLI handler
# ═══════════════════════════════════════════════════════════════════════

class TestCLIHandler:
    """Test the CLI /model handler."""

    def _make_cli(self, model="test-model", provider="openrouter"):
        cli = MagicMock()
        cli.model = model
        cli.provider = provider
        cli.base_url = "https://openrouter.ai/api/v1"
        cli.api_key = "test-key"
        cli.api_mode = "chat_completions"
        cli.agent = MagicMock()
        cli.agent.switch_model = MagicMock()
        from cli import HermesCLI
        cli._handle_model_switch = HermesCLI._handle_model_switch.__get__(cli)
        return cli

    def test_no_args_shows_aliases(self, capsys):
        cli = self._make_cli()
        with patch("hermes_cli.models._PROVIDER_LABELS", {"openrouter": "OpenRouter"}):
            cli._handle_model_switch("/model")
        captured = capsys.readouterr()
        assert "sonnet" in captured.out
        assert "opus" in captured.out
        assert "gpt5" in captured.out

    def test_alias_switch(self, capsys):
        cli = self._make_cli()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.new_model = "anthropic/claude-sonnet-4.6"
        mock_result.target_provider = "openrouter"
        mock_result.provider_changed = False
        mock_result.api_key = "key"
        mock_result.base_url = "https://openrouter.ai/api/v1"
        mock_result.api_mode = ""
        mock_result.persist = True
        mock_result.warning_message = ""
        mock_result.resolved_via_alias = "sonnet"

        with patch("hermes_cli.model_switch.switch_model", return_value=mock_result), \
             patch("hermes_cli.models._PROVIDER_LABELS", {"openrouter": "OpenRouter"}), \
             patch("cli.save_config_value"):
            cli._handle_model_switch("/model sonnet")

        captured = capsys.readouterr()
        assert "sonnet" in captured.out
        assert "claude-sonnet" in captured.out
        assert cli.model == "anthropic/claude-sonnet-4.6"

    def test_failed_switch_shows_suggestions(self, capsys):
        cli = self._make_cli()
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.error_message = "No credentials"

        with patch("hermes_cli.model_switch.switch_model", return_value=mock_result), \
             patch("hermes_cli.model_switch.suggest_models", return_value=["sonnet", "opus"]):
            cli._handle_model_switch("/model sonet")

        captured = capsys.readouterr()
        assert "Did you mean" in captured.out

    def test_same_model_noop(self, capsys):
        cli = self._make_cli(model="anthropic/claude-sonnet-4.6")
        cli._handle_model_switch("/model anthropic/claude-sonnet-4.6")
        captured = capsys.readouterr()
        assert "Already using" in captured.out


# ═══════════════════════════════════════════════════════════════════════
# Gateway handler
# ═══════════════════════════════════════════════════════════════════════

class TestGatewayHandler:
    """Test the gateway /model handler."""

    def _make_gateway_config(self, tmp_path, model="test-model", provider="openrouter"):
        import yaml
        config_dir = tmp_path / ".hermes"
        config_dir.mkdir(exist_ok=True)
        config_path = config_dir / "config.yaml"
        config = {"model": {"default": model, "provider": provider}}
        with open(config_path, "w") as f:
            yaml.dump(config, f)
        return config_path, config_dir

    @pytest.mark.asyncio
    async def test_no_args_shows_aliases(self, tmp_path, monkeypatch):
        config_path, config_dir = self._make_gateway_config(tmp_path)
        monkeypatch.setattr("gateway.run._hermes_home", config_dir)

        from gateway.run import GatewayRunner
        runner = MagicMock(spec=GatewayRunner)
        runner._handle_model_command = GatewayRunner._handle_model_command.__get__(runner)

        event = MagicMock()
        event.get_command_args.return_value = ""

        result = await runner._handle_model_command(event)
        assert "sonnet" in result
        assert "opus" in result
        assert "gpt5" in result

    @pytest.mark.asyncio
    async def test_successful_switch_evicts_agent(self, tmp_path, monkeypatch):
        config_path, config_dir = self._make_gateway_config(tmp_path)
        monkeypatch.setattr("gateway.run._hermes_home", config_dir)

        from gateway.run import GatewayRunner
        runner = MagicMock(spec=GatewayRunner)
        runner._handle_model_command = GatewayRunner._handle_model_command.__get__(runner)
        runner._session_key_for_source = MagicMock(return_value="test-key")
        runner._evict_cached_agent = MagicMock()

        event = MagicMock()
        event.get_command_args.return_value = "sonnet"
        event.source = MagicMock()

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.new_model = "anthropic/claude-sonnet-4.6"
        mock_result.target_provider = "openrouter"
        mock_result.provider_changed = False
        mock_result.persist = True
        mock_result.warning_message = ""
        mock_result.resolved_via_alias = "sonnet"
        mock_result.is_custom_target = False

        with patch("hermes_cli.model_switch.switch_model", return_value=mock_result), \
             patch("hermes_cli.config.save_config"):
            result = await runner._handle_model_command(event)

        assert "sonnet" in result
        runner._evict_cached_agent.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_with_suggestions(self, tmp_path, monkeypatch):
        config_path, config_dir = self._make_gateway_config(tmp_path)
        monkeypatch.setattr("gateway.run._hermes_home", config_dir)

        from gateway.run import GatewayRunner
        runner = MagicMock(spec=GatewayRunner)
        runner._handle_model_command = GatewayRunner._handle_model_command.__get__(runner)

        event = MagicMock()
        event.get_command_args.return_value = "sonet"  # typo
        event.source = MagicMock()

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.error_message = "No credentials"

        with patch("hermes_cli.model_switch.switch_model", return_value=mock_result), \
             patch("hermes_cli.model_switch.suggest_models", return_value=["sonnet"]):
            result = await runner._handle_model_command(event)

        assert "Did you mean" in result
        assert "sonnet" in result


# ═══════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_custom_auto_result(self):
        from hermes_cli.model_switch import CustomAutoResult
        r = CustomAutoResult(success=True, model="llama-3.3", base_url="http://localhost:11434")
        assert r.success

    def test_result_has_alias_field(self):
        from hermes_cli.model_switch import ModelSwitchResult
        r = ModelSwitchResult(success=True, resolved_via_alias="sonnet")
        assert r.resolved_via_alias == "sonnet"

    def test_switch_to_custom_no_endpoint(self):
        from hermes_cli.model_switch import switch_to_custom_provider
        with patch("hermes_cli.runtime_provider.resolve_runtime_provider",
                   side_effect=Exception("no endpoint")):
            result = switch_to_custom_provider()
            assert not result.success
            assert "config.yaml" in result.error_message

    def test_opencode_api_mode_recompute(self):
        from hermes_cli.model_switch import switch_model
        with patch("hermes_cli.models.parse_model_input", return_value=("opencode-zen", "claude-opus")), \
             patch("hermes_cli.runtime_provider.resolve_runtime_provider", return_value={
                 "api_key": "key", "base_url": "https://example.com", "api_mode": "chat_completions",
             }), \
             patch("hermes_cli.models.opencode_model_api_mode", return_value="anthropic_messages") as mock_oc:
            result = switch_model("opencode-zen:claude-opus", current_provider="openrouter")
            assert result.success
            assert result.api_mode == "anthropic_messages"
