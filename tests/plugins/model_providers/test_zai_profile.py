"""Unit tests for Z.AI / GLM reasoning-control wiring.

GLM-5.2 on Z.AI's OpenAI-compatible ``/api/paas/v4`` endpoint exposes a
native ``reasoning_effort`` knob with two enabled levels (high / max).
"""

from __future__ import annotations

import pytest


@pytest.fixture
def zai_profile():
    """Resolve the registered Z.AI provider profile."""
    import model_tools  # noqa: F401
    import providers

    profile = providers.get_provider_profile("zai")
    assert profile is not None, "zai provider profile must be registered"
    return profile


class TestZaiGLM52Reasoning:
    def test_high_maps_to_high(self, zai_profile):
        extra_body, top_level = zai_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": "high"},
            model="glm-5.2",
        )
        assert extra_body == {}
        assert top_level == {"reasoning_effort": "high"}

    def test_low_and_medium_clamp_up_to_high(self, zai_profile):
        for effort in ("low", "medium", "minimal"):
            extra_body, top_level = zai_profile.build_api_kwargs_extras(
                reasoning_config={"enabled": True, "effort": effort},
                model="glm-5.2",
            )
            assert extra_body == {}
            assert top_level == {"reasoning_effort": "high"}

    @pytest.mark.parametrize("effort", ["xhigh", "max"])
    def test_strong_efforts_map_to_max(self, zai_profile, effort):
        extra_body, top_level = zai_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": effort},
            model="z-ai/glm-5.2",
        )
        assert extra_body == {}
        assert top_level == {"reasoning_effort": "max"}

    def test_disabled_leaves_server_default(self, zai_profile):
        extra_body, top_level = zai_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": False, "effort": "high"},
            model="glm-5.2",
        )
        assert extra_body == {}
        assert top_level == {}

    def test_no_config_leaves_server_default(self, zai_profile):
        extra_body, top_level = zai_profile.build_api_kwargs_extras(
            reasoning_config=None,
            model="glm-5.2",
        )
        assert extra_body == {}
        assert top_level == {}

    def test_no_effort_leaves_server_default(self, zai_profile):
        extra_body, top_level = zai_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True},
            model="glm-5.2",
        )
        assert extra_body == {}
        assert top_level == {}

    @pytest.mark.parametrize(
        "model",
        ["glm-5-2", "glm-5p2", "accounts/fireworks/models/glm-5p2", "zai-org-glm-5-2"],
    )
    def test_alias_spellings_recognized(self, zai_profile, model):
        extra_body, top_level = zai_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": "max"},
            model=model,
        )
        assert top_level == {"reasoning_effort": "max"}


class TestZaiModelGating:
    @pytest.mark.parametrize(
        "model",
        ["glm-5.1", "glm-5", "glm-4.7", "glm-4-9b", "", None],
    )
    def test_non_glm_5_2_models_emit_nothing(self, zai_profile, model):
        extra_body, top_level = zai_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": "high"},
            model=model,
        )
        assert extra_body == {}
        assert top_level == {}


class TestZaiFullKwargsIntegration:
    def test_glm_5_2_reaches_top_level(self, zai_profile):
        from agent.transports.chat_completions import ChatCompletionsTransport

        kwargs = ChatCompletionsTransport().build_kwargs(
            model="glm-5.2",
            messages=[{"role": "user", "content": "ping"}],
            tools=None,
            provider_profile=zai_profile,
            reasoning_config={"enabled": True, "effort": "max"},
            base_url="https://api.z.ai/api/paas/v4",
        )
        assert kwargs["reasoning_effort"] == "max"
        assert "extra_body" not in kwargs
