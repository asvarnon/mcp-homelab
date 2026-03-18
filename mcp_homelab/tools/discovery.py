"""Infrastructure discovery tool.

Provides a single composite scan that returns a topology snapshot —
all nodes, running containers, VMs, and network interfaces in one call.
Designed as a bootstrapping tool for agent cold-start sessions.

Individual subsystem failures are captured and reported inline rather
than aborting the entire scan.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Coroutine, Sequence
from typing import Any

from mcp_homelab.tools import nodes, proxmox, opnsense
from mcp_homelab.tools.nodes import NodeSummary
from mcp_homelab.core.config import proxmox_configured, opnsense_configured

logger = logging.getLogger(__name__)


async def _safe_call(label: str, coro: Awaitable[Any]) -> tuple[str, Any]:
    """Execute an async callable, returning (label, result|error_dict).

    On exception, returns a dict with 'error' key instead of propagating.
    """
    try:
        result = await coro
        return label, result
    except Exception as exc:
        logger.warning("scan: %s failed: %s", label, exc)
        return label, {"error": f"{type(exc).__name__}: {exc}"}


def _extract_by_prefix(
    prefix: str,
    result_map: dict[str, Any],
    node_list: Sequence[NodeSummary],
) -> dict[str, Any]:
    """Extract per-node results from result_map by label prefix."""
    out: dict[str, Any] = {}
    for node in node_list:
        key = f"{prefix}:{node['name']}"
        if key in result_map:
            out[node["name"]] = result_map[key]
    return out


async def scan_infrastructure() -> dict[str, Any]:
    """Return a topology snapshot of the entire homelab.

    Gathers data from all subsystems in parallel with per-subsystem
    error isolation.  If a subsystem is unreachable, its section
    contains an error message instead of data.

    Returns:
        Dict with keys:
        - nodes: list of node summaries (from config)
        - node_status: dict mapping hostname → status or error
        - containers: dict mapping hostname → container list or error
        - hardware: dict mapping hostname → hardware specs or error
        - vms: list of VMs or error dict
        - interfaces: list of network interfaces or error dict
    """
    # Phase 1: Get node list (from config — never fails)
    node_list = await nodes.list_nodes()

    # Phase 2: Parallel fetch — status, containers, VMs, interfaces
    tasks: list[Coroutine[Any, Any, tuple[str, Any]]] = []

    # Per-node queries
    for node in node_list:
        name = node["name"]
        if node.get("ssh_enabled"):
            tasks.append(_safe_call(f"status:{name}", nodes.get_node_status(name)))
            tasks.append(_safe_call(f"hardware:{name}", nodes.get_hardware_specs(name)))
            if node.get("docker_enabled"):
                tasks.append(_safe_call(f"containers:{name}", nodes.list_containers(name)))

    # Cluster-wide queries (only if configured)
    if proxmox_configured():
        tasks.append(_safe_call("vms", proxmox.list_vms()))
    if opnsense_configured():
        tasks.append(_safe_call("interfaces", opnsense.get_interface_status()))

    results = await asyncio.gather(*tasks)
    result_map: dict[str, Any] = {label: data for label, data in results}

    # Phase 3: Assemble structured response
    node_status = _extract_by_prefix("status", result_map, node_list)
    containers = _extract_by_prefix("containers", result_map, node_list)
    hardware = _extract_by_prefix("hardware", result_map, node_list)

    # Filter interfaces to only active, assigned ones
    raw_interfaces = result_map.get("interfaces", {"error": "OPNsense not configured"})
    if isinstance(raw_interfaces, list):
        interfaces: Any = [
            iface for iface in raw_interfaces
            if iface.get("name") and iface.get("status") == "up"
        ]
    else:
        interfaces = raw_interfaces

    return {
        "nodes": node_list,
        "node_status": node_status,
        "containers": containers,
        "hardware": hardware,
        "vms": result_map.get("vms", {"error": "Proxmox not configured"}),
        "interfaces": interfaces,
    }
