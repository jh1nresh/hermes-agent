"""Hermes-managed ``uv`` resolution and bootstrap.

``hermes update`` and the other dependency-install paths need a *known-good*
``uv`` to drive ``uv pip install`` against the Hermes venv. The naive
``shutil.which("uv")`` is dangerous: on Windows it frequently returns an
Anaconda/conda-shipped ``uv`` whose own environment assumptions collide with
the Hermes venv we point it at via ``VIRTUAL_ENV``, and the install breaks. The
same class of failure shows up on POSIX when a stale ``pip install uv==0.7.20``
sits earlier on PATH (the installer already works around that — see
``ensure_fts5`` in ``scripts/install.sh``).

The durable fix is to stop trusting an arbitrary PATH ``uv`` and instead
*vendor* one under ``$HERMES_HOME/bin`` — the same convention already used for
tirith (``tools/tirith_security.py``) and bws (``agent/secret_sources/
bitwarden.py``). Resolution prefers trusted locations and only falls back to
PATH when nothing trusted exists:

    1. ``$HERMES_HOME/bin/uv[.exe]``        (our managed copy — preferred)
    2. ``PROJECT_ROOT/venv/{Scripts,bin}/uv[.exe]``  (the Hermes venv's own uv)
    3. ``~/.local/bin/uv[.exe]``            (uv's official installer target)
    4. ``~/.cargo/bin/uv[.exe]``            (cargo-installed uv)
    5. ``shutil.which("uv")``               (PATH fallback — last resort)

``ensure_uv()`` adds a final step: if nothing trusted resolves, it installs a
fresh standalone uv into ``$HERMES_HOME/bin`` via the official installer using
``UV_UNMANAGED_INSTALL`` (POSIX) / ``UV_INSTALL_DIR`` (Windows), so a poisoned
PATH can never again hijack ``hermes update``.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Project root: hermes_cli/ -> repo root. Mirrors hermes_cli/main.py's
# PROJECT_ROOT so a git/source install can find the venv's own uv.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _is_windows() -> bool:
    return os.name == "nt"


def _uv_exe_name() -> str:
    return "uv.exe" if _is_windows() else "uv"


def hermes_bin_dir() -> Path:
    """``$HERMES_HOME/bin`` — where Hermes stores its managed binaries.

    Profile-aware via ``get_hermes_home()``. Created on demand by
    :func:`ensure_uv`; this accessor never has the side effect of mkdir.
    """
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home()) / "bin"


def _is_usable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def _candidate_paths() -> list[Path]:
    """Trusted ``uv`` locations, in preference order (managed → PATH)."""
    exe = _uv_exe_name()
    bindir = "Scripts" if _is_windows() else "bin"

    candidates: list[Path] = [
        hermes_bin_dir() / exe,
        PROJECT_ROOT / "venv" / bindir / exe,
    ]

    home = Path(os.path.expanduser("~"))
    candidates += [
        home / ".local" / "bin" / exe,
        home / ".cargo" / "bin" / exe,
    ]
    return candidates


def resolve_uv() -> Optional[str]:
    """Return a path to a known-good ``uv``, or ``None`` if none is found.

    Probes the trusted locations first (managed copy, venv, official installer
    dirs) and only then falls back to ``shutil.which("uv")``. Pure lookup — no
    install, no network, no side effects. Safe to call from hot paths.
    """
    for cand in _candidate_paths():
        if _is_usable(cand):
            return str(cand)
    return shutil.which("uv")


def _install_standalone_uv(dest: Path) -> Optional[str]:
    """Install a fresh standalone uv into ``dest`` via the official installer.

    Uses ``UV_UNMANAGED_INSTALL`` (POSIX) / ``UV_INSTALL_DIR`` (Windows) so the
    binary lands exactly in ``dest`` and nowhere else — no PATH edits, no shell
    profile changes, no interference with an existing system uv. Returns the
    path to the installed binary, or ``None`` on failure.
    """
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / _uv_exe_name()

    try:
        if _is_windows():
            # PowerShell installer honours $env:UV_INSTALL_DIR for the target
            # dir and $env:UV_UNMANAGED_INSTALL to skip PATH/registry edits.
            env = {
                **os.environ,
                "UV_INSTALL_DIR": str(dest),
                "UV_UNMANAGED_INSTALL": str(dest),
            }
            ps_cmd = (
                "$ErrorActionPreference='Stop'; "
                "irm https://astral.sh/uv/install.ps1 | iex"
            )
            subprocess.run(
                ["powershell", "-ExecutionPolicy", "ByPass", "-NoProfile", "-Command", ps_cmd],
                env=env,
                check=True,
                capture_output=True,
                timeout=180,
            )
        else:
            # Shell installer: pipe through `sh` with UV_*_INSTALL pointing at
            # dest. Matches scripts/install.sh ensure_fts5's fresh-uv bootstrap.
            installer = subprocess.run(
                ["curl", "-LsSf", "https://astral.sh/uv/install.sh"],
                check=True,
                capture_output=True,
                timeout=120,
            )
            env = {
                **os.environ,
                "UV_INSTALL_DIR": str(dest),
                "UV_UNMANAGED_INSTALL": str(dest),
            }
            subprocess.run(
                ["sh"],
                input=installer.stdout,
                env=env,
                check=True,
                capture_output=True,
                timeout=180,
            )
    except Exception as exc:  # noqa: BLE001 — never block update on this
        logger.warning("standalone uv bootstrap into %s failed: %s", dest, exc)
        return None

    if _is_usable(target):
        return str(target)
    logger.warning("standalone uv installer ran but %s is not usable", target)
    return None


def ensure_uv(*, install_if_missing: bool = True) -> Optional[str]:
    """Return a known-good ``uv``, installing a managed one if necessary.

    Resolution order is :func:`resolve_uv`. When that returns ``None`` and
    ``install_if_missing`` is True, bootstrap a standalone uv into
    ``$HERMES_HOME/bin`` and return it. Returns ``None`` only when no uv could
    be found and the bootstrap failed (or was disabled).
    """
    found = resolve_uv()
    if found:
        return found
    if not install_if_missing:
        return None
    return _install_standalone_uv(hermes_bin_dir())
