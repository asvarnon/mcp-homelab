"""Unit tests for core/config.py.

Tests config loading, environment variable validation, bootstrap logic,
and the Pydantic models. File I/O is isolated via tmp_path fixtures.

Java comparison: Testing Spring's @ConfigurationProperties loading.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_homelab.core.config import (
    AppConfig,
    HostConfig,
    OPNsenseConfig,
    ProxmoxConfig,
    bootstrap_config_dir,
    get_config_dir,
    load_config,
    opnsense_configured,
    proxmox_configured,
    validate_env,
)


# ===========================================================================
# Pydantic Models
# ===========================================================================


class TestHostConfig:
    def test_minimal(self) -> None:
        host = HostConfig(hostname="test", ip="198.51.100.1")
        assert host.ssh is False
        assert host.sudo_docker is False
        assert host.vlan is None
        assert host.description == ""
        assert host.type is None
        assert host.os == "linux"

    def test_rejects_invalid_os(self) -> None:
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="'linux'.*'freebsd'"):
            HostConfig(hostname="test", ip="198.51.100.1", os="windows")  # type: ignore[arg-type]

    def test_full(self) -> None:
        host = HostConfig(
            hostname="test-node-1",
            ip="192.0.2.10",
            vlan=50,
            ssh=True,
            ssh_user="admin",
            ssh_key_path="~/.ssh/id_ed25519",
            sudo_docker=True,
            description="Game server",
            type="baremetal",
        )
        assert host.vlan == 50
        assert host.ssh_user == "admin"
        assert host.type == "baremetal"

    def test_rejects_missing_required(self) -> None:
        with pytest.raises(Exception):
            HostConfig()  # type: ignore[call-arg]


class TestAppConfig:
    def test_valid_config(self, sample_config: AppConfig) -> None:
        assert "test-node-1" in sample_config.hosts
        assert sample_config.proxmox is not None
        assert sample_config.proxmox.host == "192.0.2.20"
        assert sample_config.opnsense is not None
        assert sample_config.opnsense.host == "192.0.2.1"

    def test_proxmox_defaults(self) -> None:
        pve = ProxmoxConfig(host="198.51.100.1")
        assert pve.port == 8006
        assert pve.verify_ssl is False

    def test_opnsense_defaults(self) -> None:
        opn = OPNsenseConfig(host="198.51.100.1")
        assert opn.verify_ssl is False

    def test_ssh_only_config(self) -> None:
        """Proxmox and OPNsense sections are optional."""
        config = AppConfig(
            hosts={"box": HostConfig(hostname="box", ip="198.51.100.1")},
        )
        assert config.proxmox is None
        assert config.opnsense is None
        assert "box" in config.hosts

    def test_legacy_nodes_key_accepted(self) -> None:
        """Configs using deprecated 'nodes' key should still load into .hosts."""
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            config = AppConfig.model_validate({
                "nodes": {"box": {"hostname": "box", "ip": "198.51.100.1"}},
            })
        assert "box" in config.hosts
        assert any("deprecated" in str(warning.message).lower() for warning in w)

    def test_null_hosts_coerced_to_empty_dict(self) -> None:
        """YAML 'hosts:' with no value parses as None — model should accept it."""
        config = AppConfig.model_validate({"hosts": None})
        assert config.hosts == {}


# ===========================================================================
# bootstrap_config_dir
# ===========================================================================


class TestBootstrapConfigDir:
    def test_sets_env_when_unset(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("MCP_HOMELAB_CONFIG_DIR", raising=False)
        bootstrap_config_dir(tmp_path)
        assert os.environ["MCP_HOMELAB_CONFIG_DIR"] == str(tmp_path.resolve())

    def test_does_not_overwrite_existing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", "/existing/path")
        bootstrap_config_dir(tmp_path)
        assert os.environ["MCP_HOMELAB_CONFIG_DIR"] == "/existing/path"


# ===========================================================================
# get_config_dir
# ===========================================================================


class TestGetConfigDir:
    def test_returns_env_var_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        result = get_config_dir()
        assert result == tmp_path.resolve()

    def test_falls_back_to_cwd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_HOMELAB_CONFIG_DIR", raising=False)
        result = get_config_dir()
        assert result == Path.cwd()

    def test_resolved_fresh_each_call(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Verify it's not cached — changing the env var changes the result."""
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        first = get_config_dir()

        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(other))
        second = get_config_dir()

        assert first != second


# ===========================================================================
# load_config
# ===========================================================================


class TestLoadConfig:
    def test_loads_from_file(self, tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_config_dir))
        config = load_config()
        assert "test-node-1" in config.hosts
        assert config.proxmox is not None
        assert config.proxmox.host == "192.0.2.20"

    def test_explicit_path(self, tmp_config_dir: Path) -> None:
        config = load_config(tmp_config_dir / "config.yaml")
        assert "test-node-1" in config.hosts

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.yaml")


# ===========================================================================
# validate_env
# ===========================================================================


class TestValidateEnv:
    """validate_env checks env vars conditionally based on config sections.

    It calls load_config() internally, so we need the config dir set up.
    """

    def test_passes_with_all_vars(
        self, tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch, mock_env: None,
    ) -> None:
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_config_dir))
        validate_env()  # Should not raise

    def test_fails_missing_proxmox_token(
        self, tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_config_dir))
        monkeypatch.setenv("OPNSENSE_API_KEY", "k")
        monkeypatch.setenv("OPNSENSE_API_SECRET", "s")
        monkeypatch.delenv("PROXMOX_TOKEN_ID", raising=False)
        monkeypatch.delenv("PROXMOX_TOKEN_SECRET", raising=False)
        with pytest.raises(EnvironmentError, match="PROXMOX_TOKEN_ID"):
            validate_env()

    def test_fails_missing_opnsense_key(
        self, tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_config_dir))
        monkeypatch.setenv("PROXMOX_TOKEN_ID", "t")
        monkeypatch.setenv("PROXMOX_TOKEN_SECRET", "s")
        monkeypatch.delenv("OPNSENSE_API_KEY", raising=False)
        monkeypatch.delenv("OPNSENSE_API_SECRET", raising=False)
        with pytest.raises(EnvironmentError, match="OPNSENSE_API_KEY"):
            validate_env()

    def test_ssh_only_config_needs_no_env_vars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A config with only nodes (no proxmox/opnsense) should pass with zero env vars."""
        from ruamel.yaml import YAML

        ssh_only_config = {
            "hosts": {
                "my-box": {
                    "hostname": "my-box",
                    "ip": "192.168.1.50",
                    "ssh": True,
                    "ssh_user": "admin",
                    "ssh_key_path": "~/.ssh/id_ed25519",
                },
            },
        }
        yaml = YAML()
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(ssh_only_config, f)

        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        # Clear all API env vars
        for var in ("PROXMOX_TOKEN_ID", "PROXMOX_TOKEN_SECRET",
                    "OPNSENSE_API_KEY", "OPNSENSE_API_SECRET"):
            monkeypatch.delenv(var, raising=False)

        validate_env()  # Should not raise — no API sections in config

    def test_proxmox_only_config_ignores_opnsense_vars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Config with proxmox but no opnsense should only require Proxmox vars."""
        from ruamel.yaml import YAML

        config_data = {
            "hosts": {"test-node-2": {"hostname": "test-node-2", "ip": "198.51.100.1"}},
            "proxmox": {"host": "198.51.100.1", "port": 8006, "verify_ssl": False},
        }
        yaml = YAML()
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("PROXMOX_TOKEN_ID", "t")
        monkeypatch.setenv("PROXMOX_TOKEN_SECRET", "s")
        monkeypatch.delenv("OPNSENSE_API_KEY", raising=False)
        monkeypatch.delenv("OPNSENSE_API_SECRET", raising=False)

        validate_env()  # Should not raise — opnsense not in config


# ===========================================================================
# Integration availability helpers
# ===========================================================================


class TestConfiguredHelpers:
    def test_proxmox_configured_true(
        self, tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_config_dir))
        assert proxmox_configured() is True

    def test_proxmox_configured_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from ruamel.yaml import YAML

        yaml = YAML()
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump({"hosts": {"box": {"hostname": "box", "ip": "198.51.100.1"}}}, f)
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        assert proxmox_configured() is False

    def test_opnsense_configured_true(
        self, tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_config_dir))
        assert opnsense_configured() is True

    def test_opnsense_configured_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from ruamel.yaml import YAML

        yaml = YAML()
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump({"hosts": {"box": {"hostname": "box", "ip": "198.51.100.1"}}}, f)
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        assert opnsense_configured() is False
