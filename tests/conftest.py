"""Shared fixtures for mcp-homelab tests.

Provides reusable test scaffolding: temp config dirs, mock SSH clients,
sample config objects, and environment variable helpers.

These are the equivalent of JUnit @BeforeAll / @BeforeEach fixtures.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from core.config import AppConfig, HostConfig, OPNsenseConfig, ProxmoxConfig


# ---------------------------------------------------------------------------
# Sample data — reusable across all test modules
# ---------------------------------------------------------------------------

SAMPLE_NODE_CONFIG: dict[str, Any] = {
    "hostname": "test-node-1",
    "ip": "192.0.2.10",
    "vlan": 50,
    "ssh": True,
    "ssh_user": "admin",
    "ssh_key_path": "~/.ssh/id_ed25519",
    "sudo_docker": True,
    "description": "Game server node",
}

SAMPLE_CONFIG_DICT: dict[str, Any] = {
    "hosts": {
        "test-node-1": SAMPLE_NODE_CONFIG,
        "test-node-2": {
            "hostname": "test-node-2",
            "ip": "192.0.2.20",
            "vlan": 50,
            "ssh": True,
            "ssh_user": "root",
            "ssh_key_path": "~/.ssh/id_ed25519",
            "sudo_docker": False,
            "description": "Proxmox hypervisor",
        },
    },
    "proxmox": {"host": "192.0.2.20", "port": 8006, "verify_ssl": False},
    "opnsense": {"host": "192.0.2.1", "verify_ssl": False},
}

SAMPLE_ENV_VARS: dict[str, str] = {
    "PROXMOX_TOKEN_ID": "test@pam!mcp",
    "PROXMOX_TOKEN_SECRET": "00000000-0000-0000-0000-000000000000",
    "OPNSENSE_API_KEY": "test-key",
    "OPNSENSE_API_SECRET": "test-secret",
    "SSH_USER": "admin",
    "SSH_KEY_PATH": "~/.ssh/id_ed25519",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_config() -> AppConfig:
    """Return a fully-populated AppConfig for testing."""
    return AppConfig(**SAMPLE_CONFIG_DICT)


@pytest.fixture()
def tmp_config_dir(tmp_path: Path) -> Path:
    """Create a temp directory with config.yaml and .env files.

    Returns the directory path. Think of this as a disposable test workspace.
    """
    from ruamel.yaml import YAML

    # Write config.yaml
    yaml = YAML()
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(SAMPLE_CONFIG_DICT, f)

    # Write .env
    env_path = tmp_path / ".env"
    env_lines = [f"{k}={v}" for k, v in SAMPLE_ENV_VARS.items()]
    env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")

    return tmp_path


@pytest.fixture()
def env_vars() -> dict[str, str]:
    """Inject sample env vars for the duration of a test.

    Uses monkeypatch-style context manager to set and restore.
    """
    return SAMPLE_ENV_VARS.copy()


@pytest.fixture()
def mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set all required environment variables for the test."""
    for key, value in SAMPLE_ENV_VARS.items():
        monkeypatch.setenv(key, value)


@pytest.fixture()
def mock_ssh_client() -> MagicMock:
    """Return a mock paramiko.SSHClient with standard wiring."""
    client = MagicMock()
    transport = MagicMock()
    transport.is_active.return_value = True
    client.get_transport.return_value = transport

    # Default: command succeeds with empty output
    stdout = MagicMock()
    stdout.read.return_value = b""
    stdout.channel.recv_exit_status.return_value = 0
    stderr = MagicMock()
    stderr.read.return_value = b""
    client.exec_command.return_value = (MagicMock(), stdout, stderr)

    return client


def make_ssh_output(text: str) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Helper to build exec_command return value with given stdout text."""
    stdin = MagicMock()
    stdout = MagicMock()
    stdout.read.return_value = text.encode()
    stdout.channel.recv_exit_status.return_value = 0
    stderr = MagicMock()
    stderr.read.return_value = b""
    return stdin, stdout, stderr
