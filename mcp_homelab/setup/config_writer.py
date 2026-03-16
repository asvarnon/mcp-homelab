"""Safe config.yaml and .env writer using ruamel.yaml for round-trip editing.

Preserves comments, key order, and quoting style when modifying config.yaml.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap


def _load_yaml(path: Path) -> tuple[YAML, CommentedMap]:
    """Load a YAML file preserving comments and formatting.

    Returns the YAML instance (needed for writing) and the parsed data.
    If the file doesn't exist, returns an empty CommentedMap.
    """
    _yaml = YAML()
    _yaml.preserve_quotes = True  # type: ignore[assignment]
    _yaml.allow_unicode = True

    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = _yaml.load(f)
        if data is None:
            data = CommentedMap()
    else:
        data = CommentedMap()

    return _yaml, data


def upsert_node(config_path: Path, name: str, node_data: dict[str, Any]) -> None:
    """Add or update a host entry in config.yaml.

    Preserves all existing comments, formatting, and other sections.
    Supports both 'hosts' (preferred) and legacy 'nodes' key.
    New configs get 'hosts'; existing configs keep whichever key they have.
    """
    _yaml, data = _load_yaml(config_path)

    # Determine which top-level key to use
    if "hosts" in data:
        section_key = "hosts"
    elif "nodes" in data:
        section_key = "nodes"
    else:
        section_key = "hosts"
        data[section_key] = CommentedMap()

    hosts = data[section_key]

    # Strip the trailing blank-line comment from the current last entry's last key.
    # ruamel.yaml stores inter-section blank lines as trailing CommentTokens on the
    # last value of the preceding section.  When we append a new entry, that token
    # would create a spurious gap *inside* the hosts block instead of *after* it.
    old_keys = list(hosts.keys())
    if old_keys:
        last_entry = hosts[old_keys[-1]]
        if hasattr(last_entry, "ca") and last_entry.ca.items:
            last_key = list(last_entry.keys())[-1]
            if last_key in last_entry.ca.items:
                last_entry.ca.items[last_key][2] = None

    hosts[name] = CommentedMap(node_data)

    # Add exactly one blank line before the first section after the hosts key.
    keys = list(data.keys())
    section_idx = keys.index(section_key)
    if section_idx + 1 < len(keys):
        next_key = keys[section_idx + 1]
        data.yaml_set_comment_before_after_key(next_key, before="\n")

    with open(config_path, "wb") as f:
        _yaml.dump(data, f)


def upsert_proxmox(config_path: Path, host: str, port: int = 8006, verify_ssl: bool = False) -> None:
    """Add or update the proxmox section in config.yaml."""
    _yaml, data = _load_yaml(config_path)

    data["proxmox"] = CommentedMap({
        "host": host,
        "port": port,
        "verify_ssl": verify_ssl,
    })

    with open(config_path, "wb") as f:
        _yaml.dump(data, f)


def upsert_opnsense(config_path: Path, host: str, verify_ssl: bool = False) -> None:
    """Add or update the opnsense section in config.yaml."""
    _yaml, data = _load_yaml(config_path)

    data["opnsense"] = CommentedMap({
        "host": host,
        "verify_ssl": verify_ssl,
    })

    with open(config_path, "wb") as f:
        _yaml.dump(data, f)


def upsert_env_var(env_path: Path, key: str, value: str) -> None:
    """Set a key=value in the .env file.

    Updates existing key or appends if not present.
    Preserves comments and other entries.
    """
    lines: list[str] = []
    found = False

    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(f"{key}=") or stripped == key:
                lines[i] = f"{key}={value}\n"
                found = True
                break

    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(f"{key}={value}\n")

    env_path.write_text("".join(lines), encoding="utf-8")
