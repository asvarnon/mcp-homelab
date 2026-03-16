"""Unit tests for mcp_homelab/setup/proxmox_setup.py and opnsense_setup.py.

Tests the guided setup wizards by mocking all interactive prompts and
verifying the resulting config.yaml and .env files.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from ruamel.yaml import YAML

from mcp_homelab.setup.proxmox_setup import _test_connection as proxmox_test_connection
from mcp_homelab.setup.proxmox_setup import run_proxmox_setup
from mcp_homelab.setup.opnsense_setup import _test_connection as opnsense_test_connection
from mcp_homelab.setup.opnsense_setup import run_opnsense_setup


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_config(tmp_path: Path) -> Path:
    """Create a minimal config.yaml + .env for tests."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("hosts: {}\n", encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text("# env\n", encoding="utf-8")
    return config_path


def _load_yaml(path: Path) -> dict:
    yaml = YAML()
    with open(path) as f:
        return yaml.load(f)


# ===========================================================================
# Proxmox Setup
# ===========================================================================


class TestRunProxmoxSetup:
    def test_writes_config_and_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_path = _seed_config(tmp_path)

        # Mock all prompts in order: IP, port, verify_ssl, token_id, token_secret, test_connection
        monkeypatch.setattr("mcp_homelab.setup.proxmox_setup.prompt_ip", lambda _: "10.0.50.20")
        monkeypatch.setattr("mcp_homelab.setup.proxmox_setup.prompt_int", lambda *a, **kw: 8006)
        monkeypatch.setattr("mcp_homelab.setup.proxmox_setup.prompt_yn", lambda *a, **kw: False)
        monkeypatch.setattr("mcp_homelab.setup.proxmox_setup.prompt_str", lambda _: "admin@pam!mcp")
        monkeypatch.setattr("mcp_homelab.setup.proxmox_setup.prompt_secret", lambda _: "secret-token-value")

        run_proxmox_setup(config_path=config_path)

        # Verify config.yaml
        data = _load_yaml(config_path)
        assert data["proxmox"]["host"] == "10.0.50.20"
        assert data["proxmox"]["port"] == 8006
        assert data["proxmox"]["verify_ssl"] is False

        # Verify .env
        env_content = (tmp_path / ".env").read_text()
        assert "PROXMOX_TOKEN_ID=admin@pam!mcp" in env_content
        assert "PROXMOX_TOKEN_SECRET=secret-token-value" in env_content

    def test_preserves_existing_hosts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "hosts:\n  gamehost:\n    ip: 10.0.50.10\n",
            encoding="utf-8",
        )
        (tmp_path / ".env").write_text("", encoding="utf-8")

        monkeypatch.setattr("mcp_homelab.setup.proxmox_setup.prompt_ip", lambda _: "10.0.50.20")
        monkeypatch.setattr("mcp_homelab.setup.proxmox_setup.prompt_int", lambda *a, **kw: 8006)
        monkeypatch.setattr("mcp_homelab.setup.proxmox_setup.prompt_yn", lambda *a, **kw: False)
        monkeypatch.setattr("mcp_homelab.setup.proxmox_setup.prompt_str", lambda _: "admin@pam!mcp")
        monkeypatch.setattr("mcp_homelab.setup.proxmox_setup.prompt_secret", lambda _: "secret")

        run_proxmox_setup(config_path=config_path)

        data = _load_yaml(config_path)
        assert "gamehost" in data["hosts"]
        assert data["proxmox"]["host"] == "10.0.50.20"

    def test_runs_connection_test_when_accepted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_path = _seed_config(tmp_path)
        test_called = False

        monkeypatch.setattr("mcp_homelab.setup.proxmox_setup.prompt_ip", lambda _: "10.0.50.20")
        monkeypatch.setattr("mcp_homelab.setup.proxmox_setup.prompt_int", lambda *a, **kw: 8006)
        monkeypatch.setattr("mcp_homelab.setup.proxmox_setup.prompt_str", lambda _: "admin@pam!mcp")
        monkeypatch.setattr("mcp_homelab.setup.proxmox_setup.prompt_secret", lambda _: "secret")

        yn_calls = iter([False, True])  # verify_ssl=False, test_connection=True
        monkeypatch.setattr("mcp_homelab.setup.proxmox_setup.prompt_yn", lambda *a, **kw: next(yn_calls))

        def fake_test(*args: object, **kwargs: object) -> str:
            nonlocal test_called
            test_called = True
            return "✓ Connected to Proxmox VE 8.2"

        monkeypatch.setattr("mcp_homelab.setup.proxmox_setup._test_connection", fake_test)

        run_proxmox_setup(config_path=config_path)
        assert test_called


class TestProxmoxTestConnection:
    def test_returns_success_on_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeResponse:
            status_code = 200
            def json(self) -> dict:
                return {"data": {"version": "8.2.4"}}

        monkeypatch.setattr("httpx.get", lambda *a, **kw: FakeResponse())
        result = proxmox_test_connection("10.0.0.1", 8006, False, "id", "secret")
        assert "✓" in result
        assert "8.2.4" in result

    def test_returns_error_on_non_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeResponse:
            status_code = 401

        monkeypatch.setattr("httpx.get", lambda *a, **kw: FakeResponse())
        result = proxmox_test_connection("10.0.0.1", 8006, False, "id", "secret")
        assert "✗" in result
        assert "401" in result

    def test_returns_error_on_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*a: object, **kw: object) -> None:
            raise ConnectionError("refused")

        monkeypatch.setattr("httpx.get", boom)
        result = proxmox_test_connection("10.0.0.1", 8006, False, "id", "secret")
        assert "✗" in result


# ===========================================================================
# OPNsense Setup
# ===========================================================================


class TestRunOpnsenseSetup:
    def test_writes_config_and_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_path = _seed_config(tmp_path)

        monkeypatch.setattr("mcp_homelab.setup.opnsense_setup.prompt_ip", lambda _: "10.0.50.1")
        monkeypatch.setattr("mcp_homelab.setup.opnsense_setup.prompt_yn", lambda *a, **kw: False)
        monkeypatch.setattr("mcp_homelab.setup.opnsense_setup.prompt_str", lambda _: "api-key-123")
        monkeypatch.setattr("mcp_homelab.setup.opnsense_setup.prompt_secret", lambda _: "api-secret-456")

        run_opnsense_setup(config_path=config_path)

        # Verify config.yaml
        data = _load_yaml(config_path)
        assert data["opnsense"]["host"] == "10.0.50.1"
        assert data["opnsense"]["verify_ssl"] is False

        # Verify .env
        env_content = (tmp_path / ".env").read_text()
        assert "OPNSENSE_API_KEY=api-key-123" in env_content
        assert "OPNSENSE_API_SECRET=api-secret-456" in env_content

    def test_preserves_existing_sections(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "hosts:\n  pve:\n    ip: 10.0.50.20\n"
            "proxmox:\n  host: 10.0.50.20\n  port: 8006\n",
            encoding="utf-8",
        )
        (tmp_path / ".env").write_text("PROXMOX_TOKEN_ID=existing\n", encoding="utf-8")

        monkeypatch.setattr("mcp_homelab.setup.opnsense_setup.prompt_ip", lambda _: "10.0.50.1")
        monkeypatch.setattr("mcp_homelab.setup.opnsense_setup.prompt_yn", lambda *a, **kw: False)
        monkeypatch.setattr("mcp_homelab.setup.opnsense_setup.prompt_str", lambda _: "key")
        monkeypatch.setattr("mcp_homelab.setup.opnsense_setup.prompt_secret", lambda _: "secret")

        run_opnsense_setup(config_path=config_path)

        data = _load_yaml(config_path)
        assert data["proxmox"]["host"] == "10.0.50.20"
        assert data["opnsense"]["host"] == "10.0.50.1"

        env_content = (tmp_path / ".env").read_text()
        assert "PROXMOX_TOKEN_ID=existing" in env_content

    def test_runs_connection_test_when_accepted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_path = _seed_config(tmp_path)
        test_called = False

        monkeypatch.setattr("mcp_homelab.setup.opnsense_setup.prompt_ip", lambda _: "10.0.50.1")
        monkeypatch.setattr("mcp_homelab.setup.opnsense_setup.prompt_str", lambda _: "key")
        monkeypatch.setattr("mcp_homelab.setup.opnsense_setup.prompt_secret", lambda _: "secret")

        yn_calls = iter([False, True])  # verify_ssl=False, test_connection=True
        monkeypatch.setattr("mcp_homelab.setup.opnsense_setup.prompt_yn", lambda *a, **kw: next(yn_calls))

        def fake_test(*args: object, **kwargs: object) -> str:
            nonlocal test_called
            test_called = True
            return "✓ Connected to OPNsense"

        monkeypatch.setattr("mcp_homelab.setup.opnsense_setup._test_connection", fake_test)

        run_opnsense_setup(config_path=config_path)
        assert test_called


class TestOpnsenseTestConnection:
    def test_returns_success_on_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeResponse:
            status_code = 200

        monkeypatch.setattr("httpx.get", lambda *a, **kw: FakeResponse())
        result = opnsense_test_connection("10.0.0.1", False, "key", "secret")
        assert "✓" in result

    def test_returns_error_on_non_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeResponse:
            status_code = 403

        monkeypatch.setattr("httpx.get", lambda *a, **kw: FakeResponse())
        result = opnsense_test_connection("10.0.0.1", False, "key", "secret")
        assert "✗" in result
        assert "403" in result

    def test_returns_error_on_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*a: object, **kw: object) -> None:
            raise ConnectionError("refused")

        monkeypatch.setattr("httpx.get", boom)
        result = opnsense_test_connection("10.0.0.1", False, "key", "secret")
        assert "✗" in result
