"""Tests for watchers/engine.py — due-check, prompt rendering, run_watcher outcomes.

These avoid touching the real AIAgent / cron delivery — we patch them so the
engine logic (dedup, baseline, error capture) is isolated.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from watchers.engine import _is_due, _render_prompt, run_watcher, tick
from watchers.providers import PROVIDERS, ProviderError
from watchers.store import WatcherSubscription, save_watcher


@pytest.fixture
def watcher_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    import importlib
    import hermes_constants

    importlib.reload(hermes_constants)
    return home


class TestIsDue:
    def test_new_watcher_is_always_due(self):
        sub = WatcherSubscription(name="n", provider="http_json", interval_seconds=60)
        assert _is_due(sub) is True

    def test_recent_run_is_not_due(self):
        sub = WatcherSubscription(
            name="n",
            provider="http_json",
            interval_seconds=60,
            last_run_at=time.time() - 10,
        )
        assert _is_due(sub) is False

    def test_old_run_is_due(self):
        sub = WatcherSubscription(
            name="n",
            provider="http_json",
            interval_seconds=60,
            last_run_at=time.time() - 120,
        )
        assert _is_due(sub) is True

    def test_disabled_never_due(self):
        sub = WatcherSubscription(
            name="n",
            provider="http_json",
            interval_seconds=60,
            last_run_at=None,
            enabled=False,
        )
        assert _is_due(sub) is False

    def test_interval_floor_prevents_flooding(self):
        """Interval below 5s is clamped to 5s so a bad config can't DDOS."""
        sub = WatcherSubscription(
            name="n",
            provider="http_json",
            interval_seconds=1,
            last_run_at=time.time() - 3,
        )
        assert _is_due(sub) is False


class TestRenderPrompt:
    def test_default_prompt_shows_count_and_items(self):
        sub = WatcherSubscription(name="feed", provider="rss")
        rendered = _render_prompt("", [{"id": "a"}, {"id": "b"}], sub)
        assert "feed" in rendered
        assert "2 new event" in rendered
        assert '"id": "a"' in rendered

    def test_placeholder_substitution(self):
        sub = WatcherSubscription(name="pr-watcher", provider="github")
        template = "Watcher {name} saw {count} new items:\n{items_json}"
        rendered = _render_prompt(template, [{"x": 1}], sub)
        assert rendered.startswith("Watcher pr-watcher saw 1 new items:")
        assert '"x": 1' in rendered

    def test_unknown_placeholders_pass_through(self):
        """Users shouldn't be punished for typos with a KeyError."""
        sub = WatcherSubscription(name="w", provider="http_json")
        rendered = _render_prompt("hi {unknown} there", [], sub)
        assert "{unknown}" in rendered


class TestRunWatcher:
    def test_unknown_provider_returns_error(self, watcher_home):
        sub = WatcherSubscription(name="bad", provider="does-not-exist")
        outcome = run_watcher(sub)
        assert outcome["status"] == "error"
        assert "Unknown watcher provider" in outcome["error"]

    def test_provider_error_is_captured(self, watcher_home):
        def failing(_config, _watermark):
            raise ProviderError("backend down")

        PROVIDERS["unit-failing"] = failing
        try:
            sub = WatcherSubscription(name="w", provider="unit-failing", config={})
            outcome = run_watcher(sub)
            assert outcome["status"] == "error"
            assert "backend down" in outcome["error"]
        finally:
            PROVIDERS.pop("unit-failing", None)

    def test_empty_delta_produces_no_delivery(self, watcher_home):
        def noop(_c, _wm):
            return [], {"seen_ids": ["old"]}

        PROVIDERS["unit-noop"] = noop
        try:
            sub = WatcherSubscription(name="w", provider="unit-noop")
            with patch("watchers.engine._deliver_payload") as deliver_mock, \
                 patch("watchers.engine._run_agent_for_watcher") as agent_mock:
                outcome = run_watcher(sub)
                assert outcome["status"] == "ok"
                assert outcome["new_events"] == 0
                deliver_mock.assert_not_called()
                agent_mock.assert_not_called()
        finally:
            PROVIDERS.pop("unit-noop", None)

    def test_deliver_only_skips_agent(self, watcher_home):
        def new_item(_c, _wm):
            return [{"id": "1", "title": "new thing"}], {"seen_ids": ["1"]}

        PROVIDERS["unit-fresh"] = new_item
        try:
            sub = WatcherSubscription(
                name="w", provider="unit-fresh", deliver="local", deliver_only=True
            )
            with patch("watchers.engine._deliver_payload", return_value=None) as deliver_mock, \
                 patch("watchers.engine._run_agent_for_watcher") as agent_mock:
                outcome = run_watcher(sub)
                assert outcome["status"] == "ok"
                assert outcome["new_events"] == 1
                deliver_mock.assert_called_once()
                agent_mock.assert_not_called()
                # The prompt was passed verbatim as the delivery payload.
                delivered_content = deliver_mock.call_args[0][1]
                assert "new thing" in delivered_content
        finally:
            PROVIDERS.pop("unit-fresh", None)

    def test_agent_mode_invokes_agent_then_delivers_its_response(self, watcher_home):
        def new_item(_c, _wm):
            return [{"id": "1"}], {"seen_ids": ["1"]}

        PROVIDERS["unit-fresh2"] = new_item
        try:
            sub = WatcherSubscription(name="w", provider="unit-fresh2", deliver="local")
            with patch(
                "watchers.engine._run_agent_for_watcher", return_value="Agent's reply"
            ) as agent_mock, patch(
                "watchers.engine._deliver_payload", return_value=None
            ) as deliver_mock:
                outcome = run_watcher(sub)
            agent_mock.assert_called_once()
            deliver_mock.assert_called_once()
            assert deliver_mock.call_args[0][1] == "Agent's reply"
            assert outcome["status"] == "ok"
        finally:
            PROVIDERS.pop("unit-fresh2", None)


class TestTickLoop:
    def test_tick_skips_not_due_watchers(self, watcher_home):
        """Watchers whose interval hasn't elapsed are not polled."""
        sub = WatcherSubscription(
            name="fresh",
            provider="http_json",
            interval_seconds=3600,
            last_run_at=time.time() - 60,
        )
        save_watcher(sub)
        with patch("watchers.engine.run_watcher") as run_mock:
            outcomes = tick()
        run_mock.assert_not_called()
        assert outcomes == []

    def test_tick_runs_due_watcher_and_persists_last_run_at(self, watcher_home):
        sub = WatcherSubscription(
            name="stale",
            provider="http_json",
            interval_seconds=60,
            last_run_at=time.time() - 300,
        )
        save_watcher(sub)

        with patch(
            "watchers.engine.run_watcher",
            return_value={"name": "stale", "status": "ok", "new_events": 0, "error": None},
        ):
            outcomes = tick()

        from watchers.store import get_watcher

        refreshed = get_watcher("stale")
        assert refreshed is not None
        assert refreshed.last_run_at is not None
        assert refreshed.last_run_at > time.time() - 5  # just set
        assert outcomes[0]["name"] == "stale"

    def test_tick_captures_per_watcher_errors_without_crashing(self, watcher_home):
        """A crashing watcher doesn't kill the whole tick."""
        for i in range(3):
            save_watcher(WatcherSubscription(name=f"w{i}", provider="http_json"))

        def fake_run(sub, *, now=None, adapters=None, loop=None):
            if sub.name == "w1":
                return {"name": sub.name, "status": "error", "new_events": 0,
                        "error": "boom"}
            return {"name": sub.name, "status": "ok", "new_events": 0, "error": None}

        with patch("watchers.engine.run_watcher", side_effect=fake_run):
            outcomes = tick()

        by_name = {o["name"]: o for o in outcomes}
        assert by_name["w0"]["status"] == "ok"
        assert by_name["w1"]["status"] == "error"
        assert by_name["w2"]["status"] == "ok"
