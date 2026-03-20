"""Configuration loader for mcp-homelab.

Reads host definitions from config.yaml and secrets from environment variables.
No secrets are ever stored in config.yaml.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path
from typing import Any, Literal, NamedTuple

import warnings

from ruamel.yaml import YAML
from dotenv import load_dotenv
from pydantic import AnyHttpUrl, BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


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


class ServerConfig(BaseModel):
    transport: Literal["stdio", "http"] = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    public_url: AnyHttpUrl | None = None


class AppConfig(BaseModel):
    hosts: dict[str, HostConfig] = Field(default_factory=dict)
    proxmox: ProxmoxConfig | None = None
    opnsense: OPNsenseConfig | None = None
    server: ServerConfig = Field(default_factory=ServerConfig)

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_nodes_key(cls, data: dict) -> dict:  # type: ignore[override]
        """Accept 'nodes' as a deprecated alias for 'hosts'.

        Also coerces ``hosts: null`` (common in YAML when key has only
        commented-out entries) to an empty dict so Pydantic doesn't reject it.
        """
        if isinstance(data, dict) and "nodes" in data and "hosts" not in data:
            warnings.warn(
                "Config key 'nodes' is deprecated — rename it to 'hosts' in config.yaml.",
                DeprecationWarning,
                stacklevel=2,
            )
            data["hosts"] = data.pop("nodes")
        if isinstance(data, dict) and data.get("hosts") is None:
            data["hosts"] = {}
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


_IS_POSIX = os.name != "nt"


def _warn_file_permissions(path: Path, max_mode: int, label: str) -> None:
    """Log a warning if *path* has permissions more open than *max_mode*.

    Only group/other bits are checked — owner-only bits (e.g. execute)
    are ignored since they don't affect exposure to other users.
    Skipped on Windows where POSIX permission bits don't apply.
    """
    if not _IS_POSIX or not path.is_file():
        return
    mode = stat.S_IMODE(os.stat(path).st_mode)
    exposure = mode & (stat.S_IRWXG | stat.S_IRWXO)
    if exposure & ~max_mode:
        logger.warning(
            "%s (%s) has mode %04o — expected %04o or stricter. "
            "Run: chmod %04o %s",
            label, path, mode, max_mode, max_mode, path,
        )


def load_env() -> None:
    """Load the .env file from the config directory.

    Must be called before any env-var accessors are used.
    """
    env_path = get_config_dir() / ".env"
    _warn_file_permissions(env_path, 0o600, ".env")
    load_dotenv(env_path)


# The secrets that can be loaded from systemd credentials directory.
_CREDENTIAL_KEYS: tuple[str, ...] = (
    "PROXMOX_TOKEN_ID",
    "PROXMOX_TOKEN_SECRET",
    "OPNSENSE_API_KEY",
    "OPNSENSE_API_SECRET",
    "MCP_CLIENT_ID",
    "MCP_CLIENT_SECRET",
)


def load_from_credentials_dir() -> None:
    """Load secrets from systemd's CREDENTIALS_DIRECTORY if available.

    When the service runs with LoadCredentialEncrypted= directives,
    systemd sets CREDENTIALS_DIRECTORY to a tmpfs path containing
    decrypted credential files. Each file is named after the env var
    (e.g., ``PROXMOX_TOKEN_ID``) and contains the secret value.

    Precedence (highest wins): shell env > credentials > .env.
    Already-set env vars are never overwritten — this allows operators
    to override credentials via the shell or process manager.

    Falls back silently when CREDENTIALS_DIRECTORY is not set (dev
    mode, non-systemd environments).
    """
    credentials_dir_raw = os.environ.get("CREDENTIALS_DIRECTORY")
    if not credentials_dir_raw:
        logger.debug("CREDENTIALS_DIRECTORY not set, skipping credential loading")
        return

    credentials_dir = Path(credentials_dir_raw)
    if not credentials_dir.is_dir():
        logger.warning(
            "CREDENTIALS_DIRECTORY is set but directory does not exist: %s",
            credentials_dir,
        )
        return

    loaded_from_credentials = 0
    loaded_from_environment = 0

    for key in _CREDENTIAL_KEYS:
        if os.environ.get(key):
            logger.info("%s: loaded from environment", key)
            loaded_from_environment += 1
            continue

        credential_path = credentials_dir / key
        if credential_path.is_file():
            try:
                value = credential_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                logger.warning(
                    "Failed to read credential file %s: %s; "
                    "falling back to environment/.env",
                    credential_path,
                    exc,
                )
                continue
            os.environ[key] = value
            logger.info("%s: loaded from credentials directory", key)
            loaded_from_credentials += 1

    logger.info(
        "Credential loading complete: %d from credentials directory, %d from environment",
        loaded_from_credentials,
        loaded_from_environment,
    )


_WILDCARD_HOSTS: frozenset[str] = frozenset({"0.0.0.0", "::", "::0", "0:0:0:0:0:0:0:0"})


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

    if config.server.transport == "http":
        if config.server.host in _WILDCARD_HOSTS and config.server.public_url is None:
            raise EnvironmentError(
                "server.host is '0.0.0.0' (all interfaces) but server.public_url is not set. "
                "The MCP SDK requires a valid public URL for Host header validation. "
                "Set server.public_url to the URL clients will use (e.g., 'http://203.0.113.111:8000')."
            )

        # OAuth client credentials: both or neither.
        client_id = os.environ.get("MCP_CLIENT_ID", "")
        client_secret = os.environ.get("MCP_CLIENT_SECRET", "")
        has_id = bool(client_id)
        has_secret = bool(client_secret)
        if has_id != has_secret:
            raise EnvironmentError(
                "MCP_CLIENT_ID and MCP_CLIENT_SECRET must both be set or both be empty. "
                f"Got: MCP_CLIENT_ID={'set' if has_id else 'empty'}, "
                f"MCP_CLIENT_SECRET={'set' if has_secret else 'empty'}."
            )
        _MIN_CREDENTIAL_LEN = 32
        if has_id and len(client_id) < _MIN_CREDENTIAL_LEN:
            raise EnvironmentError(
                f"MCP_CLIENT_ID must be at least {_MIN_CREDENTIAL_LEN} characters "
                f"(got {len(client_id)}). Use a cryptographically random value."
            )
        if has_secret and len(client_secret) < _MIN_CREDENTIAL_LEN:
            raise EnvironmentError(
                f"MCP_CLIENT_SECRET must be at least {_MIN_CREDENTIAL_LEN} characters "
                f"(got {len(client_secret)}). Use a cryptographically random value."
            )

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
