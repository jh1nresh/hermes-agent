"""Narrow approvals.write_file policy for the file mutation pair."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from contextvars import copy_context
from types import SimpleNamespace
from unittest.mock import MagicMock, patch as mock_patch

import pytest

import tools.approval as approval
import tools.file_tools as file_tools
from hermes_cli.config import DEFAULT_CONFIG
from tools.registry import registry


@pytest.fixture(autouse=True)
def _approval_state(monkeypatch):
    monkeypatch.setattr(approval, "get_current_session_key", lambda default="default": "write-test")
    monkeypatch.setattr(approval, "is_approved", lambda session, pattern: False)
    monkeypatch.setattr(approval, "is_current_session_yolo_enabled", lambda: False)
    monkeypatch.setattr(approval, "_YOLO_MODE_FROZEN", False)
    monkeypatch.setattr("tools.terminal_tool._get_approval_callback", lambda: None)
    monkeypatch.setattr(file_tools, "_check_file_reqs", lambda: True)


def _mode(monkeypatch, mode: str) -> None:
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"approvals": {"write_file": mode}},
    )


def _result(raw: str | dict) -> dict:
    return json.loads(raw) if isinstance(raw, str) else raw


def test_default_config_keeps_file_mutations_allowed():
    assert DEFAULT_CONFIG["approvals"]["write_file"] == "allow"


def test_allow_executes_write_file_and_patch_without_approval(tmp_path, monkeypatch):
    _mode(monkeypatch, "allow")
    monkeypatch.setattr(
        approval,
        "request_tool_approval",
        lambda *args, **kwargs: pytest.fail("allow must not request approval"),
    )
    target = tmp_path / "example.txt"

    assert "error" not in _result(registry.dispatch("write_file", {"path": str(target), "content": "old"}))
    assert "error" not in _result(
        registry.dispatch(
            "patch",
            {"mode": "replace", "path": str(target), "old_string": "old", "new_string": "new"},
        )
    )
    assert target.read_text() == "new"


@pytest.mark.parametrize("tool_name", ["write_file", "patch"])
def test_ask_uses_shared_gate_once_and_blocks_mutation(tmp_path, monkeypatch, tool_name):
    _mode(monkeypatch, "ask")
    calls = []

    def deny(name, reason, **kwargs):
        calls.append((name, reason, kwargs))
        return {"approved": False, "message": "BLOCKED: user denied write"}

    monkeypatch.setattr(approval, "request_tool_approval", deny)
    target = tmp_path / "blocked.txt"
    if tool_name == "write_file":
        args = {"path": str(target), "content": "new"}
    else:
        target.write_text("old")
        args = {"mode": "replace", "path": str(target), "old_string": "old", "new_string": "new"}

    result = _result(registry.dispatch(tool_name, args))

    assert result["error"] == "BLOCKED: user denied write"
    assert len(calls) == 1
    assert calls[0][0] == tool_name
    assert calls[0][2] == {"rule_key": "write_file", "honor_yolo": False}
    assert (not target.exists()) if tool_name == "write_file" else target.read_text() == "old"


def test_read_and_search_are_not_gated(tmp_path, monkeypatch):
    _mode(monkeypatch, "ask")
    monkeypatch.setattr(
        approval,
        "request_tool_approval",
        lambda *args, **kwargs: pytest.fail("read-only file tools must not request approval"),
    )
    target = tmp_path / "example.txt"
    target.write_text("needle")

    assert "needle" in registry.dispatch("read_file", {"path": str(target)})
    assert "needle" in registry.dispatch(
        "search_files", {"pattern": "needle", "path": str(tmp_path)}
    )


def test_agent_dispatch_prompts_once_despite_execution_middleware(tmp_path, monkeypatch):
    _mode(monkeypatch, "ask")
    calls = []
    monkeypatch.setattr(
        approval,
        "request_tool_approval",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"approved": True, "message": None},
    )
    target = tmp_path / "agent-dispatch.txt"
    from model_tools import handle_function_call

    result = _result(handle_function_call("write_file", {"path": str(target), "content": "ok"}))

    assert "error" not in result
    assert target.read_text() == "ok"
    assert len(calls) == 1


def test_ask_cli_path_prompts_and_executes_once(tmp_path, monkeypatch):
    _mode(monkeypatch, "ask")
    monkeypatch.setattr(approval, "_is_interactive_cli", lambda: True)
    monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
    prompts = []
    monkeypatch.setattr(
        approval,
        "prompt_dangerous_approval",
        lambda target, description, **kwargs: prompts.append((target, description)) or "once",
    )
    target = tmp_path / "cli.txt"

    result = _result(registry.dispatch("write_file", {"path": str(target), "content": "ok"}))

    assert "error" not in result
    assert target.read_text() == "ok"
    assert len(prompts) == 1


def test_ask_gateway_path_submits_pending_and_does_not_write(tmp_path, monkeypatch):
    _mode(monkeypatch, "ask")
    monkeypatch.setattr(approval, "_is_interactive_cli", lambda: False)
    monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: True)
    submitted = []
    monkeypatch.setattr(approval, "submit_pending", lambda session, data: submitted.append((session, data)))
    target = tmp_path / "gateway.txt"

    result = _result(registry.dispatch("write_file", {"path": str(target), "content": "no"}))

    assert result["error"]
    assert not target.exists()
    assert len(submitted) == 1
    assert submitted[0][1]["pattern_key"] == "plugin_rule:write_file"


@pytest.mark.parametrize("yolo_scope", ["process", "session"])
def test_ask_is_enforced_even_in_yolo(tmp_path, monkeypatch, yolo_scope):
    _mode(monkeypatch, "ask")
    monkeypatch.setattr(approval, "_YOLO_MODE_FROZEN", yolo_scope == "process")
    monkeypatch.setattr(
        approval, "is_current_session_yolo_enabled", lambda: yolo_scope == "session"
    )
    monkeypatch.setattr(approval, "_is_interactive_cli", lambda: True)
    monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
    prompts = []
    monkeypatch.setattr(
        approval,
        "prompt_dangerous_approval",
        lambda *args, **kwargs: prompts.append(args) or "deny",
    )
    target = tmp_path / "yolo.txt"

    result = _result(registry.dispatch("write_file", {"path": str(target), "content": "no"}))

    assert result["error"]
    assert not target.exists()
    assert len(prompts) == 1


def test_ask_without_human_fails_closed(tmp_path, monkeypatch):
    _mode(monkeypatch, "ask")
    monkeypatch.setattr(approval, "_is_interactive_cli", lambda: False)
    monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
    monkeypatch.setattr(approval, "env_var_enabled", lambda name: False)
    target = tmp_path / "headless.txt"

    result = _result(registry.dispatch("write_file", {"path": str(target), "content": "no"}))

    assert "no interactive user or gateway" in result["error"].lower()
    assert not target.exists()


def test_native_sensitive_path_guard_runs_before_approval(monkeypatch):
    _mode(monkeypatch, "ask")
    monkeypatch.setattr(
        approval, "request_tool_approval",
        lambda *args, **kwargs: pytest.fail("invalid write must not prompt"),
    )
    protected = "/etc/hermes-write-approval-test"

    result = _result(registry.dispatch("write_file", {"path": protected, "content": "no"}))

    assert "sensitive system path" in result["error"].lower()


def test_native_syntax_gate_runs_before_approval(tmp_path, monkeypatch):
    _mode(monkeypatch, "ask")
    monkeypatch.setattr(
        approval, "request_tool_approval",
        lambda *args, **kwargs: pytest.fail("invalid write must not prompt"),
    )
    target = tmp_path / "invalid.json"

    result = _result(registry.dispatch("write_file", {"path": str(target), "content": '{"broken":'}))

    assert "json" in result["error"].lower()
    assert not target.exists()


def test_invalid_explicit_mode_fails_closed(tmp_path, monkeypatch):
    _mode(monkeypatch, "deny")
    target = tmp_path / "invalid.txt"

    result = _result(registry.dispatch("write_file", {"path": str(target), "content": "ok"}))

    assert "invalid approvals.write_file" in result["error"].lower()
    assert not target.exists()


def test_config_read_exception_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: (_ for _ in ()).throw(OSError("unreadable config")),
    )
    target = tmp_path / "config-error.txt"

    result = _result(registry.dispatch("write_file", {"path": str(target), "content": "no"}))

    assert "could not resolve approvals.write_file" in result["error"].lower()
    assert not target.exists()


def test_profile_reload_replaces_permanent_write_grant(monkeypatch):
    monkeypatch.undo()
    approval._session_approved.clear()
    approval._permanent_approved.clear()
    configs = iter([
        {"command_allowlist": ["plugin_rule:write_file"]},
        {"command_allowlist": []},
    ])
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: next(configs))

    approval.load_permanent_allowlist()
    assert approval.is_approved("s", "plugin_rule:write_file")
    approval.load_permanent_allowlist()
    assert not approval.is_approved("s", "plugin_rule:write_file")


def test_profile_reload_config_failure_clears_prior_grant(monkeypatch):
    monkeypatch.undo()
    approval.load_permanent({"plugin_rule:write_file"})
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: (_ for _ in ()).throw(OSError("bad profile config")),
    )

    approval.load_permanent_allowlist()

    assert not approval.is_approved("s", "plugin_rule:write_file")


def test_plugin_approve_overlap_uses_one_shared_session_decision(tmp_path, monkeypatch):
    monkeypatch.undo()
    _mode(monkeypatch, "ask")
    monkeypatch.setattr(file_tools, "_check_file_reqs", lambda: True)
    monkeypatch.setattr(approval, "get_current_session_key", lambda default="default": "overlap")
    monkeypatch.setattr(approval, "_is_interactive_cli", lambda: True)
    monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
    approval._session_approved.clear()
    approval._permanent_approved.clear()
    prompts = []
    monkeypatch.setattr(
        approval, "prompt_dangerous_approval",
        lambda *args, **kwargs: prompts.append(args) or "session",
    )
    import hermes_cli.plugins as plugins
    monkeypatch.setattr(
        plugins, "_get_pre_tool_call_directive_details",
        lambda *args, **kwargs: plugins._PreToolCallDirective(
            action="approve", message="plugin wants confirmation", rule_key="plugin-key"
        ),
    )
    target = tmp_path / "overlap.txt"
    from model_tools import handle_function_call

    result = _result(handle_function_call("write_file", {"path": str(target), "content": "ok"}))

    assert "error" not in result
    assert target.read_text() == "ok"
    assert len(prompts) == 1


def test_plugin_approve_once_is_consumed_without_second_prompt(tmp_path, monkeypatch):
    """Once bridges only this dispatch; it is neither dropped nor persisted."""
    monkeypatch.undo()
    _mode(monkeypatch, "ask")
    monkeypatch.setattr(file_tools, "_check_file_reqs", lambda: True)
    monkeypatch.setattr(approval, "get_current_session_key", lambda default="default": "once")
    monkeypatch.setattr(approval, "_is_interactive_cli", lambda: True)
    monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
    approval._session_approved.clear()
    approval._permanent_approved.clear()
    prompts = []
    monkeypatch.setattr(
        approval, "prompt_dangerous_approval",
        lambda *args, **kwargs: prompts.append(args) or "once",
    )
    import hermes_cli.plugins as plugins
    monkeypatch.setattr(
        plugins, "_get_pre_tool_call_directive_details",
        lambda *args, **kwargs: plugins._PreToolCallDirective(
            action="approve", message="plugin wants confirmation", rule_key="plugin-key"
        ),
    )
    from model_tools import handle_function_call
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"

    assert "error" not in _result(handle_function_call("write_file", {"path": str(first), "content": "1"}))
    assert "error" not in _result(handle_function_call("write_file", {"path": str(second), "content": "2"}))

    assert len(prompts) == 2
    assert not approval.is_approved("once", "plugin_rule:write_file")


def test_plugin_once_capability_is_cleaned_when_downstream_guard_blocks(
    tmp_path, monkeypatch
):
    monkeypatch.undo()
    _mode(monkeypatch, "ask")
    monkeypatch.setattr(file_tools, "_check_file_reqs", lambda: True)
    calls = []
    monkeypatch.setattr(
        approval,
        "request_tool_approval",
        lambda *args, **kwargs: calls.append(args) or {
            "approved": True, "message": None, "approval_scope": "once"
        },
    )
    import hermes_cli.plugins as plugins
    monkeypatch.setattr(
        plugins,
        "_get_pre_tool_call_directive_details",
        lambda *args, **kwargs: plugins._PreToolCallDirective(
            action="approve", message="plugin wants confirmation", rule_key="plugin-key"
        ),
    )
    monkeypatch.setattr(
        "acp_adapter.edit_approval.maybe_require_edit_approval",
        lambda *args, **kwargs: "blocked after plugin",
    )
    from model_tools import handle_function_call
    args = {"path": str(tmp_path / "blocked.txt"), "content": "ok"}

    assert "blocked after plugin" in handle_function_call("write_file", args)
    assert len(calls) == 1
    monkeypatch.setattr(
        approval,
        "request_tool_approval",
        lambda *args, **kwargs: calls.append(args) or {
            "approved": False, "message": "denied"
        },
    )

    result = _result(registry.dispatch("write_file", args))
    assert result["error"]
    assert len(calls) == 2
    assert not (tmp_path / "blocked.txt").exists()


def test_plugin_once_capability_is_cleaned_when_acp_guard_raises_before_retry(
    tmp_path, monkeypatch
):
    """ACP exceptions cannot leave plugin once consent redeemable by a retry."""
    monkeypatch.undo()
    _mode(monkeypatch, "ask")
    monkeypatch.setattr(file_tools, "_check_file_reqs", lambda: True)
    calls = []
    monkeypatch.setattr(
        approval,
        "request_tool_approval",
        lambda *args, **kwargs: calls.append(args) or {
            "approved": True,
            "message": None,
            "approval_scope": "once",
        },
    )
    import hermes_cli.plugins as plugins
    monkeypatch.setattr(
        plugins,
        "_get_pre_tool_call_directive_details",
        lambda *args, **kwargs: plugins._PreToolCallDirective(
            action="approve", message="plugin wants confirmation", rule_key="plugin-key"
        ),
    )
    monkeypatch.setattr(
        "acp_adapter.edit_approval.maybe_require_edit_approval",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("hostile ACP failure")),
    )
    from model_tools import handle_function_call
    target = tmp_path / "acp-exception-retry.txt"
    args = {"path": str(target), "content": "must-not-write"}

    first = _result(handle_function_call("write_file", args))
    assert "approval guard failed" in first["error"].lower()
    assert len(calls) == 1
    assert not target.exists()

    monkeypatch.setattr(
        approval,
        "request_tool_approval",
        lambda *args, **kwargs: calls.append(args) or {
            "approved": False,
            "message": "denied on retry",
        },
    )

    retry = _result(registry.dispatch("write_file", args))
    assert retry["error"] == "denied on retry"
    assert len(calls) == 2
    assert not target.exists()


@pytest.mark.parametrize("acp_outcome", ["block", "raise"])
def test_handle_function_call_same_id_retry_cannot_replay_plugin_once_after_acp_failure(
    tmp_path, monkeypatch, acp_outcome
):
    """The public dispatch ID must identify both the grant and ACP cleanup."""
    monkeypatch.undo()
    _mode(monkeypatch, "ask")
    monkeypatch.setattr(file_tools, "_check_file_reqs", lambda: True)
    import hermes_cli.plugins as plugins
    plugin_requires_approval = True

    def plugin_directive(*args, **kwargs):
        if plugin_requires_approval:
            return plugins._PreToolCallDirective(
                action="approve", message="plugin confirmation", rule_key="plugin-key"
            )
        return plugins._PreToolCallDirective(action="allow", message=None, rule_key=None)

    monkeypatch.setattr(
        plugins, "_get_pre_tool_call_directive_details", plugin_directive
    )
    decisions = iter([
        {"approved": True, "message": None, "approval_scope": "once"},
        {"approved": False, "message": "denied on same-id retry"},
    ])
    prompts = []

    def decide(*args, **kwargs):
        prompts.append((args, kwargs))
        return next(decisions)

    monkeypatch.setattr(approval, "request_tool_approval", decide)
    acp_calls = 0

    def acp_guard(*args, **kwargs):
        nonlocal acp_calls
        acp_calls += 1
        if acp_calls > 1:
            return None
        if acp_outcome == "raise":
            raise RuntimeError("hostile ACP failure")
        return "blocked after plugin approval"

    monkeypatch.setattr(
        "acp_adapter.edit_approval.maybe_require_edit_approval", acp_guard
    )
    from model_tools import handle_function_call
    target = tmp_path / f"same-id-{acp_outcome}.txt"
    args = {"path": str(target), "content": "must-not-write"}
    tool_call_id = f"write-{acp_outcome}-1"

    first = handle_function_call("write_file", args, tool_call_id=tool_call_id)
    assert "blocked" in first.lower() or "approval guard failed" in first.lower()
    assert len(prompts) == 1
    assert not target.exists()

    # Remove the plugin escalation for the retry: the built-in gate itself now
    # denies. A stale once grant would skip that gate and mutate immediately.
    plugin_requires_approval = False
    retry = _result(
        handle_function_call("write_file", args, tool_call_id=tool_call_id)
    )
    assert retry["error"] == "denied on same-id retry"
    assert len(prompts) == 2
    assert acp_calls == 2
    assert not target.exists()


def test_once_capability_cannot_be_replayed_from_copied_or_parent_context():
    args = {"path": "/tmp/capability.txt", "content": "ok"}
    entry = registry.get_entry("write_file")
    reset_token = file_tools.grant_file_mutation_once_capability(entry, "write_file", args)
    sibling = copy_context()
    try:
        assert file_tools._consume_file_mutation_once_capability(entry, "write_file", args)
        assert not sibling.run(
            file_tools._consume_file_mutation_once_capability, entry, "write_file", args
        )
        assert not file_tools._consume_file_mutation_once_capability(entry, "write_file", args)
    finally:
        file_tools.reset_file_mutation_once_capability(reset_token)


def test_once_capability_is_bound_to_exact_entry_tool_and_arguments():
    args = {"path": "/tmp/capability.txt", "content": "ok"}
    entry = registry.get_entry("write_file")
    reset_token = file_tools.grant_file_mutation_once_capability(entry, "write_file", args)
    try:
        assert not file_tools._consume_file_mutation_once_capability(
            registry.get_entry("patch"), "write_file", args
        )
        assert not file_tools._consume_file_mutation_once_capability(entry, "patch", args)
        assert not file_tools._consume_file_mutation_once_capability(
            entry, "write_file", dict(args)
        )
        args["content"] = "mutated"
        assert not file_tools._consume_file_mutation_once_capability(entry, "write_file", args)
    finally:
        file_tools.reset_file_mutation_once_capability(reset_token)


def test_same_named_custom_registry_tool_never_uses_builtin_gate(monkeypatch):
    from tools.registry import ToolRegistry
    custom = ToolRegistry()
    custom.register(
        name="write_file", toolset="custom", schema={},
        handler=lambda args, **kwargs: "custom-ok",
    )
    _mode(monkeypatch, "ask")
    monkeypatch.setattr(
        approval, "request_tool_approval",
        lambda *args, **kwargs: pytest.fail("custom tool is not the core mutation tool"),
    )

    assert custom.dispatch("write_file", {}) == "custom-ok"


def test_symlink_swap_during_write_approval_is_revalidated(tmp_path, monkeypatch):
    _mode(monkeypatch, "ask")
    safe = tmp_path / "safe.txt"
    sensitive = tmp_path / "sensitive.txt"
    safe.write_text("safe")
    sensitive.write_text("secret")
    link = tmp_path / "target.txt"
    link.symlink_to(safe)

    def approve_and_swap(*args, **kwargs):
        link.unlink()
        link.symlink_to(sensitive)
        return {"approved": True, "message": None}

    monkeypatch.setattr(approval, "request_tool_approval", approve_and_swap)
    real_sensitive = file_tools._check_sensitive_path
    monkeypatch.setattr(
        file_tools, "_check_sensitive_path",
        lambda path, task_id="default": (
            "Refusing swapped sensitive target"
            if os.path.realpath(path) == str(sensitive)
            else real_sensitive(path, task_id)
        ),
    )

    result = _result(registry.dispatch("write_file", {"path": str(link), "content": "changed"}))

    assert "swapped sensitive" in result["error"].lower()
    assert sensitive.read_text() == "secret"


def test_v4a_uses_canonical_target_when_symlink_swaps_at_apply_boundary(
    tmp_path, monkeypatch
):
    _mode(monkeypatch, "allow")
    safe = tmp_path / "safe.txt"
    sensitive = tmp_path / "sensitive.txt"
    safe.write_text("old")
    sensitive.write_text("old")
    link = tmp_path / "target.txt"
    link.symlink_to(safe)
    patch_body = (
        "*** Begin Patch\n"
        f"*** Update File: {link}\n"
        "@@\n-old\n+new\n"
        "*** End Patch"
    )
    from tools.file_operations import ShellFileOperations
    real_apply = ShellFileOperations.patch_v4a_operations

    def swap_then_apply(self, operations):
        link.unlink()
        link.symlink_to(sensitive)
        return real_apply(self, operations)

    monkeypatch.setattr(ShellFileOperations, "patch_v4a_operations", swap_then_apply)

    result = _result(registry.dispatch("patch", {"mode": "patch", "patch": patch_body}))

    assert "error" not in result
    assert safe.read_text() == "new"
    assert sensitive.read_text() == "old"


def test_real_cli_rejects_invalid_write_approval_mode(tmp_path):
    env = os.environ.copy()
    env["HERMES_HOME"] = str(tmp_path)
    repo = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    proc = subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "config", "set", "approvals.write_file", "deny"],
        cwd=repo, env=env, text=True, capture_output=True,
    )

    assert proc.returncode != 0
    assert "allow" in (proc.stdout + proc.stderr)
    assert not (tmp_path / "config.yaml").exists()


def test_permanent_grants_are_concurrent_hermes_home_scoped(tmp_path, monkeypatch):
    monkeypatch.undo()
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    barrier = threading.Barrier(2)
    results = {}
    home_a, home_b = tmp_path / "a", tmp_path / "b"

    def worker(name, home, grant):
        token = set_hermes_home_override(home)
        try:
            approval.load_permanent({"plugin_rule:write_file"} if grant else set())
            barrier.wait()
            results[name] = approval.is_approved("shared-session", "plugin_rule:write_file")
        finally:
            reset_hermes_home_override(token)

    a = threading.Thread(target=worker, args=("a", home_a, True))
    b = threading.Thread(target=worker, args=("b", home_b, False))
    a.start()
    b.start()
    a.join()
    b.join()

    assert results == {"a": True, "b": False}


def test_always_is_shared_across_write_file_and_patch(tmp_path, monkeypatch):
    monkeypatch.undo()
    _mode(monkeypatch, "ask")
    monkeypatch.setattr(file_tools, "_check_file_reqs", lambda: True)
    monkeypatch.setattr(approval, "get_current_session_key", lambda default="default": "shared")
    monkeypatch.setattr(approval, "_is_interactive_cli", lambda: True)
    monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
    monkeypatch.setattr(approval, "save_permanent_allowlist", lambda patterns: None)
    approval._session_approved.clear()
    approval._permanent_approved.clear()
    prompts = []
    monkeypatch.setattr(
        approval, "prompt_dangerous_approval",
        lambda *args, **kwargs: prompts.append(args) or "always",
    )
    target = tmp_path / "shared.txt"

    assert "error" not in _result(registry.dispatch("write_file", {"path": str(target), "content": "old"}))
    assert "error" not in _result(registry.dispatch("patch", {
        "mode": "replace", "path": str(target), "old_string": "old", "new_string": "new",
    }))

    assert target.read_text() == "new"
    assert len(prompts) == 1


def _write_agent(monkeypatch):
    """Build the real AIAgent executor with only the file tool exposed."""
    from run_agent import AIAgent

    tool_defs = [{
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "write",
            "parameters": {"type": "object", "properties": {}},
        },
    }]
    with (
        mock_patch("run_agent.get_tool_definitions", return_value=tool_defs),
        mock_patch("run_agent.check_toolset_requirements", return_value={}),
        mock_patch(
            "hermes_cli.config.load_config",
            return_value={"approvals": {"write_file": "ask"}},
        ),
        mock_patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    agent.client = MagicMock()
    setattr(agent, "_cached_system_prompt", "test")
    setattr(agent, "_use_prompt_caching", False)
    return agent


def _write_call(call_id, path, content="ok"):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(
            name="write_file",
            arguments=json.dumps({"path": str(path), "content": content}),
        ),
    )


def _approve_plugin(monkeypatch):
    import hermes_cli.plugins as plugins

    monkeypatch.setattr(
        plugins,
        "_get_pre_tool_call_directive_details",
        lambda *args, **kwargs: plugins._PreToolCallDirective(
            action="approve", message="plugin confirmation", rule_key="plugin-key"
        ),
    )


def test_real_concurrent_agent_two_writes_prompt_once_per_call(tmp_path, monkeypatch):
    """Batch pre-gates must not overwrite the first call's one-shot grant."""
    monkeypatch.undo()
    monkeypatch.setattr(file_tools, "_check_file_reqs", lambda: True)
    _approve_plugin(monkeypatch)
    prompts = []
    monkeypatch.setattr(
        approval,
        "request_tool_approval",
        lambda *args, **kwargs: prompts.append((args, kwargs)) or {
            "approved": True, "message": None, "approval_scope": "once"
        },
    )
    agent = _write_agent(monkeypatch)
    _mode(monkeypatch, "ask")
    first, second = tmp_path / "first.txt", tmp_path / "second.txt"
    message = SimpleNamespace(
        content="",
        tool_calls=[
            _write_call("write-1", first, "one"),
            _write_call("write-2", second, "two"),
        ],
    )
    results = []

    agent._execute_tool_calls_concurrent(message, results, "task-write")

    assert first.read_text() == "one"
    assert second.read_text() == "two"
    assert len(prompts) == 2
    assert [item["tool_call_id"] for item in results] == ["write-1", "write-2"]


def test_real_sequential_guardrail_block_revokes_call_grant_before_retry(
    tmp_path, monkeypatch
):
    """A pre-gate approval cannot survive a downstream guardrail block."""
    monkeypatch.undo()
    monkeypatch.setattr(file_tools, "_check_file_reqs", lambda: True)
    _approve_plugin(monkeypatch)
    prompts = []
    monkeypatch.setattr(
        approval,
        "request_tool_approval",
        lambda *args, **kwargs: prompts.append((args, kwargs)) or {
            "approved": True, "message": None, "approval_scope": "once"
        },
    )
    agent = _write_agent(monkeypatch)
    _mode(monkeypatch, "ask")
    target = tmp_path / "retry.txt"
    call = _write_call("blocked-write", target)
    message = SimpleNamespace(content="", tool_calls=[call])
    blocked_results = []
    guardrails = getattr(agent, "_tool_guardrails")
    guardrails.before_call = lambda *args, **kwargs: SimpleNamespace(
        allows_execution=False,
        message="blocked downstream",
        action="block",
        code="test",
    )
    agent._guardrail_block_result = lambda decision: json.dumps({"error": decision.message})

    agent._execute_tool_calls_sequential(message, blocked_results, "task-write")
    assert not target.exists()
    assert len(prompts) == 1

    guardrails.before_call = lambda *args, **kwargs: SimpleNamespace(
        allows_execution=True,
        message=None,
        action="allow",
        code=None,
    )
    retry_results = []
    agent._execute_tool_calls_sequential(message, retry_results, "task-write")

    assert target.read_text() == "ok"
    assert len(prompts) == 2
