"""Tests for the codex-cli external-process provider."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

# CRITICAL: import directly from the module to avoid module-level side effects
from hermes_cli.auth import (
    PROVIDER_REGISTRY,
    get_external_process_provider_status,
    get_auth_status,
    resolve_external_process_provider_credentials,
)


class TestCodexCLIProviderRegistry:
    """Test that the codex-cli provider is correctly registered."""

    def test_provider_registered(self):
        assert "codex-cli" in PROVIDER_REGISTRY
        pconfig = PROVIDER_REGISTRY["codex-cli"]
        assert pconfig.name == "OpenAI Codex CLI"
        assert pconfig.auth_type == "external_process"
        assert pconfig.inference_base_url == "codex-cli://local"
        assert pconfig.base_url_env_var == "CODEX_CLI_BASE_URL"

    def test_aliases_resolve(self):
        from hermes_cli.auth import resolve_provider

        assert resolve_provider("codexcli") == "codex-cli"
        assert resolve_provider("openai-codex-cli") == "codex-cli"


class TestCodexCLIStatus:
    """Test the external-process status helper for codex-cli."""

    def test_status_not_configured_when_codex_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            status = get_external_process_provider_status("codex-cli")
            assert status["configured"] is False
            assert status["provider"] == "codex-cli"

    def test_status_configured_when_codex_exists(self):
        with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
            with patch("shutil.which", return_value="/opt/homebrew/bin/codex"):
                status = get_external_process_provider_status("codex-cli")
                assert status["configured"] is True
                assert status["provider"] == "codex-cli"
                assert status["resolved_command"] == "/opt/homebrew/bin/codex"
                assert status["command"] == "codex"

    def test_auth_status_dispatches(self):
        with patch.dict(os.environ, {}, clear=True):
            status = get_auth_status("codex-cli")
            # Should not throw, returns a dict even when not configured
            assert isinstance(status, dict)
            assert "configured" in status or "logged_in" in status

    def test_status_with_custom_command_env(self):
        with patch.dict(os.environ, {"HERMES_CODEX_CLI_COMMAND": "/usr/local/bin/my-codex"}, clear=False):
            status = get_external_process_provider_status("codex-cli")
            assert status["command"] == "/usr/local/bin/my-codex"
            assert status["command"] == "/usr/local/bin/my-codex"

    def test_status_with_custom_args_env(self):
        with patch.dict(os.environ, {
            "HERMES_CODEX_CLI_ARGS": "exec --json --model gpt-5.5",
        }, clear=False):
            status = get_external_process_provider_status("codex-cli")
            assert "exec" in status["args"]
            assert "--json" in status["args"]
            assert "--model" in status["args"]

    def test_status_unknown_provider(self):
        status = get_external_process_provider_status("nonexistent")
        assert status == {"configured": False}


class TestCodexCLICredentials:
    """Test the credential resolver for codex-cli."""

    def test_resolves_command_path_when_available(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("shutil.which", return_value="/opt/homebrew/bin/codex"):
                creds = resolve_external_process_provider_credentials("codex-cli")
                assert creds["provider"] == "codex-cli"
                assert creds["command"] == "/opt/homebrew/bin/codex"
                assert creds["api_key"] == "codex-cli"
                assert creds["base_url"] == "codex-cli://local"
                assert "--json" in creds["args"]
                assert "--ephemeral" in creds["args"]

    def test_raises_when_command_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("shutil.which", return_value=None):
                with pytest.raises(Exception) as exc_info:
                    resolve_external_process_provider_credentials("codex-cli")
                assert "codex-cli" in str(exc_info.value).lower() or "codex" in str(exc_info.value).lower()

    def test_custom_command_from_env(self):
        with patch.dict(os.environ, {"HERMES_CODEX_CLI_COMMAND": "/usr/local/bin/custom-codex"}, clear=False):
            with patch("shutil.which", return_value="/usr/local/bin/custom-codex"):
                creds = resolve_external_process_provider_credentials("codex-cli")
                assert creds["command"] == "/usr/local/bin/custom-codex"
