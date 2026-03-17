"""Configuration loader for mcp-homelab.

Reads host definitions from config.yaml and secrets from environment variables.
No secrets are ever stored in config.yaml.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, NamedTuple

import warnings

from ruamel.yaml import YAML
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator


class HostConfig(BaseModel):
    hostname: str
    ip: str
    vlan: int | None = None
    ssh: bool = False
    ssh_user: str | None = None
    ssh_key_path: str | None = None
    docker: bool = False
    sudo_docker: bool = False
    description: str = ""
    type: str | None = None  # optional: baremetal, vm, container
    os: Literal["linux", "freebsd"] = "linux"


# Backward-compatible alias — existing code that imports NodeConfig still works.
NodeConfig = HostConfig


class ProxmoxConfig(BaseModel):
    host: str
    port: int = 8006
    verify_ssl: bool = False
    default_node: str | None = None
    default_storage: str = "local-lvm"
    default_bridge: str = "vmbr0"


class OPNsenseConfig(BaseModel):
    host: str
    verify_ssl: bool = False


class AppConfig(BaseModel):
    hosts: dict[str, HostConfig] = Field(default_factory=dict)
    proxmox: ProxmoxConfig | None = None
    opnsense: OPNsenseConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_nodes_key(cls, data: dict) -> dict:  # type: ignore[override]
        """Accept 'nodes' as a deprecated alias for 'hosts'."""
        if isinstance(data, dict) and "nodes" in data and "hosts" not in data:
            warnings.warn(
                "Config key 'nodes' is deprecated — rename it to 'hosts' in config.yaml.",
                DeprecationWarning,
                stacklevel=2,
            )
            data["hosts"] = data.pop("nodes")
        return data


_config: AppConfig | None = None


# --- .env loading and startup validation ---


def bootstrap_config_dir(anchor: Path) -> None:
    """Set MCP_HOMELAB_CONFIG_DIR if not already set.

    Call this early in entry points (server.py, cli.py) to ensure
    config files are found even when spawned from a foreign cwd.
    """
    if not os.environ.get("MCP_HOMELAB_CONFIG_DIR"):
        os.environ["MCP_HOMELAB_CONFIG_DIR"] = str(anchor.resolve())


def get_config_dir() -> Path:
    """Return the configuration directory path.

    Resolved fresh on each call so that callers who set
    MCP_HOMELAB_CONFIG_DIR after import still get the right path.

    Resolution order:
        1. MCP_HOMELAB_CONFIG_DIR env var (absolute path)
        2. Current working directory
    """
    env_dir = os.environ.get("MCP_HOMELAB_CONFIG_DIR", "")
    if env_dir:
        return Path(env_dir).resolve()
    return Path.cwd()


def load_env() -> None:
    """Load the .env file from the config directory.

    Must be called before any env-var accessors are used.
    """
    load_dotenv(get_config_dir() / ".env")


def validate_env() -> None:
    """Check that required environment variables are set.

    SSH_USER and SSH_KEY_PATH are optional if every node in config.yaml
    has per-node ssh_user and ssh_key_path defined. They serve as
    fallback defaults for nodes that don't specify their own.

    Raises:
        EnvironmentError: If any required vars are missing or empty.
    """
    # Only require API tokens if the corresponding config section exists.
    # SSH-only setups (no proxmox/opnsense in config.yaml) need no env vars.
    config = load_config()
    missing: list[str] = []

    if config.proxmox is not None:
        for var in ("PROXMOX_TOKEN_ID", "PROXMOX_TOKEN_SECRET"):
            if not os.environ.get(var):
                missing.append(var)

    if config.opnsense is not None:
        for var in ("OPNSENSE_API_KEY", "OPNSENSE_API_SECRET"):
            if not os.environ.get(var):
                missing.append(var)

    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}"
        )


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load and validate config.yaml.

    Args:
        config_path: Explicit path to config.yaml.  Defaults to the
                     config directory (cwd or MCP_HOMELAB_CONFIG_DIR).

    Returns:
        Validated AppConfig instance.
    """
    if config_path is None:
        config_path = get_config_dir() / "config.yaml"

    if not config_path.is_file():
        raise FileNotFoundError(
            f"Config file not found: {config_path}\n"
            f"Run 'mcp-homelab init' to generate one, or copy config.yaml.example."
        )

    _yaml = YAML()
    with open(config_path, "r") as f:
        raw: dict[str, Any] = _yaml.load(f)

    global _config
    _config = AppConfig(**raw)
    return _config


# --- Environment variable accessors ---

def get_ssh_user() -> str:
    value = os.environ.get("SSH_USER")
    if not value:
        raise EnvironmentError(
            "SSH_USER not set. Either set it in .env as a global default, "
            "or add ssh_user to each node in config.yaml."
        )
    return value


def get_ssh_key_path() -> Path:
    value = os.environ.get("SSH_KEY_PATH")
    if not value:
        raise EnvironmentError(
            "SSH_KEY_PATH not set. Either set it in .env as a global default, "
            "or add ssh_key_path to each node in config.yaml."
        )
    return Path(value)


class ProxmoxToken(NamedTuple):
    token_id: str
    token_secret: str


def get_proxmox_token() -> ProxmoxToken:
    """Returns named (token_id, token_secret)."""
    return ProxmoxToken(os.environ["PROXMOX_TOKEN_ID"], os.environ["PROXMOX_TOKEN_SECRET"])


def get_proxmox_token_id() -> str:
    return os.environ["PROXMOX_TOKEN_ID"]


def get_proxmox_token_secret() -> str:
    return os.environ["PROXMOX_TOKEN_SECRET"]


def get_proxmox_config() -> ProxmoxConfig | None:
    """Return the ProxmoxConfig from the loaded AppConfig, or None."""
    return _config.proxmox if _config else None


class OPNsenseCredentials(NamedTuple):
    api_key: str
    api_secret: str


def get_opnsense_credentials() -> OPNsenseCredentials:
    """Returns named (api_key, api_secret)."""
    return OPNsenseCredentials(os.environ["OPNSENSE_API_KEY"], os.environ["OPNSENSE_API_SECRET"])


# --- Integration availability helpers ---


def proxmox_configured() -> bool:
    """Return True if a proxmox section is present in config.yaml."""
    return load_config().proxmox is not None


def opnsense_configured() -> bool:
    """Return True if an opnsense section is present in config.yaml."""
    return load_config().opnsense is not None
