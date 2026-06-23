"""ZAI / GLM provider profile.

Z.AI's ``/api/paas/v4`` endpoint is OpenAI-compatible. GLM-5.2 exposes a
native ``reasoning_effort`` knob with exactly two levels — ``high`` and
``max`` — when thinking is enabled (per Z.AI / BigModel docs). Hermes' richer
effort scale is collapsed onto those two so the user's effort preference
actually reaches the model instead of being silently dropped.
"""

from __future__ import annotations

from typing import Any

from providers import register_provider
from providers.base import ProviderProfile


def _is_glm_5_2(model: str | None) -> bool:
    """Detect GLM-5.2 across the alias spellings providers use.

    Covers the canonical ``glm-5.2`` plus the ``glm-5-2`` / ``glm-5p2``
    variants seen on relays (Fireworks ``glm-5p2``, etc.) and any
    vendor-prefixed form (``z-ai/glm-5.2``, ``zai-org-glm-5-2``).
    """
    m = (model or "").strip().lower()
    if not m:
        return False
    return any(token in m for token in ("glm-5.2", "glm-5-2", "glm-5p2"))


def _glm_5_2_reasoning_extras(
    reasoning_config: dict | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Map Hermes reasoning effort onto GLM-5.2's native ``high``/``max``.

    GLM-5.2 only supports two effort levels. ``xhigh``/``max`` request the
    top tier; everything else that is enabled requests ``high`` (its
    minimum thinking level). When reasoning is explicitly disabled, or no
    config is supplied, the server default is left untouched.
    """
    if not isinstance(reasoning_config, dict):
        return {}, {}
    if reasoning_config.get("enabled") is False:
        return {}, {}

    effort = (reasoning_config.get("effort") or "").strip().lower()
    if not effort or effort == "none":
        return {}, {}

    if effort in {"xhigh", "max"}:
        return {}, {"reasoning_effort": "max"}
    # low / medium / minimal / high all clamp to GLM-5.2's minimum: high.
    return {}, {"reasoning_effort": "high"}


class ZaiProfile(ProviderProfile):
    """Z.AI / GLM — GLM-5.2 native reasoning_effort controls."""

    def build_api_kwargs_extras(
        self, *, reasoning_config: dict | None = None, model: str | None = None, **context
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not _is_glm_5_2(model):
            return {}, {}
        return _glm_5_2_reasoning_extras(reasoning_config)


zai = ZaiProfile(
    name="zai",
    aliases=("glm", "z-ai", "z.ai", "zhipu"),
    env_vars=("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"),
    display_name="Z.AI (GLM)",
    description="Z.AI / GLM — Zhipu AI models",
    signup_url="https://z.ai/",
    fallback_models=(
        "glm-5.2",
        "glm-5",
        "glm-4-9b",
    ),
    base_url="https://api.z.ai/api/paas/v4",
    default_aux_model="glm-4.5-flash",
)

register_provider(zai)
