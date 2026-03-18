"""Unit tests for tools/opnsense.py.

Tests DHCP lease parsing, interface status normalization, and alias
content splitting with mocked OPNsenseClient responses.
"""

from __future__ import annotations

import pytest

from core.config import AppConfig, HostConfig
from tools.opnsense import (
    check_ip_available,
    get_dhcp_leases,
    get_firewall_aliases,
    get_interface_status,
)


@pytest.fixture(autouse=True)
def mock_opnsense_client(monkeypatch: pytest.MonkeyPatch):
    """Replace the module-level _client with a mock."""
    from unittest.mock import AsyncMock, MagicMock

    mock_client = MagicMock()
    mock_client.get = AsyncMock()
    monkeypatch.setattr("tools.opnsense._client", mock_client)
    # Default: OPNsense is configured (happy path)
    monkeypatch.setattr("tools.opnsense.opnsense_configured", lambda: True)
    return mock_client


# ===========================================================================
# get_dhcp_leases
# ===========================================================================


class TestGetDhcpLeases:
    @pytest.mark.asyncio
    async def test_parses_lease_rows(self, mock_opnsense_client) -> None:
        mock_opnsense_client.get.return_value = {
            "rows": [
                {
                    "address": "192.0.2.10",
                    "mac": "aa:bb:cc:dd:ee:ff",
                    "hostname": "test-node-1",
                    "if": "lan",
                    "status": "online",
                    "starts": "2024-01-01",
                    "ends": "2024-01-02",
                },
            ]
        }
        result = await get_dhcp_leases()
        assert len(result) == 1
        assert result[0]["ip"] == "192.0.2.10"
        assert result[0]["mac"] == "aa:bb:cc:dd:ee:ff"
        assert result[0]["hostname"] == "test-node-1"
        assert result[0]["interface"] == "lan"

    @pytest.mark.asyncio
    async def test_empty_leases(self, mock_opnsense_client) -> None:
        mock_opnsense_client.get.return_value = {"rows": []}
        assert await get_dhcp_leases() == []

    @pytest.mark.asyncio
    async def test_missing_fields_default_empty(self, mock_opnsense_client) -> None:
        mock_opnsense_client.get.return_value = {"rows": [{}]}
        result = await get_dhcp_leases()
        assert result[0]["ip"] == ""
        assert result[0]["hostname"] == ""


# ===========================================================================
# get_interface_status
# ===========================================================================


class TestGetInterfaceStatus:
    @pytest.mark.asyncio
    async def test_parses_list_response(self, mock_opnsense_client) -> None:
        mock_opnsense_client.get.return_value = [
            {
                "identifier": "wan",
                "description": "WAN",
                "status": "up",
                "addr4": "203.0.113.1",
                "gw4": "203.0.113.254",
                "device": "igb0",
                "routes": [],
            }
        ]
        result = await get_interface_status()
        assert len(result) == 1
        assert result[0]["name"] == "wan"
        assert result[0]["address"] == "203.0.113.1"
        assert result[0]["device"] == "igb0"

    @pytest.mark.asyncio
    async def test_falls_back_to_ipv4_field(self, mock_opnsense_client) -> None:
        """Some OPNsense versions use 'ipv4' instead of 'addr4'."""
        mock_opnsense_client.get.return_value = [
            {"name": "lan", "ipv4": "198.51.100.1"}
        ]
        result = await get_interface_status()
        assert result[0]["address"] == "198.51.100.1"

    @pytest.mark.asyncio
    async def test_handles_dict_response(self, mock_opnsense_client) -> None:
        """Response can be a dict instead of a list."""
        mock_opnsense_client.get.return_value = {
            "wan": {"identifier": "wan", "status": "up", "device": "igb0"},
            "lan": {"identifier": "lan", "status": "up", "device": "igb1"},
        }
        result = await get_interface_status()
        assert len(result) == 2


# ===========================================================================
# get_firewall_aliases
# ===========================================================================


class TestGetFirewallAliases:
    @pytest.mark.asyncio
    async def test_parses_aliases(self, mock_opnsense_client) -> None:
        mock_opnsense_client.get.return_value = {
            "rows": [
                {
                    "name": "blocked_hosts",
                    "type": "host",
                    "description": "Blocked IPs",
                    "content": "198.51.100.1,198.51.100.2",
                    "enabled": "1",
                },
            ]
        }
        result = await get_firewall_aliases()
        assert len(result) == 1
        assert result[0]["name"] == "blocked_hosts"
        assert result[0]["entries"] == ["198.51.100.1", "198.51.100.2"]

    @pytest.mark.asyncio
    async def test_splits_newline_content(self, mock_opnsense_client) -> None:
        """Content field can use newlines instead of commas."""
        mock_opnsense_client.get.return_value = {
            "rows": [{"name": "a", "type": "host", "content": "1.1.1.1\n2.2.2.2"}]
        }
        result = await get_firewall_aliases()
        assert result[0]["entries"] == ["1.1.1.1", "2.2.2.2"]

    @pytest.mark.asyncio
    async def test_empty_content(self, mock_opnsense_client) -> None:
        mock_opnsense_client.get.return_value = {
            "rows": [{"name": "empty", "type": "host", "content": ""}]
        }
        result = await get_firewall_aliases()
        assert result[0]["entries"] == []

    @pytest.mark.asyncio
    async def test_strips_whitespace_from_entries(self, mock_opnsense_client) -> None:
        mock_opnsense_client.get.return_value = {
            "rows": [{"name": "a", "type": "host", "content": " 1.1.1.1 , 2.2.2.2 "}]
        }
        result = await get_firewall_aliases()
        assert result[0]["entries"] == ["1.1.1.1", "2.2.2.2"]


# ===========================================================================
# check_ip_available
# ===========================================================================


class TestCheckIpAvailable:
    @pytest.mark.asyncio
    async def test_available_ip(self, mock_opnsense_client, monkeypatch) -> None:
        """IP with no DHCP lease and no config entry is available."""
        mock_opnsense_client.get.return_value = {"rows": []}
        monkeypatch.setattr("tools.opnsense.load_config", lambda: _empty_config())
        result = await check_ip_available("10.10.10.200")
        assert result["available"] is True
        assert result["conflicts"] == []
        assert result["ip"] == "10.10.10.200"
        assert "dhcp_leases" in result["sources_checked"]
        assert "config_hosts" in result["sources_checked"]
        assert result["warnings"] == []

    @pytest.mark.asyncio
    async def test_conflict_dhcp_lease(self, mock_opnsense_client, monkeypatch) -> None:
        """IP with an active DHCP lease is not available."""
        mock_opnsense_client.get.return_value = {
            "rows": [
                {
                    "address": "10.10.10.101",
                    "mac": "aa:bb:cc:dd:ee:ff",
                    "hostname": "uptime-kuma",
                    "status": "online",
                },
            ]
        }
        monkeypatch.setattr("tools.opnsense.load_config", lambda: _empty_config())
        result = await check_ip_available("10.10.10.101")
        assert result["available"] is False
        assert len(result["conflicts"]) == 1
        assert result["conflicts"][0]["source"] == "dhcp_lease"
        assert "uptime-kuma" in result["conflicts"][0]["detail"]

    @pytest.mark.asyncio
    async def test_conflict_config_host(self, mock_opnsense_client, monkeypatch) -> None:
        """IP assigned to a config.yaml host is not available."""
        mock_opnsense_client.get.return_value = {"rows": []}
        config = AppConfig(
            hosts={"gamehost": HostConfig(hostname="gamehost", ip="10.50.50.10", description="Game server")},
            proxmox=None,
            opnsense=None,
        )
        monkeypatch.setattr("tools.opnsense.load_config", lambda: config)
        result = await check_ip_available("10.50.50.10")
        assert result["available"] is False
        assert len(result["conflicts"]) == 1
        assert result["conflicts"][0]["source"] == "config_host"
        assert "gamehost" in result["conflicts"][0]["detail"]

    @pytest.mark.asyncio
    async def test_both_dhcp_and_config_conflict(self, mock_opnsense_client, monkeypatch) -> None:
        """IP that conflicts in both DHCP and config returns two conflicts."""
        mock_opnsense_client.get.return_value = {
            "rows": [{"address": "10.10.10.101", "mac": "aa:bb:cc:dd:ee:ff", "hostname": "box", "status": "online"}]
        }
        config = AppConfig(
            hosts={"monitor": HostConfig(hostname="monitor", ip="10.10.10.101", description="Monitoring VM")},
            proxmox=None,
            opnsense=None,
        )
        monkeypatch.setattr("tools.opnsense.load_config", lambda: config)
        result = await check_ip_available("10.10.10.101")
        assert result["available"] is False
        assert len(result["conflicts"]) == 2
        sources = {c["source"] for c in result["conflicts"]}
        assert sources == {"dhcp_lease", "config_host"}

    @pytest.mark.asyncio
    async def test_no_match_different_ip(self, mock_opnsense_client, monkeypatch) -> None:
        """Existing leases for other IPs don't cause false positives."""
        mock_opnsense_client.get.return_value = {
            "rows": [{"address": "10.10.10.50", "hostname": "pve", "mac": "aa:bb:cc:00:00:01", "status": "online"}]
        }
        config = AppConfig(
            hosts={"pve": HostConfig(hostname="pve", ip="10.10.10.50", description="Proxmox")},
            proxmox=None,
            opnsense=None,
        )
        monkeypatch.setattr("tools.opnsense.load_config", lambda: config)
        result = await check_ip_available("10.10.10.200")
        assert result["available"] is True
        assert result["conflicts"] == []

    @pytest.mark.asyncio
    async def test_config_load_failure_warns(self, mock_opnsense_client, monkeypatch) -> None:
        """If config.yaml can't be loaded, DHCP check still runs and warning is surfaced."""
        mock_opnsense_client.get.return_value = {
            "rows": [{"address": "10.10.10.5", "hostname": "router", "mac": "ff:ff:ff:00:00:01", "status": "online"}]
        }
        monkeypatch.setattr("tools.opnsense.load_config", _raise_config_error)
        result = await check_ip_available("10.10.10.5")
        assert result["available"] is False
        assert len(result["conflicts"]) == 1
        assert result["conflicts"][0]["source"] == "dhcp_lease"
        assert "config_hosts" not in result["sources_checked"]
        assert len(result["warnings"]) == 1
        assert "config.yaml" in result["warnings"][0]

    @pytest.mark.asyncio
    async def test_config_load_failure_no_dhcp_conflict(self, mock_opnsense_client, monkeypatch) -> None:
        """Config failure with clean DHCP returns available=True but with warning."""
        mock_opnsense_client.get.return_value = {"rows": []}
        monkeypatch.setattr("tools.opnsense.load_config", _raise_config_error)
        result = await check_ip_available("10.10.10.200")
        assert result["available"] is True
        assert len(result["warnings"]) == 1
        assert "dhcp_leases" in result["sources_checked"]
        assert "config_hosts" not in result["sources_checked"]

    @pytest.mark.asyncio
    async def test_invalid_ip_raises(self, mock_opnsense_client) -> None:
        """Non-IP strings raise ValueError."""
        with pytest.raises(ValueError, match="Invalid IP"):
            await check_ip_available("not-an-ip")

    @pytest.mark.asyncio
    async def test_cidr_raises(self, mock_opnsense_client) -> None:
        """CIDR notation is rejected — must be a single host address."""
        with pytest.raises(ValueError, match="Invalid IP"):
            await check_ip_available("10.10.10.0/24")

    @pytest.mark.asyncio
    async def test_empty_string_raises(self, mock_opnsense_client) -> None:
        """Empty string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid IP"):
            await check_ip_available("")

    @pytest.mark.asyncio
    async def test_rejects_zero_padded_ip(self, mock_opnsense_client) -> None:
        """Zero-padded octets are ambiguous and rejected."""
        with pytest.raises(ValueError, match="Invalid IP"):
            await check_ip_available("010.010.010.001")

    @pytest.mark.asyncio
    async def test_normalizes_whitespace(self, mock_opnsense_client, monkeypatch) -> None:
        """Leading/trailing whitespace is stripped before validation."""
        mock_opnsense_client.get.return_value = {"rows": []}
        monkeypatch.setattr("tools.opnsense.load_config", lambda: _empty_config())
        result = await check_ip_available("  10.10.10.200  ")
        assert result["available"] is True
        assert result["ip"] == "10.10.10.200"


def _empty_config() -> AppConfig:
    return AppConfig(hosts={}, proxmox=None, opnsense=None)


def _raise_config_error() -> None:
    raise FileNotFoundError("config.yaml not found")


# ===========================================================================
# Not-configured guards
# ===========================================================================


class TestOPNsenseNotConfigured:
    """When OPNsense section is absent from config.yaml, tools return error dicts."""

    @pytest.fixture(autouse=True)
    def disable_opnsense(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("tools.opnsense.opnsense_configured", lambda: False)

    @pytest.mark.asyncio
    async def test_get_dhcp_leases_returns_error(self) -> None:
        result = await get_dhcp_leases()
        assert len(result) == 1
        assert "error" in result[0]
        assert "not configured" in result[0]["error"].lower()

    @pytest.mark.asyncio
    async def test_get_interface_status_returns_error(self) -> None:
        result = await get_interface_status()
        assert len(result) == 1
        assert "error" in result[0]

    @pytest.mark.asyncio
    async def test_get_firewall_aliases_returns_error(self) -> None:
        result = await get_firewall_aliases()
        assert len(result) == 1
        assert "error" in result[0]

    @pytest.mark.asyncio
    async def test_check_ip_available_returns_error(self) -> None:
        result = await check_ip_available("10.10.10.1")
        assert "error" in result
        assert "not configured" in result["error"].lower()
