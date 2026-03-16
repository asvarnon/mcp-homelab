"""Interactive node setup: SSH test, capability detection, config write.

Walks the user through adding or reconfiguring a node in config.yaml.
"""

from __future__ import annotations

from pathlib import Path

from core.config import get_config_dir

from mcp_homelab.setup.prompts import (
    prompt_ip,
    prompt_int_optional,
    prompt_node_name,
    prompt_path,
    prompt_str,
    prompt_yn,
)
from mcp_homelab.setup.config_writer import upsert_node
from mcp_homelab.setup.ssh_helpers import (
    Capabilities,
    SSHConnectError,
    connect,
    detect_capabilities,
)


def _print_capabilities(caps: Capabilities) -> None:
    """Print detected capabilities with check/cross marks."""

    def _mark(ok: bool) -> str:
        return "✓" if ok else "✗"

    docker_detail = ""
    if caps.docker and caps.docker_needs_sudo:
        docker_detail = " (needs sudo)"
    elif not caps.docker:
        docker_detail = " not found"

    print(f"  → Docker:    {_mark(caps.docker)}{docker_detail}")
    print(f"  → Proxmox:   {_mark(caps.proxmox)}{'' if caps.proxmox else ' not a Proxmox node'}")
    print(f"  → OPNsense:  {_mark(caps.opnsense)}{'' if caps.opnsense else ' not an OPNsense node'}")

    if caps.dmidecode:
        print("  → dmidecode: ✓")
    elif caps.dmidecode_needs_sudo_fix:
        print("  → dmidecode: ✗ needs passwordless sudo")
    else:
        print("  → dmidecode: ✗ not installed")


def _print_sudoers_instructions(user: str, host: str) -> None:
    """Print manual instructions for setting up dmidecode sudoers."""
    print()
    print("  dmidecode needs passwordless sudo. Run this on the node:")
    print()
    print(f"    ssh {user}@{host}")
    print(f"    echo '{user} ALL=(ALL) NOPASSWD: /usr/sbin/dmidecode' | sudo tee /etc/sudoers.d/mcp-homelab")
    print("    sudo visudo -cf /etc/sudoers.d/mcp-homelab")
    print()
    print("  Then re-run: mcp-homelab setup check")


def run_node_setup(name: str | None = None, config_path: Path | None = None) -> None:
    """Interactive node setup flow.

    Args:
        name: Pre-supplied node name (skips the name prompt if given).
        config_path: Path to config.yaml. Defaults to cwd/config.yaml.
    """
    if config_path is None:
        config_path = get_config_dir() / "config.yaml"

    print()
    print("─── Node Setup ───")
    print()

    # Step 0: Node identity
    if name is None:
        name = prompt_node_name("Node name")
    else:
        print(f"  Node name: {name}")

    ip = prompt_ip("IP address")
    vlan = prompt_int_optional("VLAN")
    ssh_user = prompt_str("SSH user")
    ssh_key_path = prompt_path("SSH key path", default="~/.ssh/id_ed25519")

    # Step 1: Test SSH
    print()
    print("[1/3] Testing SSH connectivity...")
    try:
        client = connect(ip, ssh_user, ssh_key_path)
    except SSHConnectError as exc:
        print(f"  → ✗ {exc}")
        print()
        print("Fix the SSH issue and try again.")
        return

    try:
        print(f"  → ✓ Connected as {ssh_user}@{ip}")

        # Step 2: Detect capabilities
        print()
        print("[2/3] Detecting capabilities...")
        caps = detect_capabilities(client)
        _print_capabilities(caps)

        # Step 3: Sudoers instructions if needed
        if caps.dmidecode_needs_sudo_fix:
            if prompt_yn("Show sudoers setup instructions?", default=True):
                _print_sudoers_instructions(ssh_user, ip)
    finally:
        client.close()

    # Step 4: Write config
    print()
    print("[3/3] Writing config entry...")

    node_data: dict[str, object] = {
        "hostname": name,
        "ip": ip,
        "ssh": True,
        "ssh_user": ssh_user,
        "ssh_key_path": ssh_key_path,
    }
    if vlan is not None:
        node_data["vlan"] = vlan
    if caps.docker:
        node_data["sudo_docker"] = caps.docker_needs_sudo

    description = prompt_str("Description (short)", default=f"{name} node")
    node_data["description"] = description

    upsert_node(config_path, name, node_data)
    print(f"  → Updated config.yaml: hosts.{name} ✓")
    print()
    print(f"Node '{name}' ready.")
