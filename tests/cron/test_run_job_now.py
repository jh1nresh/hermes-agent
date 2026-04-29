"""Tests for on-demand cron execution — cron.scheduler.run_job_now and the
tool/API ``action='run'`` branching between inline execution (no gateway)
and defer-to-ticker (gateway running).

Regression target: issue #16612 — ``cronjob run`` previously only called
``trigger_job()``, which just sets ``next_run_at=now``.  When no gateway
ticker was running, the job never actually executed and the user saw
``state: scheduled`` with ``last_run_at: null`` forever.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# run_job_now — inline execution unit tests
# ---------------------------------------------------------------------------


class TestRunJobNow:
    """cron.scheduler.run_job_now executes a single job inline."""

    def _make_job(self, job_id="inline-job", **overrides):
        job = {
            "id": job_id,
            "name": "inline test",
            "enabled": True,
            "schedule": {"kind": "interval", "value": "every 1h"},
            "state": "scheduled",
            "prompt": "do a thing",
        }
        job.update(overrides)
        return job

    def test_returns_none_for_missing_job(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        # Reload scheduler so _LOCK_DIR picks up the temp HERMES_HOME.
        import importlib
        import cron.scheduler
        importlib.reload(cron.scheduler)
        from cron.scheduler import run_job_now

        with patch("cron.jobs.get_job", return_value=None):
            assert run_job_now("does-not-exist") is None

    def test_executes_inline_and_returns_updated_job(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        import importlib
        import cron.scheduler
        importlib.reload(cron.scheduler)
        from cron.scheduler import run_job_now

        job = self._make_job()
        updated_after_run = {**job, "last_run_at": "2026-04-28T00:00:00", "last_status": "ok"}

        # cron.scheduler.run_job_now imports get_job lazily from cron.jobs,
        # so patch at the source (cron.jobs.get_job).  Three calls:
        # (1) existence check, (2) re-load after advance, (3) final return.
        with patch("cron.jobs.get_job", side_effect=[job, job, updated_after_run]), \
             patch("cron.scheduler.advance_next_run", return_value=True) as adv, \
             patch("cron.scheduler.run_job", return_value=(True, "# output", "final reply", None)), \
             patch("cron.scheduler.save_job_output", return_value="/tmp/out.md"), \
             patch("cron.scheduler._deliver_result", return_value=None), \
             patch("cron.scheduler.mark_job_run") as mark:

            result = run_job_now("inline-job")

        # advance_next_run is called BEFORE the agent run to preserve
        # at-most-once semantics (matches tick() behaviour).
        adv.assert_called_once_with("inline-job")
        mark.assert_called_once()
        # mark_job_run is called with (job_id, success=True, error=None, delivery_error=None)
        args, kwargs = mark.call_args
        assert args[0] == "inline-job"
        assert args[1] is True
        assert result == updated_after_run

    def test_inline_records_failure_when_agent_errors(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        import importlib
        import cron.scheduler
        importlib.reload(cron.scheduler)
        from cron.scheduler import run_job_now

        job = self._make_job()
        updated = {**job, "last_run_at": "2026-04-28T00:00:00", "last_status": "error", "last_error": "boom"}

        with patch("cron.jobs.get_job", side_effect=[job, job, updated]), \
             patch("cron.scheduler.advance_next_run", return_value=True), \
             patch("cron.scheduler.run_job", return_value=(False, "# output", "", "boom")), \
             patch("cron.scheduler.save_job_output", return_value="/tmp/out.md"), \
             patch("cron.scheduler._deliver_result", return_value=None), \
             patch("cron.scheduler.mark_job_run") as mark:

            result = run_job_now("inline-job")

        args, kwargs = mark.call_args
        assert args[1] is False  # success=False
        assert args[2] == "boom"  # error propagated
        assert result["last_status"] == "error"


# ---------------------------------------------------------------------------
# cronjob(action="run") tool — gateway-aware branching
# ---------------------------------------------------------------------------


class TestCronjobRunTool:
    """tools.cronjob_tools._handle_run_action branches on gateway presence."""

    def _fake_job(self, job_id="tool-job", **overrides):
        job = {
            "id": job_id,
            "name": "tool test",
            "enabled": True,
            "schedule": {"kind": "interval", "value": "every 1h"},
            "state": "scheduled",
            "prompt": "do a thing",
            "deliver": "local",
        }
        job.update(overrides)
        return job

    def test_gateway_running_defers_to_tick(self, tmp_path, monkeypatch):
        """When gateway PIDs exist, action='run' only calls trigger_job."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from tools.cronjob_tools import cronjob

        job = self._fake_job()
        deferred = {**job, "next_run_at": "2026-04-28T00:00:00"}

        with patch("tools.cronjob_tools.get_job", return_value=job), \
             patch("tools.cronjob_tools.trigger_job", return_value=deferred) as trig, \
             patch("tools.cronjob_tools._gateway_ticker_running", return_value=True):
            out = cronjob(action="run", job_id="tool-job")

        result = json.loads(out)
        assert result["success"] is True
        trig.assert_called_once_with("tool-job")
        assert "next tick" in result["message"].lower() or "within" in result["message"].lower()
        # The deferred path must NOT claim the job already executed.
        assert result["job"].get("last_run_at") in (None, "")

    def test_gateway_not_running_executes_inline(self, tmp_path, monkeypatch):
        """When no gateway PIDs exist, action='run' invokes run_job_now
        and the returned job carries last_run_at + last_status=ok."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from tools.cronjob_tools import cronjob

        job = self._fake_job()
        executed = {
            **job,
            "last_run_at": "2026-04-28T00:00:00",
            "last_status": "ok",
        }

        with patch("tools.cronjob_tools.get_job", return_value=job), \
             patch("tools.cronjob_tools._gateway_ticker_running", return_value=False), \
             patch("cron.scheduler.run_job_now", return_value=executed) as run_now, \
             patch("tools.cronjob_tools.trigger_job") as trig:
            out = cronjob(action="run", job_id="tool-job")

        result = json.loads(out)
        assert result["success"] is True
        run_now.assert_called_once_with("tool-job")
        trig.assert_not_called()  # defer path must not be taken
        assert result["job"]["last_run_at"] == "2026-04-28T00:00:00"
        assert result["job"]["last_status"] == "ok"
        assert "inline" in result["message"].lower()

    def test_gateway_not_running_falls_back_to_defer_on_lock_contention(self, tmp_path, monkeypatch):
        """If run_job_now returns None (tick lock held), fall back to defer
        so the caller isn't left with a silent no-op."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from tools.cronjob_tools import cronjob

        job = self._fake_job()
        deferred = {**job, "next_run_at": "2026-04-28T00:00:00"}

        with patch("tools.cronjob_tools.get_job", return_value=job), \
             patch("tools.cronjob_tools._gateway_ticker_running", return_value=False), \
             patch("cron.scheduler.run_job_now", return_value=None), \
             patch("tools.cronjob_tools.trigger_job", return_value=deferred) as trig:
            out = cronjob(action="run", job_id="tool-job")

        result = json.loads(out)
        assert result["success"] is True
        trig.assert_called_once_with("tool-job")
        assert "another cron tick" in result["message"].lower() or "tick" in result["message"].lower()

    def test_unknown_job_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from tools.cronjob_tools import cronjob

        # get_job at the TOP of cronjob() is what short-circuits missing jobs.
        with patch("tools.cronjob_tools.get_job", return_value=None):
            out = cronjob(action="run", job_id="nope")

        result = json.loads(out)
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_inline_execution_surfaces_failure(self, tmp_path, monkeypatch):
        """Failed inline exec — the message must indicate the failure
        instead of claiming a clean run."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from tools.cronjob_tools import cronjob

        job = self._fake_job()
        executed = {
            **job,
            "last_run_at": "2026-04-28T00:00:00",
            "last_status": "error",
            "last_error": "model timed out",
        }

        with patch("tools.cronjob_tools.get_job", return_value=job), \
             patch("tools.cronjob_tools._gateway_ticker_running", return_value=False), \
             patch("cron.scheduler.run_job_now", return_value=executed):
            out = cronjob(action="run", job_id="tool-job")

        result = json.loads(out)
        assert result["success"] is True  # the tool call itself succeeded
        assert result["job"]["last_status"] == "error"
        assert "model timed out" in result["message"]


# ---------------------------------------------------------------------------
# _execute_and_record — shared helper covering both tick and run_job_now
# ---------------------------------------------------------------------------


class TestExecuteAndRecord:
    """_execute_and_record is the shared end-to-end helper used by both
    the tick loop and run_job_now."""

    def _make_job(self):
        return {
            "id": "shared-helper-job",
            "name": "shared helper",
            "deliver": "local",
        }

    def test_success_path_delivers_and_marks_ok(self):
        from cron.scheduler import _execute_and_record

        with patch("cron.scheduler.run_job", return_value=(True, "# out", "final", None)), \
             patch("cron.scheduler.save_job_output", return_value="/tmp/out.md"), \
             patch("cron.scheduler._deliver_result", return_value=None) as deliver, \
             patch("cron.scheduler.mark_job_run") as mark:
            ok = _execute_and_record(self._make_job())

        assert ok is True
        deliver.assert_called_once()
        mark.assert_called_once()
        args, kwargs = mark.call_args
        assert args[1] is True
        # mark_job_run(job_id, success, error, delivery_error=...)
        assert kwargs.get("delivery_error") is None

    def test_empty_response_marked_as_failure(self):
        """Issue #8585 behaviour must be preserved after the refactor."""
        from cron.scheduler import _execute_and_record

        with patch("cron.scheduler.run_job", return_value=(True, "# out", "", None)), \
             patch("cron.scheduler.save_job_output", return_value="/tmp/out.md"), \
             patch("cron.scheduler._deliver_result"), \
             patch("cron.scheduler.mark_job_run") as mark:
            _execute_and_record(self._make_job())

        args, _ = mark.call_args
        assert args[1] is False
        assert "empty response" in args[2].lower()

    def test_exception_in_run_job_marks_error(self):
        from cron.scheduler import _execute_and_record

        with patch("cron.scheduler.run_job", side_effect=RuntimeError("boom")), \
             patch("cron.scheduler.mark_job_run") as mark:
            ok = _execute_and_record(self._make_job())

        assert ok is False
        mark.assert_called_once()
        args, _ = mark.call_args
        assert args[1] is False
        assert "boom" in args[2]
