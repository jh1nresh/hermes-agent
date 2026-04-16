"""Tests for the Gemini TTS provider in tools/tts_tool.py."""

import base64
import os
import struct
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "HERMES_SESSION_PLATFORM",
        "MINIMAX_API_KEY",
        "ELEVENLABS_API_KEY",
        "OPENAI_API_KEY",
        "VOICE_TOOLS_OPENAI_KEY",
        "MISTRAL_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def _gemini_response(pcm_bytes: bytes) -> dict:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"inlineData": {"data": base64.b64encode(pcm_bytes).decode()}}
                    ]
                }
            }
        ]
    }


def _mock_urlopen(response_payload: dict):
    resp_body = __import__("json").dumps(response_payload).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = resp_body
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestGenerateGeminiTts:
    def test_missing_api_key_raises_value_error(self, tmp_path):
        from tools.tts_tool import _generate_gemini_tts

        with pytest.raises(ValueError, match="GEMINI_API_KEY"):
            _generate_gemini_tts("Hello", str(tmp_path / "out.wav"), {})

    def test_google_api_key_fallback_accepted(self, tmp_path, monkeypatch):
        from tools.tts_tool import _generate_gemini_tts

        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
        pcm = b"\x01\x00\x02\x00\x03\x00"
        with patch(
            "tools.tts_tool.urllib.request.urlopen",
            return_value=_mock_urlopen(_gemini_response(pcm)),
        ):
            result = _generate_gemini_tts("Hi", str(tmp_path / "out.wav"), {})

        assert result == str(tmp_path / "out.wav")

    def test_writes_wav_with_correct_pcm_params(self, tmp_path, monkeypatch):
        import wave

        from tools.tts_tool import _generate_gemini_tts

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        pcm = struct.pack("<6h", 0, 1, 2, 3, 4, 5)
        with patch(
            "tools.tts_tool.urllib.request.urlopen",
            return_value=_mock_urlopen(_gemini_response(pcm)),
        ):
            out = tmp_path / "out.wav"
            _generate_gemini_tts("Hi", str(out), {})

        with wave.open(str(out), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 24000
            assert wf.readframes(wf.getnframes()) == pcm

    def test_default_model_and_voice_in_payload(self, tmp_path, monkeypatch):
        import json as _json

        from tools.tts_tool import (
            DEFAULT_GEMINI_TTS_MODEL,
            DEFAULT_GEMINI_TTS_VOICE,
            _generate_gemini_tts,
        )

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = _json.loads(req.data.decode())
            captured["headers"] = dict(req.headers)
            return _mock_urlopen(_gemini_response(b"\x00\x00"))

        with patch("tools.tts_tool.urllib.request.urlopen", side_effect=fake_urlopen):
            _generate_gemini_tts("hello", str(tmp_path / "out.wav"), {})

        assert DEFAULT_GEMINI_TTS_MODEL in captured["url"]
        voice_cfg = captured["body"]["generationConfig"]["speechConfig"]["voiceConfig"][
            "prebuiltVoiceConfig"
        ]
        assert voice_cfg["voiceName"] == DEFAULT_GEMINI_TTS_VOICE
        # Header keys normalize to capitalized form via urllib
        assert captured["headers"].get("X-goog-api-key") == "test-key"

    def test_config_overrides(self, tmp_path, monkeypatch):
        import json as _json

        from tools.tts_tool import _generate_gemini_tts

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = _json.loads(req.data.decode())
            return _mock_urlopen(_gemini_response(b"\x00\x00"))

        with patch("tools.tts_tool.urllib.request.urlopen", side_effect=fake_urlopen):
            config = {"gemini": {"model": "gemini-2.5-pro-preview-tts", "voice": "Puck"}}
            _generate_gemini_tts("hi", str(tmp_path / "out.wav"), config)

        assert "gemini-2.5-pro-preview-tts" in captured["url"]
        voice_cfg = captured["body"]["generationConfig"]["speechConfig"]["voiceConfig"][
            "prebuiltVoiceConfig"
        ]
        assert voice_cfg["voiceName"] == "Puck"

    def test_http_error_surfaced_as_runtime_error(self, tmp_path, monkeypatch):
        import urllib.error

        from tools.tts_tool import _generate_gemini_tts

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        err = urllib.error.HTTPError(
            "https://example", 429, "Too Many Requests", {}, None
        )
        err.read = MagicMock(return_value=b'{"error": "rate limit"}')

        with patch("tools.tts_tool.urllib.request.urlopen", side_effect=err):
            with pytest.raises(RuntimeError, match="429"):
                _generate_gemini_tts("hi", str(tmp_path / "out.wav"), {})

    def test_missing_audio_payload_raises_runtime_error(self, tmp_path, monkeypatch):
        from tools.tts_tool import _generate_gemini_tts

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        bad_response = {"candidates": [{"content": {"parts": []}}]}
        with patch(
            "tools.tts_tool.urllib.request.urlopen",
            return_value=_mock_urlopen(bad_response),
        ):
            with pytest.raises(RuntimeError, match="missing audio payload"):
                _generate_gemini_tts("hi", str(tmp_path / "out.wav"), {})


class TestTtsDispatcherGemini:
    def test_dispatcher_routes_to_gemini(self, tmp_path, monkeypatch):
        import json

        from tools.tts_tool import text_to_speech_tool

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        pcm = struct.pack("<2h", 100, -100)
        with patch(
            "tools.tts_tool.urllib.request.urlopen",
            return_value=_mock_urlopen(_gemini_response(pcm)),
        ), patch(
            "tools.tts_tool._load_tts_config", return_value={"provider": "gemini"}
        ):
            # Force .wav output so we skip the ffmpeg / Opus conversion branch
            output_path = str(tmp_path / "out.wav")
            result = json.loads(text_to_speech_tool("Hello", output_path=output_path))

        assert result["success"] is True
        assert result["provider"] == "gemini"


class TestCheckTtsRequirementsGemini:
    def test_gemini_key_returns_true(self, monkeypatch):
        from tools.tts_tool import check_tts_requirements

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        with patch("tools.tts_tool._import_edge_tts", side_effect=ImportError), patch(
            "tools.tts_tool._import_elevenlabs", side_effect=ImportError
        ), patch("tools.tts_tool._import_openai_client", side_effect=ImportError), patch(
            "tools.tts_tool._import_mistral_client", side_effect=ImportError
        ), patch("tools.tts_tool._check_neutts_available", return_value=False):
            assert check_tts_requirements() is True

    def test_google_api_key_also_accepted(self, monkeypatch):
        from tools.tts_tool import check_tts_requirements

        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
        with patch("tools.tts_tool._import_edge_tts", side_effect=ImportError), patch(
            "tools.tts_tool._import_elevenlabs", side_effect=ImportError
        ), patch("tools.tts_tool._import_openai_client", side_effect=ImportError), patch(
            "tools.tts_tool._import_mistral_client", side_effect=ImportError
        ), patch("tools.tts_tool._check_neutts_available", return_value=False):
            assert check_tts_requirements() is True

    def test_no_key_returns_false(self):
        from tools.tts_tool import check_tts_requirements

        with patch("tools.tts_tool._import_edge_tts", side_effect=ImportError), patch(
            "tools.tts_tool._import_elevenlabs", side_effect=ImportError
        ), patch("tools.tts_tool._import_openai_client", side_effect=ImportError), patch(
            "tools.tts_tool._import_mistral_client", side_effect=ImportError
        ), patch("tools.tts_tool._check_neutts_available", return_value=False):
            assert check_tts_requirements() is False


class TestGeminiTtsEdgeCases:
    """Tests for edge cases and conversion paths added during salvage review."""

    def test_empty_pcm_raises_runtime_error(self, tmp_path, monkeypatch):
        from tools.tts_tool import _generate_gemini_tts

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        with patch(
            "tools.tts_tool.urllib.request.urlopen",
            return_value=_mock_urlopen(_gemini_response(b"")),
        ):
            with pytest.raises(RuntimeError, match="empty audio data"):
                _generate_gemini_tts("hi", str(tmp_path / "out.wav"), {})

    def test_text_part_before_audio_is_handled(self, tmp_path, monkeypatch):
        """If the response has a text part before the audio part, still extract audio."""
        from tools.tts_tool import _generate_gemini_tts

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        pcm = b"\x01\x00\x02\x00"
        mixed_response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Here is your audio"},
                            {"inlineData": {"data": base64.b64encode(pcm).decode()}},
                        ]
                    }
                }
            ]
        }
        with patch(
            "tools.tts_tool.urllib.request.urlopen",
            return_value=_mock_urlopen(mixed_response),
        ):
            result = _generate_gemini_tts("hi", str(tmp_path / "out.wav"), {})
        assert result == str(tmp_path / "out.wav")

    def test_base_url_config_override(self, tmp_path, monkeypatch):
        import json as _json

        from tools.tts_tool import _generate_gemini_tts

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            return _mock_urlopen(_gemini_response(b"\x00\x00"))

        with patch("tools.tts_tool.urllib.request.urlopen", side_effect=fake_urlopen):
            config = {"gemini": {"base_url": "https://custom.api.example.com/v1"}}
            _generate_gemini_tts("hi", str(tmp_path / "out.wav"), config)

        assert "custom.api.example.com" in captured["url"]

    def test_wav_to_mp3_conversion_with_ffmpeg(self, tmp_path, monkeypatch):
        from tools.tts_tool import _generate_gemini_tts

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        pcm = b"\x01\x00\x02\x00\x03\x00"
        mp3_path = str(tmp_path / "out.mp3")

        with patch(
            "tools.tts_tool.urllib.request.urlopen",
            return_value=_mock_urlopen(_gemini_response(pcm)),
        ), patch("shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "subprocess.run"
        ) as mock_run:
            result = _generate_gemini_tts("hi", mp3_path, {})

        # ffmpeg should be called to convert .wav -> .mp3
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/bin/ffmpeg"
        assert mp3_path in cmd

    def test_wav_to_ogg_no_ffmpeg_renames(self, tmp_path, monkeypatch):
        from tools.tts_tool import _generate_gemini_tts

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        pcm = b"\x01\x00\x02\x00"
        ogg_path = str(tmp_path / "out.ogg")

        with patch(
            "tools.tts_tool.urllib.request.urlopen",
            return_value=_mock_urlopen(_gemini_response(pcm)),
        ), patch("shutil.which", return_value=None):
            result = _generate_gemini_tts("hi", ogg_path, {})

        # Without ffmpeg, the WAV content gets renamed to .ogg path
        assert result == ogg_path
        assert os.path.exists(ogg_path)

    def test_url_error_surfaced_as_runtime_error(self, tmp_path, monkeypatch):
        import urllib.error

        from tools.tts_tool import _generate_gemini_tts

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        err = urllib.error.URLError("Name or service not known")

        with patch("tools.tts_tool.urllib.request.urlopen", side_effect=err):
            with pytest.raises(RuntimeError, match="connection failed"):
                _generate_gemini_tts("hi", str(tmp_path / "out.wav"), {})
