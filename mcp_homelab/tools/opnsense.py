"""OPNsense REST API tools.

Tools for querying firewall state — DHCP leases, interfaces, aliases,
and IP availability checks.
All functions use OPNsenseClient for transport — no direct httpx usage here.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from typing_extensions import TypedDict

from mcp_homelab.core.config import load_config, opnsense_configured
from mcp_homelab.core.opnsense_api import OPNsenseAPIError, OPNsenseClient

_client = OPNsenseClient()

_NOT_CONFIGURED: dict[str, str] = {
    "error": "OPNsense is not configured. Add an 'opnsense' section to config.yaml and set OPNSENSE_API_KEY / OPNSENSE_API_SECRET in .env."
}


async def get_dhcp_leases() -> list[dict[str, Any]]:
    """Return active DHCP leases from OPNsense.

    Queries the DHCPv4 lease table and returns a normalised list.

    Returns:
        List of dicts with keys: ip, mac, hostname, interface,
        status, lease_start, lease_end.

    Raises:
        OPNsenseAPIError: On non-2xx API response.
    """
    if not opnsense_configured():
        return [_NOT_CONFIGURED]

    data = await _client.get("/dhcpv4/leases/searchLease")
    rows: list[dict[str, Any]] = data.get("rows", [])
    return [
        {
            "ip": row.get("address", ""),
            "mac": row.get("mac", ""),
            "hostname": row.get("hostname", ""),
            "interface": row.get("if", ""),
            "status": row.get("status", ""),
            "lease_start": row.get("starts", ""),
            "lease_end": row.get("ends", ""),
        }
        for row in rows
    ]


async def get_interface_status() -> list[dict[str, Any]]:
    """Return interface overview from OPNsense.

    Queries the interfaces overview endpoint and returns a normalised
    list.  This endpoint is covered by the 'Status: Interfaces' privilege.

    Returns:
        List of dicts with keys: name, description, status, address,
        gateway, routes, device.

    Raises:
        OPNsenseAPIError: On non-2xx API response.
    """
    if not opnsense_configured():
        return [_NOT_CONFIGURED]

    data = await _client.get("/interfaces/overview/export")
    results: list[dict[str, Any]] = []
    for iface in data if isinstance(data, list) else data.values():
        results.append(
            {
                "name": iface.get("identifier", iface.get("name", "")),
                "description": iface.get("description", ""),
                "status": iface.get("status", ""),
                "address": iface.get("addr4", iface.get("ipv4", "")),
                "gateway": iface.get("gw4", ""),
                "routes": iface.get("routes", []),
                "device": iface.get("device", ""),
            }
        )
    return results


async def get_firewall_aliases() -> list[dict[str, Any]]:
    """Return defined firewall aliases and their contents.

    Queries the alias search endpoint and returns a normalised list.
    The comma-separated ``content`` field is split into a list of entries.

    Returns:
        List of dicts with keys: name, type, description, entries, enabled.

    Raises:
        OPNsenseAPIError: On non-2xx API response.
    """
    if not opnsense_configured():
        return [_NOT_CONFIGURED]

    data = await _client.get("/firewall/alias/searchItem")
    rows: list[dict[str, Any]] = data.get("rows", [])
    return [
        {
            "name": row.get("name", ""),
            "type": row.get("type", ""),
            "description": row.get("description", ""),
            "entries": [
                entry for entry in
                (e.strip() for e in row.get("content", "").replace("\n", ",").split(","))
                if entry
            ],
            "enabled": row.get("enabled", ""),
        }
        for row in rows
    ]


class IpConflict(TypedDict):
    source: str
    detail: str


class IpAvailabilityResult(TypedDict):
    ip: str
    available: bool
    conflicts: list[IpConflict]
    sources_checked: list[str]
    warnings: list[str]


def _normalize_ip(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Parse and normalize an IP address string.

    Raises:
        ValueError: If the input is not a valid unicast IP address.
    """
    try:
        addr = ipaddress.ip_address(raw.strip())
    except ValueError:
        raise ValueError(f"Invalid IP address: {raw!r}")
    return addr


async def check_ip_available(ip: str) -> IpAvailabilityResult | dict:
    """Check whether an IP address is available for assignment.

    Requires OPNsense to be configured — DHCP lease data comes from the
    OPNsense API. This tool does NOT support other DHCP servers.

    Cross-references the given IP against:
    1. Active DHCP leases from OPNsense (requires OPNsense configuration)
    2. Static host entries in config.yaml

    Use this before ``create_lxc`` or any manual IP assignment to avoid
    conflicts.

    Args:
        ip: IPv4 or IPv6 address to check (e.g. "10.10.10.120").
            Must be a single host address, not CIDR notation.

    Returns:
        IpAvailabilityResult with ``available`` flag, list of conflicts,
        which data sources were checked, and any warnings about partial
        results.

    Raises:
        ValueError: If ``ip`` is not a valid IP address.
    """
    if not opnsense_configured():
        return _NOT_CONFIGURED

    normalized = _normalize_ip(ip)
    normalized_str = str(normalized)

    conflicts: list[IpConflict] = []
    sources_checked: list[str] = []
    warnings: list[str] = []

    # Check DHCP leases
    data = await _client.get("/dhcpv4/leases/searchLease")
    rows: list[dict[str, Any]] = data.get("rows", [])
    sources_checked.append("dhcp_leases")
    for row in rows:
        try:
            lease_ip = _normalize_ip(row.get("address", ""))
        except ValueError:
            continue
        if lease_ip == normalized:
            hostname = row.get("hostname", "unknown")
            mac = row.get("mac", "unknown")
            status = row.get("status", "unknown")
            conflicts.append(IpConflict(
                source="dhcp_lease",
                detail=f"Leased to {hostname} (MAC {mac}, status {status})",
            ))

    # Check config.yaml hosts
    try:
        config = load_config()
        sources_checked.append("config_hosts")
        for name, host in config.hosts.items():
            try:
                host_ip = _normalize_ip(host.ip)
            except ValueError:
                continue
            if host_ip == normalized:
                conflicts.append(IpConflict(
                    source="config_host",
                    detail=f"Assigned to host '{name}' ({host.description})",
                ))
    except (FileNotFoundError, EnvironmentError, ValueError) as exc:
        warnings.append(f"config.yaml check skipped: {exc}")

    return IpAvailabilityResult(
        ip=normalized_str,
        available=len(conflicts) == 0,
        conflicts=conflicts,
        sources_checked=sources_checked,
        warnings=warnings,
    )
