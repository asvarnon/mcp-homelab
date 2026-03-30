"""Read-only validation of existing mcp-homelab configuration.

Checks SSH connectivity, Docker access, API connections, and dmidecode
for every configured node and integration. No side effects.
"""

from __future__ import annotations

from pathlib import Path

from mcp_homelab.core.config import (
    AppConfig,
    get_config_dir,
    get_opnsense_credentials,
    get_proxmox_token,
    load_config,
)
from mcp_homelab.setup.ssh_helpers import SSHConnectError, connect, detect_capabilities


def _check_node(name: str, ip: str, user: str, key_path: str) -> dict[str, str]:
    """Check a single node's SSH, Docker, and dmidecode status.

    Returns a dict of capability names to status strings (✓ or ✗).
    """
    result: dict[str, str] = {"ssh": "✗", "docker": "✗", "dmidecode": "✗"}

    try:
        client = connect(ip, user, key_path)
    except SSHConnectError:
        return result

    try:
        result["ssh"] = "✓"
        caps = detect_capabilities(client)
        result["docker"] = "✓" if caps.docker else "✗"
        result["dmidecode"] = "✓" if caps.dmidecode else "✗"
    finally:
        client.close()

    return result


def _check_proxmox(config: AppConfig) -> str:
    """Test Proxmox API connectivity. Returns status string."""
    if config.proxmox is None:
        return "✗ not configured"
    try:
        import httpx

        try:
            token = get_proxmox_token()
        except KeyError:
            return "✗ missing API token in .env"

        if not token.token_id or not token.token_secret:
            return "✗ missing API token in .env"

        url = f"https://{config.proxmox.host}:{config.proxmox.port}/api2/json/version"
        headers = {"Authorization": f"PVEAPIToken={token.token_id}={token.token_secret}"}

        resp = httpx.get(url, headers=headers, verify=config.proxmox.verify_ssl, timeout=10)
        if resp.status_code == 200:
            version = resp.json().get("data", {}).get("version", "unknown")
            return f"✓ connected (pve {version})"
        return f"✗ HTTP {resp.status_code}"
    except Exception as exc:
        return f"✗ {exc}"


def _check_opnsense(config: AppConfig) -> str:
    """Test OPNsense API connectivity. Returns status string."""
    if config.opnsense is None:
        return "✗ not configured"
    try:
        import httpx

        try:
            creds = get_opnsense_credentials()
        except KeyError:
            return "✗ missing API credentials in .env"

        if not creds.api_key or not creds.api_secret:
            return "✗ missing API credentials in .env"

        url = f"https://{config.opnsense.host}/api/dhcpv4/leases/searchLease"
        resp = httpx.get(
            url,
            auth=(creds.api_key, creds.api_secret),
            verify=config.opnsense.verify_ssl,
            timeout=10,
        )
        if resp.status_code == 200:
            return "✓ connected"
        return f"✗ HTTP {resp.status_code}"
    except Exception as exc:
        return f"✗ {exc}"


def run_check(config_path: Path | None = None) -> None:
    """Run a full read-only health check against existing config.

    Args:
        config_path: Path to config.yaml. Defaults to cwd/config.yaml.
    """
    if config_path is None:
        config_path = get_config_dir() / "config.yaml"

    if not config_path.exists():
        print(f"  ✗ Config not found: {config_path}")
        print("  Run 'mcp-homelab init' first.")
        return

    # Load .env so API credentials are available
    from dotenv import load_dotenv
    env_path = config_path.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    config = load_config(config_path)

    node_count = len(config.hosts)
    has_proxmox = bool(
        config.proxmox and config.proxmox.host and config.proxmox.host != "0.0.0.0"
    )
    has_opnsense = bool(
        config.opnsense and config.opnsense.host and config.opnsense.host != "0.0.0.0"
    )

    parts = [f"{node_count} host{'s' if node_count != 1 else ''}"]
    if has_proxmox:
        parts.append("proxmox")
    if has_opnsense:
        parts.append("opnsense")

    print()
    print(f"Config: {config_path} \u2713 ({', '.join(parts)})")
    print()

    # Hosts
    print("Hosts:")
    all_ok = True

    for name, node in config.hosts.items():
        if not node.ssh:
            print(f"  {name:<15} SSH disabled (skipped)")
            continue

        user = node.ssh_user or ""
        key_path = node.ssh_key_path or ""
        if not user or not key_path:
            print(f"  {name:<15} SSH ✗ (missing ssh_user or ssh_key_path)")
            all_ok = False
            continue

        status = _check_node(name, node.ip, user, key_path)
        line = f"  {name:<15} SSH {status['ssh']}  Docker {status['docker']}  dmidecode {status['dmidecode']}"
        print(line)
        if status["ssh"] == "✗":
            all_ok = False

    print()

    # Proxmox
    if has_proxmox:
        proxmox_status = _check_proxmox(config)
        print(f"Proxmox API: {proxmox_status}")
        if proxmox_status.startswith("✗"):
            all_ok = False
    else:
        print("Proxmox API: not configured")

    # OPNsense
    if has_opnsense:
        opnsense_status = _check_opnsense(config)
        print(f"OPNsense API: {opnsense_status}")
        if opnsense_status.startswith("✗"):
            all_ok = False
    else:
        print("OPNsense API: not configured")

    print()
    if all_ok:
        print("All checks passed.")
    else:
        print("Some checks failed. Fix the issues above and re-run: mcp-homelab setup check")
