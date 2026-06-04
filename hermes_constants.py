"""Shared constants for Hermes Agent.

Import-safe module with no dependencies — can be imported from anywhere
without risk of circular imports.
"""

import os
import sys
import sysconfig
from contextvars import ContextVar, Token
from pathlib import Path


_profile_fallback_warned: bool = False
_UNSET = object()
_HERMES_HOME_OVERRIDE: ContextVar[str | object] = ContextVar(
    "_HERMES_HOME_OVERRIDE", default=_UNSET
)


def set_hermes_home_override(path: str | Path | None) -> Token:
    """Set a context-local Hermes home override and return its reset token.

    This is for in-process, per-task scoping.  It deliberately does not mutate
    ``os.environ`` because that is shared by every thread in the process.
    """
    value: str | object = _UNSET if path is None else str(path)
    return _HERMES_HOME_OVERRIDE.set(value)


def reset_hermes_home_override(token: Token) -> None:
    """Restore the previous context-local Hermes home override."""
    _HERMES_HOME_OVERRIDE.reset(token)


def get_hermes_home_override() -> str | None:
    """Return the active context-local Hermes home override, if any."""
    override = _HERMES_HOME_OVERRIDE.get()
    if override is _UNSET or not override:
        return None
    return str(override)


def _get_platform_default_hermes_home() -> Path:
    """Return the platform-native default Hermes home path."""
    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
        return base / "hermes"
    return Path.home() / ".hermes"


def get_hermes_home() -> Path:
    """Return the Hermes home directory (default: platform-native path).

    Reads HERMES_HOME env var, falls back to the platform-native default.
    This is the single source of truth — all other copies should import this.

    When ``HERMES_HOME`` is unset but an ``active_profile`` file indicates
    a non-default profile is active, logs a loud one-shot warning to
    ``errors.log`` so cross-profile data corruption is diagnosable instead
    of silent.  Behavior is unchanged otherwise — we still return
    the platform-native default — because raising here would brick 30+ module-level
    callers that import this at load time.  Subprocess spawners are
    expected to propagate ``HERMES_HOME`` explicitly (see the systemd
    template in ``hermes_cli/gateway.py`` and the kanban dispatcher in
    ``hermes_cli/kanban_db.py``).  See https://github.com/NousResearch/hermes-agent/issues/18594.
    """
    override = get_hermes_home_override()
    if override:
        return Path(override)

    val = os.environ.get("HERMES_HOME", "").strip()
    if val:
        return Path(val)

    # Guard: if a non-default profile is sticky-active, warn once that
    # the fallback to the default profile is almost certainly wrong.
    global _profile_fallback_warned
    if not _profile_fallback_warned:
        try:
            fallback_home = _get_platform_default_hermes_home()
            active_path = fallback_home / "active_profile"
            active = active_path.read_text().strip() if active_path.exists() else ""
        except (UnicodeDecodeError, OSError):
            active = ""
        if active and active != "default":
            _profile_fallback_warned = True
            # Write directly to stderr.  We intentionally do NOT route this
            # through ``logging`` because (a) this function is called at
            # module-import time from 30+ sites, often before logging is
            # configured, and (b) root-logger propagation would double-emit
            # on consoles where a StreamHandler is already attached.
            msg = (
                f"[HERMES_HOME fallback] HERMES_HOME is unset but active "
                f"profile is {active!r}. Falling back to {fallback_home}, which "
                f"is the DEFAULT profile — not {active!r}. Any data this "
                f"process writes will land in the wrong profile. The "
                f"subprocess spawner should pass HERMES_HOME explicitly "
                f"(see issue #18594)."
            )
            try:
                sys.stderr.write(msg + "\n")
                sys.stderr.flush()
            except Exception:
                pass

    return _get_platform_default_hermes_home()


def get_default_hermes_root() -> Path:
    """Return the root Hermes directory for profile-level operations.

    In standard deployments this is the platform-native Hermes home
    (``~/.hermes`` on POSIX, ``%LOCALAPPDATA%\\hermes`` on native Windows).

    In Docker or custom deployments where ``HERMES_HOME`` points outside
    ``~/.hermes`` (e.g. ``/opt/data``), returns ``HERMES_HOME`` directly
    — that IS the root.

    In profile mode where ``HERMES_HOME`` is ``<root>/profiles/<name>``,
    returns ``<root>`` so that ``profile list`` can see all profiles.
    Works both for standard (``~/.hermes/profiles/coder``) and Docker
    (``/opt/data/profiles/coder``) layouts.

    Import-safe — no dependencies beyond stdlib.
    """
    native_home = _get_platform_default_hermes_home()
    env_home = os.environ.get("HERMES_HOME", "")
    if not env_home:
        return native_home
    env_path = Path(env_home)
    try:
        env_path.resolve().relative_to(native_home.resolve())
        # HERMES_HOME is under ~/.hermes (normal or profile mode)
        return native_home
    except ValueError:
        pass

    # Docker / custom deployment.
    # Check if this is a profile path: <root>/profiles/<name>
    # If the immediate parent dir is named "profiles", the root is
    # the grandparent — this covers Docker profiles correctly.
    if env_path.parent.name == "profiles":
        return env_path.parent.parent

    # Not a profile path — HERMES_HOME itself is the root
    return env_path


def _get_packaged_data_dir(name: str) -> Path | None:
    """Return an installed data-files directory if one exists.

    Used to discover bundled skills/optional-skills when Hermes is installed
    from a wheel that emitted them via setuptools data_files.
    """
    candidates = []
    for scheme in ("data", "purelib", "platlib"):
        raw = sysconfig.get_path(scheme)
        if raw:
            candidates.append(Path(raw) / name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def get_optional_skills_dir(default: Path | None = None) -> Path:
    """Return the optional-skills directory, honoring package-manager wrappers.

    Packaged installs may ship ``optional-skills`` outside the Python package
    tree and expose it via ``HERMES_OPTIONAL_SKILLS``.
    """
    override = os.getenv("HERMES_OPTIONAL_SKILLS", "").strip()
    if override:
        return Path(override)
    packaged = _get_packaged_data_dir("optional-skills")
    if packaged is not None:
        return packaged
    if default is not None:
        return default
    return get_hermes_home() / "optional-skills"


def get_optional_mcps_dir(default: Path | None = None) -> Path:
    """Return the optional-mcps directory, honoring package-manager wrappers.

    Mirrors :func:`get_optional_skills_dir` for the MCP catalog (Nous-approved
    Model Context Protocol servers shipped with the repo but disabled by
    default). Packaged installs may ship ``optional-mcps`` outside the Python
    package tree and expose it via ``HERMES_OPTIONAL_MCPS``.
    """
    override = os.getenv("HERMES_OPTIONAL_MCPS", "").strip()
    if override:
        return Path(override)
    packaged = _get_packaged_data_dir("optional-mcps")
    if packaged is not None:
        return packaged
    if default is not None:
        return default
    return get_hermes_home() / "optional-mcps"


def get_bundled_skills_dir(default: Path | None = None) -> Path:
    """Return the bundled skills directory for source and packaged installs.

    Resolution order:
        1. ``HERMES_BUNDLED_SKILLS`` env var (Nix wrapper / explicit override)
        2. Wheel-installed ``<sysconfig data>/skills`` (pip install path)
        3. Caller-supplied ``default`` (typically the source-checkout path)
        4. ``<HERMES_HOME>/skills`` last-resort
    """
    override = os.getenv("HERMES_BUNDLED_SKILLS", "").strip()
    if override:
        return Path(override)
    packaged = _get_packaged_data_dir("skills")
    if packaged is not None:
        return packaged
    if default is not None:
        return default
    return get_hermes_home() / "skills"


def get_hermes_dir(new_subpath: str, old_name: str) -> Path:
    """Resolve a Hermes subdirectory with backward compatibility.

    New installs get the consolidated layout (e.g. ``cache/images``).
    Existing installs that already have the old path (e.g. ``image_cache``)
    keep using it — no migration required.

    Args:
        new_subpath: Preferred path relative to HERMES_HOME (e.g. ``"cache/images"``).
        old_name: Legacy path relative to HERMES_HOME (e.g. ``"image_cache"``).

    Returns:
        Absolute ``Path`` — old location if it exists on disk, otherwise the new one.
    """
    home = get_hermes_home()
    old_path = home / old_name
    if old_path.exists():
        return old_path
    return home / new_subpath


def display_hermes_home() -> str:
    """Return a user-friendly display string for the current HERMES_HOME.

    Uses ``~/`` shorthand for readability::

        default:  ``~/.hermes``
        profile:  ``~/.hermes/profiles/coder``
        custom:   ``/opt/hermes-custom``

    Use this in **user-facing** print/log messages instead of hardcoding
    ``~/.hermes``.  For code that needs a real ``Path``, use
    :func:`get_hermes_home` instead.
    """
    home = get_hermes_home()
    try:
        return "~/" + str(home.relative_to(Path.home()))
    except ValueError:
        return str(home)


def secure_parent_dir(path: Path) -> None:
    """Chmod ``0o700`` on the parent directory of *path*, but only if safe.

    Refuses to chmod ``/`` or any top-level directory (resolved parent with
    fewer than 3 parts, i.e. ``/`` or any direct child like ``/usr``) to
    prevent catastrophic host bricking when ``HERMES_HOME`` or other path
    env vars resolve to an unexpected location.

    See https://github.com/NousResearch/hermes-agent/issues/25821.
    """
    parent = path.parent.resolve()
    # Refuse root and its direct children (/usr, /home, /var, /tmp, …).
    if parent == Path("/") or len(parent.parts) < 3:
        return
    try:
        os.chmod(parent, 0o700)
    except OSError:
        pass


def get_subprocess_home() -> str | None:
    """Return a per-profile HOME directory for subprocesses, or None.

    When ``{HERMES_HOME}/home/`` exists on disk, subprocesses should use it
    as ``HOME`` so system tools (git, ssh, gh, npm …) write their configs
    inside the Hermes data directory instead of the OS-level ``/root`` or
    ``~/``.  This provides:

    * **Docker persistence** — tool configs land inside the persistent volume.
    * **Profile isolation** — each profile gets its own git identity, SSH
      keys, gh tokens, etc.

    The Python process's own ``os.environ["HOME"]`` and ``Path.home()`` are
    **never** modified — only subprocess environments should inject this value.
    Activation is directory-based: if the ``home/`` subdirectory doesn't
    exist, returns ``None`` and behavior is unchanged.
    """
    hermes_home = get_hermes_home_override() or os.getenv("HERMES_HOME")
    if not hermes_home:
        return None
    profile_home = os.path.join(hermes_home, "home")
    if os.path.isdir(profile_home):
        return profile_home
    return None


VALID_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")


def parse_reasoning_effort(effort: str) -> dict | None:
    """Parse a reasoning effort level into a config dict.

    Valid levels: "none", "minimal", "low", "medium", "high", "xhigh".
    Returns None when the input is empty or unrecognized (caller uses default).
    Returns {"enabled": False} for "none".
    Returns {"enabled": True, "effort": <level>} for valid effort levels.
    """
    if not effort or not effort.strip():
        return None
    effort = effort.strip().lower()
    if effort == "none":
        return {"enabled": False}
    if effort in VALID_REASONING_EFFORTS:
        return {"enabled": True, "effort": effort}
    return None


def is_termux() -> bool:
    """Return True when running inside a Termux (Android) environment.

    Checks ``TERMUX_VERSION`` (set by Termux) or the Termux-specific
    ``PREFIX`` path.  Import-safe — no heavy deps.
    """
    prefix = os.getenv("PREFIX", "")
    return bool(os.getenv("TERMUX_VERSION") or "com.termux/files/usr" in prefix)


_wsl_detected: bool | None = None


def is_wsl() -> bool:
    """Return True when running inside WSL (Windows Subsystem for Linux).

    Checks ``/proc/version`` for the ``microsoft`` marker that both WSL1
    and WSL2 inject.  Result is cached for the process lifetime.
    Import-safe — no heavy deps.
    """
    global _wsl_detected
    if _wsl_detected is not None:
        return _wsl_detected
    try:
        with open("/proc/version", "r", encoding="utf-8") as f:
            _wsl_detected = "microsoft" in f.read().lower()
    except Exception:
        _wsl_detected = False
    return _wsl_detected


_container_detected: bool | None = None


def is_container() -> bool:
    """Return True when running inside a Docker/Podman container.

    Checks ``/.dockerenv`` (Docker), ``/run/.containerenv`` (Podman),
    and ``/proc/1/cgroup`` for container runtime markers.  Result is
    cached for the process lifetime.  Import-safe — no heavy deps.
    """
    global _container_detected
    if _container_detected is not None:
        return _container_detected
    if os.path.exists("/.dockerenv"):
        _container_detected = True
        return True
    if os.path.exists("/run/.containerenv"):
        _container_detected = True
        return True
    try:
        with open("/proc/1/cgroup", "r", encoding="utf-8") as f:
            cgroup = f.read()
            if "docker" in cgroup or "podman" in cgroup or "/lxc/" in cgroup:
                _container_detected = True
                return True
    except OSError:
        pass
    _container_detected = False
    return False


# ─── Well-Known Paths ─────────────────────────────────────────────────────────


def get_config_path() -> Path:
    """Return the path to ``config.yaml`` under HERMES_HOME.

    Replaces the ``get_hermes_home() / "config.yaml"`` pattern repeated
    in 7+ files (skill_utils.py, hermes_logging.py, hermes_time.py, etc.).
    """
    return get_hermes_home() / "config.yaml"


def get_skills_dir() -> Path:
    """Return the path to the skills directory under HERMES_HOME."""
    return get_hermes_home() / "skills"



def get_env_path() -> Path:
    """Return the path to the ``.env`` file under HERMES_HOME."""
    return get_hermes_home() / ".env"


# ─── Command-Link & Bundled-Node Locations ───────────────────────────────────
#
# Canonical, single source of truth for *where the installer places executables
# so they land on PATH*.  This MUST stay in lockstep with the bash helper
# ``get_command_link_dir()`` in ``scripts/install.sh`` and ``_nb_get_link_dir()``
# in ``scripts/lib/node-bootstrap.sh``.  Historically this logic was duplicated
# (and went stale) in doctor.py, profiles.py, uninstall.py and backup.py, which
# caused root-FHS installs to look for / write the ``hermes`` command and node
# symlinks in ``~/.local/bin`` even though they actually live in
# ``/usr/local/bin``.  See PR #38889.


def _is_root_fhs_layout() -> bool:
    """Return True when this is a root install using the Linux FHS layout.

    Mirrors ``resolve_install_layout()`` in ``scripts/install.sh``: root (uid 0)
    on Linux uses ``/usr/local/lib/hermes-agent`` for code and ``/usr/local/bin``
    for the command link.  We detect it the same way the installer's own runtime
    guard does (``_ensure_fhs_path_guard`` in main.py): Linux + uid 0 + a command
    link present at ``/usr/local/bin/hermes`` OR code at
    ``/usr/local/lib/hermes-agent``.  Falling back to the uid check alone keeps
    this correct *during* an install before the symlink exists.
    """
    if sys.platform != "linux":
        return False
    try:
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            return False
    except OSError:
        return False
    # Confirm it's actually the FHS layout (not a root user who installed into
    # ~/.local/bin anyway).  A legacy git install at <HERMES_HOME>/hermes-agent
    # means resolve_install_layout() kept the ~/.local/bin layout — mirror that.
    if (get_hermes_home() / "hermes-agent" / ".git").exists():
        return False
    if Path("/usr/local/bin/hermes").exists():
        return True
    if Path("/usr/local/lib/hermes-agent").exists():
        return True
    # No markers yet (e.g. mid-install): for a Linux root user the installer
    # defaults to the FHS layout, so assume FHS.
    return True


def command_link_dir() -> Path:
    """Return the directory where the ``hermes`` command (and bundled node/npm/
    npx symlinks, profile-alias wrappers) are placed so they land on PATH.

    Resolution mirrors ``get_command_link_dir()`` in ``scripts/install.sh``:

    * Termux  → ``$PREFIX/bin``
    * root FHS install on Linux → ``/usr/local/bin``
    * everything else (the common non-root case, and Windows) → ``~/.local/bin``
    """
    if is_termux():
        prefix = os.environ.get("PREFIX", "").strip()
        if prefix:
            return Path(prefix) / "bin"
    if _is_root_fhs_layout():
        return Path("/usr/local/bin")
    return Path.home() / ".local" / "bin"


def command_link_display_dir() -> str:
    """User-friendly display string for :func:`command_link_dir`.

    Uses ``~/.local/bin`` shorthand and ``$PREFIX/bin`` for Termux, matching
    ``get_command_link_display_dir()`` in ``scripts/install.sh``.
    """
    if is_termux() and os.environ.get("PREFIX", "").strip():
        return "$PREFIX/bin"
    if _is_root_fhs_layout():
        return "/usr/local/bin"
    return "~/.local/bin"


def command_link_candidate_dirs() -> list[Path]:
    """All directories the installer may have placed command links in.

    Used by uninstall and other cleanup paths that must find links regardless
    of which layout created them (e.g. an old ``~/.local/bin`` install upgraded
    to FHS, or vice-versa).  Always includes ``~/.local/bin`` plus the
    layout-specific dirs, de-duplicated and order-preserving.
    """
    dirs: list[Path] = [Path.home() / ".local" / "bin"]
    if sys.platform == "linux":
        dirs.append(Path("/usr/local/bin"))
    prefix = os.environ.get("PREFIX", "").strip()
    if prefix and "com.termux" in prefix:
        dirs.append(Path(prefix) / "bin")
    # De-dupe while preserving order.
    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        key = str(d)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def bundled_node_bin_dir() -> Path:
    """Return the bundled Node.js ``bin`` directory: ``<HERMES_HOME>/node/bin``.

    This is where ``install_node()`` / ``node-bootstrap.sh`` extract the
    Hermes-managed Node runtime.  Profile-aware via :func:`get_hermes_home`.
    Discovery code that gates a feature on ``node``/``npm``/``npx`` should fall
    back to this directory when ``shutil.which`` returns nothing, so a misplaced
    or missing PATH symlink doesn't make an installed runtime invisible.
    """
    return get_hermes_home() / "node" / "bin"


def find_node_executable(name: str = "node") -> str | None:
    """Resolve a Node executable (node/npm/npx) with bundled fallback.

    Returns an absolute path string if found on PATH or in the bundled
    ``<HERMES_HOME>/node/bin`` directory, else ``None``.  Prefer this over a
    bare ``shutil.which(name)`` anywhere a feature depends on Node, so an
    off-PATH bundled install still works.
    """
    import shutil

    on_path = shutil.which(name)
    if on_path:
        return on_path
    candidate = bundled_node_bin_dir() / name
    if sys.platform == "win32" and not candidate.suffix:
        # Windows ships node.exe / npm.cmd; try common suffixes.
        for suffix in (".exe", ".cmd", ".bat", ""):
            c = candidate.with_suffix(suffix) if suffix else candidate
            if c.exists() and os.access(c, os.X_OK):
                return str(c)
        return None
    if candidate.exists() and os.access(candidate, os.X_OK):
        return str(candidate)
    return None


# ─── Network Preferences ─────────────────────────────────────────────────────


def apply_ipv4_preference(force: bool = False) -> None:
    """Monkey-patch ``socket.getaddrinfo`` to prefer IPv4 connections.

    On servers with broken or unreachable IPv6, Python tries AAAA records
    first and hangs for the full TCP timeout before falling back to IPv4.
    This affects httpx, requests, urllib, the OpenAI SDK — everything that
    uses ``socket.getaddrinfo``.

    When *force* is True, patches ``getaddrinfo`` so that calls with
    ``family=AF_UNSPEC`` (the default) resolve as ``AF_INET`` instead,
    skipping IPv6 entirely.  If no A record exists, falls back to the
    original unfiltered resolution so pure-IPv6 hosts still work.

    Safe to call multiple times — only patches once.
    Set ``network.force_ipv4: true`` in ``config.yaml`` to enable.
    """
    if not force:
        return

    import socket

    # Guard against double-patching
    if getattr(socket.getaddrinfo, "_hermes_ipv4_patched", False):
        return

    _original_getaddrinfo = socket.getaddrinfo

    def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if family == 0:  # AF_UNSPEC — caller didn't request a specific family
            try:
                return _original_getaddrinfo(
                    host, port, socket.AF_INET, type, proto, flags
                )
            except socket.gaierror:
                # No A record — fall back to full resolution (pure-IPv6 hosts)
                return _original_getaddrinfo(host, port, family, type, proto, flags)
        return _original_getaddrinfo(host, port, family, type, proto, flags)

    _ipv4_getaddrinfo._hermes_ipv4_patched = True  # type: ignore[attr-defined]
    socket.getaddrinfo = _ipv4_getaddrinfo  # type: ignore[assignment]


# ─── Streaming Response Constants ────────────────────────────────────────────

# Response ID for partial stream stubs used during error recovery
PARTIAL_STREAM_STUB_ID = "partial-stream-stub"

FINISH_REASON_LENGTH = "length"


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS_URL = f"{OPENROUTER_BASE_URL}/models"
