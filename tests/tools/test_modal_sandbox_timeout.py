"""Tests verifying the Modal sandbox timeout bug fix.

Bug: `lifetime_seconds` from container_config was never passed through to
`sandbox_kwargs["timeout"]`, so Modal always used its default of 3600s.

Fix applied to:
- tools/terminal_tool.py: `_create_environment()` now sets
  `sandbox_kwargs["timeout"]` from `cc.get("lifetime_seconds", 3600)`
- tools/terminal_tool.py: `container_config` dict now includes
  `"lifetime_seconds"` from config
- tools/environments/managed_modal.py: `_create_sandbox()` reads timeout
  from `self._sandbox_kwargs` instead of hardcoding 3_600_000
"""

import sys
import types
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Repo root on sys.path
# ---------------------------------------------------------------------------
_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

# ---------------------------------------------------------------------------
# Load terminal_tool (may be skipped if deps are missing)
# ---------------------------------------------------------------------------
try:
    import tools.terminal_tool as _tt_mod
except ImportError:
    pytest.skip("tools.terminal_tool not importable (missing deps)", allow_module_level=True)

TOOLS_DIR = _repo_root / "tools"


# ---------------------------------------------------------------------------
# Helpers shared with test_managed_modal_environment.py
# ---------------------------------------------------------------------------

def _reset_modules(prefixes: tuple):
    for name in list(sys.modules):
        if name.startswith(prefixes):
            sys.modules.pop(name, None)


def _install_fake_tools_package(*, credential_mounts=None):
    """Install a minimal fake tools package so managed_modal.py can be loaded
    without network access or real Modal credentials."""
    _reset_modules(("tools", "agent", "hermes_cli"))

    hermes_cli = types.ModuleType("hermes_cli")
    hermes_cli.__path__ = []  # type: ignore[attr-defined]
    sys.modules["hermes_cli"] = hermes_cli
    sys.modules["hermes_cli.config"] = types.SimpleNamespace(
        get_hermes_home=lambda: Path(tempfile.gettempdir()) / "hermes-home",
    )

    tools_package = types.ModuleType("tools")
    tools_package.__path__ = [str(TOOLS_DIR)]  # type: ignore[attr-defined]
    sys.modules["tools"] = tools_package

    env_package = types.ModuleType("tools.environments")
    env_package.__path__ = [str(TOOLS_DIR / "environments")]  # type: ignore[attr-defined]
    sys.modules["tools.environments"] = env_package

    interrupt_event = threading.Event()
    sys.modules["tools.interrupt"] = types.SimpleNamespace(
        set_interrupt=lambda value=True: interrupt_event.set() if value else interrupt_event.clear(),
        is_interrupted=lambda: interrupt_event.is_set(),
        _interrupt_event=interrupt_event,
    )

    class _DummyBaseEnvironment:
        def __init__(self, cwd: str = "/root", timeout: int = 60, env=None):
            self.cwd = cwd
            self.timeout = timeout
            self.env = env or {}

    sys.modules["tools.environments.base"] = types.SimpleNamespace(
        BaseEnvironment=_DummyBaseEnvironment,
    )
    sys.modules["tools.managed_tool_gateway"] = types.SimpleNamespace(
        resolve_managed_tool_gateway=lambda vendor: types.SimpleNamespace(
            vendor=vendor,
            gateway_origin="https://modal-gateway.example.com",
            nous_user_token="user-token",
            managed_mode=True,
        )
    )
    sys.modules["tools.credential_files"] = types.SimpleNamespace(
        get_credential_file_mounts=lambda: list(credential_mounts or []),
    )

    return interrupt_event


class _FakeResponse:
    """Minimal requests.Response substitute."""

    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


# ===========================================================================
# Tests: _create_environment (direct modal path)
# ===========================================================================

class TestCreateEnvironmentTimeoutPassthrough:
    """_create_environment() must set sandbox_kwargs['timeout'] from lifetime_seconds."""

    def test_lifetime_seconds_7200_reaches_modal_environment(self, monkeypatch):
        """When container_config has lifetime_seconds=7200, ModalEnvironment gets timeout=7200."""
        captured_kwargs = {}
        sentinel = object()

        def _fake_modal_env(**kwargs):
            captured_kwargs.update(kwargs)
            return sentinel

        # Force the direct backend so we hit ModalEnvironment, not ManagedModalEnvironment
        monkeypatch.setattr(
            _tt_mod,
            "_get_modal_backend_state",
            lambda _: {"selected_backend": "direct"},
        )
        monkeypatch.setattr(_tt_mod, "_ModalEnvironment", _fake_modal_env)

        result = _tt_mod._create_environment(
            env_type="modal",
            image="python:3.11",
            cwd="/root",
            timeout=60,
            container_config={"lifetime_seconds": 7200},
        )

        assert result is sentinel, "Should have used our fake ModalEnvironment"
        modal_sandbox_kwargs = captured_kwargs.get("modal_sandbox_kwargs", {})
        assert modal_sandbox_kwargs.get("timeout") == 7200, (
            f"Expected timeout=7200 in modal_sandbox_kwargs, got: {modal_sandbox_kwargs}"
        )

    def test_lifetime_seconds_defaults_to_3600_when_absent(self, monkeypatch):
        """When lifetime_seconds is not in container_config, timeout defaults to 3600."""
        captured_kwargs = {}
        sentinel = object()

        def _fake_modal_env(**kwargs):
            captured_kwargs.update(kwargs)
            return sentinel

        monkeypatch.setattr(
            _tt_mod,
            "_get_modal_backend_state",
            lambda _: {"selected_backend": "direct"},
        )
        monkeypatch.setattr(_tt_mod, "_ModalEnvironment", _fake_modal_env)

        result = _tt_mod._create_environment(
            env_type="modal",
            image="python:3.11",
            cwd="/root",
            timeout=60,
            container_config={},  # no lifetime_seconds
        )

        assert result is sentinel
        modal_sandbox_kwargs = captured_kwargs.get("modal_sandbox_kwargs", {})
        assert modal_sandbox_kwargs.get("timeout") == 3600, (
            f"Expected default timeout=3600, got: {modal_sandbox_kwargs}"
        )

    def test_lifetime_seconds_none_container_config_defaults_to_3600(self, monkeypatch):
        """When container_config is None, timeout defaults to 3600."""
        captured_kwargs = {}
        sentinel = object()

        def _fake_modal_env(**kwargs):
            captured_kwargs.update(kwargs)
            return sentinel

        monkeypatch.setattr(
            _tt_mod,
            "_get_modal_backend_state",
            lambda _: {"selected_backend": "direct"},
        )
        monkeypatch.setattr(_tt_mod, "_ModalEnvironment", _fake_modal_env)

        result = _tt_mod._create_environment(
            env_type="modal",
            image="python:3.11",
            cwd="/root",
            timeout=60,
            container_config=None,  # None container_config
        )

        assert result is sentinel
        modal_sandbox_kwargs = captured_kwargs.get("modal_sandbox_kwargs", {})
        assert modal_sandbox_kwargs.get("timeout") == 3600, (
            f"Expected default timeout=3600, got: {modal_sandbox_kwargs}"
        )

    def test_lifetime_seconds_7200_reaches_managed_modal_environment(self, monkeypatch):
        """When managed backend is selected, ManagedModalEnvironment also gets timeout=7200."""
        captured_kwargs = {}
        sentinel = object()

        def _fake_managed_env(**kwargs):
            captured_kwargs.update(kwargs)
            return sentinel

        monkeypatch.setattr(
            _tt_mod,
            "_get_modal_backend_state",
            lambda _: {"selected_backend": "managed"},
        )
        monkeypatch.setattr(_tt_mod, "_ManagedModalEnvironment", _fake_managed_env)

        result = _tt_mod._create_environment(
            env_type="modal",
            image="python:3.11",
            cwd="/root",
            timeout=60,
            container_config={"lifetime_seconds": 7200},
        )

        assert result is sentinel
        modal_sandbox_kwargs = captured_kwargs.get("modal_sandbox_kwargs", {})
        assert modal_sandbox_kwargs.get("timeout") == 7200, (
            f"Expected timeout=7200 in modal_sandbox_kwargs for managed env, got: {modal_sandbox_kwargs}"
        )


# ===========================================================================
# Tests: container_config includes lifetime_seconds from _get_env_config
# ===========================================================================

class TestContainerConfigLifetimeSeconds:
    """container_config dict built in terminal_tool must include lifetime_seconds."""

    def test_container_config_includes_lifetime_seconds_from_env(self, monkeypatch):
        """TERMINAL_LIFETIME_SECONDS env var flows into container_config."""
        monkeypatch.setenv("TERMINAL_ENV", "modal")
        monkeypatch.setenv("TERMINAL_LIFETIME_SECONDS", "7200")
        config = _tt_mod._get_env_config()
        assert config.get("lifetime_seconds") == 7200, (
            f"Expected lifetime_seconds=7200 in config, got: {config.get('lifetime_seconds')}"
        )

    def test_container_config_lifetime_seconds_default_is_300(self, monkeypatch):
        """Without TERMINAL_LIFETIME_SECONDS, the default should be 300 (cleanup thread default)."""
        monkeypatch.setenv("TERMINAL_ENV", "modal")
        monkeypatch.delenv("TERMINAL_LIFETIME_SECONDS", raising=False)
        config = _tt_mod._get_env_config()
        assert "lifetime_seconds" in config, "lifetime_seconds must be present in config"
        # Default from code is 300
        assert config["lifetime_seconds"] == 300, (
            f"Expected default lifetime_seconds=300, got: {config['lifetime_seconds']}"
        )


# ===========================================================================
# Tests: ManagedModalEnvironment._create_sandbox uses sandbox_kwargs timeout
# ===========================================================================

class TestManagedModalTimeoutPassthrough:
    """ManagedModalEnvironment must read timeout from sandbox_kwargs, not hardcode 3_600_000."""

    @pytest.fixture(autouse=True)
    def _restore_modules(self):
        """Save and restore sys.modules so fake package doesn't leak."""
        saved = {
            name: mod for name, mod in sys.modules.items()
            if name.startswith(("tools", "hermes_cli"))
        }
        yield
        _reset_modules(("tools", "hermes_cli"))
        sys.modules.update(saved)

    def test_sandbox_created_with_7200_timeout(self, monkeypatch):
        """ManagedModalEnvironment with lifetime_seconds=7200 sends timeoutMs=7_200_000."""
        _install_fake_tools_package()

        # Load managed_modal fresh after installing fake package
        from importlib.util import spec_from_file_location, module_from_spec
        spec = spec_from_file_location(
            "tools.environments.managed_modal",
            TOOLS_DIR / "environments" / "managed_modal.py",
        )
        managed_modal = module_from_spec(spec)
        sys.modules["tools.environments.managed_modal"] = managed_modal
        spec.loader.exec_module(managed_modal)

        create_payloads = []

        def fake_request(method, url, headers=None, json=None, timeout=None):
            if method == "POST" and url.endswith("/v1/sandboxes"):
                create_payloads.append(json)
                return _FakeResponse(200, {"id": "sandbox-1"})
            if method == "POST" and url.endswith("/terminate"):
                return _FakeResponse(200, {"status": "terminated"})
            raise AssertionError(f"Unexpected request: {method} {url}")

        monkeypatch.setattr(managed_modal.requests, "request", fake_request)

        env = managed_modal.ManagedModalEnvironment(
            image="python:3.11",
            modal_sandbox_kwargs={"timeout": 7200},
        )
        env.cleanup()

        assert len(create_payloads) == 1
        payload = create_payloads[0]
        assert payload["timeoutMs"] == 7_200_000, (
            f"Expected timeoutMs=7_200_000 (7200s * 1000), got: {payload['timeoutMs']}. "
            "ManagedModalEnvironment must read timeout from sandbox_kwargs, not hardcode 3600."
        )

    def test_sandbox_created_with_default_3600_timeout(self, monkeypatch):
        """ManagedModalEnvironment with no explicit timeout sends timeoutMs=3_600_000."""
        _install_fake_tools_package()

        from importlib.util import spec_from_file_location, module_from_spec
        spec = spec_from_file_location(
            "tools.environments.managed_modal",
            TOOLS_DIR / "environments" / "managed_modal.py",
        )
        managed_modal = module_from_spec(spec)
        sys.modules["tools.environments.managed_modal"] = managed_modal
        spec.loader.exec_module(managed_modal)

        create_payloads = []

        def fake_request(method, url, headers=None, json=None, timeout=None):
            if method == "POST" and url.endswith("/v1/sandboxes"):
                create_payloads.append(json)
                return _FakeResponse(200, {"id": "sandbox-1"})
            if method == "POST" and url.endswith("/terminate"):
                return _FakeResponse(200, {"status": "terminated"})
            raise AssertionError(f"Unexpected request: {method} {url}")

        monkeypatch.setattr(managed_modal.requests, "request", fake_request)

        env = managed_modal.ManagedModalEnvironment(
            image="python:3.11",
            modal_sandbox_kwargs={},  # no timeout key — should default to 3600
        )
        env.cleanup()

        assert len(create_payloads) == 1
        payload = create_payloads[0]
        assert payload["timeoutMs"] == 3_600_000, (
            f"Expected default timeoutMs=3_600_000, got: {payload['timeoutMs']}"
        )

    def test_sandbox_created_with_none_kwargs_defaults_to_3600(self, monkeypatch):
        """ManagedModalEnvironment with modal_sandbox_kwargs=None defaults to 3600."""
        _install_fake_tools_package()

        from importlib.util import spec_from_file_location, module_from_spec
        spec = spec_from_file_location(
            "tools.environments.managed_modal",
            TOOLS_DIR / "environments" / "managed_modal.py",
        )
        managed_modal = module_from_spec(spec)
        sys.modules["tools.environments.managed_modal"] = managed_modal
        spec.loader.exec_module(managed_modal)

        create_payloads = []

        def fake_request(method, url, headers=None, json=None, timeout=None):
            if method == "POST" and url.endswith("/v1/sandboxes"):
                create_payloads.append(json)
                return _FakeResponse(200, {"id": "sandbox-1"})
            if method == "POST" and url.endswith("/terminate"):
                return _FakeResponse(200, {"status": "terminated"})
            raise AssertionError(f"Unexpected request: {method} {url}")

        monkeypatch.setattr(managed_modal.requests, "request", fake_request)

        env = managed_modal.ManagedModalEnvironment(
            image="python:3.11",
            modal_sandbox_kwargs=None,
        )
        env.cleanup()

        assert len(create_payloads) == 1
        payload = create_payloads[0]
        assert payload["timeoutMs"] == 3_600_000, (
            f"Expected default timeoutMs=3_600_000, got: {payload['timeoutMs']}"
        )
