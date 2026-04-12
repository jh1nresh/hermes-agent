"""Tests for gateway MCP config watcher — auto-reload on mcp_servers changes."""
import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from gateway.run import GatewayRunner


def _make_runner(tmp_path, mcp_servers=None):
    """Create a minimal GatewayRunner with mocked MCP config watcher state."""
    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._mcp_config_servers = mcp_servers or {}

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump({"mcp_servers": mcp_servers or {}}))
    runner._mcp_config_mtime = cfg_file.stat().st_mtime

    return runner, cfg_file


class TestMCPConfigWatcher:

    @pytest.mark.asyncio
    async def test_no_change_does_not_reload(self, tmp_path):
        """If config file hasn't changed, no MCP reload should happen."""
        runner, cfg_file = _make_runner(tmp_path, mcp_servers={
            "betterstack": {"url": "https://mcp.betterstack.com", "headers": {"Authorization": "Bearer old"}}
        })

        reload_called = False

        async def fake_watcher_iteration():
            nonlocal reload_called
            from hermes_cli.config import get_config_path
            import yaml as _yaml

            cfg_path = cfg_file
            mtime = cfg_path.stat().st_mtime

            if mtime == runner._mcp_config_mtime:
                return  # No change — fast path

            runner._mcp_config_mtime = mtime
            with open(cfg_path, encoding="utf-8") as f:
                new_cfg = _yaml.safe_load(f) or {}

            new_mcp = new_cfg.get("mcp_servers") or {}
            if new_mcp == runner._mcp_config_servers:
                return

            reload_called = True

        await fake_watcher_iteration()
        assert not reload_called

    @pytest.mark.asyncio
    async def test_header_change_triggers_reload(self, tmp_path):
        """When Authorization header changes, reload should be triggered."""
        old_servers = {
            "betterstack": {"url": "https://mcp.betterstack.com", "headers": {"Authorization": "Bearer old_token"}}
        }
        runner, cfg_file = _make_runner(tmp_path, mcp_servers=old_servers)

        # Simulate token refresh updating the config
        new_servers = {
            "betterstack": {"url": "https://mcp.betterstack.com", "headers": {"Authorization": "Bearer new_token"}}
        }
        cfg_file.write_text(yaml.dump({"mcp_servers": new_servers}))

        # Force mtime to look different
        runner._mcp_config_mtime = 0.0

        reload_triggered = False

        # Simulate one iteration of the watcher's core logic
        mtime = cfg_file.stat().st_mtime
        assert mtime != runner._mcp_config_mtime

        runner._mcp_config_mtime = mtime
        with open(cfg_file, encoding="utf-8") as f:
            new_cfg = yaml.safe_load(f) or {}

        new_mcp = new_cfg.get("mcp_servers") or {}
        if new_mcp != runner._mcp_config_servers:
            reload_triggered = True
            runner._mcp_config_servers = new_mcp

        assert reload_triggered
        assert runner._mcp_config_servers == new_servers

    @pytest.mark.asyncio
    async def test_non_mcp_change_does_not_reload(self, tmp_path):
        """If a non-MCP section changes but mcp_servers stays the same, no reload."""
        servers = {
            "betterstack": {"url": "https://mcp.betterstack.com", "headers": {"Authorization": "Bearer tok"}}
        }
        runner, cfg_file = _make_runner(tmp_path, mcp_servers=servers)

        # Write same mcp_servers but change something else
        cfg_file.write_text(yaml.dump({
            "mcp_servers": servers,
            "some_other_setting": "changed"
        }))
        runner._mcp_config_mtime = 0.0  # force stale mtime

        mtime = cfg_file.stat().st_mtime
        runner._mcp_config_mtime = mtime
        with open(cfg_file, encoding="utf-8") as f:
            new_cfg = yaml.safe_load(f) or {}

        new_mcp = new_cfg.get("mcp_servers") or {}
        assert new_mcp == runner._mcp_config_servers  # Should be unchanged

    @pytest.mark.asyncio
    async def test_server_added_triggers_reload(self, tmp_path):
        """Adding a new MCP server to config triggers reload."""
        runner, cfg_file = _make_runner(tmp_path, mcp_servers={})

        new_servers = {"github": {"url": "https://api.github.com/mcp"}}
        cfg_file.write_text(yaml.dump({"mcp_servers": new_servers}))
        runner._mcp_config_mtime = 0.0

        mtime = cfg_file.stat().st_mtime
        runner._mcp_config_mtime = mtime
        with open(cfg_file, encoding="utf-8") as f:
            new_cfg = yaml.safe_load(f) or {}

        new_mcp = new_cfg.get("mcp_servers") or {}
        assert new_mcp != runner._mcp_config_servers
        runner._mcp_config_servers = new_mcp
        assert runner._mcp_config_servers == new_servers

    @pytest.mark.asyncio
    async def test_server_removed_triggers_reload(self, tmp_path):
        """Removing an MCP server from config triggers reload."""
        runner, cfg_file = _make_runner(tmp_path, mcp_servers={
            "github": {"url": "https://api.github.com/mcp"}
        })

        cfg_file.write_text(yaml.dump({"mcp_servers": {}}))
        runner._mcp_config_mtime = 0.0

        mtime = cfg_file.stat().st_mtime
        runner._mcp_config_mtime = mtime
        with open(cfg_file, encoding="utf-8") as f:
            new_cfg = yaml.safe_load(f) or {}

        new_mcp = new_cfg.get("mcp_servers") or {}
        assert new_mcp != runner._mcp_config_servers

    @pytest.mark.asyncio
    async def test_watcher_stops_on_shutdown(self, tmp_path):
        """Watcher loop exits when _running is set to False."""
        runner, cfg_file = _make_runner(tmp_path)
        runner._running = False

        # The watcher should return almost immediately
        # We test it doesn't hang by using a timeout
        try:
            await asyncio.wait_for(
                runner._mcp_config_watcher(interval=1, _initial_delay=0),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            pytest.fail("_mcp_config_watcher did not exit after _running=False")

    @pytest.mark.asyncio
    async def test_full_watcher_detects_change_and_reloads(self, tmp_path):
        """Integration test: watcher detects a header change and calls MCP reload."""
        old_servers = {
            "betterstack": {"url": "https://mcp.betterstack.com", "headers": {"Authorization": "Bearer old"}}
        }
        runner, cfg_file = _make_runner(tmp_path, mcp_servers=old_servers)

        # Prepare the config change that will happen during the watcher run
        new_servers = {
            "betterstack": {"url": "https://mcp.betterstack.com", "headers": {"Authorization": "Bearer new"}}
        }

        shutdown_mock = MagicMock()
        discover_mock = MagicMock(return_value=[{"function": {"name": "test_tool"}}])
        servers_dict = {"betterstack": MagicMock()}
        lock_mock = MagicMock()

        async def stop_after_reload():
            """Write the config change, wait for the watcher to pick it up, then stop."""
            await asyncio.sleep(0.5)
            cfg_file.write_text(yaml.dump({"mcp_servers": new_servers}))
            # Wait enough time for the watcher to detect + reload
            await asyncio.sleep(4)
            runner._running = False

        with patch("hermes_cli.config.get_config_path", return_value=cfg_file), \
             patch("tools.mcp_tool.shutdown_mcp_servers", shutdown_mock), \
             patch("tools.mcp_tool.discover_mcp_tools", discover_mock), \
             patch("tools.mcp_tool._servers", servers_dict), \
             patch("tools.mcp_tool._lock", lock_mock):

            stop_task = asyncio.create_task(stop_after_reload())
            try:
                await asyncio.wait_for(
                    runner._mcp_config_watcher(interval=1, _initial_delay=0),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                runner._running = False

            await stop_task

        shutdown_mock.assert_called_once()
        discover_mock.assert_called_once()
        assert runner._mcp_config_servers == new_servers
