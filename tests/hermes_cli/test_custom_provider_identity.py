"""Custom provider entry identity lookup tests."""

import hermes_cli.runtime_provider as rp


def test_find_custom_provider_identity_matches_legacy_custom_providers_list(monkeypatch):
    monkeypatch.setattr(
        rp,
        "load_config",
        lambda: {
            "custom_providers": [
                {"name": "MiMo v2.5 Pro", "base_url": "https://api.mimo.example/v1"}
            ]
        },
    )

    assert (
        rp.find_custom_provider_identity("https://api.mimo.example/v1/")
        == "custom:mimo-v2.5-pro"
    )


def test_find_custom_provider_identity_matches_providers_dict_key(monkeypatch):
    monkeypatch.setattr(
        rp,
        "load_config",
        lambda: {"providers": {"local gpu": {"api": "http://127.0.0.1:8000/v1"}}},
    )

    assert rp.find_custom_provider_identity("HTTP://127.0.0.1:8000/v1") == "custom:local-gpu"


def test_find_custom_provider_identity_matches_providers_dict_url_alias(monkeypatch):
    monkeypatch.setattr(
        rp,
        "load_config",
        lambda: {"providers": {"proxy": {"url": "https://proxy.example/anthropic"}}},
    )

    assert rp.find_custom_provider_identity("https://proxy.example/anthropic") == "custom:proxy"


def test_find_custom_provider_identity_returns_none_for_unknown_url(monkeypatch):
    monkeypatch.setattr(
        rp,
        "load_config",
        lambda: {
            "custom_providers": [
                {"name": "known", "base_url": "https://known.example/v1"}
            ]
        },
    )

    assert rp.find_custom_provider_identity("https://other.example/v1") is None


def test_find_custom_provider_identity_ignores_bad_config(monkeypatch):
    monkeypatch.setattr(rp, "load_config", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    assert rp.find_custom_provider_identity("https://known.example/v1") is None
