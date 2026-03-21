"""Tools package — shared helpers for tool modules."""

from __future__ import annotations


def _not_configured_error(integration: str, config_key: str, env_vars: str) -> dict[str, str]:
    """Return a standardised 'not configured' error dict.

    Args:
        integration: Human-readable name (e.g. "OPNsense", "Proxmox").
        config_key: config.yaml section name (e.g. "opnsense", "proxmox").
        env_vars: Required env var names for the error message.
    """
    return {
        "error": (
            f"{integration} is not configured. Add a '{config_key}' section "
            f"to config.yaml and set {env_vars} in .env."
        ),
    }
