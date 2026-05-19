"""Regression tests for GitHub #8430.

The 64K minimum-context-length gate in ``agent.agent_init`` used to fire
even when the user had explicitly set ``model.context_length`` in
``config.yaml``, contradicting its own error message ("…or set
model.context_length in config.yaml to override"). These tests pin the
fix: the gate now skips when ``agent._config_context_length`` is not
None.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent import agent_init
from agent.model_metadata import MINIMUM_CONTEXT_LENGTH


def _make_minimal_agent(context_length: int, config_context_length=None):
    """Build the bare-minimum agent shape the minimum-context gate inspects."""
    return SimpleNamespace(
        model="qwen3-235b-a22b",
        context_compressor=SimpleNamespace(context_length=context_length),
        _config_context_length=config_context_length,
    )


def _run_gate(agent) -> None:
    """Inline the agent_init gate exactly as it exists on main."""
    _ctx = getattr(agent.context_compressor, "context_length", 0)
    _user_override = getattr(agent, "_config_context_length", None) is not None
    if _ctx and _ctx < MINIMUM_CONTEXT_LENGTH and not _user_override:
        raise ValueError(
            f"Model {agent.model} has a context window of {_ctx:,} tokens, "
            f"which is below the minimum {MINIMUM_CONTEXT_LENGTH:,} required "
            f"by Hermes Agent.  Choose a model with at least "
            f"{MINIMUM_CONTEXT_LENGTH // 1000}K context, or set "
            f"model.context_length in config.yaml to override."
        )


def test_below_minimum_no_override_raises():
    """No user override → 32K model is rejected."""
    agent = _make_minimal_agent(context_length=32_768, config_context_length=None)
    with pytest.raises(ValueError) as exc_info:
        _run_gate(agent)
    err = str(exc_info.value)
    assert "32,768" in err
    assert "below the minimum" in err


def test_below_minimum_with_explicit_override_is_honoured():
    """#8430: user explicitly set model.context_length=32768 → respect it."""
    agent = _make_minimal_agent(context_length=32_768, config_context_length=32_768)
    # Must not raise.
    _run_gate(agent)


def test_below_minimum_with_smaller_override_is_honoured():
    """User can dial it below the model's actual ctx if they want (their funeral)."""
    agent = _make_minimal_agent(context_length=16_000, config_context_length=16_000)
    _run_gate(agent)


def test_at_or_above_minimum_no_override_passes():
    """Models meeting the minimum pass without an override."""
    agent = _make_minimal_agent(context_length=128_000, config_context_length=None)
    _run_gate(agent)


def test_zero_context_length_skips_gate():
    """A zero/unset context_length doesn't trip the gate (existing behaviour)."""
    agent = _make_minimal_agent(context_length=0, config_context_length=None)
    _run_gate(agent)


def test_gate_lives_at_expected_location():
    """Smoke test: the production gate string is still where we expect it.

    Catches accidental refactors that remove the override path entirely.
    """
    import inspect
    src = inspect.getsource(agent_init)
    assert "_config_context_length" in src
    assert "MINIMUM_CONTEXT_LENGTH" in src
    # The override guard must appear in the same conditional as MINIMUM_CONTEXT_LENGTH.
    assert "not _user_override" in src or "_config_context_length is None" in src
