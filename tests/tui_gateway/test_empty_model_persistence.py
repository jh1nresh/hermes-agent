"""Unresolved model config must not become a sticky placeholder."""

from unittest.mock import patch

import tui_gateway.server as server


def test_resolve_model_returns_empty_without_user_selection():
    with (
        patch.dict("os.environ", {}, clear=True),
        patch.object(server, "_load_cfg", return_value={"model": {"provider": "custom"}}),
    ):
        assert server._resolve_model() == ""


def test_resolve_model_uses_corrected_config_on_next_read():
    configs = iter(
        [
            {"model": {"provider": "custom"}},
            {"model": {"provider": "custom", "default": "real-model"}},
        ]
    )
    with (
        patch.dict("os.environ", {}, clear=True),
        patch.object(server, "_load_cfg", side_effect=lambda: next(configs)),
    ):
        assert server._resolve_model() == ""
        assert server._resolve_model() == "real-model"