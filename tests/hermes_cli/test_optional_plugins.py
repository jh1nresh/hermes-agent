"""Tests for optional-plugins (official) install path in plugins_cmd."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_official_plugin_dir(tmp_path: Path, category: str, name: str) -> Path:
    """Create a minimal optional-plugin directory structure."""
    plugin_dir = tmp_path / "optional-plugins" / category / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        f"name: {name}\nversion: 1.0.0\ndescription: Test plugin\n"
    )
    (plugin_dir / "__init__.py").write_text("def register(ctx): pass\n")
    return plugin_dir


# ---------------------------------------------------------------------------
# _resolve_official_plugin
# ---------------------------------------------------------------------------

class TestResolveOfficialPlugin:
    def test_returns_none_for_git_url(self, tmp_path):
        from hermes_cli.plugins_cmd import _resolve_official_plugin
        with patch("hermes_cli.plugins_cmd._optional_plugins_dir", return_value=tmp_path / "optional-plugins"):
            result = _resolve_official_plugin("https://github.com/owner/repo.git")
        assert result is None

    def test_returns_none_for_owner_repo(self, tmp_path):
        from hermes_cli.plugins_cmd import _resolve_official_plugin
        with patch("hermes_cli.plugins_cmd._optional_plugins_dir", return_value=tmp_path / "optional-plugins"):
            result = _resolve_official_plugin("owner/repo")
        assert result is None

    def test_returns_none_for_missing_plugin(self, tmp_path):
        from hermes_cli.plugins_cmd import _resolve_official_plugin
        (tmp_path / "optional-plugins").mkdir()
        with patch("hermes_cli.plugins_cmd._optional_plugins_dir", return_value=tmp_path / "optional-plugins"):
            result = _resolve_official_plugin("official/observability/nonexistent")
        assert result is None

    def test_returns_path_for_existing_plugin(self, tmp_path):
        from hermes_cli.plugins_cmd import _resolve_official_plugin
        plugin_dir = _make_official_plugin_dir(tmp_path, "observability", "langfuse")
        with patch("hermes_cli.plugins_cmd._optional_plugins_dir", return_value=tmp_path / "optional-plugins"):
            result = _resolve_official_plugin("official/observability/langfuse")
        assert result == plugin_dir

    def test_accepts_without_official_prefix(self, tmp_path):
        from hermes_cli.plugins_cmd import _resolve_official_plugin
        plugin_dir = _make_official_plugin_dir(tmp_path, "observability", "langfuse")
        with patch("hermes_cli.plugins_cmd._optional_plugins_dir", return_value=tmp_path / "optional-plugins"):
            result = _resolve_official_plugin("observability/langfuse")
        assert result == plugin_dir

    def test_traversal_blocked(self, tmp_path):
        from hermes_cli.plugins_cmd import _resolve_official_plugin
        (tmp_path / "optional-plugins").mkdir()
        with patch("hermes_cli.plugins_cmd._optional_plugins_dir", return_value=tmp_path / "optional-plugins"):
            result = _resolve_official_plugin("official/../../etc/passwd")
        assert result is None


# ---------------------------------------------------------------------------
# _list_official_plugins
# ---------------------------------------------------------------------------

class TestListOfficialPlugins:
    def test_empty_when_no_optional_plugins_dir(self, tmp_path):
        from hermes_cli.plugins_cmd import _list_official_plugins
        with patch("hermes_cli.plugins_cmd._optional_plugins_dir", return_value=tmp_path / "nonexistent"):
            result = _list_official_plugins()
        assert result == []

    def test_lists_plugins_with_descriptions(self, tmp_path):
        from hermes_cli.plugins_cmd import _list_official_plugins
        _make_official_plugin_dir(tmp_path, "observability", "langfuse")
        _make_official_plugin_dir(tmp_path, "observability", "other-plugin")
        with patch("hermes_cli.plugins_cmd._optional_plugins_dir", return_value=tmp_path / "optional-plugins"):
            result = _list_official_plugins()
        identifiers = [r[0] for r in result]
        assert "official/observability/langfuse" in identifiers
        assert "official/observability/other-plugin" in identifiers

    def test_descriptions_parsed_from_yaml(self, tmp_path):
        from hermes_cli.plugins_cmd import _list_official_plugins
        plugin_dir = _make_official_plugin_dir(tmp_path, "observability", "langfuse")
        with patch("hermes_cli.plugins_cmd._optional_plugins_dir", return_value=tmp_path / "optional-plugins"):
            result = _list_official_plugins()
        assert any(desc == "Test plugin" for _, desc in result)


# ---------------------------------------------------------------------------
# cmd_install — official path
# ---------------------------------------------------------------------------

class TestCmdInstallOfficial:
    def test_install_official_plugin_copies_files(self, tmp_path, monkeypatch):
        from hermes_cli.plugins_cmd import cmd_install
        plugin_dir = _make_official_plugin_dir(tmp_path, "observability", "langfuse")
        user_plugins = tmp_path / "user-plugins"
        user_plugins.mkdir()

        monkeypatch.setattr("hermes_cli.plugins_cmd._optional_plugins_dir",
                            lambda: tmp_path / "optional-plugins")
        monkeypatch.setattr("hermes_cli.plugins_cmd._plugins_dir",
                            lambda: user_plugins)
        # Non-interactive: don't prompt
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        cmd_install("official/observability/langfuse", enable=False)

        installed = user_plugins / "langfuse"
        assert installed.is_dir()
        assert (installed / "plugin.yaml").exists()
        assert (installed / "__init__.py").exists()

    def test_install_official_plugin_respects_force(self, tmp_path, monkeypatch):
        from hermes_cli.plugins_cmd import cmd_install
        plugin_dir = _make_official_plugin_dir(tmp_path, "observability", "langfuse")
        user_plugins = tmp_path / "user-plugins"
        user_plugins.mkdir()
        # Pre-create to simulate already-installed
        already = user_plugins / "langfuse"
        already.mkdir()
        (already / "old.txt").write_text("old")

        monkeypatch.setattr("hermes_cli.plugins_cmd._optional_plugins_dir",
                            lambda: tmp_path / "optional-plugins")
        monkeypatch.setattr("hermes_cli.plugins_cmd._plugins_dir",
                            lambda: user_plugins)
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        cmd_install("official/observability/langfuse", force=True, enable=False)

        # Old file should be gone, new files present
        assert not (already / "old.txt").exists()
        assert (already / "plugin.yaml").exists()

    def test_install_official_plugin_exits_without_force_when_exists(self, tmp_path, monkeypatch):
        from hermes_cli.plugins_cmd import cmd_install
        _make_official_plugin_dir(tmp_path, "observability", "langfuse")
        user_plugins = tmp_path / "user-plugins"
        user_plugins.mkdir()
        (user_plugins / "langfuse").mkdir()

        monkeypatch.setattr("hermes_cli.plugins_cmd._optional_plugins_dir",
                            lambda: tmp_path / "optional-plugins")
        monkeypatch.setattr("hermes_cli.plugins_cmd._plugins_dir",
                            lambda: user_plugins)

        with pytest.raises(SystemExit):
            cmd_install("official/observability/langfuse", enable=False)

    def test_git_url_not_mistaken_for_official(self, tmp_path, monkeypatch):
        """A git URL must not trigger the official install path."""
        from hermes_cli.plugins_cmd import _resolve_official_plugin
        with patch("hermes_cli.plugins_cmd._optional_plugins_dir",
                   return_value=tmp_path / "optional-plugins"):
            assert _resolve_official_plugin("https://github.com/owner/repo") is None
            assert _resolve_official_plugin("owner/repo") is None
