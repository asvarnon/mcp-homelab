"""mcp-homelab — MCP server entry point.

Registers all tools from the tools/ package and serves them
via the Anthropic MCP Python SDK.

Can be run directly (``python server.py``) or via the CLI
(``mcp-homelab serve``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Bootstrap config dir from server.py location so MCP clients
# that spawn from a foreign cwd can find config.yaml and .env.
from core.config import bootstrap_config_dir
bootstrap_config_dir(Path(__file__).resolve().parent)

from mcp.server.fastmcp import FastMCP

from tools import nodes, proxmox, opnsense, discovery, context_gen
from tools.nodes import NodeSummary, NodeStatus, ContainerInfo

mcp = FastMCP("homelab")

# ---------------------------------------------------------------------------
# Node tools (SSH)
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_nodes() -> list[NodeSummary]:
    """List all configured homelab nodes. Call this first to discover valid node names for other tools."""
    return await nodes.list_nodes()


@mcp.tool()
async def get_node_status(hostname: str) -> NodeStatus:
    """Get uptime, CPU%, RAM, and disk usage for a homelab node. Use list_nodes to get valid hostname values."""
    return await nodes.get_node_status(hostname)


@mcp.tool()
async def list_containers(hostname: str) -> list[ContainerInfo]:
    """List Docker containers running on a homelab node. Use list_nodes to get valid hostname values."""
    return await nodes.list_containers(hostname)


@mcp.tool()
async def get_container_logs(hostname: str, container: str, lines: int = 50) -> str:
    """Get the last N lines of logs from a Docker container. Use list_nodes to get valid hostname values."""
    return await nodes.get_container_logs(hostname, container, lines)


@mcp.tool()
async def restart_container(hostname: str, container: str) -> str:
    """Restart a Docker container on a homelab node. Use list_nodes to get valid hostname values."""
    return await nodes.restart_container(hostname, container)


# ---------------------------------------------------------------------------
# Proxmox tools (REST API)
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_vms() -> list[dict]:
    """List all VMs on the Proxmox hypervisor with status and resource info."""
    return await proxmox.list_vms()


@mcp.tool()
async def get_vm_status(vmid: int) -> dict:
    """Get detailed status for a specific Proxmox VM."""
    return await proxmox.get_vm_status(vmid)


@mcp.tool()
async def start_vm(vmid: int) -> str:
    """Start a stopped Proxmox VM."""
    return await proxmox.start_vm(vmid)


@mcp.tool()
async def stop_vm(vmid: int) -> str:
    """Gracefully stop a running Proxmox VM."""
    return await proxmox.stop_vm(vmid)


# ---------------------------------------------------------------------------
# OPNsense tools (REST API)
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_dhcp_leases() -> list[dict]:
    """Get active DHCP leases from OPNsense."""
    return await opnsense.get_dhcp_leases()


@mcp.tool()
async def get_interface_status() -> list[dict]:
    """Get interface names, IPs, and up/down state from OPNsense."""
    return await opnsense.get_interface_status()


@mcp.tool()
async def get_firewall_aliases() -> list[dict]:
    """Get firewall alias definitions from OPNsense."""
    return await opnsense.get_firewall_aliases()


# ---------------------------------------------------------------------------
# Discovery tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def scan_infrastructure() -> dict:
    """Scan the entire homelab and return a topology snapshot. Call this first to understand what nodes, VMs, containers, and network interfaces exist. Subsystems that are unreachable will show an error instead of failing the whole scan."""
    return await discovery.scan_infrastructure()


@mcp.tool()
async def generate_context() -> dict:
    """Generate or refresh a documentation workspace from live infrastructure data. Creates Markdown files under context/generated/. Previous versions are archived automatically. Run this after infrastructure changes to keep docs current."""
    scan = await discovery.scan_infrastructure()
    return await context_gen.generate_context(scan)


@mcp.tool()
async def list_context_files() -> dict:
    """List all files in the context directory. Returns a manifest with file paths, sizes, and last-modified dates. Use this to discover what documentation exists — both generated infrastructure docs and any user-curated content — before reading specific files."""
    return await context_gen.list_context_files()


if __name__ == "__main__":
    from core.config import load_env, validate_env
    load_env()
    validate_env()
    mcp.run()
