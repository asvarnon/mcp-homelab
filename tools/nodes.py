"""SSH-based node tools.

Tools for querying host status, Docker containers, and logs via SSH.
All functions use SSHManager for transport — no direct paramiko usage here.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any
from typing_extensions import TypedDict

from core.config import load_config
from core.ssh import SSHManager


class FilesystemInfo(TypedDict):
    filesystem: str
    mount: str
    total_gb: int
    used_gb: int
    available_gb: int
    use_percent: str


class NodeStatus(TypedDict):
    uptime: str
    cpu_percent: float
    ram_used_mb: int
    ram_total_mb: int
    filesystems: list[FilesystemInfo]


class DiskInfo(TypedDict):
    name: str
    size: str
    model: str


class MemoryModule(TypedDict):
    size: str
    type: str
    speed: str
    manufacturer: str
    form_factor: str
    locator: str


class HardwareSpecs(TypedDict):
    cpu_model: str
    cpu_cores: int
    cpu_sockets: int
    architecture: str
    ram_total_mb: int
    ram_display: str
    memory_modules: list[MemoryModule]
    disks: list[DiskInfo]
    virtualization: str
    is_vm: bool


class CpuInfo(TypedDict):
    cpu_model: str
    cpu_cores: int
    cpu_sockets: int
    architecture: str


class NodeSummary(TypedDict):
    name: str
    ip: str
    vlan: int | None
    ssh_enabled: bool
    docker_enabled: bool
    description: str


class ContainerInfo(TypedDict):
    name: str
    image: str
    image_title: str
    compose_service: str
    status: str
    ports: str


_ssh = SSHManager()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

async def _compound_ssh_query(
    hostname: str,
    commands: list[str],
    separator: str = "---SEPARATOR---",
) -> list[list[str]]:
    """Run multiple commands over a single SSH connection.

    Joins commands with echo separators, splits the output back
    into per-command sections.

    Returns:
        List of line-lists, one per command in the same order.
    """
    parts: list[str] = []
    for i, cmd in enumerate(commands):
        if i > 0:
            parts.append(f"echo '{separator}'")
        parts.append(cmd)
    full_cmd = " && ".join(parts)
    raw = await _ssh.execute_async(hostname, full_cmd)
    return [section.splitlines() for section in raw.split(separator)]


def _sanitize_container_name(name: str) -> str:
    """Strip characters that aren't valid in a Docker container name."""
    return re.sub(r"[^a-zA-Z0-9_.-]", "", name)


def _parse_uptime(lines: list[str]) -> str:
    """Extract the human-readable uptime string from ``uptime -p`` output."""
    for line in lines:
        if line.startswith("up "):
            return line
    return "unknown"


def _parse_cpu_percent(lines: list[str]) -> float:
    """Derive CPU usage % from the idle value in ``top`` output."""
    for line in lines:
        m = re.search(r"(\d+\.?\d*)\s*id", line)
        if m:
            return round(100.0 - float(m.group(1)), 1)
    return 0.0


def _parse_memory_mb(lines: list[str]) -> tuple[int, int]:
    """Return ``(used_mb, total_mb)`` from ``free -m`` output."""
    for line in lines:
        if line.startswith("Mem:"):
            parts = line.split()
            if len(parts) >= 3:
                return int(parts[2]), int(parts[1])
    return 0, 0


_SKIP_FS_TYPES = frozenset({"tmpfs", "efivarfs", "devtmpfs", "overlay"})


def _parse_disk_gb(lines: list[str]) -> list[FilesystemInfo]:
    """Return a list of filesystem dicts from ``df -BG`` output.

    Each dict has keys: filesystem, mount, total_gb, used_gb, available_gb,
    use_percent.  Only real (non-tmpfs/efivarfs) filesystems are included.
    """
    results: list[FilesystemInfo] = []
    for line in lines:
        parts = line.split()
        # Expect: Filesystem  1G-blocks  Used  Available  Use%  Mounted on
        if len(parts) < 6 or not parts[1].endswith("G"):
            continue
        fs_name = parts[0]
        # Skip virtual/temp filesystems
        if fs_name in _SKIP_FS_TYPES or fs_name.startswith("tmpfs"):
            continue
        results.append({
            "filesystem": fs_name,
            "mount": parts[5],
            "total_gb": int(parts[1].rstrip("G")),
            "used_gb": int(parts[2].rstrip("G")),
            "available_gb": int(parts[3].rstrip("G")),
            "use_percent": parts[4],
        })
    return results


def _extract_label(labels: str, key: str) -> str:
    """Extract a specific label value from Docker's comma-separated labels string."""
    prefix = key + "="
    for part in labels.split(","):
        part = part.strip()
        if part.startswith(prefix):
            return part[len(prefix):]
    return ""


def _parse_docker_ps(raw: str) -> list[ContainerInfo]:
    """Parse ``docker ps --format json`` output into structured dicts."""
    if not raw:
        return []
    containers: list[ContainerInfo] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        labels = obj.get("Labels", "")
        containers.append({
            "name": obj.get("Names", ""),
            "image": obj.get("Image", ""),
            "image_title": _extract_label(labels, "org.opencontainers.image.title"),
            "compose_service": _extract_label(labels, "com.docker.compose.service"),
            "status": obj.get("Status", ""),
            "ports": obj.get("Ports", ""),
        })
    return containers


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------

async def list_nodes() -> list[NodeSummary]:
    """Return all configured nodes and their connection details.

    Returns:
        List of dicts with keys: name, ip, vlan, ssh_enabled,
        docker_enabled, description.
    """
    config = load_config()
    return [
        NodeSummary(
            name=key,
            ip=host.ip,
            vlan=host.vlan,
            ssh_enabled=host.ssh,
            docker_enabled=host.docker,
            description=host.description,
        )
        for key, host in config.hosts.items()
    ]


async def get_node_status(hostname: str) -> NodeStatus:
    """Return uptime, CPU%, RAM usage, and disk usage for a node."""
    commands = ["uptime -p", "top -bn1 | grep '%Cpu'", "free -m | grep '^Mem:'", "df -BG"]
    sections = await _compound_ssh_query(hostname, commands)

    # Pad so we always have one section per command
    padded = sections + [[] for _ in range(len(commands) - len(sections))]
    uptime_lines, cpu_lines, mem_lines, disk_lines = padded[:4]

    ram_used, ram_total = _parse_memory_mb(mem_lines)

    return {
        "uptime": _parse_uptime(uptime_lines),
        "cpu_percent": _parse_cpu_percent(cpu_lines),
        "ram_used_mb": ram_used,
        "ram_total_mb": ram_total,
        "filesystems": _parse_disk_gb(disk_lines),
    }


async def list_containers(hostname: str) -> list[ContainerInfo]:
    """List Docker containers on a node.

    Args:
        hostname: Logical node name that runs Docker.

    Returns:
        List of dicts with keys: name, image, status, ports.
    """
    raw = await _ssh.execute_docker_async(
        hostname,
        "ps --format '{{json .}}'",
    )
    return _parse_docker_ps(raw)


async def get_container_logs(hostname: str, container: str, lines: int = 50) -> str:
    """Return the last N lines of logs for a container.

    Args:
        hostname: Logical node name.
        container: Container name.
        lines: Number of log lines to retrieve (default 50).

    Returns:
        Log output as a string.
    """
    safe_name = _sanitize_container_name(container)
    safe_lines = int(lines)
    return await _ssh.execute_docker_async(
        hostname,
        f"logs --tail {safe_lines} {safe_name}",
    )


async def restart_container(hostname: str, container: str) -> str:
    """Restart a Docker container on a node.

    Args:
        hostname: Logical node name.
        container: Container name to restart.

    Returns:
        Confirmation message.
    """
    safe_name = _sanitize_container_name(container)
    await _ssh.execute_docker_async(hostname, f"restart {safe_name}")
    return f"Container '{safe_name}' restarted on {hostname}"


# ---------------------------------------------------------------------------
# Hardware spec helpers
# ---------------------------------------------------------------------------

def _parse_lscpu(lines: list[str]) -> CpuInfo:
    """Extract CPU details from ``lscpu`` output."""
    fields: dict[str, str] = {}
    for line in lines:
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()

    cores_str = fields.get("CPU(s)", "0")
    sockets_str = fields.get("Socket(s)", "0")
    return CpuInfo(
        cpu_model=fields.get("Model name", "unknown"),
        cpu_cores=int(cores_str) if cores_str.isdigit() else 0,
        cpu_sockets=int(sockets_str) if sockets_str.isdigit() else 0,
        architecture=fields.get("Architecture", "unknown"),
    )


def _parse_meminfo(lines: list[str]) -> int:
    """Extract total RAM in MB from ``/proc/meminfo`` output."""
    for line in lines:
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) // 1024
    return 0


def _parse_lsblk(lines: list[str]) -> list[DiskInfo]:
    """Extract physical disks from ``lsblk -d`` output.

    Only rows with TYPE ``disk`` are included.
    """
    disks: list[DiskInfo] = []
    for line in lines:
        parts = line.split(None, 3)
        if len(parts) < 3:
            continue
        name, size, dtype = parts[0], parts[1], parts[2]
        if dtype != "disk":
            continue
        model = parts[3].strip() if len(parts) > 3 else ""
        disks.append({"name": name, "size": size, "model": model})
    return disks


def _parse_virt(lines: list[str]) -> str:
    """Extract virtualization type from ``systemd-detect-virt`` output."""
    for line in lines:
        stripped = line.strip()
        if stripped:
            return stripped
    return "unknown"


def _parse_dmidecode(lines: list[str]) -> list[MemoryModule]:
    """Extract per-DIMM info from ``dmidecode --type 17`` output.

    Returns a list of populated memory slots.  Empty slots and
    lines from hosts without dmidecode access are silently skipped.
    """
    raw_modules: list[dict[str, str]] = []
    current: dict[str, str] = {}

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Memory Device"):
            if current and current.get("size"):
                raw_modules.append(current)
            current = {}
            continue
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key, value = key.strip(), value.strip()
            if key == "Size":
                current["size"] = value
            elif key == "Type":
                current["type"] = value
            elif key == "Speed":
                current["speed"] = value
            elif key == "Manufacturer":
                current["manufacturer"] = value
            elif key == "Form Factor":
                current["form_factor"] = value
            elif key == "Locator":
                current["locator"] = value

    # Flush last block
    if current and current.get("size"):
        raw_modules.append(current)

    # Filter out empty slots and build typed results
    return [
        MemoryModule(
            size=m.get("size", ""),
            type=m.get("type", ""),
            speed=m.get("speed", ""),
            manufacturer=m.get("manufacturer", ""),
            form_factor=m.get("form_factor", ""),
            locator=m.get("locator", ""),
        )
        for m in raw_modules
        if m.get("size", "").lower() not in ("", "no module installed", "not installed")
    ]


def _round_to_consumer_gb(total_mb: int) -> int:
    """Round raw MB to the nearest standard consumer RAM size in GB."""
    if total_mb <= 0:
        return 0
    gb = total_mb / 1024
    power = math.ceil(math.log2(gb))
    return 2 ** power


async def get_hardware_specs(hostname: str) -> HardwareSpecs:
    """Return hardware specification for a node."""
    commands = [
        "lscpu",
        "cat /proc/meminfo | head -5",
        "lsblk -d -o NAME,SIZE,TYPE,MODEL --noheadings",
        "(systemd-detect-virt || true)",
        "(sudo -n dmidecode --type 17 2>/dev/null || true)",
    ]
    sections = await _compound_ssh_query(hostname, commands)

    # Pad so we always have one section per command
    padded = sections + [[] for _ in range(len(commands) - len(sections))]
    lscpu_lines, meminfo_lines, lsblk_lines, virt_lines, dmi_lines = padded[:5]

    cpu_info = _parse_lscpu(lscpu_lines)
    virt_type = _parse_virt(virt_lines)
    ram_total = _parse_meminfo(meminfo_lines)

    return HardwareSpecs(
        cpu_model=cpu_info["cpu_model"],
        cpu_cores=cpu_info["cpu_cores"],
        cpu_sockets=cpu_info["cpu_sockets"],
        architecture=cpu_info["architecture"],
        ram_total_mb=ram_total,
        ram_display=f"{_round_to_consumer_gb(ram_total)} GB",
        memory_modules=_parse_dmidecode(dmi_lines),
        disks=_parse_lsblk(lsblk_lines),
        virtualization=virt_type,
        is_vm=virt_type != "none",
    )
