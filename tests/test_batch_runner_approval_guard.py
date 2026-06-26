"""Regression test for #35164: batch_runner must not bypass the dangerous-command
approval guard.

batch_runner.py runs non-interactively and sets none of HERMES_INTERACTIVE /
HERMES_GATEWAY_SESSION / HERMES_EXEC_ASK, so check_all_command_guards() used to
fall through to its non-interactive auto-approve path and execute every flagged
dangerous command (arbitrary command execution from prompt-injected JSONL
datasets). The fix marks the batch process as a cron-style session
(HERMES_CRON_SESSION=1) so the cron deny-by-default policy applies.
"""

import os

import tools.approval as ap


def _clear_session_env(monkeypatch):
    for var in ("HERMES_INTERACTIVE", "HERMES_GATEWAY_SESSION", "HERMES_EXEC_ASK"):
        monkeypatch.delenv(var, raising=False)


class TestBatchRunnerApprovalGuard:
    def test_no_cron_env_auto_approves_dangerous(self, monkeypatch):
        """Without the cron session marker, the non-interactive path approves —
        this is the pre-fix bug state, kept as the contrast case."""
        _clear_session_env(monkeypatch)
        monkeypatch.delenv("HERMES_CRON_SESSION", raising=False)
        monkeypatch.setattr(ap, "_YOLO_MODE_FROZEN", False)
        monkeypatch.setattr(ap, "is_current_session_yolo_enabled", lambda: False)
        monkeypatch.setattr(ap, "_get_approval_mode", lambda: "manual")
        res = ap.check_all_command_guards("rm -rf /important/data", env_type="local")
        assert res["approved"] is True

    def test_cron_session_blocks_dangerous_under_deny(self, monkeypatch):
        """With HERMES_CRON_SESSION=1 (what batch_runner now sets) and the
        default cron deny mode, a flagged dangerous command is blocked."""
        _clear_session_env(monkeypatch)
        monkeypatch.setenv("HERMES_CRON_SESSION", "1")
        monkeypatch.setattr(ap, "_YOLO_MODE_FROZEN", False)
        monkeypatch.setattr(ap, "is_current_session_yolo_enabled", lambda: False)
        monkeypatch.setattr(ap, "_get_approval_mode", lambda: "manual")
        monkeypatch.setattr(ap, "_get_cron_approval_mode", lambda: "deny")
        res = ap.check_all_command_guards("rm -rf /important/data", env_type="local")
        assert res["approved"] is False
        assert "BLOCKED" in (res["message"] or "")

    def test_batch_runner_sets_cron_session_marker(self):
        """_process_single_prompt sets HERMES_CRON_SESSION before constructing
        the agent. Asserted at source level so the marker can't be silently
        dropped in a refactor."""
        import inspect
        import batch_runner

        src = inspect.getsource(batch_runner._process_single_prompt)
        assert 'os.environ.setdefault("HERMES_CRON_SESSION"' in src
