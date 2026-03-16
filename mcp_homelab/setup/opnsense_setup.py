"""Guided OPNsense API setup wizard.

Walks the user through creating an OPNsense API key pair and configuring
the connection in config.yaml and .env.
"""

from __future__ import annotations

from pathlib import Path

from core.config import get_config_dir

from mcp_homelab.setup.config_writer import upsert_env_var, upsert_opnsense
from mcp_homelab.setup.prompts import prompt_ip, prompt_secret, prompt_str, prompt_yn


def _test_connection(host: str, verify_ssl: bool, api_key: str, api_secret: str) -> str:
    """Quick connectivity test against the OPNsense API. Returns status string."""
    try:
        import httpx

        url = f"https://{host}/api/core/firmware/status"
        resp = httpx.get(url, auth=(api_key, api_secret), verify=verify_ssl, timeout=10)
        if resp.status_code == 200:
            return "✓ Connected to OPNsense"
        return f"✗ HTTP {resp.status_code}"
    except Exception as exc:
        return f"✗ {exc}"


def run_opnsense_setup(config_path: Path | None = None) -> None:
    """Interactive OPNsense API configuration flow.

    Args:
        config_path: Path to config.yaml. Defaults to cwd/config.yaml.
    """
    if config_path is None:
        config_path = get_config_dir() / "config.yaml"

    env_path = config_path.parent / ".env"

    print()
    print("─── OPNsense API Setup ───")
    print()
    print("This configures the OPNsense API connection.")
    print("You'll need an API key + secret pair. To create one:")
    print()
    print("  1. Log in to OPNsense web UI (https://<host>)")
    print("  2. Go to System → Access → Users")
    print("  3. Edit your user (or create a dedicated API user)")
    print("  4. Scroll to 'API keys' and click the + button")
    print("  5. Download the key file — it contains both key and secret")
    print()

    # Collect inputs
    host = prompt_ip("OPNsense host IP")
    verify_ssl = prompt_yn("Verify SSL", default=False)
    print()
    api_key = prompt_str("API Key")
    api_secret = prompt_secret("API Secret")

    # Write config.yaml
    print()
    print("[1/2] Writing config.yaml...")
    upsert_opnsense(config_path, host=host, verify_ssl=verify_ssl)
    print("  → Updated opnsense section ✓")

    # Write .env
    print()
    print("[2/2] Writing .env...")
    upsert_env_var(env_path, "OPNSENSE_API_KEY", api_key)
    upsert_env_var(env_path, "OPNSENSE_API_SECRET", api_secret)
    print("  → Updated OPNSENSE_API_KEY ✓")
    print("  → Updated OPNSENSE_API_SECRET ✓")

    # Optional connectivity test
    print()
    if prompt_yn("Test connection now?", default=True):
        status = _test_connection(host, verify_ssl, api_key, api_secret)
        print(f"  → {status}")

    print()
    print("Done. Run 'mcp-homelab setup check' to verify all integrations.")
