"""Unit tests for mcp_homelab/setup/client_setup.py.

Tests JSONC stripping, config path detection, server entry construction,
and the upsert functions. File I/O uses temp directories.
"""

from __future__ import annotations

import json
import platform
from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_homelab.setup.client_setup import (
    _claude_desktop_config_path,
    _load_json,
    _server_entry_stdio,
    _strip_jsonc_comments,
    _windows_claude_config_path,
    _write_json,
    upsert_claude_desktop,
    upsert_vscode,
)


# ===========================================================================
# _claude_desktop_config_path
# ===========================================================================


class TestClaudeDesktopConfigPath:
    def test_returns_path_on_windows(self) -> None:
        with patch("mcp_homelab.setup.client_setup.platform.system", return_value="Windows"):
            # Windows path now routes through Store-vs-traditional detection.
            result = _claude_desktop_config_path()
            assert result is not None
            assert result.name == "claude_desktop_config.json"
            assert "Claude" in str(result)

    def test_returns_path_on_macos(self) -> None:
        with patch("mcp_homelab.setup.client_setup.platform.system", return_value="Darwin"):
            result = _claude_desktop_config_path()
            assert result is not None
            assert "Claude" in str(result)

    def test_returns_none_on_linux(self) -> None:
        with patch("mcp_homelab.setup.client_setup.platform.system", return_value="Linux"):
            assert _claude_desktop_config_path() is None


# ===========================================================================
# _load_json
# ===========================================================================


class TestLoadJson:
    def test_loads_valid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "test.json"
        path.write_text('{"key": "value"}', encoding="utf-8")
        assert _load_json(path) == {"key": "value"}

    def test_returns_empty_for_missing_file(self, tmp_path: Path) -> None:
        assert _load_json(tmp_path / "nope.json") == {}

    def test_returns_empty_for_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.json"
        path.write_text("", encoding="utf-8")
        assert _load_json(path) == {}

    def test_strips_single_line_comments(self, tmp_path: Path) -> None:
        """VS Code JSON files often have // comments."""
        path = tmp_path / "vscode.json"
        path.write_text(
            '{\n'
            '  // This is a comment\n'
            '  "key": "value"\n'
            '}\n',
            encoding="utf-8",
        )
        assert _load_json(path) == {"key": "value"}

    def test_strips_block_comments(self, tmp_path: Path) -> None:
        path = tmp_path / "vscode.json"
        path.write_text(
            '{\n'
            '  /* block comment */\n'
            '  "key": "value"\n'
            '}\n',
            encoding="utf-8",
        )
        assert _load_json(path) == {"key": "value"}

    def test_strips_multiline_block_comments(self, tmp_path: Path) -> None:
        path = tmp_path / "vscode.json"
        path.write_text(
            '{\n'
            '  /*\n'
            '   * multi-line\n'
            '   * block comment\n'
            '   */\n'
            '  "key": "value"\n'
            '}\n',
            encoding="utf-8",
        )
        assert _load_json(path) == {"key": "value"}

    def test_preserves_urls_in_strings(self, tmp_path: Path) -> None:
        """URLs contain // which must not be treated as comments (R6)."""
        path = tmp_path / "mcp.json"
        path.write_text(
            '{\n'
            '  "command": "https://example.com/api",\n'
            '  "url": "http://localhost:8080/v1"\n'
            '}\n',
            encoding="utf-8",
        )
        result = _load_json(path)
        assert result["command"] == "https://example.com/api"
        assert result["url"] == "http://localhost:8080/v1"

    def test_preserves_urls_with_comments_on_same_line(self, tmp_path: Path) -> None:
        """Comment after a URL-containing value should be stripped."""
        path = tmp_path / "mcp.json"
        path.write_text(
            '{\n'
            '  "url": "https://pve.local:8006/api2/json" // proxmox\n'
            '}\n',
            encoding="utf-8",
        )
        result = _load_json(path)
        assert result["url"] == "https://pve.local:8006/api2/json"

    def test_preserves_file_paths_in_strings(self, tmp_path: Path) -> None:
        """Windows-style paths could confuse naive regex."""
        path = tmp_path / "mcp.json"
        path.write_text(
            '{\n'
            '  "command": "C:\\\\Python310\\\\python.exe",\n'
            '  "args": ["//server/share/script.py"]\n'
            '}\n',
            encoding="utf-8",
        )
        result = _load_json(path)
        assert result["command"] == "C:\\Python310\\python.exe"
        assert result["args"] == ["//server/share/script.py"]


class TestWindowsClaudeConfigPath:
    def test_prefers_store_path_when_exists(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        store_path = (
            tmp_path
            / "AppData"
            / "Local"
            / "Packages"
            / "Claude_pzs8sxrjxfjjc"
            / "LocalCache"
            / "Roaming"
            / "Claude"
        )
        store_path.mkdir(parents=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        assert _windows_claude_config_path() == store_path

    def test_falls_back_to_traditional_when_no_store(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        traditional_path = tmp_path / "AppData" / "Roaming" / "Claude"

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        assert _windows_claude_config_path() == traditional_path

    def test_full_path_includes_store_prefix(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        store_path = (
            tmp_path
            / "AppData"
            / "Local"
            / "Packages"
            / "Claude_pzs8sxrjxfjjc"
            / "LocalCache"
            / "Roaming"
            / "Claude"
        )
        store_path.mkdir(parents=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr("mcp_homelab.setup.client_setup.platform.system", lambda: "Windows")

        result = _claude_desktop_config_path()
        assert result is not None
        assert "Packages" in str(result)
        assert "Claude_pzs8sxrjxfjjc" in str(result)


class TestStripJsoncComments:
    """Direct tests for _strip_jsonc_comments edge cases."""

    def test_url_in_string_preserved(self) -> None:
        text = '{"url": "https://example.com"}'
        assert _strip_jsonc_comments(text) == text

    def test_comment_after_string_removed(self) -> None:
        text = '{"key": "val"} // comment'
        assert '"key": "val"' in _strip_jsonc_comments(text)
        assert "comment" not in _strip_jsonc_comments(text)

    def test_block_comment_inside_url_string_preserved(self) -> None:
        """A string like 'https://x.com/*/path' must not be mangled."""
        text = '{"url": "https://x.com/*/path"}'
        assert _strip_jsonc_comments(text) == text

    def test_escaped_quote_in_string(self) -> None:
        text = r'{"key": "say \"hello\""}'
        result = _strip_jsonc_comments(text)
        assert result == text


# ===========================================================================
# _write_json
# ===========================================================================


class TestWriteJson:
    def test_writes_formatted_json(self, tmp_path: Path) -> None:
        path = tmp_path / "out.json"
        _write_json(path, {"a": 1})
        content = path.read_text(encoding="utf-8")
        assert '"a": 1' in content
        assert content.endswith("\n")

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        path = tmp_path / "deep" / "nested" / "out.json"
        _write_json(path, {"key": "value"})
        assert path.exists()

    def test_atomic_write(self, tmp_path: Path) -> None:
        """After write, no .tmp file should remain."""
        path = tmp_path / "out.json"
        _write_json(path, {"key": "value"})
        assert not path.with_suffix(".tmp").exists()


# ===========================================================================
# _server_entry_stdio
# ===========================================================================


class TestServerEntryStdio:
    def test_contains_required_keys(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        entry = _server_entry_stdio()
        assert "command" in entry
        assert "args" in entry
        assert "env" in entry
        assert "MCP_HOMELAB_CONFIG_DIR" in entry["env"]

    def test_args_points_to_server_py(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        entry = _server_entry_stdio()
        assert entry["args"][0].endswith("server.py")

    def test_uses_venv_python_if_exists(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))

        # Create a fake venv python
        if platform.system() == "Windows":
            venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
        else:
            venv_python = tmp_path / ".venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("fake python")

        entry = _server_entry_stdio()
        assert entry["command"] == str(venv_python)


# ===========================================================================
# upsert_claude_desktop
# ===========================================================================


class TestUpsertClaudeDesktop:
    def test_dry_run_returns_json(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        result = upsert_claude_desktop(dry_run=True)
        assert result is not None
        data = json.loads(result)
        assert "mcpServers" in data
        assert "homelab" in data["mcpServers"]

    def test_writes_config_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        config_file = tmp_path / "claude_desktop_config.json"

        # Mock the path function to return our tmp file
        with patch(
            "mcp_homelab.setup.client_setup._claude_desktop_config_path",
            return_value=config_file,
        ):
            result = upsert_claude_desktop(dry_run=False)
            assert result is None
            data = json.loads(config_file.read_text())
            assert "homelab" in data["mcpServers"]

    def test_preserves_other_servers(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        config_file = tmp_path / "claude_desktop_config.json"
        config_file.write_text(
            json.dumps({"mcpServers": {"other-server": {"command": "other"}}}),
            encoding="utf-8",
        )

        with patch(
            "mcp_homelab.setup.client_setup._claude_desktop_config_path",
            return_value=config_file,
        ):
            upsert_claude_desktop(dry_run=False)
            data = json.loads(config_file.read_text())
            assert "other-server" in data["mcpServers"]
            assert "homelab" in data["mcpServers"]

    def test_raises_on_unsupported_os(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        with patch(
            "mcp_homelab.setup.client_setup._claude_desktop_config_path",
            return_value=None,
        ):
            with pytest.raises(RuntimeError, match="not found for this OS"):
                upsert_claude_desktop(dry_run=False)


# ===========================================================================
# upsert_vscode
# ===========================================================================


class TestUpsertVscode:
    def test_dry_run_returns_json(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        result = upsert_vscode(dry_run=True)
        assert result is not None
        data = json.loads(result)
        assert "servers" in data
        assert "homelab" in data["servers"]

    def test_writes_to_vscode_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir()

        upsert_vscode(dry_run=False)

        mcp_json = vscode_dir / "mcp.json"
        assert mcp_json.exists()
        data = json.loads(mcp_json.read_text())
        assert "homelab" in data["servers"]

    def test_creates_vscode_dir_if_absent(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        # No .vscode/ dir exists

        upsert_vscode(dry_run=False)

        mcp_json = tmp_path / ".vscode" / "mcp.json"
        assert mcp_json.exists()
