"""OpenRouter/Nous Fusion preset configuration helpers."""

from __future__ import annotations

import re
from typing import Any

FUSION_MODEL_SLUG = "openrouter/fusion"
_FUSION_PREFIX = "fusion/"
_SUPPORTED_PROVIDERS = {"openrouter", "nous"}
_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def _slugify(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw.startswith(_FUSION_PREFIX):
        raw = raw[len(_FUSION_PREFIX):]
    raw = _SLUG_RE.sub("-", raw).strip("-._")
    return raw or "custom"


def fusion_model_slug(slug: Any) -> str:
    """Return the user-facing model slug for a Fusion preset."""
    return f"{_FUSION_PREFIX}{_slugify(slug)}"


def _clean_model(value: Any) -> str:
    return str(value or "").strip()


def _clean_models(values: Any) -> list[str]:
    if isinstance(values, str):
        raw = [part.strip() for part in values.split(",")]
    elif isinstance(values, (list, tuple)):
        raw = [_clean_model(item) for item in values]
    else:
        raw = []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
        if len(out) >= 8:
            break
    return out


def normalize_fusion_preset(raw: Any) -> dict[str, Any] | None:
    """Normalize one Fusion preset from config/dashboard input.

    Config shape:

        provider: openrouter | nous
        slug: research
        analysis_models: [anthropic/..., openai/...]
        judge_model: openai/...        # optional
        max_tool_calls: 8              # optional, 1..16
        max_completion_tokens: 4096    # optional
        reasoning: {...}               # optional
        temperature: 0.4               # optional, 0..2
    """
    if not isinstance(raw, dict):
        return None
    provider = str(raw.get("provider") or "openrouter").strip().lower()
    if provider not in _SUPPORTED_PROVIDERS:
        return None
    slug = _slugify(raw.get("slug") or raw.get("name") or "custom")
    analysis_models = _clean_models(raw.get("analysis_models") or raw.get("models"))
    if not analysis_models:
        return None

    preset: dict[str, Any] = {
        "provider": provider,
        "slug": slug,
        "model_slug": fusion_model_slug(slug),
        "analysis_models": analysis_models,
    }
    judge_model = _clean_model(raw.get("judge_model") or raw.get("model"))
    if judge_model:
        preset["judge_model"] = judge_model

    max_tool_calls = raw.get("max_tool_calls")
    try:
        mtc = int(max_tool_calls) if max_tool_calls is not None else None
    except (TypeError, ValueError):
        mtc = None
    if mtc is not None and 1 <= mtc <= 16:
        preset["max_tool_calls"] = mtc

    max_completion_tokens = raw.get("max_completion_tokens")
    try:
        mct = int(max_completion_tokens) if max_completion_tokens is not None else None
    except (TypeError, ValueError):
        mct = None
    if mct is not None and mct > 0:
        preset["max_completion_tokens"] = mct

    reasoning = raw.get("reasoning")
    if isinstance(reasoning, dict) and reasoning:
        preset["reasoning"] = dict(reasoning)

    temperature = raw.get("temperature")
    try:
        temp = float(temperature) if temperature is not None else None
    except (TypeError, ValueError):
        temp = None
    if temp is not None and 0.0 <= temp <= 2.0:
        preset["temperature"] = temp

    return preset


def normalize_fusion_presets(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("presets") or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        preset = normalize_fusion_preset(item)
        if not preset:
            continue
        key = (preset["provider"], preset["slug"])
        if key in seen:
            continue
        seen.add(key)
        out.append(preset)
    return out


def get_fusion_presets(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if config is None:
        try:
            from hermes_cli.config import load_config
            config = load_config()
        except Exception:
            config = {}
    raw = (config or {}).get("fusion")
    return normalize_fusion_presets(raw)


def fusion_model_slugs_for_provider(provider: str, config: dict[str, Any] | None = None) -> list[str]:
    provider_norm = str(provider or "").strip().lower()
    return [
        preset["model_slug"]
        for preset in get_fusion_presets(config)
        if preset.get("provider") == provider_norm
    ]


def append_fusion_model_ids(
    provider: str,
    models: list[str],
    config: dict[str, Any] | None = None,
) -> list[str]:
    seen = {str(model).lower() for model in models}
    out = list(models)
    for slug in fusion_model_slugs_for_provider(provider, config):
        if slug.lower() not in seen:
            out.append(slug)
            seen.add(slug.lower())
    return out


def append_fusion_model_tuples(
    provider: str,
    models: list[tuple[str, str]],
    config: dict[str, Any] | None = None,
) -> list[tuple[str, str]]:
    seen = {str(model).lower() for model, _ in models}
    out = list(models)
    for slug in fusion_model_slugs_for_provider(provider, config):
        if slug.lower() not in seen:
            out.append((slug, "custom Fusion preset"))
            seen.add(slug.lower())
    return out


def resolve_fusion_preset(
    provider: str,
    model: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    provider_norm = str(provider or "").strip().lower()
    model_norm = str(model or "").strip().lower()
    if provider_norm not in _SUPPORTED_PROVIDERS or not model_norm.startswith(_FUSION_PREFIX):
        return None
    for preset in get_fusion_presets(config):
        if preset.get("provider") == provider_norm and preset.get("model_slug", "").lower() == model_norm:
            return dict(preset)
    return None


def fusion_tool_from_preset(preset: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "analysis_models": list(preset.get("analysis_models") or []),
    }
    if preset.get("judge_model"):
        params["model"] = preset["judge_model"]
    for key in ("max_tool_calls", "max_completion_tokens", "reasoning", "temperature"):
        if key in preset:
            params[key] = preset[key]
    return {"type": "openrouter:fusion", "parameters": params}


def fusion_request_overrides_for_model(
    provider: str,
    model: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return top-level Chat Completions kwargs for a custom Fusion slug."""
    preset = resolve_fusion_preset(provider, model, config)
    if not preset:
        return None
    return {
        "model": FUSION_MODEL_SLUG,
        "tools": [fusion_tool_from_preset(preset)],
        "tool_choice": "required",
    }


def request_model_for_fusion_preset(model: str, preset: dict[str, Any] | None) -> str:
    """Map a custom Fusion preset slug to the provider-served router model."""
    return FUSION_MODEL_SLUG if preset else model
