"""Source-of-truth contracts for built-in providers without models.dev catalogs."""

from __future__ import annotations

from typing import Dict, List, Tuple

VOLCENGINE_PROVIDER = "volcengine"
BYTEPLUS_PROVIDER = "byteplus"

VOLCENGINE_STANDARD_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
VOLCENGINE_CODING_PLAN_BASE_URL = "https://ark.cn-beijing.volces.com/api/coding/v3"
BYTEPLUS_STANDARD_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
BYTEPLUS_CODING_PLAN_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/coding/v3"

VOLCENGINE_STANDARD_MODELS: Tuple[str, ...] = (
    "doubao-seed-2-0-pro-260215",
    "doubao-seed-2-0-lite-260215",
    "doubao-seed-2-0-mini-260215",
    "doubao-seed-2-0-code-preview-260215",
    "kimi-k2-5-260127",
    "glm-4-7-251222",
    "deepseek-v3-2-251201",
)

VOLCENGINE_CODING_PLAN_MODELS: Tuple[str, ...] = (
    "doubao-seed-2.0-code",
    "doubao-seed-2.0-pro",
    "doubao-seed-2.0-lite",
    "doubao-seed-code",
    "minimax-m2.5",
    "glm-4.7",
    "deepseek-v3.2",
    "kimi-k2.5",
)

BYTEPLUS_STANDARD_MODELS: Tuple[str, ...] = (
    "seed-2-0-pro-260328",
    "seed-2-0-lite-260228",
    "seed-2-0-mini-260215",
    "kimi-k2-5-260127",
    "glm-4-7-251222",
)

BYTEPLUS_CODING_PLAN_MODELS: Tuple[str, ...] = (
    "dola-seed-2.0-pro",
    "dola-seed-2.0-lite",
    "bytedance-seed-code",
    "glm-4.7",
    "kimi-k2.5",
    "gpt-oss-120b",
)

VOLCENGINE_STANDARD_MODEL_REFS: Tuple[str, ...] = tuple(
    f"{VOLCENGINE_PROVIDER}/{model_id}" for model_id in VOLCENGINE_STANDARD_MODELS
)
VOLCENGINE_CODING_PLAN_MODEL_REFS: Tuple[str, ...] = tuple(
    f"{VOLCENGINE_PROVIDER}-coding-plan/{model_id}" for model_id in VOLCENGINE_CODING_PLAN_MODELS
)
BYTEPLUS_STANDARD_MODEL_REFS: Tuple[str, ...] = tuple(
    f"{BYTEPLUS_PROVIDER}/{model_id}" for model_id in BYTEPLUS_STANDARD_MODELS
)
BYTEPLUS_CODING_PLAN_MODEL_REFS: Tuple[str, ...] = tuple(
    f"{BYTEPLUS_PROVIDER}-coding-plan/{model_id}" for model_id in BYTEPLUS_CODING_PLAN_MODELS
)

PROVIDER_MODEL_CATALOGS: Dict[str, Tuple[str, ...]] = {
    VOLCENGINE_PROVIDER: VOLCENGINE_STANDARD_MODEL_REFS + VOLCENGINE_CODING_PLAN_MODEL_REFS,
    BYTEPLUS_PROVIDER: BYTEPLUS_STANDARD_MODEL_REFS + BYTEPLUS_CODING_PLAN_MODEL_REFS,
}

MODEL_CONTEXT_WINDOWS: Dict[str, int] = {
    "doubao-seed-2-0-pro-260215": 256000,
    "doubao-seed-2-0-lite-260215": 256000,
    "doubao-seed-2-0-mini-260215": 256000,
    "doubao-seed-2-0-code-preview-260215": 256000,
    "kimi-k2-5-260127": 256000,
    "glm-4-7-251222": 200000,
    "deepseek-v3-2-251201": 128000,
    "doubao-seed-2.0-code": 256000,
    "doubao-seed-2.0-pro": 256000,
    "doubao-seed-2.0-lite": 256000,
    "doubao-seed-code": 256000,
    "minimax-m2.5": 200000,
    "glm-4.7": 200000,
    "deepseek-v3.2": 128000,
    "kimi-k2.5": 256000,
    "seed-2-0-pro-260328": 256000,
    "seed-2-0-lite-260228": 256000,
    "seed-2-0-mini-260215": 256000,
}


def provider_models(provider_id: str) -> List[str]:
    """Return the full user-facing model catalog for a provider."""
    return list(PROVIDER_MODEL_CATALOGS.get(provider_id, ()))


def _bare_model_name(model_name: str) -> str:
    value = (model_name or "").strip()
    if not value:
        return ""
    if "/" in value:
        return value.split("/", 1)[1].strip()
    return value


def is_coding_plan_model(provider_id: str, model_name: str) -> bool:
    """Return True when a model belongs to the coding-plan catalog."""
    raw = (model_name or "").strip()
    bare = _bare_model_name(raw)
    if provider_id == VOLCENGINE_PROVIDER:
        return raw in VOLCENGINE_CODING_PLAN_MODEL_REFS or bare in VOLCENGINE_CODING_PLAN_MODELS
    if provider_id == BYTEPLUS_PROVIDER:
        return raw in BYTEPLUS_CODING_PLAN_MODEL_REFS or bare in BYTEPLUS_CODING_PLAN_MODELS
    return False


def base_url_for_provider_model(provider_id: str, model_name: str) -> str:
    """Resolve the source-of-truth base URL for a provider+model pair."""
    if provider_id == VOLCENGINE_PROVIDER:
        if is_coding_plan_model(provider_id, model_name):
            return VOLCENGINE_CODING_PLAN_BASE_URL
        return VOLCENGINE_STANDARD_BASE_URL
    if provider_id == BYTEPLUS_PROVIDER:
        if is_coding_plan_model(provider_id, model_name):
            return BYTEPLUS_CODING_PLAN_BASE_URL
        return BYTEPLUS_STANDARD_BASE_URL
    return ""


def model_context_window(model_name: str) -> int | None:
    """Return a known context window for a model, if specified by the contract."""
    bare = _bare_model_name(model_name)
    return MODEL_CONTEXT_WINDOWS.get(bare)
