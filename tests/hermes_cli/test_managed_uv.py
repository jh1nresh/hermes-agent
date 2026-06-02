"""Tests for hermes_cli.managed_uv — trusted uv resolution + bootstrap.

The contract under test: ``resolve_uv()`` must prefer a Hermes-managed uv
(``$HERMES_HOME/bin``), then the venv's uv, then the official-installer dirs
(``~/.local/bin``, ``~/.cargo/bin``), and only fall back to ``shutil.which``
when nothing trusted exists. This is what stops a conda/Anaconda uv earlier on
PATH from hijacking ``hermes update`` on Windows.
"""

import os
import stat
from pathlib import Path

import pytest

import hermes_cli.managed_uv as mu


def _make_uv(path: Path, tag: str = "x") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho {tag}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated HOME + HERMES_HOME + project root, with a fresh empty PATH dir."""
    home = tmp_path / "home"
    hermes_home = tmp_path / "hermes_home"
    project = tmp_path / "project"
    pathdir = tmp_path / "pathbin"
    for p in (home, hermes_home, project, pathdir):
        p.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("PATH", str(pathdir))
    # Point the module's PROJECT_ROOT at our temp project so the venv probe is
    # deterministic regardless of where the test runs.
    monkeypatch.setattr(mu, "PROJECT_ROOT", project)
    # POSIX layout for the test (resolution order is identical cross-platform;
    # only the exe name / bin subdir differ, covered by _uv_exe_name()).
    monkeypatch.setattr(mu, "_is_windows", lambda: False)

    return {
        "home": home,
        "hermes_home": hermes_home,
        "project": project,
        "pathdir": pathdir,
    }


def test_resolve_none_when_no_uv_anywhere(env):
    assert mu.resolve_uv() is None


def test_resolve_falls_back_to_path(env):
    path_uv = _make_uv(env["pathdir"] / "uv", "PATH")
    assert mu.resolve_uv() == path_uv


def test_managed_beats_path(env):
    _make_uv(env["pathdir"] / "uv", "PATH")
    managed = _make_uv(env["hermes_home"] / "bin" / "uv", "MANAGED")
    assert mu.resolve_uv() == managed


def test_venv_beats_path(env):
    _make_uv(env["pathdir"] / "uv", "PATH")
    venv_uv = _make_uv(env["project"] / "venv" / "bin" / "uv", "VENV")
    assert mu.resolve_uv() == venv_uv


def test_managed_beats_venv(env):
    _make_uv(env["project"] / "venv" / "bin" / "uv", "VENV")
    managed = _make_uv(env["hermes_home"] / "bin" / "uv", "MANAGED")
    assert mu.resolve_uv() == managed


def test_local_bin_beats_path(env):
    _make_uv(env["pathdir"] / "uv", "PATH")
    local_uv = _make_uv(env["home"] / ".local" / "bin" / "uv", "LOCAL")
    assert mu.resolve_uv() == local_uv


def test_venv_beats_local_bin(env):
    _make_uv(env["home"] / ".local" / "bin" / "uv", "LOCAL")
    venv_uv = _make_uv(env["project"] / "venv" / "bin" / "uv", "VENV")
    assert mu.resolve_uv() == venv_uv


def test_hermes_bin_dir_is_under_hermes_home(env):
    assert mu.hermes_bin_dir() == env["hermes_home"] / "bin"


def test_ensure_uv_no_install_returns_resolved(env):
    path_uv = _make_uv(env["pathdir"] / "uv", "PATH")
    assert mu.ensure_uv(install_if_missing=False) == path_uv


def test_ensure_uv_no_install_returns_none_when_missing(env):
    assert mu.ensure_uv(install_if_missing=False) is None


def test_ensure_uv_installs_when_missing(env, monkeypatch):
    called = {}

    def fake_install(dest: Path):
        called["dest"] = dest
        return str(dest / "uv")

    monkeypatch.setattr(mu, "_install_standalone_uv", fake_install)
    result = mu.ensure_uv(install_if_missing=True)
    assert result == str(env["hermes_home"] / "bin" / "uv")
    assert called["dest"] == env["hermes_home"] / "bin"


def test_ensure_uv_skips_install_when_resolved(env, monkeypatch):
    _make_uv(env["hermes_home"] / "bin" / "uv", "MANAGED")
    monkeypatch.setattr(
        mu, "_install_standalone_uv", lambda dest: pytest.fail("should not install")
    )
    result = mu.ensure_uv(install_if_missing=True)
    assert result is not None and result.endswith("/bin/uv")


def test_non_executable_file_is_skipped(env):
    # A uv file that exists but isn't executable must not be selected.
    managed = env["hermes_home"] / "bin" / "uv"
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.write_text("not executable")
    managed.chmod(0o644)
    path_uv = _make_uv(env["pathdir"] / "uv", "PATH")
    assert mu.resolve_uv() == path_uv
