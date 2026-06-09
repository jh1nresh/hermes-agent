"""Fixtures shared across hermes_cli kanban tests."""

from __future__ import annotations

import sys

import pytest


@pytest.fixture
def all_assignees_spawnable(monkeypatch):
    """Pretend every assignee maps to a real Hermes profile.

    Most dispatcher tests use synthetic assignees ("alice", "bob") that
    don't correspond to actual profile directories on disk. Without this
    patch, the dispatcher's profile-exists guard (PR #20105) routes
    those tasks into ``skipped_nonspawnable`` instead of spawning, which
    would break tests that assert spawn behavior.
    """
    from hermes_cli import profiles
    monkeypatch.setattr(profiles, "profile_exists", lambda name: True)


@pytest.fixture(autouse=True)
def _suppress_concurrent_hermes_gate(request, monkeypatch):
    """Default ``_detect_concurrent_hermes_instances`` to ``[]`` on Windows hosts.

    The Windows update path refuses to proceed when another ``hermes.exe`` is
    detected (issue #26670). On a developer's Windows machine running the test
    suite via ``hermes`` itself, this would flag the running agent as a
    concurrent instance and abort every ``cmd_update`` test. This fixture
    stubs the helper to ``[]`` so those tests run cleanly.

    Scope: the helper short-circuits to ``[]`` via ``not _is_windows()`` on
    every non-Windows host (Linux CI, macOS), so there is nothing to suppress
    there — and importing + monkeypatching ``hermes_cli.main`` for every test
    in the package is exactly what raced a partially-initialized module under
    pytest's per-test spawn isolation (the AttributeError flake). Gating the
    whole fixture behind ``sys.platform == "win32"`` means CI never imports or
    mutates ``main`` here, removing the race at its source while preserving the
    Windows-dev behavior the fixture exists for.

    Tests that need to call the REAL function (e.g. unit tests for the helper
    itself, or that force ``_is_windows`` True) opt out with
    ``@pytest.mark.real_concurrent_gate``.
    """
    if sys.platform != "win32":
        return
    if request.node.get_closest_marker("real_concurrent_gate"):
        return
    try:
        from hermes_cli import main as _cli_main
    except Exception:
        return
    # raising=False: defense-in-depth against a transiently partial
    # hermes_cli.main module under spawn isolation. The attribute always
    # exists once main.py finishes importing, so a no-op when it's briefly
    # absent is the correct, race-free default.
    monkeypatch.setattr(
        _cli_main,
        "_detect_concurrent_hermes_instances",
        lambda *_a, **_k: [],
        raising=False,
    )
