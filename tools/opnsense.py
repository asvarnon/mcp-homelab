"""OPNsense REST API tools.

Tools for querying firewall state — DHCP leases, interfaces, aliases.
All functions use OPNsenseClient for transport — no direct httpx usage here.
"""

from __future__ import annotations

from typing import Any

from core.config import opnsense_configured
from core.opnsense_api import OPNsenseAPIError, OPNsenseClient

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
