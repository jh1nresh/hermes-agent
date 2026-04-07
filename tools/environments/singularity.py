"""Singularity/Apptainer persistent container environment.

Security-hardened with --containall, --no-home, capability dropping.
Supports configurable resource limits and optional filesystem persistence
via writable overlay directories that survive across sessions.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from hermes_constants import get_hermes_home
from tools.environments.base import BaseEnvironment

logger = logging.getLogger(__name__)

_SNAPSHOT_STORE = get_hermes_home() / "singularity_snapshots.json"


def _find_singularity_executable() -> str:
    """Locate the apptainer or singularity CLI binary.

    Returns the executable name (``"apptainer"`` or ``"singularity"``).
    Raises ``RuntimeError`` with install instructions if neither is found.
    """
    if shutil.which("apptainer"):
        return "apptainer"
    if shutil.which("singularity"):
        return "singularity"
    raise RuntimeError(
        "Neither 'apptainer' nor 'singularity' was found in PATH. "
        "Install Apptainer (https://apptainer.org/docs/admin/main/installation.html) "
        "or Singularity and ensure the CLI is available."
    )


def _ensure_singularity_available() -> str:
    """Preflight check: resolve the executable and verify it responds.

    Returns the executable name on success.
    Raises ``RuntimeError`` with an actionable message on failure.
    """
    exe = _find_singularity_executable()

    try:
        result = subprocess.run(
            [exe, "version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        raise RuntimeError(
            f"Singularity backend selected but the resolved executable '{exe}' "
            "could not be executed. Check your installation."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"'{exe} version' timed out. The runtime may be misconfigured."
        )

    if result.returncode != 0:
        stderr = result.stderr.strip()[:200]
        raise RuntimeError(
            f"'{exe} version' failed (exit code {result.returncode}): {stderr}"
        )

    return exe


def _load_snapshots() -> Dict[str, str]:
    if _SNAPSHOT_STORE.exists():
        try:
            return json.loads(_SNAPSHOT_STORE.read_text())
        except Exception:
            pass
    return {}


def _save_snapshots(data: Dict[str, str]) -> None:
    _SNAPSHOT_STORE.parent.mkdir(parents=True, exist_ok=True)
    _SNAPSHOT_STORE.write_text(json.dumps(data, indent=2))


# -------------------------------------------------------------------------
# Singularity helpers (scratch dir, SIF cache, SIF building)
# -------------------------------------------------------------------------

def _get_scratch_dir() -> Path:
    """Get the best directory for Singularity sandboxes.

    Resolution order:
      1. TERMINAL_SCRATCH_DIR (explicit override)
      2. TERMINAL_SANDBOX_DIR / singularity (shared sandbox root)
      3. /scratch (common on HPC clusters)
      4. ~/.hermes/sandboxes/singularity (fallback)
    """
    custom_scratch = os.getenv("TERMINAL_SCRATCH_DIR")
    if custom_scratch:
        scratch_path = Path(custom_scratch)
        scratch_path.mkdir(parents=True, exist_ok=True)
        return scratch_path

    from tools.environments.base import get_sandbox_dir
    sandbox = get_sandbox_dir() / "singularity"

    scratch = Path("/scratch")
    if scratch.exists() and os.access(scratch, os.W_OK):
        user_scratch = scratch / os.getenv("USER", "hermes") / "hermes-agent"
        user_scratch.mkdir(parents=True, exist_ok=True)
        logger.info("Using /scratch for sandboxes: %s", user_scratch)
        return user_scratch

    sandbox.mkdir(parents=True, exist_ok=True)
    return sandbox


def _get_apptainer_cache_dir() -> Path:
    """Get the Apptainer cache directory for SIF images."""
    cache_dir = os.getenv("APPTAINER_CACHEDIR")
    if cache_dir:
        cache_path = Path(cache_dir)
        cache_path.mkdir(parents=True, exist_ok=True)
        return cache_path
    scratch = _get_scratch_dir()
    cache_path = scratch / ".apptainer"
    cache_path.mkdir(parents=True, exist_ok=True)
    return cache_path


_sif_build_lock = threading.Lock()


def _get_or_build_sif(image: str, executable: str = "apptainer") -> str:
    """Get or build a SIF image from a docker:// URL.

    Returns the path unchanged if it's already a .sif file.
    For docker:// URLs, checks the cache and builds if needed.
    """
    if image.endswith('.sif') and Path(image).exists():
        return image
    if not image.startswith('docker://'):
        return image

    image_name = image.replace('docker://', '').replace('/', '-').replace(':', '-')
    cache_dir = _get_apptainer_cache_dir()
    sif_path = cache_dir / f"{image_name}.sif"

    if sif_path.exists():
        return str(sif_path)

    with _sif_build_lock:
        if sif_path.exists():
            return str(sif_path)

        logger.info("Building SIF image (one-time setup)...")
        logger.info("  Source: %s", image)
        logger.info("  Target: %s", sif_path)

        tmp_dir = cache_dir / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["APPTAINER_TMPDIR"] = str(tmp_dir)
        env["APPTAINER_CACHEDIR"] = str(cache_dir)

        try:
            result = subprocess.run(
                [executable, "build", str(sif_path), image],
                capture_output=True, text=True, timeout=600, env=env,
            )
            if result.returncode != 0:
                logger.warning("SIF build failed, falling back to docker:// URL")
                logger.warning("  Error: %s", result.stderr[:500])
                return image
            logger.info("SIF image built successfully")
            return str(sif_path)
        except subprocess.TimeoutExpired:
            logger.warning("SIF build timed out, falling back to docker:// URL")
            if sif_path.exists():
                sif_path.unlink()
            return image
        except Exception as e:
            logger.warning("SIF build error: %s, falling back to docker:// URL", e)
            return image


# -------------------------------------------------------------------------
# SingularityEnvironment
# -------------------------------------------------------------------------

class SingularityEnvironment(BaseEnvironment):
    """Hardened Singularity/Apptainer container with resource limits and persistence.

    Security: --containall (isolated PID/IPC/mount namespaces, no host home mount),
    --no-home, writable-tmpfs for scratch space. The container cannot see or modify
    the host filesystem outside of explicitly bound paths.

    Persistence: when enabled, the writable overlay directory is preserved across
    sessions so installed packages and files survive cleanup/restore.
    """

    def __init__(
        self,
        image: str,
        cwd: str = "~",
        timeout: int = 60,
        cpu: float = 0,
        memory: int = 0,
        disk: int = 0,
        persistent_filesystem: bool = False,
        task_id: str = "default",
    ):
        super().__init__(cwd=cwd, timeout=timeout)
        self.executable = _ensure_singularity_available()
        self.image = _get_or_build_sif(image, self.executable)
        self.instance_id = f"hermes_{uuid.uuid4().hex[:12]}"
        self._instance_started = False
        self._persistent = persistent_filesystem
        self._task_id = task_id
        self._overlay_dir: Optional[Path] = None

        # Resource limits
        self._cpu = cpu
        self._memory = memory

        # Persistent overlay directory
        if self._persistent:
            overlay_base = _get_scratch_dir() / "hermes-overlays"
            overlay_base.mkdir(parents=True, exist_ok=True)
            self._overlay_dir = overlay_base / f"overlay-{task_id}"
            self._overlay_dir.mkdir(parents=True, exist_ok=True)

        self._start_instance()
        self.init_session()

    def _start_instance(self):
        cmd = [self.executable, "instance", "start"]

        # Security: full isolation from host
        cmd.extend(["--containall", "--no-home"])

        # Writable layer
        if self._persistent and self._overlay_dir:
            # Persistent writable overlay -- survives across restarts
            cmd.extend(["--overlay", str(self._overlay_dir)])
        else:
            cmd.append("--writable-tmpfs")

        # Mount credential files and skills directory (read-only).
        try:
            from tools.credential_files import get_credential_file_mounts, get_skills_directory_mount

            for mount_entry in get_credential_file_mounts():
                cmd.extend(["--bind", f"{mount_entry['host_path']}:{mount_entry['container_path']}:ro"])
                logger.info(
                    "Singularity: binding credential %s -> %s",
                    mount_entry["host_path"],
                    mount_entry["container_path"],
                )
            skills_mount = get_skills_directory_mount()
            if skills_mount:
                cmd.extend(["--bind", f"{skills_mount['host_path']}:{skills_mount['container_path']}:ro"])
                logger.info(
                    "Singularity: binding skills dir %s -> %s",
                    skills_mount["host_path"],
                    skills_mount["container_path"],
                )
        except Exception as e:
            logger.debug("Singularity: could not load credential/skills mounts: %s", e)

        # Resource limits (cgroup-based, may require root or appropriate config)
        if self._memory > 0:
            cmd.extend(["--memory", f"{self._memory}M"])
        if self._cpu > 0:
            cmd.extend(["--cpus", str(self._cpu)])

        cmd.extend([str(self.image), self.instance_id])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                raise RuntimeError(f"Failed to start instance: {result.stderr}")
            self._instance_started = True
            logger.info("Singularity instance %s started (persistent=%s)", 
                        self.instance_id, self._persistent)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Instance start timed out")

    # ------------------------------------------------------------------
    # Unified execution model — _run_bash is the only execution method
    # ------------------------------------------------------------------

    def _run_bash(self, cmd_string: str, *,
                  timeout: int | None = None,
                  stdin_data: str | None = None) -> subprocess.Popen:
        if not self._instance_started:
            raise RuntimeError("Singularity instance not started")
        cmd = [self.executable, "exec",
               f"instance://{self.instance_id}",
               "bash", "-c", cmd_string]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            text=True,
        )
        if stdin_data is not None:
            try:
                proc.stdin.write(stdin_data)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        return proc

    def _run_bash_login(self, cmd_string: str) -> subprocess.Popen:
        if not self._instance_started:
            raise RuntimeError("Singularity instance not started")
        cmd = [self.executable, "exec",
               f"instance://{self.instance_id}",
               "bash", "-l", "-c", cmd_string]
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True,
        )

    def cleanup(self):
        """Stop the instance. If persistent, the overlay dir survives for next creation."""
        if self._instance_started:
            try:
                subprocess.run(
                    [self.executable, "instance", "stop", self.instance_id],
                    capture_output=True, text=True, timeout=30,
                )
                logger.info("Singularity instance %s stopped", self.instance_id)
            except Exception as e:
                logger.warning("Failed to stop Singularity instance %s: %s", self.instance_id, e)
            self._instance_started = False

        # Record overlay path for persistence restoration
        if self._persistent and self._overlay_dir:
            snapshots = _load_snapshots()
            snapshots[self._task_id] = str(self._overlay_dir)
            _save_snapshots(snapshots)
