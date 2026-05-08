"""Windows UTF-8 bootstrap for Hermes entrypoints.

On older Windows builds, Python may start with a locale codec such as cp1252.
That makes text-mode ``open()`` without ``encoding=`` and stdio defaults prone
to Unicode decode/encode failures. Hermes touches many files in long-running
processes, so we force UTF-8 mode at process start for CLI entrypoints.
"""

from __future__ import annotations

import os
import sys

_UTF8_REEXEC_GUARD = "_HERMES_UTF8_REEXEC"


def ensure_windows_utf8_mode(
    *,
    reexec: bool = True,
    module: str | None = None,
    entrypoint_markers: tuple[str, ...] | None = None,
) -> bool:
    """Ensure UTF-8 defaults on Windows.

    Behavior:
    - Always sets ``PYTHONUTF8=1`` and ``PYTHONIOENCODING=utf-8`` on Windows.
    - If Python is already in UTF-8 mode, returns immediately.
    - Otherwise re-execs the current interpreter with ``-X utf8`` (once),
      unless marker-gated or explicitly disabled via ``reexec=False``.
    - When ``module=...`` is supplied, re-execs as ``python -m <module>`` and
      forwards original user args (excluding argv0), which avoids Windows
      console-script ``.exe`` wrappers being treated as Python scripts.

    Returns ``True`` only when a re-exec is attempted and the exec call
    unexpectedly returns (e.g. under a patched test double). In normal
    operation ``os.execvpe`` never returns on success.
    """
    if sys.platform != "win32":
        return False

    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    if getattr(sys.flags, "utf8_mode", 0) == 1:
        return False
    if not reexec:
        return False
    if os.environ.get(_UTF8_REEXEC_GUARD) == "1":
        return False

    if entrypoint_markers:
        argv0 = ""
        if getattr(sys, "argv", None):
            argv0 = os.path.basename(str(sys.argv[0])).lower()
        markers = tuple(marker.lower() for marker in entrypoint_markers if marker)
        if markers and not any(marker in argv0 for marker in markers):
            return False

    executable = getattr(sys, "executable", None)
    argv = list(getattr(sys, "argv", []))
    if not executable:
        return False

    child_env = dict(os.environ)
    child_env[_UTF8_REEXEC_GUARD] = "1"
    child_argv = [executable, "-X", "utf8"]
    if module:
        child_argv.extend(["-m", module])
        if len(argv) > 1:
            child_argv.extend(argv[1:])
    else:
        child_argv.extend(argv)

    try:
        os.execvpe(executable, child_argv, child_env)
    except OSError:
        # Best-effort fallback: env vars remain set for child processes.
        return False

    # ``exec`` should not return on success.
    return True
