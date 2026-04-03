"""Mid-chat model switching pipeline for CLI and gateway.

Core design: aliases resolve to an abstract model identity, then the
pipeline formats it for whatever provider you're currently on.  Typing
'/model sonnet' on OpenRouter gives you 'anthropic/claude-sonnet-4.6'.
Typing it on native Anthropic gives you 'claude-sonnet-4-6'.  Same
intent, correct name for each provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import get_close_matches
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════
# Model aliases — abstract identities, not provider-specific names
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ModelIdentity:
    """Abstract model identity resolved dynamically from catalogs.

    ``vendor`` + ``family`` define WHAT model you want.  The actual
    version is resolved at runtime from the provider's catalog — so
    "sonnet" always means the latest sonnet, not a hardcoded version.
    """
    vendor: str    # openai, anthropic, google, etc.
    family: str    # prefix to match: "claude-sonnet", "gpt-5", etc.


# Maps short alias → model family.  NO version numbers here — the
# catalog is searched at runtime for the first match, which is the
# latest/recommended version.
MODEL_ALIASES: dict[str, ModelIdentity] = {
    # Anthropic Claude
    "opus":            ModelIdentity("anthropic", "claude-opus"),
    "sonnet":          ModelIdentity("anthropic", "claude-sonnet"),
    "haiku":           ModelIdentity("anthropic", "claude-haiku"),
    "claude":          ModelIdentity("anthropic", "claude-opus"),

    # OpenAI GPT
    "gpt5":            ModelIdentity("openai", "gpt-5"),
    "gpt-5":           ModelIdentity("openai", "gpt-5"),
    "gpt5-mini":       ModelIdentity("openai", "gpt-5-mini"),    # family suffix narrows it
    "gpt5-pro":        ModelIdentity("openai", "gpt-5-pro"),
    "gpt5-nano":       ModelIdentity("openai", "gpt-5-nano"),
    "codex":           ModelIdentity("openai", "codex"),

    # Google Gemini
    "gemini":          ModelIdentity("google", "gemini"),
    "gemini-pro":      ModelIdentity("google", "gemini-pro"),
    "gemini-flash":    ModelIdentity("google", "gemini-flash"),

    # Others — family is broad enough to pick the latest
    "deepseek":        ModelIdentity("deepseek", "deepseek-chat"),
    "qwen":            ModelIdentity("qwen", "qwen"),
    "grok":            ModelIdentity("x-ai", "grok"),
    "glm":             ModelIdentity("z-ai", "glm"),
    "kimi":            ModelIdentity("moonshotai", "kimi"),
    "minimax":         ModelIdentity("minimax", "minimax-m2"),
    "mimo":            ModelIdentity("xiaomi", "mimo"),
    "nemotron":        ModelIdentity("nvidia", "nemotron"),
}

# Providers that use vendor/model slug format
_AGGREGATOR_PROVIDERS = {"openrouter", "nous", "ai-gateway", "kilocode"}

# Providers that use hyphens instead of dots in model names
_HYPHEN_PROVIDERS = {"anthropic", "opencode-zen", "opencode-go"}

# Common vendor prefixes on OpenRouter
_OPENROUTER_VENDORS = {
    "openai", "anthropic", "google", "deepseek", "meta", "mistral",
    "qwen", "minimax", "x-ai", "z-ai", "moonshotai", "nvidia",
    "xiaomi", "stepfun", "arcee-ai", "cohere", "databricks",
}


# ═══════════════════════════════════════════════════════════════════════
# Result types
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ModelSwitchResult:
    """Result of a model switch attempt."""
    success: bool
    new_model: str = ""
    target_provider: str = ""
    provider_changed: bool = False
    api_key: str = ""
    base_url: str = ""
    api_mode: str = ""
    persist: bool = False
    error_message: str = ""
    warning_message: str = ""
    is_custom_target: bool = False
    provider_label: str = ""
    resolved_via_alias: str = ""


@dataclass
class CustomAutoResult:
    """Result of switching to bare 'custom' with auto-detect."""
    success: bool
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    error_message: str = ""


# ═══════════════════════════════════════════════════════════════════════
# Provider-aware alias resolution
# ═══════════════════════════════════════════════════════════════════════

def _find_in_catalog(
    identity: ModelIdentity,
    provider: str,
) -> Optional[str]:
    """Find the best matching model in a provider's catalog.

    Searches for the first model whose bare name starts with the
    identity's family prefix.  Catalogs are ordered by recommendation,
    so the first match is the latest/best version.

    Returns the model name in the provider's native format, or None.
    """
    from hermes_cli.models import OPENROUTER_MODELS, _PROVIDER_MODELS

    family = identity.family.lower()
    vendor = identity.vendor.lower()

    # Split family into tokens for flexible matching.
    # "gpt-5-mini" → ["gpt", "5", "mini"] — matches "gpt-5.4-mini"
    family_tokens = [t for t in family.replace(".", "-").split("-") if t]

    def _tokens_match(name: str) -> bool:
        """Check if all family tokens appear in the model name."""
        nl = name.lower()
        return all(t in nl for t in family_tokens)

    if provider in _AGGREGATOR_PROVIDERS:
        prefix = f"{vendor}/{family}"
        # 1. Prefix match (strongest)
        for slug, _ in OPENROUTER_MODELS:
            if slug.lower().startswith(prefix):
                return slug
        # 2. Token match — all family tokens present + correct vendor
        for slug, _ in OPENROUTER_MODELS:
            if slug.lower().startswith(f"{vendor}/") and _tokens_match(slug):
                return slug
        return None

    # Non-aggregator providers
    catalog = _PROVIDER_MODELS.get(provider, [])
    # 1. Prefix match
    for model_name in catalog:
        bare = model_name.lower()
        if "/" in bare:
            bare = bare.split("/", 1)[1]
        if bare.startswith(family):
            return model_name
    # 2. Token match
    for model_name in catalog:
        if _tokens_match(model_name):
            return model_name

    return None


def resolve_alias(
    raw_input: str,
    current_provider: str = "openrouter",
) -> Optional[tuple[str, str, str]]:
    """Resolve a short alias to (provider, model_name, alias_used).

    Dynamically searches the current provider's catalog for the latest
    model matching the alias's family prefix:
    - 'sonnet' on OpenRouter → first catalog entry starting with
      'anthropic/claude-sonnet' → ('openrouter', 'anthropic/claude-sonnet-4.6', 'sonnet')
    - 'sonnet' on Anthropic → first entry starting with 'claude-sonnet'
      → ('anthropic', 'claude-sonnet-4-6', 'sonnet')
    - 'gpt5' on Anthropic → no GPT in Anthropic catalog → None
    """
    key = raw_input.strip().lower()
    if key not in MODEL_ALIASES:
        return None

    identity = MODEL_ALIASES[key]
    match = _find_in_catalog(identity, current_provider)

    if match:
        return (current_provider, match, key)

    # Not found on current provider — return None so the pipeline
    # can try fallback providers or cross-provider detection
    return None


# ═══════════════════════════════════════════════════════════════════════
# Fuzzy suggestions
# ═══════════════════════════════════════════════════════════════════════

def suggest_models(raw_input: str, limit: int = 3) -> list[str]:
    """Suggest similar model names when input doesn't match."""
    from hermes_cli.models import OPENROUTER_MODELS, _PROVIDER_MODELS

    candidates: list[str] = list(MODEL_ALIASES.keys())

    for model_id, _ in OPENROUTER_MODELS:
        candidates.append(model_id)
        if "/" in model_id:
            candidates.append(model_id.split("/", 1)[1])

    for models in _PROVIDER_MODELS.values():
        for m in models:
            candidates.append(m)
            if "/" in m:
                candidates.append(m.split("/", 1)[1])

    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        cl = c.lower()
        if cl not in seen:
            seen.add(cl)
            unique.append(c)

    query = raw_input.strip().lower()
    matches = get_close_matches(query, [c.lower() for c in unique], n=limit, cutoff=0.5)
    lower_to_orig = {c.lower(): c for c in unique}
    return [lower_to_orig.get(m, m) for m in matches]


# ═══════════════════════════════════════════════════════════════════════
# Aggregator-aware model resolution
# ═══════════════════════════════════════════════════════════════════════

def _resolve_on_aggregator(
    raw_model: str,
    current_provider: str,
) -> Optional[str]:
    """Try to resolve a bare model name within an aggregator.

    Prevents bare names from triggering unwanted provider switches.
    """
    from hermes_cli.models import OPENROUTER_MODELS

    model_lower = raw_model.lower()

    slugs = [m for m, _ in OPENROUTER_MODELS]
    slug_lower = {m.lower(): m for m in slugs}
    bare_to_slug: dict[str, str] = {}
    for s in slugs:
        if "/" in s:
            bare = s.split("/", 1)[1].lower()
            bare_to_slug[bare] = s

    # Exact match on full slug
    if model_lower in slug_lower:
        return slug_lower[model_lower]

    # Exact match on bare name
    if model_lower in bare_to_slug:
        return bare_to_slug[model_lower]

    # Already has vendor/ prefix — accept on aggregator
    if "/" in raw_model:
        vendor = raw_model.split("/", 1)[0].lower()
        if vendor in _OPENROUTER_VENDORS:
            return raw_model

    # Try prepending vendor prefixes
    for vendor in _OPENROUTER_VENDORS:
        candidate = f"{vendor}/{raw_model}"
        if candidate.lower() in slug_lower:
            return slug_lower[candidate.lower()]

    # Fuzzy match on bare names
    close = get_close_matches(model_lower, list(bare_to_slug.keys()), n=1, cutoff=0.75)
    if close:
        return bare_to_slug[close[0]]

    return None


# ═══════════════════════════════════════════════════════════════════════
# Core switch pipeline
# ═══════════════════════════════════════════════════════════════════════

def switch_model(
    raw_input: str,
    current_provider: str,
    current_model: str = "",
    current_base_url: str = "",
    current_api_key: str = "",
) -> ModelSwitchResult:
    """Core model-switching pipeline.

    Key behavior: aliases and bare names resolve on your CURRENT provider.
    '/model sonnet' on Anthropic gives you claude-sonnet-4-6 on Anthropic.
    '/model sonnet' on OpenRouter gives you anthropic/claude-sonnet-4.6.
    Only explicit provider:model syntax switches providers.
    """
    from hermes_cli.models import (
        parse_model_input,
        detect_provider_for_model,
        _PROVIDER_LABELS,
        _PROVIDER_MODELS,
        _KNOWN_PROVIDER_NAMES,
        OPENROUTER_MODELS,
        opencode_model_api_mode,
    )
    from hermes_cli.runtime_provider import resolve_runtime_provider

    stripped = raw_input.strip()
    if not stripped:
        return ModelSwitchResult(
            success=False,
            error_message="No model specified. Usage: /model <name> or /model provider:model",
        )

    on_aggregator = current_provider in _AGGREGATOR_PROVIDERS

    # ── Step 1: Alias resolution (provider-aware) ──
    alias_result = resolve_alias(stripped, current_provider)
    resolved_alias = ""
    if alias_result:
        target_provider, new_model, resolved_alias = alias_result
    else:
        # Check if this was an alias that's unavailable on the current provider
        key = stripped.strip().lower()
        if key in MODEL_ALIASES:
            identity = MODEL_ALIASES[key]
            # Model isn't available on current provider — find one that has it
            # Try aggregators first (most likely to have everything)
            for fallback in ["openrouter", "nous"]:
                if fallback != current_provider:
                    fallback_match = _find_in_catalog(identity, fallback)
                    if not fallback_match:
                        continue
                    try:
                        runtime = resolve_runtime_provider(requested=fallback)
                        if runtime.get("api_key"):
                            fallback_label = _PROVIDER_LABELS.get(fallback, fallback)
                            current_label = _PROVIDER_LABELS.get(current_provider, current_provider)
                            return ModelSwitchResult(
                                success=True,
                                new_model=fallback_match,
                                target_provider=fallback,
                                provider_changed=True,
                                api_key=runtime.get("api_key", ""),
                                base_url=runtime.get("base_url", ""),
                                api_mode=runtime.get("api_mode", ""),
                                persist=True,
                                warning_message=(
                                    f"{identity.family} isn't available on "
                                    f"{current_label} — switching to {fallback_label}."
                                ),
                                provider_label=fallback_label,
                                resolved_via_alias=key,
                            )
                    except Exception:
                        continue
            return ModelSwitchResult(
                success=False,
                error_message=(
                    f"{identity.family} isn't available on {current_provider} "
                    f"and no fallback provider is configured."
                ),
            )

        # ── Step 2: Vendor:model on aggregators ──
        if on_aggregator and ":" in stripped:
            left, right = stripped.split(":", 1)
            left_lower = left.strip().lower()
            if left_lower in _OPENROUTER_VENDORS and left_lower not in _KNOWN_PROVIDER_NAMES:
                target_provider = current_provider
                new_model = f"{left.strip()}/{right.strip()}"
            else:
                target_provider, new_model = parse_model_input(stripped, current_provider)
        else:
            # ── Step 3: Standard parse ──
            target_provider, new_model = parse_model_input(stripped, current_provider)

    if not new_model:
        return ModelSwitchResult(
            success=False,
            error_message="No model name provided. Usage: /model <name> or /model provider:model",
        )

    # ── Step 4: Aggregator-aware resolution ──
    _base = current_base_url or ""
    is_custom = current_provider == "custom" or (
        "localhost" in _base or "127.0.0.1" in _base
    )

    if not alias_result and target_provider == current_provider and on_aggregator:
        aggregator_slug = _resolve_on_aggregator(new_model, current_provider)
        if aggregator_slug:
            new_model = aggregator_slug
        else:
            detected = detect_provider_for_model(new_model, current_provider)
            if detected:
                target_provider, new_model = detected
    elif not alias_result and target_provider == current_provider and not is_custom:
        detected = detect_provider_for_model(new_model, current_provider)
        if detected:
            target_provider, new_model = detected

    provider_changed = target_provider != current_provider

    # ── Step 5: Resolve credentials ──
    api_key = current_api_key
    base_url = current_base_url
    api_mode = ""

    try:
        runtime = resolve_runtime_provider(requested=target_provider)
        api_key = runtime.get("api_key", "")
        base_url = runtime.get("base_url", "")
        api_mode = runtime.get("api_mode", "")
    except Exception as e:
        provider_label = _PROVIDER_LABELS.get(target_provider, target_provider)
        if target_provider == "custom":
            return ModelSwitchResult(
                success=False, target_provider=target_provider,
                error_message=(
                    "No custom endpoint configured.\n"
                    "Set model.base_url in config.yaml or OPENAI_BASE_URL in .env."
                ),
            )
        return ModelSwitchResult(
            success=False, target_provider=target_provider,
            error_message=(
                f"No credentials for {provider_label}.\n"
                f"Run `hermes setup` to configure it.\nDetail: {e}"
            ),
        )

    # ── Step 6: Catalog validation ──
    known_models: list[str] = []
    if target_provider in _AGGREGATOR_PROVIDERS:
        known_models = [m for m, _ in OPENROUTER_MODELS]
    elif target_provider in _PROVIDER_MODELS:
        known_models = list(_PROVIDER_MODELS[target_provider])

    model_lower = new_model.lower()
    found = any(m.lower() == model_lower for m in known_models)

    warning_message = ""
    if not found and known_models:
        close = get_close_matches(model_lower, [m.lower() for m in known_models], n=3, cutoff=0.5)
        if close:
            lower_to_orig = {m.lower(): m for m in known_models}
            suggestions = [lower_to_orig.get(c, c) for c in close]
            warning_message = f"Not in catalog — did you mean: {', '.join(f'`{s}`' for s in suggestions)}?"
        else:
            warning_message = f"`{new_model}` not in catalog — sending as-is."
    elif not found and not known_models:
        warning_message = f"No catalog for {target_provider} — accepting as-is."

    # ── Step 7: Build result ──
    provider_label = _PROVIDER_LABELS.get(target_provider, target_provider)
    is_custom_target = target_provider == "custom" or (
        base_url and "openrouter.ai" not in (base_url or "")
        and ("localhost" in (base_url or "") or "127.0.0.1" in (base_url or ""))
    )

    if target_provider in {"opencode-zen", "opencode-go"}:
        api_mode = opencode_model_api_mode(target_provider, new_model)

    return ModelSwitchResult(
        success=True, new_model=new_model, target_provider=target_provider,
        provider_changed=provider_changed, api_key=api_key, base_url=base_url,
        api_mode=api_mode, persist=True, warning_message=warning_message,
        is_custom_target=is_custom_target, provider_label=provider_label,
        resolved_via_alias=resolved_alias,
    )


def switch_to_custom_provider() -> CustomAutoResult:
    """Handle bare '/model custom' — resolve endpoint and auto-detect model."""
    from hermes_cli.runtime_provider import (
        resolve_runtime_provider,
        _auto_detect_local_model,
    )

    try:
        runtime = resolve_runtime_provider(requested="custom")
    except Exception as e:
        return CustomAutoResult(
            success=False,
            error_message=f"No custom endpoint configured.\nSet model.base_url in config.yaml or OPENAI_BASE_URL in .env.\nDetail: {e}",
        )

    cust_base = runtime.get("base_url", "")
    cust_key = runtime.get("api_key", "")

    if not cust_base or "openrouter.ai" in cust_base:
        return CustomAutoResult(
            success=False,
            error_message="No custom endpoint configured.\nSet model.base_url in config.yaml or OPENAI_BASE_URL in .env.",
        )

    detected_model = _auto_detect_local_model(cust_base)
    if not detected_model:
        return CustomAutoResult(
            success=False, base_url=cust_base, api_key=cust_key,
            error_message=f"Custom endpoint at {cust_base} responded but no model detected.\nSpecify explicitly: /model custom:<model-name>",
        )

    return CustomAutoResult(success=True, model=detected_model, base_url=cust_base, api_key=cust_key)
