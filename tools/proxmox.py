"""Proxmox VE REST API tools.

Tools for querying and managing VMs via the Proxmox API.
All functions use ProxmoxClient for transport — no direct httpx usage here.
"""

from __future__ import annotations

from core.config import proxmox_configured
from core.proxmox_api import ProxmoxClient, ProxmoxAPIError

_client = ProxmoxClient()

_NOT_CONFIGURED: dict[str, str] = {
    "error": "Proxmox is not configured. Add a 'proxmox' section to config.yaml and set PROXMOX_TOKEN_ID / PROXMOX_TOKEN_SECRET in .env."
}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

async def _find_vm_node(vmid: int) -> str:
    """Locate which PVE node a VM lives on.

    Uses the cluster resources endpoint to find the VM without
    needing to iterate nodes manually.

    Args:
        vmid: Proxmox VM ID.

    Returns:
        Node name string.

    Raises:
        ValueError: If the vmid is not found in the cluster.
    """
    resources = await _client.get("/cluster/resources?type=vm")
    for resource in resources:
        if resource.get("vmid") == vmid:
            return resource["node"]
    raise ValueError(f"VM {vmid} not found in cluster")


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------

async def list_vms() -> list[dict]:
    """Return all VMs with ID, name, status, and resource allocation.

    Queries every PVE node and aggregates the results.

    Returns:
        List of dicts with keys: vmid, name, status, cpus, memory_mb.
    """
    if not proxmox_configured():
        return [_NOT_CONFIGURED]

    nodes = await _client.get_nodes()
    vms: list[dict] = []

    for node in nodes:
        data = await _client.get(f"/nodes/{node}/qemu")
        for vm in data:
            vms.append({
                "vmid": vm["vmid"],
                "name": vm.get("name", ""),
                "status": vm.get("status", "unknown"),
                "cpus": vm.get("cpus", 0),
                "memory_mb": round(vm.get("maxmem", 0) / 1_048_576),
            })

    return vms


async def get_vm_status(vmid: int) -> dict:
    """Return detailed status for a specific VM.

    Args:
        vmid: Proxmox VM ID.

    Returns:
        Dict with keys: vmid, name, status, uptime_seconds,
        cpu_usage_percent, memory_used_mb, memory_total_mb.
    """
    if not proxmox_configured():
        return _NOT_CONFIGURED

    node = await _find_vm_node(vmid)
    data = await _client.get(f"/nodes/{node}/qemu/{vmid}/status/current")

    return {
        "vmid": data["vmid"],
        "name": data.get("name", ""),
        "status": data.get("status", "unknown"),
        "uptime_seconds": data.get("uptime", 0),
        "cpu_usage_percent": round(data.get("cpu", 0.0) * 100, 2),
        "memory_used_mb": round(data.get("mem", 0) / 1_048_576),
        "memory_total_mb": round(data.get("maxmem", 0) / 1_048_576),
    }


async def start_vm(vmid: int) -> str:
    """Start a stopped VM.

    Args:
        vmid: Proxmox VM ID.

    Returns:
        Confirmation message with task ID.
    """
    if not proxmox_configured():
        return _NOT_CONFIGURED["error"]

    node = await _find_vm_node(vmid)
    upid = await _client.post(f"/nodes/{node}/qemu/{vmid}/status/start")
    return f"VM {vmid} start initiated (task: {upid})"


async def stop_vm(vmid: int) -> str:
    """Gracefully stop a running VM.

    Args:
        vmid: Proxmox VM ID.

    Returns:
        Confirmation message with task ID.
    """
    if not proxmox_configured():
        return _NOT_CONFIGURED["error"]

    node = await _find_vm_node(vmid)
    upid = await _client.post(f"/nodes/{node}/qemu/{vmid}/status/stop")
    return f"VM {vmid} stop initiated (task: {upid})"
