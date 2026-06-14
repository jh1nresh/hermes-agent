from hermes_cli.moa_config import (
    DEFAULT_MOA_AGGREGATOR,
    DEFAULT_MOA_REFERENCE_MODELS,
    build_moa_turn_prompt,
    decode_moa_turn,
    normalize_moa_config,
)


def test_normalize_moa_config_uses_provider_model_slots():
    cfg = normalize_moa_config({})

    assert cfg["reference_models"] == DEFAULT_MOA_REFERENCE_MODELS
    assert cfg["aggregator"] == DEFAULT_MOA_AGGREGATOR
    assert all(set(slot) == {"provider", "model"} for slot in cfg["reference_models"])
    assert set(cfg["aggregator"]) == {"provider", "model"}


def test_normalize_moa_config_drops_incomplete_slots_and_keeps_valid_ones():
    cfg = normalize_moa_config(
        {
            "reference_models": [
                {"provider": "openai-codex", "model": "gpt-5.5"},
                {"provider": "openrouter", "model": ""},
                {"provider": "", "model": "anthropic/claude-opus-4.8"},
            ],
            "aggregator": {"provider": "openrouter", "model": "anthropic/claude-opus-4.8"},
        }
    )

    assert cfg["reference_models"] == [{"provider": "openai-codex", "model": "gpt-5.5"}]
    assert cfg["aggregator"] == {"provider": "openrouter", "model": "anthropic/claude-opus-4.8"}


def test_normalize_moa_config_accepts_openrouter_fusion_model_slug():
    cfg = normalize_moa_config(
        {
            "reference_models": [
                {"provider": "openrouter", "model": "openrouter/fusion"},
            ],
            "aggregator": {"provider": "openrouter", "model": "openrouter/fusion"},
        }
    )

    assert cfg["reference_models"] == [
        {"provider": "openrouter", "model": "openrouter/fusion"}
    ]
    assert cfg["aggregator"] == {"provider": "openrouter", "model": "openrouter/fusion"}


def test_build_moa_turn_prompt_keeps_user_prompt_and_marks_mode():
    prompt = build_moa_turn_prompt("write a file then inspect it")

    decoded_prompt, cfg = decode_moa_turn(prompt)
    assert decoded_prompt == "write a file then inspect it"
    assert cfg is not None
    assert cfg["reference_models"] == DEFAULT_MOA_REFERENCE_MODELS
