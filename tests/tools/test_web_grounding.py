"""Tests for web grounding citations + live summary display streaming.

Covers:
- tools/summary_display.py callback registry + single-slot semantics
- source registry stable citation IDs
- web_search_tool result annotation (source ids + citation_guidance)
- web_extract_tool trimmed-output annotation
- _try_stream_summarizer streaming fast path + fallback semantics
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tools import summary_display
from tools.web_tools import (
    CITATION_GUIDANCE,
    CITATION_GUIDANCE_AUTO,
    _get_citation_guidance,
    _get_citations_mode,
    _summary_stream_enabled,
    _try_stream_summarizer,
    get_source_id,
    reset_source_registry,
)


@pytest.fixture(autouse=True)
def _clean_state():
    reset_source_registry()
    summary_display.set_summary_stream_callback(None)
    summary_display._slot_holder = None
    yield
    reset_source_registry()
    summary_display.set_summary_stream_callback(None)
    summary_display._slot_holder = None


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

class TestSourceRegistry:
    def test_ids_are_stable_and_sequential(self):
        a = get_source_id("https://example.com/a")
        b = get_source_id("https://example.com/b")
        assert (a, b) == (1, 2)
        # Same URL → same id
        assert get_source_id("https://example.com/a") == a

    def test_normalization_fragment_and_trailing_slash(self):
        a = get_source_id("https://example.com/page")
        assert get_source_id("https://example.com/page/") == a
        assert get_source_id("https://example.com/page#section") == a

    def test_reset_clears_ids(self):
        get_source_id("https://example.com/x")
        reset_source_registry()
        assert get_source_id("https://example.com/y") == 1


# ---------------------------------------------------------------------------
# Summary display registry
# ---------------------------------------------------------------------------

class TestSummaryDisplay:
    def test_emit_noop_without_callback(self):
        # Must not raise
        summary_display.emit("delta", text="hi")

    def test_emit_invokes_callback(self):
        events = []
        summary_display.set_summary_stream_callback(
            lambda event, **kw: events.append((event, kw))
        )
        summary_display.emit("start", url="https://x.com", title="X")
        summary_display.emit("delta", text="token")
        assert events == [
            ("start", {"url": "https://x.com", "title": "X"}),
            ("delta", {"text": "token"}),
        ]

    def test_callback_exception_swallowed(self):
        def boom(event, **kw):
            raise RuntimeError("nope")
        summary_display.set_summary_stream_callback(boom)
        summary_display.emit("start", url="u", title="t")  # must not raise

    def test_slot_requires_callback(self):
        token = object()
        assert summary_display.try_acquire_stream_slot(token) is False

    def test_slot_single_holder(self):
        summary_display.set_summary_stream_callback(lambda e, **kw: None)
        t1, t2 = object(), object()
        assert summary_display.try_acquire_stream_slot(t1) is True
        assert summary_display.try_acquire_stream_slot(t2) is False
        summary_display.release_stream_slot(t1)
        assert summary_display.try_acquire_stream_slot(t2) is True
        summary_display.release_stream_slot(t2)

    def test_release_wrong_token_keeps_slot(self):
        summary_display.set_summary_stream_callback(lambda e, **kw: None)
        t1, t2 = object(), object()
        assert summary_display.try_acquire_stream_slot(t1)
        summary_display.release_stream_slot(t2)  # not the holder — no-op
        assert summary_display.try_acquire_stream_slot(t2) is False
        summary_display.release_stream_slot(t1)


# ---------------------------------------------------------------------------
# web_search_tool annotation
# ---------------------------------------------------------------------------

class TestSearchAnnotation:
    def _provider(self, results):
        provider = MagicMock()
        provider.name = "fake"
        provider.supports_search.return_value = True
        provider.search.return_value = {"success": True, "data": {"web": results}}
        return provider

    def test_results_get_source_ids_and_guidance(self):
        from tools import web_tools
        provider = self._provider([
            {"title": "A", "url": "https://a.com", "description": "aaa", "position": 1},
            {"title": "B", "url": "https://b.com", "description": "bbb", "position": 2},
        ])
        with patch.object(web_tools, "_ensure_web_plugins_loaded"), \
             patch("agent.web_search_registry.get_provider", return_value=provider), \
             patch.object(web_tools, "_get_search_backend", return_value="fake"):
            out = json.loads(web_tools.web_search_tool("query"))
        web = out["data"]["web"]
        assert web[0]["source"] == "[1]"
        assert web[1]["source"] == "[2]"
        assert out["citation_guidance"] == CITATION_GUIDANCE_AUTO

    def test_same_url_keeps_id_across_calls(self):
        from tools import web_tools
        provider = self._provider([
            {"title": "A", "url": "https://a.com", "description": "aaa", "position": 1},
        ])
        with patch.object(web_tools, "_ensure_web_plugins_loaded"), \
             patch("agent.web_search_registry.get_provider", return_value=provider), \
             patch.object(web_tools, "_get_search_backend", return_value="fake"):
            first = json.loads(web_tools.web_search_tool("query"))
            second = json.loads(web_tools.web_search_tool("query again"))
        assert first["data"]["web"][0]["source"] == second["data"]["web"][0]["source"]

    def test_empty_results_no_guidance(self):
        from tools import web_tools
        provider = self._provider([])
        with patch.object(web_tools, "_ensure_web_plugins_loaded"), \
             patch("agent.web_search_registry.get_provider", return_value=provider), \
             patch.object(web_tools, "_get_search_backend", return_value="fake"):
            out = json.loads(web_tools.web_search_tool("query"))
        assert "citation_guidance" not in out


# ---------------------------------------------------------------------------
# web_extract_tool annotation
# ---------------------------------------------------------------------------

class TestExtractAnnotation:
    def test_trimmed_results_carry_source_and_guidance(self):
        from tools import web_tools

        provider = MagicMock()
        provider.name = "fake"
        provider.supports_extract.return_value = True
        provider.extract = MagicMock(return_value=[
            {"url": "https://a.com", "title": "A", "content": "short content"},
        ])

        with patch.object(web_tools, "_ensure_web_plugins_loaded"), \
             patch("agent.web_search_registry.get_provider", return_value=provider), \
             patch.object(web_tools, "_get_extract_backend", return_value="fake"), \
             patch.object(web_tools, "check_auxiliary_model", return_value=False), \
             patch.object(web_tools, "async_is_safe_url", new=_async_true):
            out = json.loads(asyncio.run(
                web_tools.web_extract_tool(["https://a.com"], use_llm_processing=False)
            ))
        assert out["results"][0]["source"] == "[1]"
        assert out["citation_guidance"] == CITATION_GUIDANCE_AUTO

    def test_search_then_extract_share_ids(self):
        from tools import web_tools

        search_provider = MagicMock()
        search_provider.name = "fake"
        search_provider.supports_search.return_value = True
        search_provider.search.return_value = {
            "success": True,
            "data": {"web": [{"title": "A", "url": "https://a.com", "description": "d", "position": 1}]},
        }
        extract_provider = MagicMock()
        extract_provider.name = "fake"
        extract_provider.supports_extract.return_value = True
        extract_provider.extract = MagicMock(return_value=[
            {"url": "https://a.com", "title": "A", "content": "body"},
        ])

        with patch.object(web_tools, "_ensure_web_plugins_loaded"), \
             patch("agent.web_search_registry.get_provider", return_value=search_provider), \
             patch.object(web_tools, "_get_search_backend", return_value="fake"):
            search_out = json.loads(web_tools.web_search_tool("q"))

        with patch.object(web_tools, "_ensure_web_plugins_loaded"), \
             patch("agent.web_search_registry.get_provider", return_value=extract_provider), \
             patch.object(web_tools, "_get_extract_backend", return_value="fake"), \
             patch.object(web_tools, "check_auxiliary_model", return_value=False), \
             patch.object(web_tools, "async_is_safe_url", new=_async_true):
            extract_out = json.loads(asyncio.run(
                web_tools.web_extract_tool(["https://a.com"], use_llm_processing=False)
            ))

        assert search_out["data"]["web"][0]["source"] == extract_out["results"][0]["source"] == "[1]"


async def _async_true(url):
    return True


# ---------------------------------------------------------------------------
# Config toggles (web.citations / web.summary_stream)
# ---------------------------------------------------------------------------

class TestCitationModes:
    def test_default_mode_is_auto(self):
        with patch("tools.web_tools._load_web_config", return_value={}):
            assert _get_citations_mode() == "auto"
            assert _get_citation_guidance() == CITATION_GUIDANCE_AUTO

    def test_always_mode(self):
        with patch("tools.web_tools._load_web_config", return_value={"citations": "always"}):
            assert _get_citation_guidance() == CITATION_GUIDANCE

    def test_off_mode_returns_none(self):
        with patch("tools.web_tools._load_web_config", return_value={"citations": "off"}):
            assert _get_citation_guidance() is None

    def test_invalid_mode_falls_back_to_auto(self):
        with patch("tools.web_tools._load_web_config", return_value={"citations": "bogus"}):
            assert _get_citations_mode() == "auto"

    def test_off_mode_strips_annotations_from_search(self):
        from tools import web_tools
        provider = MagicMock()
        provider.name = "fake"
        provider.supports_search.return_value = True
        provider.search.return_value = {
            "success": True,
            "data": {"web": [{"title": "A", "url": "https://a.com", "description": "d", "position": 1}]},
        }
        with patch.object(web_tools, "_ensure_web_plugins_loaded"), \
             patch("agent.web_search_registry.get_provider", return_value=provider), \
             patch.object(web_tools, "_get_search_backend", return_value="fake"), \
             patch.object(web_tools, "_load_web_config", return_value={"citations": "off"}):
            out = json.loads(web_tools.web_search_tool("query"))
        assert "citation_guidance" not in out
        assert "source" not in out["data"]["web"][0]

    def test_off_mode_strips_annotations_from_extract(self):
        from tools import web_tools
        provider = MagicMock()
        provider.name = "fake"
        provider.supports_extract.return_value = True
        provider.extract = MagicMock(return_value=[
            {"url": "https://a.com", "title": "A", "content": "body"},
        ])
        with patch.object(web_tools, "_ensure_web_plugins_loaded"), \
             patch("agent.web_search_registry.get_provider", return_value=provider), \
             patch.object(web_tools, "_get_extract_backend", return_value="fake"), \
             patch.object(web_tools, "check_auxiliary_model", return_value=False), \
             patch.object(web_tools, "async_is_safe_url", new=_async_true), \
             patch.object(web_tools, "_load_web_config", return_value={"citations": "off"}):
            out = json.loads(asyncio.run(
                web_tools.web_extract_tool(["https://a.com"], use_llm_processing=False)
            ))
        assert "citation_guidance" not in out
        assert "source" not in out["results"][0]

    def test_summary_stream_toggle(self):
        with patch("tools.web_tools._load_web_config", return_value={}):
            assert _summary_stream_enabled() is True
        with patch("tools.web_tools._load_web_config", return_value={"summary_stream": False}):
            assert _summary_stream_enabled() is False


# ---------------------------------------------------------------------------
# Streaming summarizer fast path
# ---------------------------------------------------------------------------

def _make_stream_chunks(texts):
    async def _gen():
        for t in texts:
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=t))]
            )
    return _gen()


class _FakeAsyncClient:
    """Minimal async client whose chat.completions.create returns a stream."""

    def __init__(self, chunks=None, exc=None):
        self._chunks = chunks
        self._exc = exc
        self.kwargs = None
        outer = self

        class _Completions:
            async def create(self, **kwargs):
                outer.kwargs = kwargs
                if outer._exc:
                    raise outer._exc
                return _make_stream_chunks(outer._chunks)

        self.chat = SimpleNamespace(completions=_Completions())


class TestStreamSummarizer:
    def test_no_callback_returns_none_without_calling(self):
        client = _FakeAsyncClient(chunks=["x"])
        result = asyncio.run(_try_stream_summarizer(
            client, "model", {}, "sys", "user", 1000))
        assert result is None
        assert client.kwargs is None  # never reached the API

    def test_streams_and_returns_summary(self):
        events = []
        summary_display.set_summary_stream_callback(
            lambda event, **kw: events.append((event, kw)))
        client = _FakeAsyncClient(chunks=["Hello ", "world"])
        result = asyncio.run(_try_stream_summarizer(
            client, "model", {}, "sys", "user", 1000,
            context_str="Title: T\nSource: https://a.com\n\n"))
        assert result == "Hello world"
        assert client.kwargs["stream"] is True
        names = [e[0] for e in events]
        assert names == ["start", "delta", "delta", "end"]
        assert events[0][1] == {"url": "https://a.com", "title": "T"}
        assert events[-1][1]["ok"] is True
        assert events[-1][1]["char_count"] == len("Hello world")
        # Slot released
        assert summary_display.try_acquire_stream_slot(object()) is True

    def test_provider_error_falls_back(self):
        events = []
        summary_display.set_summary_stream_callback(
            lambda event, **kw: events.append((event, kw)))
        client = _FakeAsyncClient(exc=RuntimeError("stream unsupported"))
        result = asyncio.run(_try_stream_summarizer(
            client, "model", {}, "sys", "user", 1000))
        assert result is None
        # Error before "start" → no end event needed
        assert all(e[0] != "end" or e[1]["ok"] is False for e in events)
        # Slot released even on failure
        assert summary_display.try_acquire_stream_slot(object()) is True

    def test_empty_stream_falls_back(self):
        summary_display.set_summary_stream_callback(lambda e, **kw: None)
        client = _FakeAsyncClient(chunks=[])
        result = asyncio.run(_try_stream_summarizer(
            client, "model", {}, "sys", "user", 1000))
        assert result is None

    def test_slot_busy_returns_none(self):
        summary_display.set_summary_stream_callback(lambda e, **kw: None)
        holder = object()
        assert summary_display.try_acquire_stream_slot(holder)
        try:
            client = _FakeAsyncClient(chunks=["x"])
            result = asyncio.run(_try_stream_summarizer(
                client, "model", {}, "sys", "user", 1000))
            assert result is None
            assert client.kwargs is None
        finally:
            summary_display.release_stream_slot(holder)
