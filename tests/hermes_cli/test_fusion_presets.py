from hermes_cli.fusion_presets import (
    append_fusion_model_ids,
    append_fusion_model_tuples,
    fusion_model_slug,
    fusion_request_overrides_for_model,
    normalize_fusion_presets,
)


def _cfg():
    return {
        "fusion": {
            "presets": [
                {
                    "provider": "openrouter",
                    "slug": "Research Panel",
                    "analysis_models": [
                        "anthropic/claude-opus-4.8",
                        "openai/gpt-5.5",
                    ],
                    "judge_model": "google/gemini-3.5-flash",
                    "max_tool_calls": 6,
                    "max_completion_tokens": 4096,
                    "temperature": 0.3,
                },
                {
                    "provider": "nous",
                    "slug": "portal-panel",
                    "analysis_models": ["anthropic/claude-sonnet-4.6"],
                },
            ]
        }
    }


def test_normalize_fusion_presets_creates_model_slugs():
    presets = normalize_fusion_presets(_cfg()["fusion"])

    assert presets[0]["slug"] == "research-panel"
    assert presets[0]["model_slug"] == "fusion/research-panel"
    assert presets[1]["provider"] == "nous"
    assert presets[1]["model_slug"] == "fusion/portal-panel"


def test_append_fusion_models_is_provider_scoped():
    assert append_fusion_model_ids("openrouter", ["openrouter/fusion"], _cfg()) == [
        "openrouter/fusion",
        "fusion/research-panel",
    ]
    assert append_fusion_model_ids("nous", ["anthropic/claude-opus-4.8"], _cfg()) == [
        "anthropic/claude-opus-4.8",
        "fusion/portal-panel",
    ]
    assert append_fusion_model_tuples("openrouter", [], _cfg()) == [
        ("fusion/research-panel", "custom Fusion preset")
    ]


def test_fusion_request_overrides_maps_custom_slug_to_router_tool():
    overrides = fusion_request_overrides_for_model("openrouter", "fusion/research-panel", _cfg())

    assert overrides["model"] == "openrouter/fusion"
    assert overrides["tool_choice"] == "required"
    assert overrides["tools"] == [
        {
            "type": "openrouter:fusion",
            "parameters": {
                "analysis_models": ["anthropic/claude-opus-4.8", "openai/gpt-5.5"],
                "model": "google/gemini-3.5-flash",
                "max_tool_calls": 6,
                "max_completion_tokens": 4096,
                "temperature": 0.3,
            },
        }
    ]


def test_fusion_model_slug_sanitizes_names():
    assert fusion_model_slug("Research Panel!") == "fusion/research-panel"
