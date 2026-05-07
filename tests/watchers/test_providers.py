"""Tests for watchers/providers.py — watermark dedup, first-run baseline, provider registry."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from watchers.providers import (
    PROVIDERS,
    ProviderError,
    register,
    resolve_provider,
)


class TestProviderRegistry:
    def test_builtin_providers_registered(self):
        for name in ("http_json", "rss", "github"):
            assert name in PROVIDERS
            assert callable(PROVIDERS[name])

    def test_resolve_provider_case_insensitive(self):
        assert resolve_provider("HTTP_JSON") is PROVIDERS["http_json"]
        assert resolve_provider("Http_Json") is PROVIDERS["http_json"]

    def test_resolve_unknown_raises_keyerror(self):
        with pytest.raises(KeyError, match="Unknown watcher provider"):
            resolve_provider("does-not-exist")

    def test_register_adds_custom_provider(self):
        def custom(_config, _watermark):
            return [], {}

        try:
            register("unit-test-provider", custom)
            assert resolve_provider("unit-test-provider") is custom
        finally:
            PROVIDERS.pop("unit-test-provider", None)


class TestHttpJsonProvider:
    """The http_json provider: list of items → dedup by id_field."""

    def _call(self, response_body, config, watermark):
        """Invoke the provider with a mocked HTTP response."""
        provider = resolve_provider("http_json")
        with patch(
            "watchers.providers._http_get",
            return_value=json.dumps(response_body).encode("utf-8"),
        ):
            return provider(config, watermark)

    def test_first_run_records_baseline_without_emitting(self):
        """First poll of a new watcher must NOT replay the existing feed."""
        items = [{"id": 1}, {"id": 2}, {"id": 3}]
        new_items, wm = self._call(items, {"url": "x"}, {})
        assert new_items == []
        assert sorted(wm["seen_ids"]) == ["1", "2", "3"]

    def test_subsequent_poll_emits_new_items_only(self):
        # Baseline recorded from a prior poll.
        wm = {"seen_ids": ["1", "2", "3"]}
        items = [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}, {"id": 5}]
        new_items, new_wm = self._call(items, {"url": "x"}, wm)
        assert [i["id"] for i in new_items] == [4, 5]
        assert "4" in new_wm["seen_ids"]
        assert "5" in new_wm["seen_ids"]

    def test_idempotent_on_empty_delta(self):
        wm = {"seen_ids": ["1", "2"]}
        items = [{"id": 1}, {"id": 2}]
        new_items, _ = self._call(items, {"url": "x"}, wm)
        assert new_items == []

    def test_items_path_dotted_lookup(self):
        body = {"data": {"results": [{"id": 1}, {"id": 2}]}}
        _, wm = self._call(body, {"url": "x", "items_path": "data.results"}, {})
        assert sorted(wm["seen_ids"]) == ["1", "2"]

    def test_missing_id_field_skips_item(self):
        items = [{"id": 1}, {"other": 2}, {"id": 3}]
        _, wm = self._call(items, {"url": "x"}, {})
        # Item without id is dropped entirely.
        assert sorted(wm["seen_ids"]) == ["1", "3"]

    def test_custom_id_field(self):
        items = [{"uuid": "a"}, {"uuid": "b"}]
        _, wm = self._call(items, {"url": "x", "id_field": "uuid"}, {})
        assert sorted(wm["seen_ids"]) == ["a", "b"]

    def test_max_seen_caps_watermark_memory(self):
        # 600 items with max_seen=100 → only keep last 100 after cap.
        items = [{"id": i} for i in range(600)]
        _, wm = self._call(items, {"url": "x", "max_seen": 100}, {})
        assert len(wm["seen_ids"]) == 100

    def test_raises_provider_error_on_non_list_result(self):
        provider = resolve_provider("http_json")
        with patch("watchers.providers._http_get", return_value=b'{"not": "a list"}'):
            with pytest.raises(ProviderError, match="did not resolve to a list"):
                provider({"url": "x", "items_path": "not"}, {})

    def test_missing_url_raises_provider_error(self):
        provider = resolve_provider("http_json")
        with pytest.raises(ProviderError, match="'url' is required"):
            provider({}, {})

    def test_invalid_json_raises_provider_error(self):
        provider = resolve_provider("http_json")
        with patch("watchers.providers._http_get", return_value=b"not json"):
            with pytest.raises(ProviderError, match="not valid JSON"):
                provider({"url": "x"}, {})


class TestRssProvider:
    SAMPLE_RSS = b"""<?xml version="1.0"?>
    <rss version="2.0">
      <channel>
        <title>Example</title>
        <item>
          <title>First post</title>
          <link>https://example.com/1</link>
          <guid>post-1</guid>
          <description>Hello</description>
          <pubDate>Mon, 05 May 2026 10:00:00 GMT</pubDate>
        </item>
        <item>
          <title>Second post</title>
          <link>https://example.com/2</link>
          <guid>post-2</guid>
          <description>World</description>
          <pubDate>Tue, 06 May 2026 10:00:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """

    SAMPLE_ATOM = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>atom-1</id>
        <title>Atom One</title>
        <link href="https://example.com/a1"/>
        <summary>Atom summary</summary>
        <updated>2026-05-07T12:00:00Z</updated>
      </entry>
    </feed>
    """

    def test_rss_first_run_baseline(self):
        provider = resolve_provider("rss")
        with patch("watchers.providers._http_get", return_value=self.SAMPLE_RSS):
            new_items, wm = provider({"url": "http://f.example/feed"}, {})
        assert new_items == []
        assert "post-1" in wm["seen_guids"]
        assert "post-2" in wm["seen_guids"]

    def test_rss_subsequent_run_emits_only_new(self):
        provider = resolve_provider("rss")
        wm = {"seen_guids": ["post-1"]}  # only post-1 seen previously
        with patch("watchers.providers._http_get", return_value=self.SAMPLE_RSS):
            new_items, new_wm = provider({"url": "http://f.example/feed"}, wm)
        assert len(new_items) == 1
        assert new_items[0]["id"] == "post-2"
        assert new_items[0]["title"] == "Second post"
        assert new_items[0]["url"] == "https://example.com/2"

    def test_atom_format_parses(self):
        provider = resolve_provider("rss")
        with patch("watchers.providers._http_get", return_value=self.SAMPLE_ATOM):
            _, wm = provider({"url": "http://f/atom"}, {})
        assert "atom-1" in wm["seen_guids"]

    def test_invalid_xml_raises_provider_error(self):
        provider = resolve_provider("rss")
        with patch("watchers.providers._http_get", return_value=b"<not valid"):
            with pytest.raises(ProviderError, match="invalid XML"):
                provider({"url": "x"}, {})


class TestGithubProvider:
    def test_rejects_invalid_repo_format(self):
        provider = resolve_provider("github")
        with pytest.raises(ProviderError, match="must be 'owner/name'"):
            provider({"repo": "no-slash-here"}, {})

    def test_rejects_unknown_scope(self):
        provider = resolve_provider("github")
        with pytest.raises(ProviderError, match="scope must be one of"):
            provider({"repo": "a/b", "scope": "banana"}, {})

    def test_requires_repo_or_search(self):
        provider = resolve_provider("github")
        with pytest.raises(ProviderError, match="'repo' or 'search' is required"):
            provider({}, {})

    def test_github_dedups_by_id_and_baselines_on_first_run(self):
        provider = resolve_provider("github")
        fake_issues = [
            {"id": 100, "number": 1, "title": "A", "html_url": "u1", "state": "open",
             "user": {"login": "alice"}, "created_at": "t", "body": "..."},
            {"id": 200, "number": 2, "title": "B", "html_url": "u2", "state": "closed",
             "user": {"login": "bob"}, "created_at": "t", "body": "..."},
        ]
        with patch("watchers.providers._http_get", return_value=json.dumps(fake_issues).encode()):
            new_items, wm = provider({"repo": "a/b", "scope": "issues"}, {})
        assert new_items == []
        assert sorted(wm["seen_ids"]) == ["100", "200"]

        # Next poll with a new issue on top.
        fake_issues.insert(0, {
            "id": 300, "number": 3, "title": "C", "html_url": "u3", "state": "open",
            "user": {"login": "carol"}, "created_at": "t", "body": "fresh"
        })
        with patch("watchers.providers._http_get", return_value=json.dumps(fake_issues).encode()):
            new_items, _ = provider({"repo": "a/b", "scope": "issues"}, wm)
        assert len(new_items) == 1
        assert new_items[0]["id"] == "300"
        assert new_items[0]["author"] == "carol"
