"""Tests for container-aware CLI routing (NixOS container mode).

When container.enable = true in the NixOS module, the activation script
writes a .container-mode metadata file. The host CLI detects this and
execs into the container instead of running locally.
"""
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hermes_cli.config import (
    _is_inside_container,
    get_container_exec_info,
)


# =============================================================================
# _is_inside_container
# =============================================================================


def test_is_inside_container_dockerenv(tmp_path):
    """Detects /.dockerenv marker file."""
    with patch("os.path.exists") as mock_exists:
        mock_exists.side_effect = lambda p: p == "/.dockerenv"
        assert _is_inside_container() is True


def test_is_inside_container_containerenv(tmp_path):
    """Detects Podman's /run/.containerenv marker."""
    with patch("os.path.exists") as mock_exists:
        mock_exists.side_effect = lambda p: p == "/run/.containerenv"
        assert _is_inside_container() is True


def test_is_inside_container_cgroup_docker():
    """Detects 'docker' in /proc/1/cgroup."""
    with patch("os.path.exists", return_value=False), \
         patch("builtins.open", create=True) as mock_open:
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        mock_open.return_value.read = MagicMock(
            return_value="12:memory:/docker/abc123\n"
        )
        assert _is_inside_container() is True


def test_is_inside_container_false_on_host():
    """Returns False when none of the container indicators are present."""
    with patch("os.path.exists", return_value=False), \
         patch("builtins.open", side_effect=OSError("no such file")):
        assert _is_inside_container() is False


# =============================================================================
# get_container_exec_info
# =============================================================================


@pytest.fixture
def container_env(tmp_path, monkeypatch):
    """Set up a fake HERMES_HOME with .container-mode file."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    container_mode = hermes_home / ".container-mode"
    container_mode.write_text(
        "# Written by NixOS activation script. Do not edit manually.\n"
        "backend=podman\n"
        "container_name=hermes-agent\n"
        "hermes_bin=/data/current-package/bin/hermes\n"
    )
    return hermes_home


def test_get_container_exec_info_returns_metadata(container_env):
    """Reads .container-mode and returns backend/name/bin."""
    with patch("hermes_cli.config._is_inside_container", return_value=False):
        info = get_container_exec_info()

    assert info is not None
    assert info["backend"] == "podman"
    assert info["container_name"] == "hermes-agent"
    assert info["hermes_bin"] == "/data/current-package/bin/hermes"


def test_get_container_exec_info_none_inside_container(container_env):
    """Returns None when we're already inside a container."""
    with patch("hermes_cli.config._is_inside_container", return_value=True):
        info = get_container_exec_info()

    assert info is None


def test_get_container_exec_info_none_without_file(tmp_path, monkeypatch):
    """Returns None when .container-mode doesn't exist (native mode)."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    with patch("hermes_cli.config._is_inside_container", return_value=False):
        info = get_container_exec_info()

    assert info is None


def test_get_container_exec_info_defaults():
    """Falls back to defaults for missing keys."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        hermes_home = Path(tmpdir) / ".hermes"
        hermes_home.mkdir()
        (hermes_home / ".container-mode").write_text(
            "# minimal file with no keys\n"
        )

        with patch("hermes_cli.config._is_inside_container", return_value=False), \
             patch("hermes_cli.config.get_hermes_home", return_value=hermes_home):
            info = get_container_exec_info()

        assert info is not None
        assert info["backend"] == "docker"
        assert info["container_name"] == "hermes-agent"
        assert info["hermes_bin"] == "/data/current-package/bin/hermes"


def test_get_container_exec_info_docker_backend(container_env):
    """Correctly reads docker backend."""
    (container_env / ".container-mode").write_text(
        "backend=docker\n"
        "container_name=hermes-custom\n"
        "hermes_bin=/opt/hermes/bin/hermes\n"
    )

    with patch("hermes_cli.config._is_inside_container", return_value=False):
        info = get_container_exec_info()

    assert info["backend"] == "docker"
    assert info["container_name"] == "hermes-custom"
    assert info["hermes_bin"] == "/opt/hermes/bin/hermes"


# =============================================================================
# _exec_in_container
# =============================================================================


def test_exec_in_container_calls_execvp():
    """Verifies os.execvp is called with the correct command."""
    from hermes_cli.main import _exec_in_container

    container_info = {
        "backend": "podman",
        "container_name": "hermes-agent",
        "hermes_bin": "/data/current-package/bin/hermes",
    }

    with patch("shutil.which", return_value="/usr/bin/podman"), \
         patch("subprocess.run") as mock_run, \
         patch("os.execvp") as mock_exec:
        # Simulate running container
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "true\n"
        mock_run.return_value = mock_result

        _exec_in_container(container_info, ["chat", "-m", "claude-sonnet-4"])

        mock_exec.assert_called_once_with(
            "/usr/bin/podman",
            ["/usr/bin/podman", "exec", "-it", "hermes-agent",
             "/data/current-package/bin/hermes", "chat", "-m", "claude-sonnet-4"]
        )


def test_exec_in_container_strips_host_flag():
    """The --host flag is not forwarded into the container."""
    from hermes_cli.main import _exec_in_container

    container_info = {
        "backend": "podman",
        "container_name": "hermes-agent",
        "hermes_bin": "/data/current-package/bin/hermes",
    }

    with patch("shutil.which", return_value="/usr/bin/podman"), \
         patch("subprocess.run") as mock_run, \
         patch("os.execvp") as mock_exec:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "true\n"
        mock_run.return_value = mock_result

        _exec_in_container(container_info, ["chat", "--host", "-q", "hello"])

        # --host should be stripped
        exec_args = mock_exec.call_args[0][1]
        assert "--host" not in exec_args
        assert "-q" in exec_args
        assert "hello" in exec_args


def test_exec_in_container_fallback_no_runtime(capsys):
    """Falls back gracefully when container runtime is not found."""
    from hermes_cli.main import _exec_in_container

    container_info = {
        "backend": "podman",
        "container_name": "hermes-agent",
        "hermes_bin": "/data/current-package/bin/hermes",
    }

    with patch("shutil.which", return_value=None), \
         patch("os.execvp") as mock_exec:
        _exec_in_container(container_info, ["chat"])

        # Should NOT call execvp — graceful fallback
        mock_exec.assert_not_called()

    captured = capsys.readouterr()
    assert "not found on PATH" in captured.err


def test_exec_in_container_fallback_container_not_running(capsys):
    """Falls back when container exists but is not running."""
    from hermes_cli.main import _exec_in_container

    container_info = {
        "backend": "docker",
        "container_name": "hermes-agent",
        "hermes_bin": "/data/current-package/bin/hermes",
    }

    with patch("shutil.which", return_value="/usr/bin/docker"), \
         patch("subprocess.run") as mock_run, \
         patch("os.execvp") as mock_exec:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "false\n"
        mock_run.return_value = mock_result

        _exec_in_container(container_info, ["chat"])

        mock_exec.assert_not_called()

    captured = capsys.readouterr()
    assert "not running" in captured.err


def test_exec_in_container_fallback_inspect_fails():
    """Falls back when docker inspect fails entirely."""
    from hermes_cli.main import _exec_in_container

    container_info = {
        "backend": "docker",
        "container_name": "hermes-agent",
        "hermes_bin": "/data/current-package/bin/hermes",
    }

    with patch("shutil.which", return_value="/usr/bin/docker"), \
         patch("subprocess.run") as mock_run, \
         patch("os.execvp") as mock_exec:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_run.return_value = mock_result

        _exec_in_container(container_info, ["chat"])

        mock_exec.assert_not_called()
