"""Tests for watchers/store.py — subscription CRUD + watermark persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from watchers.store import (
    WatcherSubscription,
    delete_watcher,
    get_watcher,
    list_watchers,
    load_watermark,
    save_watcher,
    save_watermark,
)


@pytest.fixture
def watcher_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME so tests don't stomp user data."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    # Ensure any cached hermes_constants path is recomputed.
    import importlib

    import hermes_constants

    importlib.reload(hermes_constants)
    return home


class TestWatcherSubscription:
    def test_from_dict_fills_defaults_for_missing_keys(self):
        sub = WatcherSubscription.from_dict({"name": "x", "provider": "http_json"})
        assert sub.name == "x"
        assert sub.provider == "http_json"
        assert sub.interval_seconds == 300
        assert sub.deliver == "origin"
        assert sub.deliver_only is False
        assert sub.enabled is True
        assert sub.last_run_at is None

    def test_round_trip_preserves_fields(self, watcher_home):
        sub = WatcherSubscription(
            name="github-issues",
            provider="github",
            config={"repo": "foo/bar", "scope": "issues"},
            interval_seconds=120,
            prompt="New: {items_json}",
            skills=["a", "b"],
            deliver="telegram,discord",
            deliver_only=True,
        )
        save_watcher(sub)
        fetched = get_watcher("github-issues")
        assert fetched is not None
        assert fetched.name == "github-issues"
        assert fetched.provider == "github"
        assert fetched.config == {"repo": "foo/bar", "scope": "issues"}
        assert fetched.interval_seconds == 120
        assert fetched.skills == ["a", "b"]
        assert fetched.deliver == "telegram,discord"
        assert fetched.deliver_only is True

    def test_delete_clears_watermark(self, watcher_home):
        sub = WatcherSubscription(name="w", provider="http_json", config={"url": "x"})
        save_watcher(sub)
        save_watermark("w", {"seen_ids": ["1", "2"]})
        assert (watcher_home / "watchers" / "w.watermark.json").exists()

        assert delete_watcher("w") is True
        assert get_watcher("w") is None
        assert not (watcher_home / "watchers" / "w.watermark.json").exists()

    def test_delete_nonexistent_returns_false(self, watcher_home):
        assert delete_watcher("nope") is False

    def test_list_watchers_returns_all(self, watcher_home):
        for i in range(3):
            save_watcher(
                WatcherSubscription(name=f"w{i}", provider="http_json", config={"url": f"u{i}"})
            )
        names = sorted(w.name for w in list_watchers())
        assert names == ["w0", "w1", "w2"]

    def test_watermark_is_opaque_dict_preserved_verbatim(self, watcher_home):
        Path(watcher_home / "watchers").mkdir(parents=True, exist_ok=True)
        wm = {"seen_ids": ["a", "b"], "last_polled_at": 12345.678, "custom": {"nested": True}}
        save_watermark("test", wm)
        assert load_watermark("test") == wm

    def test_load_watermark_returns_empty_when_missing(self, watcher_home):
        assert load_watermark("nonexistent") == {}

    def test_subscriptions_file_is_valid_json(self, watcher_home):
        save_watcher(WatcherSubscription(name="a", provider="http_json", config={"url": "x"}))
        raw = (watcher_home / "watchers.json").read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert "a" in parsed
