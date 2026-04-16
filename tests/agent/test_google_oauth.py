"""Tests for the Google OAuth (google-gemini-cli) provider.

Covers:
- PKCE generation (S256 roundtrip)
- Credential save/load/clear with 0o600 permissions, atomic write
- Token exchange + refresh (success + failure)
- ``get_valid_access_token`` fresh / near-expiry / force-refresh
- Refresh-token rotation handling (preserves old when Google omits new)
- Cross-process file lock acquires and releases cleanly
- Port fallback when the preferred callback port is busy
- Manual paste fallback parses both full redirect URLs and bare codes
- Runtime provider resolution + AuthError code propagation
- get_auth_status dispatch
- _OAUTH_CAPABLE_PROVIDERS includes google-gemini-cli (and preserves existing)
- Aliases resolve to canonical slug
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import stat
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Redirect HERMES_HOME and clear Gemini env vars for every test."""
    home = tmp_path / ".hermes"
    home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    for key in (
        "HERMES_GEMINI_CLIENT_ID",
        "HERMES_GEMINI_CLIENT_SECRET",
        "HERMES_GEMINI_BASE_URL",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    return home


# =============================================================================
# PKCE
# =============================================================================

class TestPkce:
    def test_verifier_and_challenge_are_related_via_s256(self):
        from agent.google_oauth import _generate_pkce_pair

        verifier, challenge = _generate_pkce_pair()
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        assert challenge == expected

    def test_verifier_is_url_safe(self):
        from agent.google_oauth import _generate_pkce_pair

        verifier, _ = _generate_pkce_pair()
        # Per RFC 7636: url-safe base64 without padding, 43–128 chars
        assert 43 <= len(verifier) <= 128
        allowed = set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
        )
        assert set(verifier).issubset(allowed)

    def test_pairs_are_unique_across_calls(self):
        from agent.google_oauth import _generate_pkce_pair

        pairs = {_generate_pkce_pair()[0] for _ in range(20)}
        assert len(pairs) == 20


# =============================================================================
# Credential I/O
# =============================================================================

class TestCredentialIo:
    def _make(self):
        from agent.google_oauth import GoogleCredentials

        return GoogleCredentials(
            access_token="at-1",
            refresh_token="rt-1",
            expires_at=time.time() + 3600,
            client_id="client-123",
            client_secret="secret-456",
            email="user@example.com",
        )

    def test_save_and_load_roundtrip(self):
        from agent.google_oauth import load_credentials, save_credentials

        creds = self._make()
        path = save_credentials(creds)
        loaded = load_credentials()

        assert loaded is not None
        assert loaded.access_token == creds.access_token
        assert loaded.refresh_token == creds.refresh_token
        assert loaded.email == creds.email
        assert path.exists()

    def test_save_uses_0o600_permissions(self):
        from agent.google_oauth import save_credentials

        creds = self._make()
        path = save_credentials(creds)
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"

    def test_load_returns_none_when_missing(self):
        from agent.google_oauth import load_credentials

        assert load_credentials() is None

    def test_load_returns_none_on_corrupt_json(self):
        from agent.google_oauth import _credentials_path, load_credentials

        path = _credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json")
        assert load_credentials() is None

    def test_load_returns_none_when_access_token_empty(self):
        from agent.google_oauth import _credentials_path, load_credentials

        path = _credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"access_token": "", "refresh_token": "x"}))
        assert load_credentials() is None

    def test_clear_is_idempotent(self):
        from agent.google_oauth import clear_credentials, save_credentials

        save_credentials(self._make())
        clear_credentials()
        clear_credentials()  # should not raise

    def test_atomic_write_leaves_no_tmp_file(self):
        from agent.google_oauth import _credentials_path, save_credentials

        save_credentials(self._make())
        path = _credentials_path()
        leftovers = list(path.parent.glob("*.tmp.*"))
        assert leftovers == []


# =============================================================================
# Cross-process lock
# =============================================================================

class TestCrossProcessLock:
    def test_lock_acquires_and_releases(self):
        from agent.google_oauth import _credentials_lock, _lock_path

        with _credentials_lock():
            assert _lock_path().exists()

        # After release, a second acquisition should succeed immediately
        with _credentials_lock(timeout_seconds=1.0):
            pass

    def test_lock_is_reentrant_within_thread(self):
        from agent.google_oauth import _credentials_lock

        with _credentials_lock():
            with _credentials_lock():
                with _credentials_lock():
                    pass


# =============================================================================
# Client credential resolution
# =============================================================================

class TestClientIdResolution:
    def test_env_var_overrides_default(self, monkeypatch):
        from agent.google_oauth import _get_client_id

        monkeypatch.setenv("HERMES_GEMINI_CLIENT_ID", "env-client-xyz")
        assert _get_client_id() == "env-client-xyz"

    def test_missing_client_id_raises(self):
        from agent.google_oauth import GoogleOAuthError, _require_client_id

        with pytest.raises(GoogleOAuthError) as exc_info:
            _require_client_id()
        assert exc_info.value.code == "google_oauth_client_id_missing"


# =============================================================================
# Token exchange + refresh
# =============================================================================

class TestTokenExchange:
    def test_exchange_code_sends_correct_body(self, monkeypatch):
        from agent import google_oauth

        captured = {}

        def fake_post(url, data, timeout):
            captured["url"] = url
            captured["data"] = data
            return {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}

        monkeypatch.setattr(google_oauth, "_post_form", fake_post)
        monkeypatch.setenv("HERMES_GEMINI_CLIENT_ID", "cid-123")

        google_oauth.exchange_code(
            code="auth-code-abc",
            verifier="verifier-xyz",
            redirect_uri="http://127.0.0.1:8085/oauth2callback",
        )

        assert captured["data"]["grant_type"] == "authorization_code"
        assert captured["data"]["code"] == "auth-code-abc"
        assert captured["data"]["code_verifier"] == "verifier-xyz"
        assert captured["data"]["client_id"] == "cid-123"

    def test_refresh_access_token_success(self, monkeypatch):
        from agent import google_oauth

        monkeypatch.setattr(
            google_oauth, "_post_form",
            lambda *a, **kw: {"access_token": "new-at", "expires_in": 3600},
        )
        monkeypatch.setenv("HERMES_GEMINI_CLIENT_ID", "cid")

        resp = google_oauth.refresh_access_token("refresh-abc")
        assert resp["access_token"] == "new-at"

    def test_refresh_without_refresh_token_raises(self):
        from agent.google_oauth import GoogleOAuthError, refresh_access_token

        with pytest.raises(GoogleOAuthError) as exc_info:
            refresh_access_token("")
        assert exc_info.value.code == "google_oauth_refresh_token_missing"


# =============================================================================
# get_valid_access_token
# =============================================================================

class TestGetValidAccessToken:
    def _save(self, **overrides):
        from agent.google_oauth import GoogleCredentials, save_credentials

        defaults = {
            "access_token": "current-at",
            "refresh_token": "rt-1",
            "expires_at": time.time() + 3600,
            "client_id": "cid",
            "client_secret": "",
        }
        defaults.update(overrides)
        save_credentials(GoogleCredentials(**defaults))

    def test_returns_cached_token_when_fresh(self):
        from agent.google_oauth import get_valid_access_token

        self._save(expires_at=time.time() + 3600)
        token = get_valid_access_token()
        assert token == "current-at"

    def test_refreshes_when_near_expiry(self, monkeypatch):
        from agent import google_oauth

        self._save(expires_at=time.time() + 30)  # within 5-min skew
        monkeypatch.setattr(
            google_oauth, "_post_form",
            lambda *a, **kw: {"access_token": "refreshed-at", "expires_in": 3600},
        )
        token = google_oauth.get_valid_access_token()
        assert token == "refreshed-at"
        # Reloaded creds should have new access_token
        loaded = google_oauth.load_credentials()
        assert loaded.access_token == "refreshed-at"

    def test_force_refresh_ignores_expiry(self, monkeypatch):
        from agent import google_oauth

        self._save(expires_at=time.time() + 3600)  # plenty of time left
        monkeypatch.setattr(
            google_oauth, "_post_form",
            lambda *a, **kw: {"access_token": "forced-at", "expires_in": 3600},
        )
        token = google_oauth.get_valid_access_token(force_refresh=True)
        assert token == "forced-at"

    def test_raises_when_not_logged_in(self):
        from agent.google_oauth import GoogleOAuthError, get_valid_access_token

        with pytest.raises(GoogleOAuthError) as exc_info:
            get_valid_access_token()
        assert exc_info.value.code == "google_oauth_not_logged_in"

    def test_preserves_refresh_token_when_google_omits_new_one(self, monkeypatch):
        """Google sometimes omits refresh_token from refresh responses; keep the old one."""
        from agent import google_oauth

        self._save(expires_at=time.time() + 30, refresh_token="original-rt")
        # Refresh response has no refresh_token field
        monkeypatch.setattr(
            google_oauth, "_post_form",
            lambda *a, **kw: {"access_token": "new-at", "expires_in": 3600},
        )
        google_oauth.get_valid_access_token()

        loaded = google_oauth.load_credentials()
        assert loaded.refresh_token == "original-rt"

    def test_rotates_refresh_token_when_google_returns_new_one(self, monkeypatch):
        from agent import google_oauth

        self._save(expires_at=time.time() + 30, refresh_token="original-rt")
        monkeypatch.setattr(
            google_oauth, "_post_form",
            lambda *a, **kw: {
                "access_token": "new-at",
                "refresh_token": "rotated-rt",
                "expires_in": 3600,
            },
        )
        google_oauth.get_valid_access_token()

        loaded = google_oauth.load_credentials()
        assert loaded.refresh_token == "rotated-rt"


# =============================================================================
# Callback server port fallback
# =============================================================================

class TestCallbackServer:
    def test_binds_preferred_port_when_free(self):
        from agent.google_oauth import _bind_callback_server

        # Find an unused port in the 50000-60000 range so we don't collide with
        # real services even on busy dev machines.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        server, actual_port = _bind_callback_server(preferred_port=port)
        try:
            assert actual_port == port
        finally:
            server.server_close()

    def test_falls_back_to_ephemeral_when_preferred_busy(self):
        from agent.google_oauth import _bind_callback_server

        # Occupy a port so binding to it fails
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen(1)
            busy_port = blocker.getsockname()[1]

            server, actual_port = _bind_callback_server(preferred_port=busy_port)
            try:
                assert actual_port != busy_port
                assert actual_port > 0
            finally:
                server.server_close()


# =============================================================================
# Manual paste fallback
# =============================================================================

class TestPasteFallback:
    def test_accepts_full_redirect_url(self, monkeypatch):
        from agent import google_oauth

        pasted = "http://127.0.0.1:8085/oauth2callback?code=abc123&state=xyz&scope=..."
        monkeypatch.setattr("builtins.input", lambda *_: pasted)
        assert google_oauth._prompt_paste_fallback() == "abc123"

    def test_accepts_bare_code(self, monkeypatch):
        from agent import google_oauth

        monkeypatch.setattr("builtins.input", lambda *_: "raw-code-xyz")
        assert google_oauth._prompt_paste_fallback() == "raw-code-xyz"

    def test_empty_input_returns_none(self, monkeypatch):
        from agent import google_oauth

        monkeypatch.setattr("builtins.input", lambda *_: "   ")
        assert google_oauth._prompt_paste_fallback() is None


# =============================================================================
# Runtime provider integration
# =============================================================================

class TestRuntimeProvider:
    def test_resolves_when_valid_token_exists(self):
        from agent.google_oauth import GoogleCredentials, save_credentials
        from hermes_cli.auth import resolve_gemini_oauth_runtime_credentials

        save_credentials(GoogleCredentials(
            access_token="live-token",
            refresh_token="rt",
            expires_at=time.time() + 3600,
            client_id="cid",
            email="u@e.com",
        ))

        creds = resolve_gemini_oauth_runtime_credentials()
        assert creds["provider"] == "google-gemini-cli"
        assert creds["api_key"] == "live-token"
        assert creds["source"] == "google-oauth"
        assert "generativelanguage.googleapis.com" in creds["base_url"]
        assert creds["email"] == "u@e.com"

    def test_raises_autherror_when_not_logged_in(self):
        from hermes_cli.auth import AuthError, resolve_gemini_oauth_runtime_credentials

        with pytest.raises(AuthError) as exc_info:
            resolve_gemini_oauth_runtime_credentials()
        assert exc_info.value.code == "google_oauth_not_logged_in"

    def test_runtime_provider_dispatches_gemini(self):
        from agent.google_oauth import GoogleCredentials, save_credentials
        from hermes_cli.runtime_provider import resolve_runtime_provider

        save_credentials(GoogleCredentials(
            access_token="tok",
            refresh_token="rt",
            expires_at=time.time() + 3600,
            client_id="cid",
        ))

        result = resolve_runtime_provider(requested="google-gemini-cli")
        assert result["provider"] == "google-gemini-cli"
        assert result["api_mode"] == "chat_completions"
        assert result["api_key"] == "tok"

    def test_base_url_env_override(self, monkeypatch):
        from agent.google_oauth import GoogleCredentials, save_credentials
        from hermes_cli.auth import resolve_gemini_oauth_runtime_credentials

        monkeypatch.setenv("HERMES_GEMINI_BASE_URL", "https://custom.example/v1")
        save_credentials(GoogleCredentials(
            access_token="tok", refresh_token="rt",
            expires_at=time.time() + 3600, client_id="cid",
        ))

        creds = resolve_gemini_oauth_runtime_credentials()
        assert creds["base_url"] == "https://custom.example/v1"


# =============================================================================
# Provider registration touchpoints
# =============================================================================

class TestProviderRegistration:
    def test_registry_entry_exists(self):
        from hermes_cli.auth import PROVIDER_REGISTRY

        assert "google-gemini-cli" in PROVIDER_REGISTRY
        pc = PROVIDER_REGISTRY["google-gemini-cli"]
        assert pc.auth_type == "oauth_external"
        assert "generativelanguage.googleapis.com" in pc.inference_base_url

    @pytest.mark.parametrize("alias", [
        "gemini-cli", "gemini-oauth", "google-gemini-cli",
    ])
    def test_aliases_resolve(self, alias):
        from hermes_cli.auth import resolve_provider

        assert resolve_provider(alias) == "google-gemini-cli"

    def test_models_catalog_populated(self):
        from hermes_cli.models import _PROVIDER_MODELS, CANONICAL_PROVIDERS

        assert len(_PROVIDER_MODELS["google-gemini-cli"]) >= 5
        assert any(p.slug == "google-gemini-cli" for p in CANONICAL_PROVIDERS)

    def test_determine_api_mode_returns_chat_completions(self):
        from hermes_cli.providers import determine_api_mode

        mode = determine_api_mode(
            "google-gemini-cli",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        )
        assert mode == "chat_completions"

    def test_oauth_capable_set_preserves_existing_providers(self):
        """PR #10779 regressed this — make sure we DIDN'T drop anthropic/nous."""
        from hermes_cli.auth_commands import _OAUTH_CAPABLE_PROVIDERS

        for required in ("anthropic", "nous", "openai-codex", "qwen-oauth", "google-gemini-cli"):
            assert required in _OAUTH_CAPABLE_PROVIDERS, \
                f"{required} missing from _OAUTH_CAPABLE_PROVIDERS"

    def test_config_env_vars_registered(self):
        from hermes_cli.config import OPTIONAL_ENV_VARS

        for key in (
            "HERMES_GEMINI_CLIENT_ID",
            "HERMES_GEMINI_CLIENT_SECRET",
            "HERMES_GEMINI_BASE_URL",
        ):
            assert key in OPTIONAL_ENV_VARS


# =============================================================================
# Auth status dispatch
# =============================================================================

class TestAuthStatus:
    def test_status_when_not_logged_in(self):
        from hermes_cli.auth import get_auth_status

        status = get_auth_status("google-gemini-cli")
        assert status["logged_in"] is False

    def test_status_when_logged_in(self):
        from agent.google_oauth import GoogleCredentials, save_credentials
        from hermes_cli.auth import get_auth_status

        save_credentials(GoogleCredentials(
            access_token="tok", refresh_token="rt",
            expires_at=time.time() + 3600, client_id="cid",
            email="tek@nous.ai",
        ))

        status = get_auth_status("google-gemini-cli")
        assert status["logged_in"] is True
        assert status["source"] == "google-oauth"
        assert status["email"] == "tek@nous.ai"


# =============================================================================
# run_gemini_oauth_login_pure
# =============================================================================

class TestOauthLoginPure:
    def test_returns_pool_compatible_dict(self, monkeypatch):
        from agent import google_oauth

        def fake_start(**kw):
            return google_oauth.GoogleCredentials(
                access_token="at", refresh_token="rt",
                expires_at=time.time() + 3600,
                client_id="cid", email="u@e.com",
            )

        monkeypatch.setattr(google_oauth, "start_oauth_flow", fake_start)

        result = google_oauth.run_gemini_oauth_login_pure()
        assert result["access_token"] == "at"
        assert result["refresh_token"] == "rt"
        assert "expires_at_ms" in result
        assert isinstance(result["expires_at_ms"], int)
        assert result["email"] == "u@e.com"
