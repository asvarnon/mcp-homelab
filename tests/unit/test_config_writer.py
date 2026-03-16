"""Unit tests for mcp_homelab/setup/config_writer.py.

Tests YAML round-trip writing and .env file updates using real
temporary files. No mocking needed — these are pure file I/O tests.

Java comparison: Testing .properties file read/write utilities.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML


from mcp_homelab.setup.config_writer import (
    _load_yaml,
    upsert_env_var,
    upsert_node,
    upsert_opnsense,
    upsert_proxmox,
)


# ===========================================================================
# _load_yaml
# ===========================================================================


class TestLoadYaml:
    def test_loads_existing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "test.yaml"
        path.write_text("key: value\n", encoding="utf-8")
        _yaml, data = _load_yaml(path)
        assert data["key"] == "value"

    def test_returns_empty_for_missing_file(self, tmp_path: Path) -> None:
        _yaml, data = _load_yaml(tmp_path / "nonexistent.yaml")
        assert len(data) == 0

    def test_returns_empty_for_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        _yaml, data = _load_yaml(path)
        assert len(data) == 0


# ===========================================================================
# upsert_node
# ===========================================================================


class TestUpsertNode:
    def test_creates_hosts_section_if_missing(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text("proxmox:\n  host: 10.0.0.1\n", encoding="utf-8")

        upsert_node(config_path, "gamehost", {
            "hostname": "gamehost",
            "ip": "10.0.50.10",
            "ssh": True,
        })

        yaml = YAML()
        with open(config_path) as f:
            data = yaml.load(f)
        assert "gamehost" in data["hosts"]
        assert data["hosts"]["gamehost"]["ip"] == "10.0.50.10"

    def test_adds_node_to_existing(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "nodes:\n  pve:\n    hostname: pve\n    ip: 10.0.50.20\n"
            "proxmox:\n  host: 10.0.50.20\nopnsense:\n  host: 10.0.50.1\n",
            encoding="utf-8",
        )

        upsert_node(config_path, "gamehost", {
            "hostname": "gamehost",
            "ip": "10.0.50.10",
        })

        yaml = YAML()
        with open(config_path) as f:
            data = yaml.load(f)
        assert "pve" in data["nodes"]
        assert "gamehost" in data["nodes"]

    def test_preserves_existing_sections(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "nodes:\n  pve:\n    hostname: pve\n    ip: 10.0.50.20\n\n"
            "proxmox:\n  host: 10.0.50.20\n\n"
            "opnsense:\n  host: 10.0.50.1\n",
            encoding="utf-8",
        )

        upsert_node(config_path, "gamehost", {
            "hostname": "gamehost",
            "ip": "10.0.50.10",
        })

        yaml = YAML()
        with open(config_path) as f:
            data = yaml.load(f)
        assert data["proxmox"]["host"] == "10.0.50.20"
        assert data["opnsense"]["host"] == "10.0.50.1"

    def test_updates_existing_node(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "nodes:\n  pve:\n    hostname: pve\n    ip: 10.0.50.20\n"
            "proxmox:\n  host: x\nopnsense:\n  host: y\n",
            encoding="utf-8",
        )

        upsert_node(config_path, "pve", {
            "hostname": "pve",
            "ip": "10.0.50.99",  # changed
        })

        yaml = YAML()
        with open(config_path) as f:
            data = yaml.load(f)
        assert data["nodes"]["pve"]["ip"] == "10.0.50.99"


# ===========================================================================
# upsert_proxmox / upsert_opnsense
# ===========================================================================


class TestUpsertProxmox:
    def test_writes_proxmox_section(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text("nodes: {}\nopnsense:\n  host: x\n", encoding="utf-8")

        upsert_proxmox(config_path, "10.0.50.20", port=8006, verify_ssl=False)

        yaml = YAML()
        with open(config_path) as f:
            data = yaml.load(f)
        assert data["proxmox"]["host"] == "10.0.50.20"
        assert data["proxmox"]["port"] == 8006


class TestUpsertOpnsense:
    def test_writes_opnsense_section(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text("nodes: {}\nproxmox:\n  host: x\n", encoding="utf-8")

        upsert_opnsense(config_path, "10.0.50.1")

        yaml = YAML()
        with open(config_path) as f:
            data = yaml.load(f)
        assert data["opnsense"]["host"] == "10.0.50.1"
        assert data["opnsense"]["verify_ssl"] is False


# ===========================================================================
# upsert_env_var
# ===========================================================================


class TestUpsertEnvVar:
    def test_creates_file_if_missing(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        upsert_env_var(env_path, "SSH_USER", "admin")
        assert env_path.read_text().strip() == "SSH_USER=admin"

    def test_appends_new_var(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        env_path.write_text("EXISTING=value\n", encoding="utf-8")
        upsert_env_var(env_path, "NEW_VAR", "new_value")
        content = env_path.read_text()
        assert "EXISTING=value" in content
        assert "NEW_VAR=new_value" in content

    def test_updates_existing_var(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        env_path.write_text("SSH_USER=old\nOTHER=keep\n", encoding="utf-8")
        upsert_env_var(env_path, "SSH_USER", "new")
        content = env_path.read_text()
        assert "SSH_USER=new" in content
        assert "OTHER=keep" in content
        # Should not duplicate
        assert content.count("SSH_USER") == 1

    def test_preserves_comments(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        env_path.write_text("# SSH credentials\nSSH_USER=admin\n", encoding="utf-8")
        upsert_env_var(env_path, "SSH_USER", "root")
        content = env_path.read_text()
        assert "# SSH credentials" in content
        assert "SSH_USER=root" in content
