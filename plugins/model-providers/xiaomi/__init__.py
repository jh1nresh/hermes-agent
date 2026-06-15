"""Xiaomi MiMo provider profile."""

from typing import Any

from providers import register_provider
from providers.base import ProviderProfile


class XiaomiProfile(ProviderProfile):
    """Xiaomi MiMo — explicit thinking disable support."""

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict | None = None,
        **context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not reasoning_config or not isinstance(reasoning_config, dict):
            return {}, {}

        effort = str(reasoning_config.get("effort") or "").strip().lower()
        enabled = reasoning_config.get("enabled", True)
        if enabled is False or effort == "none":
            return {"thinking": {"type": "disabled"}}, {}
        return {}, {}


xiaomi = XiaomiProfile(
    name="xiaomi",
    aliases=("mimo", "xiaomi-mimo"),
    env_vars=("XIAOMI_API_KEY",),
    base_url="https://api.xiaomimimo.com/v1",
    supports_health_check=False,  # /v1/models returns 401 even with valid key
    supports_vision=True,  # mimo-v2.5 / omni variants are vision-capable
    supports_vision_tool_messages=False,  # rejects list-type tool content (400 "text is not set")
)

register_provider(xiaomi)
