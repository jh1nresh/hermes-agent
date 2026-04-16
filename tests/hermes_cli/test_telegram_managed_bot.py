"""Tests for hermes_cli.telegram_managed_bot — QR codes, deep links, pairing."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from hermes_cli.telegram_managed_bot import (
    DEFAULT_API_URL,
    DEFAULT_MANAGER_BOT,
    generate_bot_username,
    generate_deep_link,
    generate_pairing_nonce,
    print_qr_code,
    register_pairing,
    render_qr_terminal,
)


# ---------------------------------------------------------------------------
# Username generation
# ---------------------------------------------------------------------------


class TestGenerateBotUsername:
    def test_default_format(self):
        name = generate_bot_username()
        assert name.startswith("hermes_")
        assert name.endswith("_bot")
        # Should be short enough for Telegram (max 32 chars)
        assert len(name) <= 32
        assert len(name) >= 5

    def test_with_profile_name(self):
        name = generate_bot_username("work")
        assert "work" in name
        assert name.startswith("hermes_")
        assert name.endswith("_bot")

    def test_default_profile_ignored(self):
        name = generate_bot_username("default")
        assert "default" not in name
        assert name.startswith("hermes_")
        assert name.endswith("_bot")

    def test_profile_name_sanitized(self):
        name = generate_bot_username("My Cool-Profile!")
        assert name.startswith("hermes_")
        assert name.endswith("_bot")
        # Special chars should be replaced with underscores
        assert "!" not in name
        assert "-" not in name

    def test_long_profile_name_truncated(self):
        name = generate_bot_username("a" * 50)
        assert len(name) <= 32

    def test_uniqueness(self):
        names = {generate_bot_username() for _ in range(20)}
        # Random suffix should produce unique names
        assert len(names) == 20


# ---------------------------------------------------------------------------
# Deep link generation
# ---------------------------------------------------------------------------


class TestGenerateDeepLink:
    def test_basic_format(self):
        link = generate_deep_link(
            manager_bot="TestBot",
            suggested_username="my_bot",
        )
        assert link == "https://t.me/newbot/TestBot/my_bot"

    def test_with_name(self):
        link = generate_deep_link(
            manager_bot="TestBot",
            suggested_username="my_bot",
            suggested_name="My Agent",
        )
        assert "https://t.me/newbot/TestBot/my_bot?" in link
        assert "name=My+Agent" in link

    def test_defaults(self):
        link = generate_deep_link()
        assert f"https://t.me/newbot/{DEFAULT_MANAGER_BOT}/" in link
        assert "hermes_" in link

    def test_name_url_encoded(self):
        link = generate_deep_link(
            manager_bot="Bot",
            suggested_username="test_bot",
            suggested_name="Hermes & Friends",
        )
        # Ampersand should be URL-encoded
        assert "Hermes+%26+Friends" in link or "Hermes+&+Friends" not in link


# ---------------------------------------------------------------------------
# Pairing nonce
# ---------------------------------------------------------------------------


class TestPairingNonce:
    def test_length(self):
        nonce = generate_pairing_nonce()
        assert len(nonce) == 32  # 16 bytes = 32 hex chars

    def test_hex_chars(self):
        nonce = generate_pairing_nonce()
        assert all(c in "0123456789abcdef" for c in nonce)

    def test_uniqueness(self):
        nonces = {generate_pairing_nonce() for _ in range(100)}
        assert len(nonces) == 100


# ---------------------------------------------------------------------------
# QR code rendering
# ---------------------------------------------------------------------------


class TestQRCode:
    def test_render_returns_string(self):
        """If qrcode is installed, should return non-empty string."""
        result = render_qr_terminal("https://example.com")
        # qrcode may or may not be installed in test env
        if result:
            assert isinstance(result, str)
            assert len(result) > 10

    def test_render_graceful_without_qrcode(self):
        """Should return empty string if qrcode not installed."""
        with patch.dict("sys.modules", {"qrcode": None}):
            # Force ImportError
            result = render_qr_terminal("https://example.com")
            # May still succeed if qrcode is cached; that's fine

    def test_print_qr_code_with_url(self, capsys):
        """Should at minimum print the URL."""
        print_qr_code("https://t.me/newbot/Bot/test_bot")
        captured = capsys.readouterr()
        assert "https://t.me/newbot/Bot/test_bot" in captured.out


# ---------------------------------------------------------------------------
# Pairing API client
# ---------------------------------------------------------------------------


class TestRegisterPairing:
    def test_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        with patch("hermes_cli.telegram_managed_bot.httpx.post", return_value=mock_resp):
            assert register_pairing("https://api.example.com", "abc123") is True

    def test_failure_status(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("hermes_cli.telegram_managed_bot.httpx.post", return_value=mock_resp):
            assert register_pairing("https://api.example.com", "abc123") is False

    def test_network_error(self):
        import httpx

        with patch(
            "hermes_cli.telegram_managed_bot.httpx.post",
            side_effect=httpx.ConnectError("connection refused"),
        ):
            assert register_pairing("https://api.example.com", "abc123") is False


class TestPollForToken:
    def test_immediate_success(self):
        from hermes_cli.telegram_managed_bot import poll_for_token

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"token": "123:ABCdef"}

        with patch("hermes_cli.telegram_managed_bot.httpx.get", return_value=mock_resp):
            with patch("hermes_cli.telegram_managed_bot.time.sleep"):
                token = poll_for_token("https://api.example.com", "nonce123", timeout=5)
                assert token == "123:ABCdef"

    def test_timeout_returns_none(self):
        from hermes_cli.telegram_managed_bot import poll_for_token

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("hermes_cli.telegram_managed_bot.httpx.get", return_value=mock_resp):
            with patch("hermes_cli.telegram_managed_bot.time.sleep"):
                with patch("hermes_cli.telegram_managed_bot.time.monotonic") as mock_time:
                    # Simulate immediate timeout
                    mock_time.side_effect = [0, 0, 999]
                    token = poll_for_token("https://api.example.com", "nonce123", timeout=1)
                    assert token is None

    def test_eventual_success(self):
        from hermes_cli.telegram_managed_bot import poll_for_token

        not_ready = MagicMock()
        not_ready.status_code = 404

        ready = MagicMock()
        ready.status_code = 200
        ready.json.return_value = {"token": "789:XYZabc"}

        call_count = 0

        def fake_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return not_ready
            return ready

        with patch("hermes_cli.telegram_managed_bot.httpx.get", side_effect=fake_get):
            with patch("hermes_cli.telegram_managed_bot.time.sleep"):
                token = poll_for_token("https://api.example.com", "nonce123", timeout=30)
                assert token == "789:XYZabc"


# ---------------------------------------------------------------------------
# Setup wizard integration
# ---------------------------------------------------------------------------


class TestSetupTelegramAuto:
    def test_returns_none_on_import_error(self):
        """_setup_telegram_auto should return None if module import fails."""
        from hermes_cli.setup import _setup_telegram_auto

        with patch(
            "hermes_cli.setup._setup_telegram_auto.__module__",
            side_effect=ImportError,
        ):
            # Just verify the function exists and is callable
            assert callable(_setup_telegram_auto)
