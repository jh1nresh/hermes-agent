"""Regression tests for the post-update gateway respawn interpreter on Windows.

Background
----------
When the Desktop GUI runs ``hermes update``, it spawns the post-update
respawn watcher via
``hermes_cli.gateway.launch_detached_profile_gateway_restart``.  That watcher
polls the old gateway PID and, once it exits, respawns the gateway using the
argv built by ``_gateway_run_args_for_profile``.

The bug: that argv used ``get_python_path()`` — the *console* ``python.exe``.
For uv-created venvs, even ``venv\\Scripts\\pythonw.exe`` re-execs the base
interpreter as a console ``python.exe`` (the re-exec is a fresh CreateProcess
that does NOT inherit ``CREATE_NO_WINDOW``), so a blank console window pops up
after every GUI-driven update.  ``hermes gateway start`` avoided this by going
through ``_resolve_detached_python`` to get the *base* ``pythonw.exe`` plus a
``VIRTUAL_ENV`` / ``PYTHONPATH`` overlay; the post-update respawn path never
got the same treatment.

These tests lock in:
  * Windows respawn argv resolves to the base ``pythonw.exe`` (not the venv
    ``Scripts`` shim, not console ``python.exe``).
  * The respawn env carries the matching ``VIRTUAL_ENV`` / ``PYTHONPATH`` so a
    base-interpreter respawn can still import ``hermes_cli``.
  * POSIX behaviour is byte-for-byte unchanged (argv keeps ``sys.executable``'s
    resolved path; env overlay is a no-op).
"""

import sys
from pathlib import Path

import pytest

import hermes_cli.gateway as gateway
import hermes_cli.gateway_windows as gateway_windows


def _make_uv_venv(tmp_path: Path) -> dict[str, Path]:
    """Fabricate a uv-style venv layout: venv Scripts python(w).exe + a base
    interpreter referenced by pyvenv.cfg's ``home`` with its own pythonw.exe."""
    project = tmp_path / "project"
    scripts = project / "venv" / "Scripts"
    site_packages = project / "venv" / "Lib" / "site-packages"
    base = tmp_path / "uv" / "python" / "cpython-3.11-windows-x86_64-none"
    for directory in (scripts, site_packages, base):
        directory.mkdir(parents=True, exist_ok=True)

    venv_python = scripts / "python.exe"
    venv_pythonw = scripts / "pythonw.exe"
    base_pythonw = base / "pythonw.exe"
    for exe in (venv_python, venv_pythonw, base_pythonw):
        exe.write_text("", encoding="utf-8")
    (project / "venv" / "pyvenv.cfg").write_text(
        f"home = {base}\nimplementation = CPython\nuv = 0.11.14\nversion_info = 3.11.15\n",
        encoding="utf-8",
    )
    return {
        "project": project,
        "venv_python": venv_python,
        "venv_pythonw": venv_pythonw,
        "base_pythonw": base_pythonw,
        "site_packages": site_packages,
    }


class TestRespawnArgvUsesBasePythonw:
    def test_windows_uv_venv_resolves_base_pythonw(self, tmp_path, monkeypatch):
        """The respawn argv must use the base pythonw.exe for a uv venv,
        never the venv Scripts shim or console python.exe."""
        layout = _make_uv_venv(tmp_path)
        monkeypatch.setattr(gateway, "is_windows", lambda: True)
        monkeypatch.setattr(gateway, "get_python_path", lambda: str(layout["venv_python"]))

        argv = gateway._gateway_run_args_for_profile("default")

        assert argv[0] == str(layout["base_pythonw"])
        assert argv[0] != str(layout["venv_python"])
        assert argv[0] != str(layout["venv_pythonw"])
        assert argv[1:] == ["-m", "hermes_cli.main", "gateway", "run", "--replace"]

    def test_windows_non_default_profile_keeps_profile_arg(self, tmp_path, monkeypatch):
        layout = _make_uv_venv(tmp_path)
        monkeypatch.setattr(gateway, "is_windows", lambda: True)
        monkeypatch.setattr(gateway, "get_python_path", lambda: str(layout["venv_python"]))

        argv = gateway._gateway_run_args_for_profile("work")

        assert argv[0] == str(layout["base_pythonw"])
        assert argv[1:] == [
            "-m",
            "hermes_cli.main",
            "--profile",
            "work",
            "gateway",
            "run",
            "--replace",
        ]


class TestRespawnEnvOverlay:
    def test_windows_overlay_sets_virtualenv_and_pythonpath(self, tmp_path, monkeypatch):
        """A base-pythonw respawn needs VIRTUAL_ENV + PYTHONPATH so imports
        resolve without the venv launcher shim."""
        layout = _make_uv_venv(tmp_path)
        monkeypatch.setattr(gateway, "is_windows", lambda: True)
        monkeypatch.setattr(gateway, "get_python_path", lambda: str(layout["venv_python"]))

        env = gateway._gateway_respawn_env({})

        assert env["VIRTUAL_ENV"] == str(layout["project"] / "venv")
        assert env["HERMES_GATEWAY_DETACHED"] == "1"
        assert "PYTHONPATH" in env
        # Repo root and the base-interpreter site-packages must both be on it.
        assert str(gateway.PROJECT_ROOT) in env["PYTHONPATH"]
        assert str(layout["site_packages"]) in env["PYTHONPATH"]

    def test_windows_overlay_prepends_to_existing_pythonpath(self, tmp_path, monkeypatch):
        layout = _make_uv_venv(tmp_path)
        monkeypatch.setattr(gateway, "is_windows", lambda: True)
        monkeypatch.setattr(gateway, "get_python_path", lambda: str(layout["venv_python"]))
        monkeypatch.setenv("PYTHONPATH", "/preexisting/entry")

        env = gateway._gateway_respawn_env({})

        assert env["PYTHONPATH"].endswith("/preexisting/entry")
        assert str(gateway.PROJECT_ROOT) in env["PYTHONPATH"]


class TestPosixUnchanged:
    """POSIX must be byte-for-byte identical to the pre-fix behaviour."""

    def test_posix_argv_uses_get_python_path_verbatim(self, monkeypatch):
        monkeypatch.setattr(gateway, "is_windows", lambda: False)
        monkeypatch.setattr(gateway, "get_python_path", lambda: "/usr/bin/python3")

        argv = gateway._gateway_run_args_for_profile("default")

        assert argv == [
            "/usr/bin/python3",
            "-m",
            "hermes_cli.main",
            "gateway",
            "run",
            "--replace",
        ]

    def test_posix_env_overlay_is_noop(self, monkeypatch):
        monkeypatch.setattr(gateway, "is_windows", lambda: False)
        original = {"PATH": "/usr/bin", "FOO": "bar"}

        result = gateway._gateway_respawn_env(dict(original))

        assert result == original


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific regression")
class TestLiveWindowsRespawn:
    """On a real Windows host the resolver should pick a *windowed* interpreter
    (pythonw) for the running gateway's own venv, with no console flag needed."""

    def test_resolved_interpreter_is_windowless(self):
        argv = gateway._gateway_run_args_for_profile("default")
        assert argv[0].lower().endswith("pythonw.exe")
