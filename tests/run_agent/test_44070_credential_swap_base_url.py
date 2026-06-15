"""Credential-pool base_url guards for Xiaomi MiMo Token Plan sessions.

A Xiaomi session can run on a configured Token Plan base URL while env/manual
pool entries still carry the provider registry default.  Credential rotation
must not re-pin the live agent to that default endpoint.
"""

from unittest.mock import MagicMock
from typing import Any

from run_agent import AIAgent


XIAOMI_DEFAULT = "https://api.xiaomimimo.com/v1"
TOKEN_PLAN = "https://token-plan-cn.xiaomimimo.com/v1"


def _bare_agent(provider="xiaomi", base_url=TOKEN_PLAN, api_mode="chat_completions") -> Any:
    agent: Any = AIAgent.__new__(AIAgent)
    agent.provider = provider
    agent.model = "mimo-v2.5-pro"
    agent.base_url = base_url
    agent.api_mode = api_mode
    agent.api_key = "plan-key"
    agent._client_kwargs = {"api_key": "plan-key", "base_url": base_url}
    return agent


def _entry(base_url: str | None = XIAOMI_DEFAULT, key="rotated-key"):
    entry = MagicMock()
    entry.runtime_api_key = key
    entry.access_token = key
    entry.runtime_base_url = base_url
    entry.base_url = base_url
    return entry


class TestPoolEntrySwapBaseUrl:
    def test_registry_default_entry_keeps_configured_override(self):
        agent = _bare_agent()

        assert agent._pool_entry_swap_base_url(_entry(XIAOMI_DEFAULT)) == TOKEN_PLAN

    def test_per_credential_endpoint_still_wins(self):
        agent = _bare_agent()
        regional = "https://token-plan-sgp.xiaomimimo.com/v1"

        assert agent._pool_entry_swap_base_url(_entry(regional)) == regional

    def test_entry_without_url_keeps_current(self):
        agent = _bare_agent()
        entry = _entry(None)
        entry.runtime_base_url = None
        entry.base_url = None

        assert agent._pool_entry_swap_base_url(entry) == TOKEN_PLAN

    def test_agent_on_default_url_adopts_entry_url(self):
        agent = _bare_agent(base_url=XIAOMI_DEFAULT)

        assert agent._pool_entry_swap_base_url(_entry(XIAOMI_DEFAULT)) == XIAOMI_DEFAULT

    def test_unknown_provider_adopts_entry_url(self):
        agent = _bare_agent(provider="custom")

        assert agent._pool_entry_swap_base_url(_entry(XIAOMI_DEFAULT)) == XIAOMI_DEFAULT

    def test_swap_credential_preserves_override(self):
        agent = _bare_agent()
        agent._apply_client_headers_for_base_url = MagicMock()
        agent._replace_primary_openai_client = MagicMock(return_value=True)

        agent._swap_credential(_entry(XIAOMI_DEFAULT, key="fresh-key"))

        assert agent.base_url == TOKEN_PLAN
        assert agent._client_kwargs["base_url"] == TOKEN_PLAN
        assert agent.api_key == "fresh-key"
        assert agent._client_kwargs["api_key"] == "fresh-key"
        agent._replace_primary_openai_client.assert_called_once()


class TestSwitchModelDetachesForeignPool:
    def _switch_ready_agent(self, pool):
        agent = _bare_agent()
        agent._credential_pool = pool
        agent._config_context_length = None
        agent._fallback_activated = True
        agent._fallback_index = 3
        agent._fallback_chain = []
        agent._fallback_model = None
        agent._cached_system_prompt = "cached"
        agent._client_kwargs = {"api_key": "plan-key", "base_url": TOKEN_PLAN}
        agent.context_compressor = None
        agent._use_prompt_caching = False
        agent._use_native_cache_layout = False
        agent._anthropic_prompt_cache_policy = MagicMock(return_value=(False, False))
        agent._ensure_lmstudio_runtime_loaded = MagicMock()
        agent._create_openai_client = MagicMock(return_value=MagicMock())
        agent._transport_cache = {}
        return agent

    def test_cross_provider_switch_detaches_pool(self):
        pool = MagicMock()
        pool.provider = "xiaomi"
        agent = self._switch_ready_agent(pool)

        agent.switch_model(
            new_model="deepseek-chat",
            new_provider="deepseek",
            api_key="ds-key",
            base_url="https://api.deepseek.com/v1",
            api_mode="chat_completions",
        )

        assert agent._credential_pool is None
        assert agent.base_url == "https://api.deepseek.com/v1"

    def test_same_provider_switch_keeps_pool(self):
        pool = MagicMock()
        pool.provider = "xiaomi"
        agent = self._switch_ready_agent(pool)

        agent.switch_model(
            new_model="mimo-v2.5",
            new_provider="xiaomi",
            api_key="plan-key",
            base_url=TOKEN_PLAN,
            api_mode="chat_completions",
        )

        assert agent._credential_pool is pool
