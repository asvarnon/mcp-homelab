"""Proxmox VE REST API tools.

Tools for querying and managing VMs and LXC containers via the Proxmox API.
All functions use ProxmoxClient for transport — no direct httpx usage here.
"""

from __future__ import annotations

import re
from typing import Literal

from typing_extensions import TypedDict

from mcp_homelab.core.config import get_proxmox_config, proxmox_configured
from mcp_homelab.core.proxmox_api import ProxmoxClient
from mcp_homelab.tools import _not_configured_error


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


class VmCreateResult(TypedDict):
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


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_BYTES_PER_MB = 1024 ** 2   # 1_048_576
_BYTES_PER_GB = 1024 ** 3   # 1_073_741_824

_client = ProxmoxClient()

_NOT_CONFIGURED: dict[str, str] = _not_configured_error(
    "Proxmox", "proxmox", "PROXMOX_TOKEN_ID / PROXMOX_TOKEN_SECRET",
)

# Proxmox config fields: safe characters for storage pool names, bridge names,
# volume IDs (e.g. "local:iso/debian-13.iso"), and templates.
_SAFE_FIELD_RE = re.compile(r"^[A-Za-z0-9_./:@-]+$")

# Node names must be valid DNS hostnames.
_NODE_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")

# Valid Proxmox guest OS types.
_VALID_OSTYPES = frozenset({
    "l24", "l26", "win7", "win8", "win10", "win11",
    "wvista", "wxp", "w2k", "w2k3", "w2k8", "solaris", "other",
})

# Valid SCSI controller types.
_VALID_SCSIHW = frozenset({
    "virtio-scsi-pci", "virtio-scsi-single", "lsi", "lsi53c810",
    "megasas", "pvscsi",
})

# Valid CPU types.
_VALID_CPU_TYPES = frozenset({
    "host", "kvm32", "kvm64", "qemu32", "qemu64",
    "max", "x86-64-v2", "x86-64-v2-AES", "x86-64-v3", "x86-64-v4",
})


def _validate_safe_field(value: str, name: str) -> None:
    """Reject values containing characters that could inject extra config options."""
    if not _SAFE_FIELD_RE.match(value):
        raise ValueError(
            f"{name} contains invalid characters: {value!r}. "
            f"Only alphanumerics, dots, underscores, slashes, colons, @, and hyphens are allowed."
        )


def _validate_node(node: str) -> None:
    """Validate that a node name is a safe DNS hostname label."""
    if not _NODE_RE.match(node):
        raise ValueError(f"node must be a valid hostname, got {node!r}")


def _validate_vmid(vmid: int) -> None:
    """Validate that a VMID is in the Proxmox-allowed range (>= 100)."""
    if vmid < 100:
        raise ValueError(f"vmid must be >= 100 (Proxmox reserves IDs below 100), got {vmid}")


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


async def _resolve_default_node() -> str:
    """Resolve the default PVE node: prefer config, fall back to first discovered."""
    cfg = get_proxmox_config()
    nodes = await _client.get_nodes()
    if not nodes:
        raise ValueError("No Proxmox nodes found in cluster")
    if cfg and cfg.default_node and cfg.default_node in nodes:
        return cfg.default_node
    return nodes[0]


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------

async def list_vms() -> list[VmSummary] | list[dict]:
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


async def get_vm_status(vmid: int) -> VmStatus | dict:
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


async def list_lxc() -> list[LxcSummary] | list[dict]:
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


async def get_lxc_status(vmid: int) -> LxcStatus | dict:
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
    storage: str | None = None,
    bridge: str | None = None,
    vlan_tag: int | None = None,
    ip_config: str = "ip=dhcp",
    ssh_public_key: str | None = None,
    unprivileged: bool = True,
    features: str | None = None,
    start_after_create: bool = False,
    password: str | None = None,
) -> LxcCreateResult | dict:
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
        storage: Storage pool for rootfs. Defaults to proxmox.default_storage
            from config.yaml, or "local-lvm" if no config value is available.
        bridge: Network bridge name. Defaults to proxmox.default_bridge from
            config.yaml, or "vmbr0" if no config value is available.
        vlan_tag: Optional VLAN tag for the network interface.
        ip_config: IP configuration string (default: "ip=dhcp").
        ssh_public_key: Optional SSH public key to inject.
        unprivileged: Whether to create an unprivileged container.
        features: Optional Proxmox LXC feature string (e.g. "nesting=1,keyctl=1").
        start_after_create: Whether to start the container after creation.
        password: Optional root password. Not exposed via MCP to avoid leaking secrets into LLM transcripts. Use ssh_public_key for key-based auth instead.

    Returns:
        LxcCreateResult dict with vmid, node, and task_id.
    """
    if not proxmox_configured():
        return _NOT_CONFIGURED

    # Input validation — field safety (comma injection prevention)
    _validate_node(node)
    _validate_safe_field(ostemplate, "ostemplate")
    if cores < 1:
        raise ValueError(f"cores must be >= 1, got {cores}")
    if memory_mb < 16:
        raise ValueError(f"memory_mb must be >= 16, got {memory_mb}")
    if swap_mb < 0:
        raise ValueError(f"swap_mb must be >= 0, got {swap_mb}")
    if disk_gb < 1:
        raise ValueError(f"disk_gb must be >= 1, got {disk_gb}")
    if vlan_tag is not None and not (1 <= vlan_tag <= 4094):
        raise ValueError(f"vlan_tag must be in range 1-4094 when set, got {vlan_tag}")

    if storage is None or bridge is None:
        cfg = get_proxmox_config()
        if storage is None:
            storage = cfg.default_storage if cfg else "local-lvm"
        if bridge is None:
            bridge = cfg.default_bridge if cfg else "vmbr0"

    _validate_safe_field(storage, "storage")
    _validate_safe_field(bridge, "bridge")

    if vmid is None:
        next_id = await get_next_vmid()
        if not isinstance(next_id, int):
            raise RuntimeError("get_next_vmid returned unexpected type")
        _validate_vmid(next_id)
        vmid = next_id
    else:
        _validate_vmid(vmid)

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
    if features is not None:
        data["features"] = features
    if password is not None:
        data["password"] = password

    task_id = await _client.post(f"/nodes/{node}/lxc", data=data)

    return LxcCreateResult(vmid=vmid, node=node, task_id=task_id)


async def create_vm(
    node: str,
    iso: str,
    name: str | None = None,
    vmid: int | None = None,
    cores: int = 1,
    sockets: int = 1,
    cpu_type: str = "host",
    memory_mb: int = 2048,
    disk_gb: int = 16,
    storage: str | None = None,
    bridge: str | None = None,
    vlan_tag: int | None = None,
    ostype: str = "l26",
    scsihw: str = "virtio-scsi-single",
    balloon: int = 0,
    start_after_create: bool = False,
) -> VmCreateResult | dict:
    """Create a new VM on a Proxmox node.

    Args:
        node: Target PVE node name.
        iso: Volume ID of the ISO image (e.g. "local:iso/debian-13.iso").
        name: Optional VM name.
        vmid: VM ID. Auto-assigned via get_next_vmid() if None.
        cores: Number of CPU cores.
        sockets: Number of CPU sockets.
        cpu_type: CPU model presented to the VM.
        memory_mb: RAM in megabytes (2048 minimum for Ubuntu Server installer).
        disk_gb: Disk size in gigabytes.
        storage: Storage pool for the main disk. Defaults to proxmox.default_storage
            from config.yaml, or "local" if no config value is available.
        bridge: Network bridge name. Defaults to proxmox.default_bridge from
            config.yaml, or "vmbr0" if no config value is available.
        vlan_tag: Optional VLAN tag for the network interface.
        ostype: Proxmox guest OS type.
        scsihw: Proxmox SCSI controller type.
        balloon: Balloon memory setting (0 disables ballooning).
        start_after_create: Whether to start the VM after creation.

    Returns:
        VmCreateResult dict with vmid, node, and task_id.
    """
    if not proxmox_configured():
        return _NOT_CONFIGURED

    # Input validation — field safety (comma injection prevention)
    _validate_node(node)
    _validate_safe_field(iso, "iso")
    if cores < 1:
        raise ValueError(f"cores must be >= 1, got {cores}")
    if sockets < 1:
        raise ValueError(f"sockets must be >= 1, got {sockets}")
    if memory_mb < 64:
        raise ValueError(f"memory_mb must be >= 64, got {memory_mb}")
    if disk_gb < 1:
        raise ValueError(f"disk_gb must be >= 1, got {disk_gb}")
    if balloon < 0:
        raise ValueError(f"balloon must be >= 0, got {balloon}")
    if vlan_tag is not None and not (1 <= vlan_tag <= 4094):
        raise ValueError(f"vlan_tag must be in range 1-4094 when set, got {vlan_tag}")
    if ostype not in _VALID_OSTYPES:
        raise ValueError(f"ostype must be one of {sorted(_VALID_OSTYPES)}, got {ostype!r}")
    if scsihw not in _VALID_SCSIHW:
        raise ValueError(f"scsihw must be one of {sorted(_VALID_SCSIHW)}, got {scsihw!r}")
    if cpu_type not in _VALID_CPU_TYPES:
        raise ValueError(f"cpu_type must be one of {sorted(_VALID_CPU_TYPES)}, got {cpu_type!r}")

    if storage is None or bridge is None:
        cfg = get_proxmox_config()
        if storage is None:
            storage = cfg.default_storage if cfg else "local"
        if bridge is None:
            bridge = cfg.default_bridge if cfg else "vmbr0"

    _validate_safe_field(storage, "storage")
    _validate_safe_field(bridge, "bridge")

    if vmid is None:
        next_id = await get_next_vmid()
        if not isinstance(next_id, int):
            raise RuntimeError("get_next_vmid returned unexpected type")
        _validate_vmid(next_id)
        vmid = next_id
    else:
        _validate_vmid(vmid)

    net0 = f"model=virtio,bridge={bridge}"
    if vlan_tag is not None:
        net0 += f",tag={vlan_tag}"

    data: dict[str, str | int] = {
        "vmid": vmid,
        "cores": cores,
        "sockets": sockets,
        "cpu": cpu_type,
        "memory": memory_mb,
        "ostype": ostype,
        "scsihw": scsihw,
        "balloon": balloon,
        "ide2": f"{iso},media=cdrom",
        "scsi0": f"{storage}:{disk_gb},iothread=1",
        "net0": net0,
        "boot": "order=scsi0;ide2;net0",
        "start": 1 if start_after_create else 0,
        "numa": 0,
    }
    if name is not None:
        data["name"] = name

    task_id = await _client.post(f"/nodes/{node}/qemu", data=data)

    return VmCreateResult(vmid=vmid, node=node, task_id=task_id)


async def get_next_vmid() -> int | dict:
    """Get the next available VM/CT ID from the Proxmox cluster.

    Returns:
        Integer ID, or error dict if Proxmox is not configured.
    """
    if not proxmox_configured():
        return _NOT_CONFIGURED

    result = await _client.get("/cluster/nextid")
    return int(result)


async def list_storage(node: str | None = None) -> list[StorageInfo] | list[dict]:
    """List available storage on a Proxmox node with capacity info.

    Args:
        node: PVE node name. Defaults to configured proxmox.default_node
            when available, otherwise first discovered node.

    Returns:
        List of StorageInfo dicts.
    """
    if not proxmox_configured():
        return [_NOT_CONFIGURED]

    if node is None:
        node = await _resolve_default_node()

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
) -> list[TemplateInfo] | list[dict]:
    """List available OS templates for LXC container creation.

    Args:
        node: PVE node name. Defaults to configured proxmox.default_node
            when available, otherwise first discovered node.
        storage: Storage pool to query. Defaults to the first storage
            whose content field includes "vztmpl".

    Returns:
        List of TemplateInfo dicts.
    """
    if not proxmox_configured():
        return [_NOT_CONFIGURED]

    if node is None:
        node = await _resolve_default_node()

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
