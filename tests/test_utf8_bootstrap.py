"""Unit tests for Windows UTF-8 process bootstrap."""

from __future__ import annotations

import os
from types import SimpleNamespace

import utf8_bootstrap as utf8_bootstrap


def _fake_sys(
    *,
    platform: str,
    utf8_mode: int,
    argv: list[str] | None = None,
    executable: str = r"C:\Python\python.exe",
) -> SimpleNamespace:
    return SimpleNamespace(
        platform=platform,
        flags=SimpleNamespace(utf8_mode=utf8_mode),
        argv=argv or ["hermes"],
        executable=executable,
    )


def test_non_windows_noop(monkeypatch) -> None:
    monkeypatch.setattr(
        utf8_bootstrap,
        "sys",
        _fake_sys(platform="darwin", utf8_mode=0),
    )
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)

    called = {"exec": False}

    def _fake_exec(*_args, **_kwargs):
        called["exec"] = True
        raise AssertionError("exec should not run on non-Windows")

    monkeypatch.setattr(utf8_bootstrap.os, "execvpe", _fake_exec)

    assert utf8_bootstrap.ensure_windows_utf8_mode() is False
    assert called["exec"] is False
    assert "PYTHONUTF8" not in os.environ
    assert "PYTHONIOENCODING" not in os.environ


def test_windows_utf8_already_enabled_sets_env_without_reexec(monkeypatch) -> None:
    monkeypatch.setattr(
        utf8_bootstrap,
        "sys",
        _fake_sys(platform="win32", utf8_mode=1),
    )
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)

    called = {"exec": False}

    def _fake_exec(*_args, **_kwargs):
        called["exec"] = True
        raise AssertionError("exec should not run when utf8_mode=1")

    monkeypatch.setattr(utf8_bootstrap.os, "execvpe", _fake_exec)

    assert utf8_bootstrap.ensure_windows_utf8_mode() is False
    assert called["exec"] is False
    assert os.environ["PYTHONUTF8"] == "1"
    assert os.environ["PYTHONIOENCODING"] == "utf-8"


def test_windows_reexec_attempt_uses_utf8_flag(monkeypatch) -> None:
    fake_sys = _fake_sys(platform="win32", utf8_mode=0, argv=["hermes", "--help"])
    monkeypatch.setattr(utf8_bootstrap, "sys", fake_sys)
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.delenv("_HERMES_UTF8_REEXEC", raising=False)

    captured: dict[str, object] = {}

    def _fake_exec(executable, argv, env):
        captured["executable"] = executable
        captured["argv"] = argv
        captured["env"] = env
        raise OSError("blocked by test")

    monkeypatch.setattr(utf8_bootstrap.os, "execvpe", _fake_exec)

    assert (
        utf8_bootstrap.ensure_windows_utf8_mode(entrypoint_markers=("hermes",))
        is False
    )
    assert captured["executable"] == fake_sys.executable
    assert captured["argv"] == [
        fake_sys.executable,
        "-X",
        "utf8",
        *fake_sys.argv,
    ]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["_HERMES_UTF8_REEXEC"] == "1"


def test_module_reexec_uses_dash_m_and_drops_argv0(monkeypatch) -> None:
    fake_sys = _fake_sys(
        platform="win32",
        utf8_mode=0,
        argv=[r"C:\Users\me\AppData\Local\Programs\Python\Scripts\hermes.exe", "chat", "--verbose"],
    )
    monkeypatch.setattr(utf8_bootstrap, "sys", fake_sys)
    monkeypatch.delenv("_HERMES_UTF8_REEXEC", raising=False)

    captured: dict[str, object] = {}

    def _fake_exec(executable, argv, env):
        captured["executable"] = executable
        captured["argv"] = argv
        captured["env"] = env
        raise OSError("blocked by test")

    monkeypatch.setattr(utf8_bootstrap.os, "execvpe", _fake_exec)

    assert (
        utf8_bootstrap.ensure_windows_utf8_mode(
            module="hermes_cli.main",
            entrypoint_markers=("hermes",),
        )
        is False
    )
    assert captured["executable"] == fake_sys.executable
    assert captured["argv"] == [
        fake_sys.executable,
        "-X",
        "utf8",
        "-m",
        "hermes_cli.main",
        "chat",
        "--verbose",
    ]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["_HERMES_UTF8_REEXEC"] == "1"


def test_marker_mismatch_skips_reexec(monkeypatch) -> None:
    fake_sys = _fake_sys(platform="win32", utf8_mode=0, argv=["pytest", "-k", "x"])
    monkeypatch.setattr(utf8_bootstrap, "sys", fake_sys)
    monkeypatch.delenv("_HERMES_UTF8_REEXEC", raising=False)

    called = {"exec": False}

    def _fake_exec(*_args, **_kwargs):
        called["exec"] = True
        raise AssertionError("exec should be skipped for non-matching marker")

    monkeypatch.setattr(utf8_bootstrap.os, "execvpe", _fake_exec)

    assert (
        utf8_bootstrap.ensure_windows_utf8_mode(entrypoint_markers=("hermes",))
        is False
    )
    assert called["exec"] is False


def test_reexec_guard_prevents_loops(monkeypatch) -> None:
    fake_sys = _fake_sys(platform="win32", utf8_mode=0, argv=["hermes"])
    monkeypatch.setattr(utf8_bootstrap, "sys", fake_sys)
    monkeypatch.setenv("_HERMES_UTF8_REEXEC", "1")

    called = {"exec": False}

    def _fake_exec(*_args, **_kwargs):
        called["exec"] = True
        raise AssertionError("exec should be skipped when guard is set")

    monkeypatch.setattr(utf8_bootstrap.os, "execvpe", _fake_exec)

    assert utf8_bootstrap.ensure_windows_utf8_mode() is False
    assert called["exec"] is False
