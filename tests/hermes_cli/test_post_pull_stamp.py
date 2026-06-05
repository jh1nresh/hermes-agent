"""Tests for the post-pull stamp gate.

After ``hermes update``'s post-pull phase, the install dir's current commit
is written to a stamp file. Every launch (CLI/TUI/gateway) routed through
``main()`` calls ``_ensure_post_pull_current`` to confirm the stamp matches
the live commit; if not, it runs post-pull and exits asking for a re-run.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_cli import main as hm


@pytest.fixture
def stamp_dir(tmp_path):
    """Point PROJECT_ROOT (and thus the stamp path) at a temp dir."""
    with patch.object(hm, "PROJECT_ROOT", tmp_path):
        yield tmp_path


# ---------------------------------------------------------------------------
# stamp read/write round-trip
# ---------------------------------------------------------------------------
def test_write_then_read_round_trip(stamp_dir):
    assert hm._write_post_pull_stamp("abc123") is True
    assert hm._read_post_pull_stamp() == "abc123"
    assert (stamp_dir / ".post_pull_stamp").read_text().strip() == "abc123"


def test_read_missing_stamp_returns_none(stamp_dir):
    assert hm._read_post_pull_stamp() is None


def test_write_default_uses_current_commit(stamp_dir):
    with patch.object(hm, "_current_install_commit", return_value="deadbeef"):
        assert hm._write_post_pull_stamp() is True
    assert hm._read_post_pull_stamp() == "deadbeef"


def test_write_no_commit_resolvable_is_noop(stamp_dir):
    with patch.object(hm, "_current_install_commit", return_value=None):
        assert hm._write_post_pull_stamp() is False
    assert hm._read_post_pull_stamp() is None


# ---------------------------------------------------------------------------
# is-current logic
# ---------------------------------------------------------------------------
def test_is_current_true_when_stamp_matches(stamp_dir):
    with patch.object(hm, "_current_install_commit", return_value="sha1"):
        hm._write_post_pull_stamp("sha1")
        assert hm._post_pull_stamp_is_current() is True


def test_is_current_false_when_stamp_stale(stamp_dir):
    hm._write_post_pull_stamp("old_sha")
    with patch.object(hm, "_current_install_commit", return_value="new_sha"):
        assert hm._post_pull_stamp_is_current() is False


def test_is_current_false_when_stamp_missing(stamp_dir):
    with patch.object(hm, "_current_install_commit", return_value="any_sha"):
        assert hm._post_pull_stamp_is_current() is False


def test_is_current_true_when_commit_unresolvable(stamp_dir):
    # Can't determine the live commit (e.g. not a git checkout) -> never block.
    with patch.object(hm, "_current_install_commit", return_value=None):
        assert hm._post_pull_stamp_is_current() is True


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------
def test_gate_skips_update_command(stamp_dir):
    """The updater owns the stamp; gating it would recurse."""
    args = SimpleNamespace(command="update")
    with patch.object(hm, "_cmd_update_post_pull") as post_pull:
        hm._ensure_post_pull_current(args)  # must not raise/exit
    post_pull.assert_not_called()


def test_gate_skips_non_git_install(stamp_dir):
    args = SimpleNamespace(command="chat")
    with patch.object(hm, "_is_git_source_install", return_value=False), \
         patch.object(hm, "_cmd_update_post_pull") as post_pull:
        hm._ensure_post_pull_current(args)
    post_pull.assert_not_called()


def test_gate_skips_via_env_escape_hatch(stamp_dir, monkeypatch):
    monkeypatch.setenv("HERMES_SKIP_POST_PULL_GATE", "1")
    args = SimpleNamespace(command="chat")
    with patch.object(hm, "_is_git_source_install", return_value=True), \
         patch.object(hm, "_cmd_update_post_pull") as post_pull:
        hm._ensure_post_pull_current(args)
    post_pull.assert_not_called()


def test_gate_noop_when_stamp_current(stamp_dir):
    args = SimpleNamespace(command="chat")
    with patch.object(hm, "_is_git_source_install", return_value=True), \
         patch.object(hm, "_post_pull_stamp_is_current", return_value=True), \
         patch.object(hm, "_cmd_update_post_pull") as post_pull:
        hm._ensure_post_pull_current(args)
    post_pull.assert_not_called()


def test_gate_runs_post_pull_and_exits_when_stale(stamp_dir):
    args = SimpleNamespace(command="chat")
    with patch.object(hm, "_is_git_source_install", return_value=True), \
         patch.object(hm, "_post_pull_stamp_is_current", return_value=False), \
         patch.object(hm, "_cmd_update_post_pull") as post_pull, \
         patch.object(hm, "_write_post_pull_stamp") as write_stamp:
        with pytest.raises(SystemExit) as exc:
            hm._ensure_post_pull_current(args)
    assert exc.value.code == 0
    post_pull.assert_called_once()
    # post_pull is called with assume_yes=True and gateway_mode=False
    called_args = post_pull.call_args
    assert called_args.kwargs.get("gateway_mode") is False
    assert getattr(called_args.args[0], "yes") is True
    write_stamp.assert_called_once()


def test_gate_post_pull_systemexit_propagates(stamp_dir):
    """A fatal precondition in post-pull (e.g. bad Python) must not be swallowed."""
    args = SimpleNamespace(command="chat")
    with patch.object(hm, "_is_git_source_install", return_value=True), \
         patch.object(hm, "_post_pull_stamp_is_current", return_value=False), \
         patch.object(hm, "_cmd_update_post_pull", side_effect=SystemExit(1)):
        with pytest.raises(SystemExit) as exc:
            hm._ensure_post_pull_current(args)
    assert exc.value.code == 1


def test_gate_post_pull_exception_exits_nonzero(stamp_dir):
    args = SimpleNamespace(command="chat")
    with patch.object(hm, "_is_git_source_install", return_value=True), \
         patch.object(hm, "_post_pull_stamp_is_current", return_value=False), \
         patch.object(hm, "_cmd_update_post_pull", side_effect=RuntimeError("boom")):
        with pytest.raises(SystemExit) as exc:
            hm._ensure_post_pull_current(args)
    assert exc.value.code == 1


def test_gate_missing_command_attr_treated_as_non_update(stamp_dir):
    """args without a .command attr (some launch paths) must still gate."""
    args = SimpleNamespace()  # no .command
    with patch.object(hm, "_is_git_source_install", return_value=True), \
         patch.object(hm, "_post_pull_stamp_is_current", return_value=True), \
         patch.object(hm, "_cmd_update_post_pull") as post_pull:
        hm._ensure_post_pull_current(args)  # must not raise
    post_pull.assert_not_called()
