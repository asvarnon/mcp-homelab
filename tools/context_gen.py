"""Context generation tool.

Generates a Markdown documentation workspace from live infrastructure
scan data.  Creates files under ``<project>/context/generated/`` with
an overview, network details, and per-node pages.  Previous versions
of regenerated files are archived automatically.

User-editable files (like known-issues.md) are created once and never
overwritten on subsequent runs.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config import get_config_dir

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def generate_context(
    scan: dict[str, Any],
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Generate or refresh the documentation workspace.

    Args:
        scan: Infrastructure scan data from scan_infrastructure().
        output_dir: Override the context base directory.  Defaults to
                    ``<config_dir>/context/``.  Generated files are
                    always written under ``<base>/generated/``.

    Returns:
        Dict with keys:
        - files_created: list of relative paths that were written
        - files_archived: list of relative paths that were archived
        - summary: human-readable description of what happened
    """
    if not scan.get("nodes"):
        raise RuntimeError(
            "scan data contains no nodes — "
            "cannot generate context from empty data"
        )

    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M")
    date_stamp = now.strftime("%Y-%m-%d")

    base_dir = output_dir or (get_config_dir() / "context")
    gen_dir = base_dir / "generated"

    # --- One-time migration from legacy flat layout ---
    _migrate_legacy_layout(base_dir, gen_dir)

    nodes_dir = gen_dir / "nodes"
    archive_dir = gen_dir / "archived"

    gen_dir.mkdir(parents=True, exist_ok=True)
    nodes_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    files_created: list[str] = []
    files_archived: list[str] = []

    # --- Archive existing regenerated files ---
    for target in [gen_dir / "infrastructure.md", gen_dir / "network.md"]:
        archived = _archive_if_exists(target, archive_dir, date_stamp, base_dir)
        if archived:
            files_archived.append(archived)
    for node_file in nodes_dir.glob("*.md"):
        archived = _archive_if_exists(node_file, archive_dir, date_stamp, base_dir)
        if archived:
            files_archived.append(archived)

    # --- Generate files ---
    files_created.append(_write(gen_dir / "infrastructure.md", _render_infrastructure(scan, timestamp), base_dir))
    files_created.append(_write(gen_dir / "network.md", _render_network(scan, timestamp), base_dir))

    for node in scan["nodes"]:
        name = node["name"]
        node_status = scan["node_status"].get(name, {})
        containers = scan["containers"].get(name, [])
        hw = scan.get("hardware", {}).get(name, {})
        path = nodes_dir / f"{name}.md"
        files_created.append(_write(path, _render_node(node, node_status, containers, hw, timestamp), base_dir))

    # known-issues.md — create only once
    ki_path = gen_dir / "known-issues.md"
    if not ki_path.exists():
        files_created.append(_write(ki_path, _KNOWN_ISSUES_TEMPLATE, base_dir))

    summary = (
        f"Generated {len(files_created)} file(s), "
        f"archived {len(files_archived)} previous version(s). "
        f"Timestamp: {timestamp}"
    )

    return {
        "context_dir": str(base_dir),
        "files_created": files_created,
        "files_archived": files_archived,
        "summary": summary,
    }


async def list_context_files(
    context_dir: Path | None = None,
) -> dict[str, Any]:
    """Return a manifest of all files in the context directory.

    Args:
        context_dir: Override the context directory.  Defaults to
                     ``<config_dir>/context/``.

    Returns:
        Dict with keys:
        - context_dir: absolute path to the context directory
        - files: list of dicts with path, size_kb, modified
        - total_files: count
    """
    base_dir = context_dir or (get_config_dir() / "context")

    if not base_dir.is_dir():
        return {
            "context_dir": str(base_dir),
            "files": [],
            "total_files": 0,
            "note": "Context directory does not exist yet. Run generate_context first.",
        }

    files: list[dict[str, Any]] = []
    for path in sorted(base_dir.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        stat = path.stat()
        files.append({
            "path": str(path.relative_to(base_dir)),
            "size_kb": round(stat.st_size / 1024, 1),
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d"),
        })

    return {
        "context_dir": str(base_dir),
        "files": files,
        "total_files": len(files),
    }


# ---------------------------------------------------------------------------
# File utilities
# ---------------------------------------------------------------------------

_LEGACY_GENERATED_FILES = ("infrastructure.md", "network.md")
_LEGACY_GENERATED_DIRS = ("nodes", "archived")


def _migrate_legacy_layout(base_dir: Path, gen_dir: Path) -> None:
    """One-time migration from the flat context/ layout to context/generated/.

    Detects old-layout files sitting directly in *base_dir* and cleans
    them up so stale content can't confuse a model.

    * ``known-issues.md`` is moved (user-editable, must preserve).
    * All other generated files and directories are deleted — they'll
      be regenerated from live data momentarily.
    """
    if gen_dir.exists() and any(gen_dir.iterdir()):
        # generated/ already has content — migration already happened
        return

    legacy_ki = base_dir / "known-issues.md"
    has_legacy = legacy_ki.exists() or any(
        (base_dir / name).exists()
        for name in (*_LEGACY_GENERATED_FILES, *_LEGACY_GENERATED_DIRS)
    )
    if not has_legacy:
        return

    logger.info("Migrating legacy flat context layout to generated/ subdirectory")

    gen_dir.mkdir(parents=True, exist_ok=True)

    # Preserve user-editable known-issues.md
    if legacy_ki.exists():
        dest = gen_dir / "known-issues.md"
        if not dest.exists():
            shutil.move(str(legacy_ki), str(dest))
            logger.info("Moved known-issues.md \u2192 generated/known-issues.md")
        else:
            logger.warning(
                "known-issues.md exists in both %s and %s \u2014 keeping generated/ copy, deleting legacy",
                legacy_ki, dest,
            )
            legacy_ki.unlink()

    # Delete stale generated files
    for name in _LEGACY_GENERATED_FILES:
        path = base_dir / name
        if path.exists():
            path.unlink()
            logger.info("Removed legacy %s", name)

    # Delete stale generated directories
    for name in _LEGACY_GENERATED_DIRS:
        path = base_dir / name
        if path.is_dir():
            shutil.rmtree(path)
            logger.info("Removed legacy %s/", name)


def _write(path: Path, content: str, base_dir: Path) -> str:
    """Write content to *path* and return its path relative to base_dir."""
    path.write_text(content, encoding="utf-8")
    return str(path.relative_to(base_dir))


def _archive_if_exists(source: Path, archive_dir: Path, date_stamp: str, base_dir: Path) -> str | None:
    """Move *source* into *archive_dir* with a date suffix.

    Returns the relative archived path, or None if source didn't exist.
    """
    if not source.exists():
        return None
    stem = source.stem
    suffix = source.suffix
    dest = archive_dir / f"{stem}_{date_stamp}{suffix}"
    # If an archive for today already exists, overwrite it
    shutil.move(str(source), str(dest))
    return str(dest.relative_to(base_dir))


# ---------------------------------------------------------------------------
# Markdown renderers
# ---------------------------------------------------------------------------

def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """Build a Markdown table from headers and row data.

    Returns a list of lines (header, separator, data rows).
    """
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return lines


def _render_infrastructure(scan: dict[str, Any], timestamp: str) -> str:
    """Render the main infrastructure overview page."""
    lines: list[str] = [
        "# Infrastructure Overview",
        "",
        f"> Auto-generated by mcp-homelab on {timestamp}",
        "> Re-run `generate_context` to refresh. User-editable sections are preserved in other files.",
        "",
        "---",
        "",
    ]

    # -- Nodes table --
    lines.append("## Nodes")
    lines.append("")
    hw_map = scan.get("hardware", {})
    node_rows: list[list[str]] = []
    for node in scan["nodes"]:
        name = node["name"]
        status_data = scan["node_status"].get(name, {})
        if isinstance(status_data, dict) and "error" in status_data:
            status_str = f"Error: {status_data['error']}"
        elif isinstance(status_data, dict) and status_data:
            uptime = status_data.get("uptime", "unknown")
            status_str = f"{uptime}, Docker: {'Yes' if node.get('docker_enabled') else 'No'}"
        else:
            status_str = "No status (SSH disabled)" if not node.get("ssh_enabled") else "Unknown"
        hw = hw_map.get(name, {})
        if isinstance(hw, dict) and "is_vm" in hw:
            type_str = "VM" if hw["is_vm"] else "Bare Metal"
        else:
            type_str = ""
        node_rows.append([name, node["ip"], str(node["vlan"]), type_str, node.get("description", ""), status_str])
    lines.extend(_md_table(["Name", "IP", "VLAN", "Type", "Role", "Status"], node_rows))
    lines.append("")

    # -- VMs table --
    lines.append("## Virtual Machines")
    lines.append("")
    vms = scan.get("vms", {})
    if isinstance(vms, dict) and "error" in vms:
        lines.append(f"*{vms['error']}*")
        lines.append("")
        lines.append("> To enable: add a `proxmox` section to config.yaml and set API tokens in .env.")
    elif isinstance(vms, list) and vms:
        lines.extend(_md_table(
            ["VMID", "Name", "Status", "CPUs", "Memory"],
            [
                [str(vm.get("vmid", "")), vm.get("name", ""), vm.get("status", ""),
                 str(vm.get("cpus", "")), f"{vm.get('memory_mb', 0)} MB"]
                for vm in vms
            ],
        ))
    else:
        lines.append("No Proxmox VMs detected")
    lines.append("")

    # -- Containers by host --
    lines.append("## Containers")
    lines.append("")
    containers = scan.get("containers", {})
    if not containers:
        lines.append("No containers found on any node.")
    else:
        for hostname, clist in containers.items():
            lines.append(f"### {hostname}")
            lines.append("")
            if isinstance(clist, dict) and "error" in clist:
                lines.append(f"Error retrieving containers: {clist['error']}")
            elif isinstance(clist, list) and clist:
                lines.extend(_md_table(
                    ["Container", "Image", "Service", "Status"],
                    [
                        [c.get("name", ""), c.get("image", ""),
                         c.get("compose_service", ""), c.get("status", "")]
                        for c in clist
                    ],
                ))
            else:
                lines.append("No running containers.")
            lines.append("")

    # -- Network interfaces --
    lines.append("## Network Interfaces")
    lines.append("")
    interfaces = scan.get("interfaces", {})
    if isinstance(interfaces, dict) and "error" in interfaces:
        lines.append(f"*{interfaces['error']}*")
        lines.append("")
        lines.append("> To enable: add an `opnsense` section to config.yaml and set API credentials in .env.")
    elif isinstance(interfaces, list) and interfaces:
        lines.extend(_md_table(
            ["Name", "Description", "Subnet", "Device"],
            [
                [iface.get("name", ""), iface.get("description", ""),
                 iface.get("address", ""), iface.get("device", "")]
                for iface in interfaces
            ],
        ))
    else:
        lines.append("No active interfaces detected.")
    lines.append("")

    return "\n".join(lines)


def _render_network(scan: dict[str, Any], timestamp: str) -> str:
    """Render the network configuration page."""
    lines: list[str] = [
        "# Network Configuration",
        "",
        f"> Auto-generated by mcp-homelab on {timestamp}",
        "",
        "---",
        "",
    ]

    interfaces = scan.get("interfaces", {})

    # -- Active Interfaces --
    lines.append("## Active Interfaces")
    lines.append("")
    if isinstance(interfaces, dict) and "error" in interfaces:
        lines.append(f"*{interfaces['error']}*")
        lines.append("")
        lines.append("> To enable: add an `opnsense` section to config.yaml and set API credentials in .env.")
    elif isinstance(interfaces, list) and interfaces:
        lines.extend(_md_table(
            ["Interface", "Description", "Address", "Device", "Status"],
            [
                [iface.get("name", ""), iface.get("description", ""),
                 iface.get("address", ""), iface.get("device", ""),
                 iface.get("status", "")]
                for iface in interfaces
            ],
        ))
    else:
        lines.append("No active interfaces detected.")
    lines.append("")

    # -- Subnet Map --
    lines.append("## Subnet Map")
    lines.append("")
    if isinstance(interfaces, list) and interfaces:
        subnet_rows: list[list[str]] = []
        for iface in interfaces:
            addr = iface.get("address", "")
            if addr:
                subnet_rows.append([addr, iface.get("description", ""), iface.get("gateway", addr)])
        if subnet_rows:
            lines.extend(_md_table(["Subnet", "Description", "Gateway"], subnet_rows))
        else:
            lines.append("No subnet data available.")
    else:
        lines.append("No subnet data available.")
    lines.append("")

    # -- TODO --
    lines.append("## TODO")
    lines.append("")
    lines.append("- [ ] Document firewall rules and inter-VLAN policies")
    lines.append("- [ ] Document DNS configuration")
    lines.append("- [ ] Add network diagram")
    lines.append("")

    return "\n".join(lines)


def _render_node_overview(node: dict[str, Any], docker_enabled: bool) -> list[str]:
    """Render the Overview section of a node page."""
    lines = ["## Overview", ""]
    lines.extend(_md_table(
        ["Property", "Value"],
        [
            ["IP", node["ip"]],
            ["VLAN", str(node["vlan"])],
            ["Docker", "Yes" if docker_enabled else "No"],
            ["Description", node.get("description", "")],
        ],
    ))
    lines.append("")
    return lines


def _render_node_hardware(hardware: dict[str, Any]) -> list[str]:
    """Render the Hardware section of a node page."""
    lines = ["## Hardware", ""]
    has_hw = isinstance(hardware, dict) and "error" not in hardware and hardware

    if not has_hw:
        lines.append("Hardware data unavailable (SSH may be disabled).")
        lines.append("")
        return lines

    is_vm = hardware.get("is_vm")
    virt = hardware.get("virtualization", "unknown")
    if is_vm is True:
        type_str = f"Virtual Machine ({virt})"
    elif is_vm is False:
        type_str = "Bare Metal"
    else:
        type_str = "Unknown"

    cores = hardware.get("cpu_cores", 0)
    sockets = hardware.get("cpu_sockets", 0)
    cpu_model = hardware.get("cpu_model", "unknown")
    cpu_str = f"{cpu_model} ({cores} cores, {sockets} socket(s))"
    ram_display = hardware.get("ram_display", f"{hardware.get('ram_total_mb', 0)} MB")

    lines.extend(_md_table(
        ["Property", "Value"],
        [
            ["Type", type_str],
            ["Architecture", hardware.get("architecture", "unknown")],
            ["CPU", cpu_str],
            ["RAM", f"{ram_display} ({hardware.get('ram_total_mb', 0)} MB actual)"],
        ],
    ))
    lines.append("")

    modules = hardware.get("memory_modules", [])
    if modules:
        lines.append("### Memory Modules")
        lines.append("")
        lines.extend(_md_table(
            ["Slot", "Size", "Type", "Speed", "Manufacturer"],
            [
                [m.get("locator", ""), m.get("size", ""), m.get("type", ""),
                 m.get("speed", ""), m.get("manufacturer", "")]
                for m in modules
            ],
        ))
        lines.append("")

    disks = hardware.get("disks", [])
    if disks:
        lines.append("### Disks")
        lines.append("")
        lines.extend(_md_table(
            ["Device", "Size", "Model"],
            [[d.get("name", ""), d.get("size", ""), d.get("model", "")] for d in disks],
        ))
        lines.append("")

    return lines


def _render_node_resources(status: dict[str, Any]) -> list[str]:
    """Render the Resources and Filesystems sections of a node page."""
    has_status = isinstance(status, dict) and "error" not in status and status
    has_error = isinstance(status, dict) and "error" in status

    lines = ["## Resources", ""]
    if has_error:
        lines.append(f"Error retrieving status: {status['error']}")
    elif has_status:
        lines.extend(_md_table(
            ["Metric", "Value"],
            [
                ["Uptime", status.get("uptime", "unknown")],
                ["CPU", f"{status.get('cpu_percent', 0)}%"],
                ["RAM", f"{status.get('ram_used_mb', 0)} / {status.get('ram_total_mb', 0)} MB"],
            ],
        ))
    else:
        lines.append("No resource data available (SSH may be disabled).")
    lines.append("")

    # Filesystems
    lines.append("## Filesystems")
    lines.append("")
    if has_status:
        filesystems = status.get("filesystems", [])
        if filesystems:
            lines.extend(_md_table(
                ["Mount", "Size", "Used", "Available", "Use%"],
                [
                    [fs.get("mount", ""), f"{fs.get('total_gb', '')}G",
                     f"{fs.get('used_gb', '')}G", f"{fs.get('available_gb', '')}G",
                     fs.get("use_percent", "")]
                    for fs in filesystems
                ],
            ))
        else:
            lines.append("No filesystem data available.")
    else:
        lines.append("No filesystem data available.")
    lines.append("")

    return lines


def _render_node_containers(
    containers: list[dict[str, Any]] | dict[str, Any],
    docker_enabled: bool,
) -> list[str]:
    """Render the Containers section of a node page."""
    lines = ["## Containers", ""]
    if not docker_enabled:
        lines.append("No Docker on this node.")
    elif isinstance(containers, dict) and "error" in containers:
        lines.append(f"Error retrieving containers: {containers['error']}")
    elif isinstance(containers, list) and containers:
        lines.extend(_md_table(
            ["Container", "Image", "Service", "Status"],
            [
                [c.get("name", ""), c.get("image", ""),
                 c.get("compose_service", ""), c.get("status", "")]
                for c in containers
            ],
        ))
    else:
        lines.append("No running containers.")
    lines.append("")
    return lines


def _render_node(
    node: dict[str, Any],
    status: dict[str, Any],
    containers: list[dict[str, Any]] | dict[str, Any],
    hardware: dict[str, Any],
    timestamp: str,
) -> str:
    """Render a per-node documentation page."""
    name = node["name"]
    docker_enabled = node.get("docker_enabled", False)

    lines: list[str] = [
        f"# Node: {name}",
        "",
        f"> Auto-generated by mcp-homelab on {timestamp}",
        "",
        "---",
        "",
    ]

    lines.extend(_render_node_overview(node, docker_enabled))
    lines.extend(_render_node_hardware(hardware))
    lines.extend(_render_node_resources(status))
    lines.extend(_render_node_containers(containers, docker_enabled))

    # -- TODO --
    lines.append("## TODO")
    lines.append("")
    lines.append("- [ ] Document this node's role and purpose")
    lines.append("- [ ] Document access procedures")
    lines.append("- [ ] Note any hardware constraints")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Static templates
# ---------------------------------------------------------------------------

_KNOWN_ISSUES_TEMPLATE = """\
# Known Issues

> Track infrastructure issues here. This file is never overwritten by generate_context.

**Last Updated:** (manual)

---

## Active Issues

(No issues documented yet. Add entries as you discover them.)

## Resolved Issues

(Move resolved issues here for reference.)
"""
