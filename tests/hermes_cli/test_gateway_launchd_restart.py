"""Regression tests for macOS launchd gateway restart recovery.

These cover PR #40299: a successful gateway *self-restart* request used to
return immediately and report success, even though launchd might have the job
absent/unloaded — leaving the gateway (and Telegram polling) dead while the CLI
claimed the restart succeeded.
"""

import subprocess

from hermes_cli import gateway as gw


def _patch_launchd_basics(monkeypatch, tmp_path, *, pid=12345, exited=True):
    plist = tmp_path / "ai.hermes.gateway.plist"
    plist.write_text("<plist/>", encoding="utf-8")

    monkeypatch.setattr(gw, "get_launchd_label", lambda: "ai.hermes.gateway")
    monkeypatch.setattr(gw, "_launchd_domain", lambda: "gui/501")
    monkeypatch.setattr(gw, "get_launchd_plist_path", lambda: plist)
    monkeypatch.setattr(gw, "_get_restart_drain_timeout", lambda: 1.0)
    monkeypatch.setattr(gw, "_request_gateway_self_restart", lambda actual_pid: actual_pid == pid)
    monkeypatch.setattr(gw, "_wait_for_gateway_exit", lambda timeout, force_after: exited)

    import gateway.status as status

    monkeypatch.setattr(status, "get_running_pid", lambda: pid)


def test_launchd_restart_self_restart_waits_then_kickstarts(monkeypatch, tmp_path, capsys):
    """A successful self-restart request must still explicitly revive launchd.

    Regression for the Telegram outage: the update path requested a gateway
    self-restart, returned immediately, and Telegram never came back because
    nothing verified launchd actually had the job loaded/running.
    """
    _patch_launchd_basics(monkeypatch, tmp_path, exited=True)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(gw.subprocess, "run", fake_run)

    gw.launchd_restart()

    # On a clean drain we issue a plain kickstart to ensure the job is running,
    # NOT a force-kill kickstart (-k).
    assert ["launchctl", "kickstart", "gui/501/ai.hermes.gateway"] in calls
    assert ["launchctl", "kickstart", "-k", "gui/501/ai.hermes.gateway"] not in calls
    out = capsys.readouterr().out
    assert "Service restart requested" in out
    assert "Service restarted" in out


def test_launchd_restart_self_restart_bootstraps_if_job_unloaded(monkeypatch, tmp_path):
    """If kickstart reports the job is missing, bootstrap the plist and start."""
    _patch_launchd_basics(monkeypatch, tmp_path, exited=True)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if (
            cmd == ["launchctl", "kickstart", "gui/501/ai.hermes.gateway"]
            and calls.count(cmd) == 1
        ):
            # First (post-drain) kickstart: launchd has no such job loaded.
            raise subprocess.CalledProcessError(3, cmd, stderr="Could not find service")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(gw.subprocess, "run", fake_run)

    gw.launchd_restart()

    assert [
        "launchctl",
        "bootstrap",
        "gui/501",
        str(tmp_path / "ai.hermes.gateway.plist"),
    ] in calls
    assert ["launchctl", "kickstart", "gui/501/ai.hermes.gateway"] in calls


def test_launchd_restart_self_restart_force_kicks_if_drain_times_out(monkeypatch, tmp_path):
    """If the old gateway does not exit, force launchd to restart the job."""
    _patch_launchd_basics(monkeypatch, tmp_path, exited=False)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(gw.subprocess, "run", fake_run)

    gw.launchd_restart()

    assert ["launchctl", "kickstart", "-k", "gui/501/ai.hermes.gateway"] in calls
