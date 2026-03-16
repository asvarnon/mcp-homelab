"""Shared input prompt helpers with validation for the setup wizard."""

from __future__ import annotations

import getpass
import ipaddress
import re
from pathlib import Path


def prompt_str(label: str, default: str | None = None) -> str:
    """Prompt for a non-empty string, with optional default."""
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"  {label}{suffix}: ").strip()
        if not value and default:
            return default
        if value:
            return value
        print("    → Value required.")


def prompt_ip(label: str, default: str | None = None) -> str:
    """Prompt for a valid IPv4 or IPv6 address."""
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"  {label}{suffix}: ").strip()
        if not value and default:
            value = default
        try:
            ipaddress.ip_address(value)
            return value
        except ValueError:
            print(f"    → Invalid IP address: {value}")


def prompt_int(label: str, default: int | None = None) -> int:
    """Prompt for an integer, with optional default."""
    suffix = f" [{default}]" if default is not None else ""
    while True:
        value = input(f"  {label}{suffix}: ").strip()
        if not value and default is not None:
            return default
        try:
            return int(value)
        except ValueError:
            print("    → Must be a number.")


def prompt_int_optional(label: str) -> int | None:
    """Prompt for an optional integer. Empty input returns None."""
    value = input(f"  {label} (optional): ").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        print("    → Must be a number or empty. Skipping.")
        return None


def prompt_path(label: str, default: str | None = None) -> str:
    """Prompt for a filesystem path. Validates that it exists (with ~ expansion)."""
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"  {label}{suffix}: ").strip()
        if not value and default:
            value = default
        expanded = Path(value).expanduser()
        if expanded.exists():
            return value
        print(f"    → Path not found: {expanded}")


def prompt_yn(label: str, default: bool = False) -> bool:
    """Prompt for yes/no. Returns True for yes."""
    hint = "Y/n" if default else "y/N"
    value = input(f"  {label} [{hint}]: ").strip().lower()
    if not value:
        return default
    return value in ("y", "yes")


_NODE_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")


def prompt_node_name(label: str, default: str | None = None) -> str:
    """Prompt for a valid node name (alphanumeric + hyphens/underscores)."""
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"  {label}{suffix}: ").strip()
        if not value and default:
            value = default
        if not value:
            print("    → Value required.")
            continue
        if _NODE_NAME_RE.match(value):
            return value
        print("    → Must start with a letter. Only letters, numbers, hyphens, underscores.")


def prompt_secret(label: str) -> str:
    """Prompt for a secret value (hidden input). Never empty."""
    while True:
        value = getpass.getpass(f"  {label}: ").strip()
        if value:
            return value
        print("    → Value required.")
