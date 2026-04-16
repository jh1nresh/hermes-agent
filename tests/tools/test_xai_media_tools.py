import asyncio
import json
from unittest.mock import AsyncMock, MagicMock


def test_video_generate_schema_guides_prompt_without_requiring_it():
    from tools.video_generation_tool import VIDEO_GENERATE_SCHEMA

    parameters = VIDEO_GENERATE_SCHEMA["parameters"]
    properties = parameters["properties"]

    assert "prompt" not in parameters.get("required", [])
    assert "Usually pass this" in properties["prompt"]["description"]
    assert "Optional only for image-to-video" in properties["prompt"]["description"]
    assert "output" not in properties
    assert "output_upload_url" not in properties


class _FakeResponse:
    def __init__(self, *, json_payload=None, content=b""):
        self._json_payload = json_payload or {}
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self._json_payload


def _fake_httpx_client(*, post_fn, get_fn=None):
    """Build a mock httpx.AsyncClient that delegates to sync test helpers."""
    client = AsyncMock()

    async def _post(url, *, headers=None, json=None, timeout=None):
        return post_fn(url, headers=headers, json=json, timeout=timeout)

    async def _get(url, *, headers=None, timeout=None):
        return get_fn(url, headers=headers, timeout=timeout)

    client.post = _post
    if get_fn is not None:
        client.get = _get
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def test_image_generate_tool_supports_xai_provider(monkeypatch):
    from tools.image_generation_tool import image_generate_tool
    from hermes_cli import __version__

    def _fake_post(url, headers=None, json=None, timeout=None):
        assert url == "https://api.x.ai/v1/images/generations"
        assert headers["User-Agent"] == f"Hermes-Agent/{__version__}"
        assert json["model"] == "grok-imagine-image"
        assert json["aspect_ratio"] == "16:9"
        return _FakeResponse(
            json_payload={
                "data": [
                    {
                        "url": "https://cdn.example.com/generated.png",
                        "width": 1280,
                        "height": 720,
                    }
                ]
            }
        )

    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")
    monkeypatch.setattr("tools.image_generation_tool.requests.post", _fake_post)
    monkeypatch.setattr("tools.image_generation_tool._has_fal_backend", lambda: False)

    result = json.loads(
        image_generate_tool(
            prompt="a cinematic skyline at sunset",
            provider="xai",
            aspect_ratio="landscape",
        )
    )

    assert result["success"] is True
    assert result["provider"] == "xai"
    assert result["image"] == "https://cdn.example.com/generated.png"


def test_image_generate_tool_supports_xai_reference_images_for_generate(monkeypatch):
    from tools.image_generation_tool import image_generate_tool

    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(
            json_payload={
                "data": [
                    {
                        "url": "https://cdn.example.com/reference-guided.png",
                        "width": 1280,
                        "height": 720,
                    }
                ]
            }
        )

    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")
    monkeypatch.setattr("tools.image_generation_tool.requests.post", _fake_post)
    monkeypatch.setattr("tools.image_generation_tool._has_fal_backend", lambda: True)

    result = json.loads(
        image_generate_tool(
            prompt="A campaign portrait in xAI style.",
            provider="auto",
            aspect_ratio="16:9",
            reference_image_urls=[
                "https://cdn.example.com/reference-a.png",
                "https://cdn.example.com/reference-b.png",
            ],
        )
    )

    assert captured["url"] == "https://api.x.ai/v1/images/generations"
    assert captured["json"]["reference_images"] == [
        {"type": "image_url", "url": "https://cdn.example.com/reference-a.png"},
        {"type": "image_url", "url": "https://cdn.example.com/reference-b.png"},
    ]
    assert result["success"] is True
    assert result["provider"] == "xai"
    assert result["image"] == "https://cdn.example.com/reference-guided.png"


def test_image_generate_tool_supports_xai_edit_with_multiple_source_images(monkeypatch):
    from tools.image_generation_tool import image_generate_tool

    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(
            json_payload={
                "data": [
                    {
                        "url": "https://cdn.example.com/edited.png",
                        "width": 1536,
                        "height": 1024,
                    }
                ]
            }
        )

    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")
    monkeypatch.setattr("tools.image_generation_tool.requests.post", _fake_post)
    monkeypatch.setattr("tools.image_generation_tool._has_fal_backend", lambda: True)

    result = json.loads(
        image_generate_tool(
            prompt="Put the two people together in one cinematic rooftop portrait.",
            operation="edit",
            provider="auto",
            aspect_ratio="3:2",
            resolution="2k",
            source_image_urls=[
                "https://cdn.example.com/person-a.png",
                "https://cdn.example.com/person-b.png",
            ],
        )
    )

    assert captured["url"] == "https://api.x.ai/v1/images/edits"
    assert captured["json"]["images"][0]["url"] == "https://cdn.example.com/person-a.png"
    assert captured["json"]["images"][1]["url"] == "https://cdn.example.com/person-b.png"
    assert captured["json"]["aspect_ratio"] == "3:2"
    assert captured["json"]["resolution"] == "2k"
    assert result["success"] is True
    assert result["provider"] == "xai"
    assert result["operation"] == "edit"
    assert result["image"] == "https://cdn.example.com/edited.png"


def test_image_generate_tool_uses_configured_xai_provider_by_default(monkeypatch):
    from tools.image_generation_tool import image_generate_tool

    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(
            json_payload={
                "data": [
                    {
                        "url": "https://cdn.example.com/configured-xai.png",
                        "width": 1024,
                        "height": 1024,
                    }
                ]
            }
        )

    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")
    monkeypatch.setattr("tools.image_generation_tool.requests.post", _fake_post)
    monkeypatch.setattr("tools.image_generation_tool._has_fal_backend", lambda: True)
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"image_generation": {"provider": "xai"}},
    )

    result = json.loads(
        image_generate_tool(
            prompt="an xAI-first image backend test",
            aspect_ratio="square",
        )
    )

    assert captured["url"] == "https://api.x.ai/v1/images/generations"
    assert result["success"] is True
    assert result["provider"] == "xai"


def test_image_generate_tool_prefers_xai_only_features_over_saved_fal_default(monkeypatch):
    from tools.image_generation_tool import image_generate_tool

    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(
            json_payload={
                "data": [
                    {
                        "url": "https://cdn.example.com/edited-with-xai.png",
                        "width": 1024,
                        "height": 1024,
                    }
                ]
            }
        )

    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")
    monkeypatch.setattr("tools.image_generation_tool.requests.post", _fake_post)
    monkeypatch.setattr("tools.image_generation_tool._has_fal_backend", lambda: True)
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"image_generation": {"provider": "fal"}},
    )

    result = json.loads(
        image_generate_tool(
            prompt="edit this image",
            provider="auto",
            operation="edit",
            source_image_url="https://cdn.example.com/source.png",
        )
    )

    assert captured["url"] == "https://api.x.ai/v1/images/edits"
    assert result["success"] is True
    assert result["provider"] == "xai"


def test_image_generate_tool_errors_clearly_when_xai_only_features_need_xai(monkeypatch):
    from tools.image_generation_tool import image_generate_tool

    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr("tools.image_generation_tool._has_fal_backend", lambda: True)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {})

    result = json.loads(
        image_generate_tool(
            prompt="edit this image",
            provider="auto",
            operation="edit",
            source_image_url="https://cdn.example.com/source.png",
        )
    )

    assert result["success"] is False
    assert "requires xAI image support" in result["error"]


def test_video_generate_tool_polls_until_done(monkeypatch):
    from tools.video_generation_tool import video_generate_tool
    from hermes_cli import __version__

    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["submit_url"] = url
        captured["submit_json"] = json
        return _FakeResponse(json_payload={"request_id": "vid-123"})

    def _fake_get(url, headers=None, timeout=None):
        captured.setdefault("poll_urls", []).append(url)
        return _FakeResponse(
            json_payload={
                "status": "done",
                "video": {"url": "https://cdn.example.com/generated.mp4"},
            }
        )

    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")
    mock_client = _fake_httpx_client(post_fn=_fake_post, get_fn=_fake_get)
    monkeypatch.setattr("tools.video_generation_tool.httpx.AsyncClient", lambda: mock_client)

    result = json.loads(
        asyncio.run(
            video_generate_tool(
                prompt="slow drone shot over a neon city",
                duration=8,
                aspect_ratio="16:9",
                resolution="720p",
                poll_interval_seconds=0,
                timeout_seconds=30,
            )
        )
    )

    assert captured["submit_url"] == "https://api.x.ai/v1/videos/generations"
    assert captured["submit_json"]["prompt"] == "slow drone shot over a neon city"
    assert captured["poll_urls"] == ["https://api.x.ai/v1/videos/vid-123"]
    assert result["success"] is True
    assert result["provider"] == "xai"
    assert result["video"] == "https://cdn.example.com/generated.mp4"


def test_video_generate_tool_sends_hermes_user_agent(monkeypatch):
    from tools.video_generation_tool import video_generate_tool
    from hermes_cli import __version__

    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["submit_headers"] = headers
        return _FakeResponse(json_payload={"request_id": "vid-ua"})

    def _fake_get(url, headers=None, timeout=None):
        captured["poll_headers"] = headers
        return _FakeResponse(
            json_payload={
                "status": "done",
                "video": {"url": "https://cdn.example.com/generated.mp4"},
            }
        )

    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")
    mock_client = _fake_httpx_client(post_fn=_fake_post, get_fn=_fake_get)
    monkeypatch.setattr("tools.video_generation_tool.httpx.AsyncClient", lambda: mock_client)

    asyncio.run(
        video_generate_tool(
            prompt="slow drone shot over a neon city",
            duration=8,
            aspect_ratio="16:9",
            resolution="720p",
            poll_interval_seconds=0,
            timeout_seconds=30,
        )
    )

    assert captured["submit_headers"]["User-Agent"] == f"Hermes-Agent/{__version__}"
    assert captured["poll_headers"]["User-Agent"] == f"Hermes-Agent/{__version__}"


def test_video_generate_tool_supports_native_extend(monkeypatch):
    from tools.video_generation_tool import video_generate_tool

    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["submit_url"] = url
        captured["submit_json"] = json
        return _FakeResponse(json_payload={"request_id": "vid-456"})

    def _fake_get(url, headers=None, timeout=None):
        return _FakeResponse(
            json_payload={
                "status": "done",
                "video": {"url": "https://cdn.example.com/extended.mp4"},
            }
        )

    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")
    mock_client = _fake_httpx_client(post_fn=_fake_post, get_fn=_fake_get)
    monkeypatch.setattr("tools.video_generation_tool.httpx.AsyncClient", lambda: mock_client)

    result = json.loads(
        asyncio.run(
            video_generate_tool(
                prompt="Continue the shot as the camera drifts behind the subject.",
                operation="extend",
                duration=6,
                video_url="https://cdn.example.com/source.mp4",
                poll_interval_seconds=0,
                timeout_seconds=30,
            )
        )
    )

    assert captured["submit_url"] == "https://api.x.ai/v1/videos/extensions"
    assert captured["submit_json"]["video"]["url"] == "https://cdn.example.com/source.mp4"
    assert captured["submit_json"]["duration"] == 6
    assert result["success"] is True
    assert result["operation"] == "extend"
    assert result["video"] == "https://cdn.example.com/extended.mp4"


def test_video_generate_tool_recovers_promptless_extend_from_source_video_url(monkeypatch):
    from tools.video_generation_tool import video_generate_tool

    captured = {}
    source_url = "https://cdn.example.com/source.mp4"

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["submit_url"] = url
        captured["submit_json"] = json
        return _FakeResponse(json_payload={"request_id": "vid-extend-auto"})

    def _fake_get(url, headers=None, timeout=None):
        return _FakeResponse(
            json_payload={
                "status": "done",
                "video": {"url": "https://cdn.example.com/extended-auto.mp4"},
            }
        )

    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")
    mock_client = _fake_httpx_client(post_fn=_fake_post, get_fn=_fake_get)
    monkeypatch.setattr("tools.video_generation_tool.httpx.AsyncClient", lambda: mock_client)

    result = json.loads(
        asyncio.run(
            video_generate_tool(
                duration=8,
                video_url=source_url,
                poll_interval_seconds=0,
                timeout_seconds=30,
            )
        )
    )

    assert captured["submit_url"] == "https://api.x.ai/v1/videos/extensions"
    assert captured["submit_json"]["video"]["url"] == source_url
    assert captured["submit_json"]["prompt"] == "Continue the existing video naturally."
    assert result["success"] is True
    assert result["operation"] == "extend"
    assert any("default continuation prompt" in note for note in result["notes"])


def test_video_generate_tool_edit_without_prompt_still_errors(monkeypatch):
    from tools.video_generation_tool import video_generate_tool

    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")

    result = json.loads(
        asyncio.run(
            video_generate_tool(
                operation="edit",
                video_url="https://cdn.example.com/source.mp4",
            )
        )
    )

    assert result["error"] == "prompt is required for xAI video edit"


def test_video_generate_tool_uses_video_object_for_edit(monkeypatch):
    from tools.video_generation_tool import video_generate_tool

    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["submit_url"] = url
        captured["submit_json"] = json
        return _FakeResponse(json_payload={"request_id": "vid-edit"})

    def _fake_get(url, headers=None, timeout=None):
        return _FakeResponse(
            json_payload={
                "status": "done",
                "model": "grok-imagine-video",
                "video": {
                    "url": "https://cdn.example.com/edited.mp4",
                    "duration": 8,
                    "respect_moderation": True,
                },
            }
        )

    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")
    mock_client = _fake_httpx_client(post_fn=_fake_post, get_fn=_fake_get)
    monkeypatch.setattr("tools.video_generation_tool.httpx.AsyncClient", lambda: mock_client)

    result = json.loads(
        asyncio.run(
            video_generate_tool(
                prompt="Give the subject a silver necklace.",
                operation="edit",
                video_url="https://cdn.example.com/source.mp4",
                user="jaaneek",
                poll_interval_seconds=0,
                timeout_seconds=30,
            )
        )
    )

    assert captured["submit_url"] == "https://api.x.ai/v1/videos/edits"
    assert captured["submit_json"]["video"]["url"] == "https://cdn.example.com/source.mp4"
    assert captured["submit_json"]["user"] == "jaaneek"
    assert "output" not in captured["submit_json"]
    assert result["success"] is True
    assert result["operation"] == "edit"
    assert result["video"] == "https://cdn.example.com/edited.mp4"
    assert result["respect_moderation"] is True


def test_video_generate_tool_ignores_duration_for_edit(monkeypatch):
    from tools.video_generation_tool import video_generate_tool

    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["submit_json"] = json
        return _FakeResponse(json_payload={"request_id": "vid-edit-duration"})

    def _fake_get(url, headers=None, timeout=None):
        return _FakeResponse(
            json_payload={
                "status": "done",
                "video": {
                    "url": "https://cdn.example.com/edited.mp4",
                    "duration": 8,
                },
            }
        )

    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")
    mock_client = _fake_httpx_client(post_fn=_fake_post, get_fn=_fake_get)
    monkeypatch.setattr("tools.video_generation_tool.httpx.AsyncClient", lambda: mock_client)

    result = json.loads(
        asyncio.run(
            video_generate_tool(
                prompt="Give the subject a silver necklace.",
                operation="edit",
                duration=20,
                video_url="https://cdn.example.com/source.mp4",
                poll_interval_seconds=0,
                timeout_seconds=30,
            )
        )
    )

    assert result["success"] is True
    assert "duration" not in captured["submit_json"]
    assert result["duration"] == 8


def test_video_generate_tool_supports_promptless_image_to_video(monkeypatch):
    from tools.video_generation_tool import video_generate_tool

    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["submit_url"] = url
        captured["submit_json"] = json
        return _FakeResponse(json_payload={"request_id": "vid-i2v"})

    def _fake_get(url, headers=None, timeout=None):
        return _FakeResponse(
            json_payload={
                "status": "done",
                "video": {
                    "url": "https://cdn.example.com/i2v.mp4",
                    "duration": 8,
                    "respect_moderation": True,
                },
            }
        )

    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")
    mock_client = _fake_httpx_client(post_fn=_fake_post, get_fn=_fake_get)
    monkeypatch.setattr("tools.video_generation_tool.httpx.AsyncClient", lambda: mock_client)

    result = json.loads(
        asyncio.run(
            video_generate_tool(
                prompt="",
                operation="generate",
                image_url="https://cdn.example.com/still.png",
                seconds=8,
                aspect_ratio="4:3",
                resolution="480p",
                size="848x480",
                poll_interval_seconds=0,
                timeout_seconds=30,
            )
        )
    )

    assert captured["submit_url"] == "https://api.x.ai/v1/videos/generations"
    assert "prompt" not in captured["submit_json"]
    assert captured["submit_json"]["image"]["url"] == "https://cdn.example.com/still.png"
    assert captured["submit_json"]["duration"] == 8
    assert captured["submit_json"]["aspect_ratio"] == "4:3"
    assert captured["submit_json"]["resolution"] == "480p"
    assert captured["submit_json"]["size"] == "848x480"
    assert result["success"] is True
    assert result["video"] == "https://cdn.example.com/i2v.mp4"
