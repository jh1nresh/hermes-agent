"""Tests for the post-pull subprocess hand-off in ``hermes update``.

``hermes update`` runs from the *old* install. Before this hand-off, the
post-pull steps (dep install, config migration, gateway restart) executed
stale in-memory code even though the new source was already on disk, so a bug
fixed in the pulled version still crashed the first run — users had to run
``hermes update`` a second time. After a successful pull + dep install we now
finish in a fresh subprocess running the refreshed code and forward its exit
code. Unlike an ``os.exec*`` replacement, a child subprocess keeps the parent
PID intact, so this works on Windows too.

These tests cover the gate (when we hand off vs. finish in-process), the
subprocess mechanics, and the finalize pass the child process takes.
"""

import sys
from types import SimpleNamespace

import pytest

from hermes_cli import config as hermes_config
from hermes_cli import main as hermes_main


# ---------------------------------------------------------------------------
# Managed-uv compatibility: make managed_uv helpers follow shutil.which mocking
# (mirrors the autouse fixture in test_update_autostash.py).
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _patch_managed_uv():
    import shutil
    from unittest.mock import patch

    with patch("hermes_cli.managed_uv.resolve_uv", side_effect=lambda: shutil.which("uv")), \
         patch("hermes_cli.managed_uv.ensure_uv", side_effect=lambda: shutil.which("uv")), \
         patch("hermes_cli.managed_uv.update_managed_uv", side_effect=lambda: None):
        yield


def _clear_handoff_env(monkeypatch):
    monkeypatch.delenv("HERMES_UPDATE_FINALIZE", raising=False)
    monkeypatch.delenv("HERMES_UPDATE_NO_HANDOFF", raising=False)


# ---------------------------------------------------------------------------
# _should_handoff_after_pull — the gate
# ---------------------------------------------------------------------------
def test_should_handoff_false_in_finalize_mode(monkeypatch):
    _clear_handoff_env(monkeypatch)
    assert hermes_main._should_handoff_after_pull(finalize_only=True) is False


def test_should_handoff_false_when_finalize_env_set(monkeypatch):
    _clear_handoff_env(monkeypatch)
    monkeypatch.setenv("HERMES_UPDATE_FINALIZE", "1")
    assert hermes_main._should_handoff_after_pull(finalize_only=False) is False


def test_should_handoff_false_with_opt_out_env(monkeypatch):
    _clear_handoff_env(monkeypatch)
    monkeypatch.delitem(sys.modules, "pytest", raising=False)
    monkeypatch.setenv("HERMES_UPDATE_NO_HANDOFF", "1")
    assert hermes_main._should_handoff_after_pull(finalize_only=False) is False


def test_should_handoff_false_under_pytest(monkeypatch):
    # Safety invariant: never spawn a real recursive update while the test
    # suite is running. ``pytest`` is in sys.modules during the suite, so the
    # gate must stay closed even with a clean env.
    _clear_handoff_env(monkeypatch)
    assert "pytest" in sys.modules
    assert hermes_main._should_handoff_after_pull(finalize_only=False) is False


def test_should_handoff_true_when_allowed(monkeypatch):
    _clear_handoff_env(monkeypatch)
    monkeypatch.delitem(sys.modules, "pytest", raising=False)
    assert hermes_main._should_handoff_after_pull(finalize_only=False) is True


def test_should_handoff_stays_on_for_windows(monkeypatch):
    # The whole reason we use a subprocess instead of os.exec*: it keeps the
    # parent PID intact, so the hand-off works on Windows too (the previous
    # exec-based attempt had to disable itself there).
    _clear_handoff_env(monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delitem(sys.modules, "pytest", raising=False)
    assert hermes_main._should_handoff_after_pull(finalize_only=False) is True


# ---------------------------------------------------------------------------
# _handoff_update_to_refreshed_code — the subprocess mechanics
# ---------------------------------------------------------------------------
def test_handoff_runs_subprocess_with_finalize_env(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["env"] = dict(kwargs.get("env") or {})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)
    monkeypatch.setattr(hermes_main.sys, "argv", ["hermes", "update", "--yes", "--gateway"])

    rc = hermes_main._handoff_update_to_refreshed_code()

    assert rc == 0
    assert captured["cmd"][0] == sys.executable
    assert captured["cmd"][1:3] == ["-m", "hermes_cli.main"]
    # The original CLI args (minus argv[0]) are carried through verbatim.
    assert captured["cmd"][3:] == ["update", "--yes", "--gateway"]
    # The loop-breaker guard the child reads.
    assert captured["env"]["HERMES_UPDATE_FINALIZE"] == "1"


def test_handoff_forwards_nonzero_exit_code(monkeypatch):
    monkeypatch.setattr(
        hermes_main.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=42)
    )
    monkeypatch.setattr(hermes_main.sys, "argv", ["hermes", "update"])
    assert hermes_main._handoff_update_to_refreshed_code() == 42


def test_handoff_returns_none_when_spawn_fails(monkeypatch):
    def boom(*_a, **_kw):
        raise OSError("could not spawn")

    monkeypatch.setattr(hermes_main.subprocess, "run", boom)
    monkeypatch.setattr(hermes_main.sys, "argv", ["hermes", "update"])
    # None signals the caller to finish in-process instead of bailing out.
    assert hermes_main._handoff_update_to_refreshed_code() is None


# ---------------------------------------------------------------------------
# cmd_update integration — original pass hands off, finalize pass finishes
# ---------------------------------------------------------------------------
def _setup_update_mocks(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(hermes_main, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(hermes_main, "_stash_local_changes_if_needed", lambda *a, **kw: None)
    monkeypatch.setattr(hermes_main, "_restore_stashed_changes", lambda *a, **kw: True)
    monkeypatch.setattr(hermes_config, "get_missing_env_vars", lambda required_only=True: [])
    monkeypatch.setattr(hermes_config, "get_missing_config_fields", lambda: [])
    monkeypatch.setattr(hermes_config, "check_config_version", lambda: (5, 5))
    monkeypatch.setattr(hermes_config, "migrate_config", lambda **kw: {"env_added": [], "config_added": []})
    monkeypatch.setattr(hermes_main, "_refresh_active_lazy_features", lambda: None)


def _fake_git_run(commit_count):
    recorded = []

    def side_effect(cmd, **kwargs):
        recorded.append(cmd)
        joined = " ".join(str(c) for c in cmd)
        if "fetch" in joined and "origin" in joined:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if "rev-parse" in joined and "--abbrev-ref" in joined:
            return SimpleNamespace(stdout="main\n", stderr="", returncode=0)
        if "rev-list" in joined:
            return SimpleNamespace(stdout=f"{commit_count}\n", stderr="", returncode=0)
        if "--ff-only" in joined:
            return SimpleNamespace(stdout="Already up to date.\n", stderr="", returncode=0)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return side_effect, recorded


def test_original_pass_hands_off_and_forwards_exit_code(monkeypatch, tmp_path):
    _clear_handoff_env(monkeypatch)
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(hermes_main, "_is_termux_env", lambda env=None: False)

    # Force the gate open (pytest normally closes it) and stub the child run.
    monkeypatch.setattr(hermes_main, "_should_handoff_after_pull", lambda finalize_only: True)
    monkeypatch.setattr(hermes_main, "_handoff_update_to_refreshed_code", lambda: 0)

    node_called = []
    monkeypatch.setattr(
        hermes_main, "_update_node_dependencies", lambda *a, **k: node_called.append(True)
    )

    side_effect, _recorded = _fake_git_run(commit_count="3")
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    with pytest.raises(SystemExit) as exc:
        hermes_main.cmd_update(SimpleNamespace())

    assert exc.value.code == 0
    # After a successful hand-off the parent must NOT run the remaining
    # post-pull steps — the child already did them on new code.
    assert node_called == [], "parent ran post-handoff steps after a successful hand-off"


def test_handoff_failure_falls_back_in_process(monkeypatch, tmp_path):
    _clear_handoff_env(monkeypatch)
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(hermes_main, "_is_termux_env", lambda env=None: False)

    monkeypatch.setattr(hermes_main, "_should_handoff_after_pull", lambda finalize_only: True)
    # Child couldn't be spawned -> None -> parent finishes in-process.
    monkeypatch.setattr(hermes_main, "_handoff_update_to_refreshed_code", lambda: None)

    node_called = []
    monkeypatch.setattr(
        hermes_main, "_update_node_dependencies", lambda *a, **k: node_called.append(True)
    )
    monkeypatch.setattr(hermes_main, "_build_web_ui", lambda *a, **k: None)

    side_effect, _recorded = _fake_git_run(commit_count="3")
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace())

    assert node_called == [True], "fallback should finish the post-pull steps in-process"


def test_finalize_pass_does_not_hand_off(monkeypatch, tmp_path):
    # In finalize mode the gate is closed, so we must never spawn another
    # update. Make the hand-off explode so the test fails loudly if reached.
    _clear_handoff_env(monkeypatch)
    monkeypatch.setenv("HERMES_UPDATE_FINALIZE", "1")
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(hermes_main, "_is_termux_env", lambda env=None: False)

    def no_handoff():  # pragma: no cover - asserts it's not called
        raise AssertionError("finalize pass must not hand off")

    monkeypatch.setattr(hermes_main, "_handoff_update_to_refreshed_code", no_handoff)

    side_effect, recorded = _fake_git_run(commit_count="0")
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace())

    # The whole point of the finalize pass: even with zero new commits (the
    # pull already happened in the original pass) it does NOT take the "Already
    # up to date" early return — it runs the post-pull dependency install.
    install_cmds = [c for c in recorded if "pip" in c and "install" in c]
    assert install_cmds, "finalize pass should run the dependency install, not early-return"


def test_finalize_pass_skips_pre_update_backup(monkeypatch, tmp_path):
    _clear_handoff_env(monkeypatch)
    monkeypatch.setenv("HERMES_UPDATE_FINALIZE", "1")
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(hermes_main, "_is_termux_env", lambda env=None: False)

    backup_calls = []
    monkeypatch.setattr(hermes_main, "_run_pre_update_backup", lambda args: backup_calls.append(args))

    side_effect, _recorded = _fake_git_run(commit_count="0")
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace())

    assert backup_calls == [], "finalize pass must not retake the pre-update backup"


def test_original_pass_still_runs_pre_update_backup(monkeypatch, tmp_path):
    # Sanity counter-check: a normal (non-finalize) run still takes the backup.
    # Under pytest the gate is closed, so the run finishes in-process exactly
    # as it always did.
    _clear_handoff_env(monkeypatch)
    _setup_update_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(hermes_main, "_is_termux_env", lambda env=None: False)

    backup_calls = []
    monkeypatch.setattr(hermes_main, "_run_pre_update_backup", lambda args: backup_calls.append(args))

    side_effect, _recorded = _fake_git_run(commit_count="3")
    monkeypatch.setattr(hermes_main.subprocess, "run", side_effect)

    hermes_main.cmd_update(SimpleNamespace())

    assert len(backup_calls) == 1
