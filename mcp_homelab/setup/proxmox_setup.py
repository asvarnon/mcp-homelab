"""Guided Proxmox VE API setup wizard.

Walks the user through creating a Proxmox API token and configuring
the connection in config.yaml and .env.
"""

from __future__ import annotations

from pathlib import Path

from core.config import get_config_dir

from mcp_homelab.setup.config_writer import upsert_env_var, upsert_proxmox
from mcp_homelab.setup.prompts import prompt_int, prompt_ip, prompt_secret, prompt_str, prompt_yn


def _test_connection(host: str, port: int, verify_ssl: bool, token_id: str, token_secret: str) -> str:
    """Quick connectivity test against the Proxmox API. Returns status string."""
    try:
        import httpx

        url = f"https://{host}:{port}/api2/json/version"
        headers = {"Authorization": f"PVEAPIToken={token_id}={token_secret}"}
        resp = httpx.get(url, headers=headers, verify=verify_ssl, timeout=10)
        if resp.status_code == 200:
            version = resp.json().get("data", {}).get("version", "unknown")
            return f"✓ Connected to Proxmox VE {version}"
        return f"✗ HTTP {resp.status_code}"
    except Exception as exc:
        return f"✗ {exc}"


def run_proxmox_setup(config_path: Path | None = None) -> None:
    """Interactive Proxmox API configuration flow.

    Args:
        config_path: Path to config.yaml. Defaults to cwd/config.yaml.
    """
    if config_path is None:
        config_path = get_config_dir() / "config.yaml"

    env_path = config_path.parent / ".env"

    print()
    print("─── Proxmox API Setup ───")
    print()
    print("This configures the Proxmox VE API connection.")
    print("You'll need an API token. To create one:")
    print()
    print("  1. Log in to Proxmox web UI (https://<host>:8006)")
    print("  2. Go to Datacenter → Permissions → API Tokens")
    print("  3. Click 'Add'")
    print("  4. Select a user (e.g., admin@pam) and name the token")
    print("  5. IMPORTANT: Uncheck 'Privilege Separation' for full access")
    print("  6. Copy the Token ID and Secret — the secret is only shown once")
    print()

    # Collect inputs
    host = prompt_ip("Proxmox host IP")
    port = prompt_int("Port", default=8006)
    verify_ssl = prompt_yn("Verify SSL", default=False)
    print()
    token_id = prompt_str("API Token ID (user@realm!token)")
    token_secret = prompt_secret("API Token Secret")

    # Write config.yaml
    print()
    print("[1/2] Writing config.yaml...")
    upsert_proxmox(config_path, host=host, port=port, verify_ssl=verify_ssl)
    print("  → Updated proxmox section ✓")

    # Write .env
    print()
    print("[2/2] Writing .env...")
    upsert_env_var(env_path, "PROXMOX_TOKEN_ID", token_id)
    upsert_env_var(env_path, "PROXMOX_TOKEN_SECRET", token_secret)
    print("  → Updated PROXMOX_TOKEN_ID ✓")
    print("  → Updated PROXMOX_TOKEN_SECRET ✓")

    # Optional connectivity test
    print()
    if prompt_yn("Test connection now?", default=True):
        status = _test_connection(host, port, verify_ssl, token_id, token_secret)
        print(f"  → {status}")

    print()
    print("Done. Run 'mcp-homelab setup check' to verify all integrations.")
