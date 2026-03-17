"""Proxmox VE REST API tools.

Tools for querying and managing VMs and LXC containers via the Proxmox API.
All functions use ProxmoxClient for transport — no direct httpx usage here.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from core.config import proxmox_configured
from core.proxmox_api import ProxmoxClient


# ---------------------------------------------------------------------------
# TypedDicts — structured return types
# ---------------------------------------------------------------------------


class VmSummary(TypedDict):
    vmid: int
    name: str
    status: str
    cpus: int
    memory_mb: int


class VmStatus(TypedDict):
    vmid: int
    name: str
    status: str
    uptime_seconds: int
    cpu_usage_percent: float
    memory_used_mb: int
    memory_total_mb: int


class LxcSummary(TypedDict):
    vmid: int
    name: str
    status: str
    cpus: int
    memory_mb: int
    type: str


class LxcStatus(TypedDict):
    vmid: int
    name: str
    status: str
    uptime_seconds: int
    cpu_usage_percent: float
    memory_used_mb: int
    memory_total_mb: int
    swap_used_mb: int
    swap_total_mb: int
    disk_used_gb: float
    disk_total_gb: float
    type: str


class LxcCreateResult(TypedDict):
    vmid: int
    node: str
    task_id: str


class StorageInfo(TypedDict):
    storage: str
    type: str
    content: str
    total_gb: float
    used_gb: float
    avail_gb: float
    active: bool


class TemplateInfo(TypedDict):
    volid: str
    format: str
    size_mb: float


class ProxmoxError(TypedDict):
    error: str


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_BYTES_PER_MB = 1024 ** 2   # 1_048_576
_BYTES_PER_GB = 1024 ** 3   # 1_073_741_824

_client = ProxmoxClient()

_NOT_CONFIGURED = ProxmoxError(
    error="Proxmox is not configured. Add a 'proxmox' section to config.yaml and set PROXMOX_TOKEN_ID / PROXMOX_TOKEN_SECRET in .env."
)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

async def _find_resource_node(vmid: int, resource_type: Literal["qemu", "lxc"]) -> str:
    """Locate which PVE node a VM or LXC container lives on.

    Uses the cluster resources endpoint to find the resource without
    needing to iterate nodes manually.

    Args:
        vmid: Proxmox resource ID.
        resource_type: Either ``"qemu"`` (VM) or ``"lxc"`` (container).

    Returns:
        Node name string.

    Raises:
        ValueError: If the resource is not found in the cluster.
    """
    label = "VM" if resource_type == "qemu" else "LXC container"
    resources = await _client.get("/cluster/resources?type=vm")
    for resource in resources:
        if resource.get("vmid") == vmid and resource.get("type") == resource_type:
            return resource["node"]
    raise ValueError(f"{label} {vmid} not found in cluster")


async def _find_vm_node(vmid: int) -> str:
    """Locate which PVE node a VM lives on."""
    return await _find_resource_node(vmid, "qemu")


async def _find_ct_node(vmid: int) -> str:
    """Locate which PVE node an LXC container lives on."""
    return await _find_resource_node(vmid, "lxc")


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------

async def list_vms() -> list[VmSummary] | list[ProxmoxError]:
    """Return all VMs with ID, name, status, and resource allocation.

    Queries every PVE node and aggregates the results.

    Returns:
        List of VmSummary dicts.
    """
    if not proxmox_configured():
        return [_NOT_CONFIGURED]

    nodes = await _client.get_nodes()
    vms: list[VmSummary] = []

    for node in nodes:
        data = await _client.get(f"/nodes/{node}/qemu")
        for vm in data:
            vms.append(VmSummary(
                vmid=vm["vmid"],
                name=vm.get("name", ""),
                status=vm.get("status", "unknown"),
                cpus=vm.get("cpus", 0),
                memory_mb=round(vm.get("maxmem", 0) / _BYTES_PER_MB),
            ))

    return vms


async def get_vm_status(vmid: int) -> VmStatus | ProxmoxError:
    """Return detailed status for a specific VM.

    Args:
        vmid: Proxmox VM ID.

    Returns:
        VmStatus dict with resource usage details.
    """
    if not proxmox_configured():
        return _NOT_CONFIGURED

    node = await _find_vm_node(vmid)
    data = await _client.get(f"/nodes/{node}/qemu/{vmid}/status/current")

    return VmStatus(
        vmid=data["vmid"],
        name=data.get("name", ""),
        status=data.get("status", "unknown"),
        uptime_seconds=data.get("uptime", 0),
        cpu_usage_percent=round(data.get("cpu", 0.0) * 100, 2),
        memory_used_mb=round(data.get("mem", 0) / _BYTES_PER_MB),
        memory_total_mb=round(data.get("maxmem", 0) / _BYTES_PER_MB),
    )


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


# ---------------------------------------------------------------------------
# LXC container tools
# ---------------------------------------------------------------------------


async def list_lxc() -> list[LxcSummary] | list[ProxmoxError]:
    """Return all LXC containers with ID, name, status, and resource allocation.

    Queries every PVE node and aggregates the results.

    Returns:
        List of LxcSummary dicts.
    """
    if not proxmox_configured():
        return [_NOT_CONFIGURED]

    nodes = await _client.get_nodes()
    containers: list[LxcSummary] = []

    for node in nodes:
        data = await _client.get(f"/nodes/{node}/lxc")
        for ct in data:
            containers.append(LxcSummary(
                vmid=ct["vmid"],
                name=ct.get("name", ""),
                status=ct.get("status", "unknown"),
                cpus=ct.get("cpus", 0),
                memory_mb=round(ct.get("maxmem", 0) / _BYTES_PER_MB),
                type="lxc",
            ))

    return containers


async def get_lxc_status(vmid: int) -> LxcStatus | ProxmoxError:
    """Return detailed status for a specific LXC container.

    Args:
        vmid: Proxmox container ID.

    Returns:
        LxcStatus dict with resource usage details.
    """
    if not proxmox_configured():
        return _NOT_CONFIGURED

    node = await _find_ct_node(vmid)
    data = await _client.get(f"/nodes/{node}/lxc/{vmid}/status/current")

    return LxcStatus(
        vmid=data["vmid"],
        name=data.get("name", ""),
        status=data.get("status", "unknown"),
        uptime_seconds=data.get("uptime", 0),
        cpu_usage_percent=round(data.get("cpu", 0.0) * 100, 2),
        memory_used_mb=round(data.get("mem", 0) / _BYTES_PER_MB),
        memory_total_mb=round(data.get("maxmem", 0) / _BYTES_PER_MB),
        swap_used_mb=round(data.get("swap", 0) / _BYTES_PER_MB),
        swap_total_mb=round(data.get("maxswap", 0) / _BYTES_PER_MB),
        disk_used_gb=round(data.get("disk", 0) / _BYTES_PER_GB, 2),
        disk_total_gb=round(data.get("maxdisk", 0) / _BYTES_PER_GB, 2),
        type="lxc",
    )


async def start_lxc(vmid: int) -> str:
    """Start a stopped LXC container.

    Args:
        vmid: Proxmox container ID.

    Returns:
        Confirmation message with task ID.
    """
    if not proxmox_configured():
        return _NOT_CONFIGURED["error"]

    node = await _find_ct_node(vmid)
    upid = await _client.post(f"/nodes/{node}/lxc/{vmid}/status/start")
    return f"LXC {vmid} start initiated (task: {upid})"


async def stop_lxc(vmid: int) -> str:
    """Gracefully stop a running LXC container.

    Args:
        vmid: Proxmox container ID.

    Returns:
        Confirmation message with task ID.
    """
    if not proxmox_configured():
        return _NOT_CONFIGURED["error"]

    node = await _find_ct_node(vmid)
    upid = await _client.post(f"/nodes/{node}/lxc/{vmid}/status/stop")
    return f"LXC {vmid} stop initiated (task: {upid})"


async def create_lxc(
    node: str,
    ostemplate: str,
    hostname: str | None = None,
    vmid: int | None = None,
    cores: int = 1,
    memory_mb: int = 512,
    swap_mb: int = 512,
    disk_gb: int = 4,
    storage: str = "local-lvm",
    bridge: str = "vmbr0",
    vlan_tag: int | None = None,
    ip_config: str = "ip=dhcp",
    ssh_public_key: str | None = None,
    unprivileged: bool = True,
    start_after_create: bool = False,
    password: str | None = None,
) -> LxcCreateResult | ProxmoxError:
    """Create a new LXC container on a Proxmox node.

    Args:
        node: Target PVE node name.
        ostemplate: Volume ID of the OS template (e.g. "local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst").
        hostname: Optional container hostname.
        vmid: Container ID. Auto-assigned via get_next_vmid() if None.
        cores: Number of CPU cores.
        memory_mb: RAM in megabytes.
        swap_mb: Swap in megabytes.
        disk_gb: Root disk size in gigabytes.
        storage: Storage pool for rootfs. Defaults to "local-lvm" (LVM thin
            pool — block-backed, supports snapshots, thin-provisioned). Use
            "local" for directory-backed storage if your Proxmox node has no
            LVM thin pool. Run list_storage() to check what's available.
        bridge: Network bridge name.
        vlan_tag: Optional VLAN tag for the network interface.
        ip_config: IP configuration string (default: "ip=dhcp").
        ssh_public_key: Optional SSH public key to inject.
        unprivileged: Whether to create an unprivileged container.
        start_after_create: Whether to start the container after creation.
        password: Optional root password.

    Returns:
        LxcCreateResult dict with vmid, node, and task_id.
    """
    if not proxmox_configured():
        return _NOT_CONFIGURED

    if vmid is None:
        next_id = await get_next_vmid()
        if not isinstance(next_id, int):
            raise RuntimeError("get_next_vmid returned unexpected type")
        vmid = next_id

    rootfs = f"{storage}:{disk_gb}"
    net0 = f"name=eth0,bridge={bridge},{ip_config}"
    if vlan_tag is not None:
        net0 += f",tag={vlan_tag}"

    data: dict[str, str | int] = {
        "vmid": vmid,
        "ostemplate": ostemplate,
        "cores": cores,
        "memory": memory_mb,
        "swap": swap_mb,
        "rootfs": rootfs,
        "net0": net0,
        "unprivileged": 1 if unprivileged else 0,
        "start": 1 if start_after_create else 0,
    }

    if hostname is not None:
        data["hostname"] = hostname
    if ssh_public_key is not None:
        data["ssh-public-keys"] = ssh_public_key
    if password is not None:
        data["password"] = password

    task_id = await _client.post(f"/nodes/{node}/lxc", data=data)

    return LxcCreateResult(vmid=vmid, node=node, task_id=task_id)


async def get_next_vmid() -> int | ProxmoxError:
    """Get the next available VM/CT ID from the Proxmox cluster.

    Returns:
        Integer ID, or error dict if Proxmox is not configured.
    """
    if not proxmox_configured():
        return _NOT_CONFIGURED

    result = await _client.get("/cluster/nextid")
    return int(result)


async def list_storage(node: str | None = None) -> list[StorageInfo] | list[ProxmoxError]:
    """List available storage on a Proxmox node with capacity info.

    Args:
        node: PVE node name. Defaults to the first discovered node.

    Returns:
        List of StorageInfo dicts.
    """
    if not proxmox_configured():
        return [_NOT_CONFIGURED]

    if node is None:
        nodes = await _client.get_nodes()
        if not nodes:
            raise ValueError("No Proxmox nodes found in cluster")
        node = nodes[0]

    data = await _client.get(f"/nodes/{node}/storage")
    storages: list[StorageInfo] = []

    for s in data:
        storages.append(StorageInfo(
            storage=s.get("storage", ""),
            type=s.get("type", ""),
            content=s.get("content", ""),
            total_gb=round(s.get("total", 0) / _BYTES_PER_GB, 2),
            used_gb=round(s.get("used", 0) / _BYTES_PER_GB, 2),
            avail_gb=round(s.get("avail", 0) / _BYTES_PER_GB, 2),
            active=s.get("active", 0) == 1,
        ))

    return storages


async def list_templates(
    node: str | None = None,
    storage: str | None = None,
) -> list[TemplateInfo] | list[ProxmoxError]:
    """List available OS templates for LXC container creation.

    Args:
        node: PVE node name. Defaults to the first discovered node.
        storage: Storage pool to query. Defaults to the first storage
            whose content field includes "vztmpl".

    Returns:
        List of TemplateInfo dicts.
    """
    if not proxmox_configured():
        return [_NOT_CONFIGURED]

    if node is None:
        nodes = await _client.get_nodes()
        if not nodes:
            raise ValueError("No Proxmox nodes found in cluster")
        node = nodes[0]

    if storage is None:
        all_storage = await list_storage(node)
        for s in all_storage:
            if "vztmpl" in s.get("content", ""):
                storage = s["storage"]
                break
        if storage is None:
            return []

    data = await _client.get(f"/nodes/{node}/storage/{storage}/content?content=vztmpl")
    templates: list[TemplateInfo] = []

    for t in data:
        templates.append(TemplateInfo(
            volid=t.get("volid", ""),
            format=t.get("format", ""),
            size_mb=round(t.get("size", 0) / _BYTES_PER_MB, 2),
        ))

    return templates
