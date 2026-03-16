"""Unit tests for tools/opnsense.py.

Tests DHCP lease parsing, interface status normalization, and alias
content splitting with mocked OPNsenseClient responses.
"""

from __future__ import annotations

import pytest

from tools.opnsense import get_dhcp_leases, get_firewall_aliases, get_interface_status


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
                    "address": "10.0.50.10",
                    "mac": "aa:bb:cc:dd:ee:ff",
                    "hostname": "gamehost",
                    "if": "lan",
                    "status": "online",
                    "starts": "2024-01-01",
                    "ends": "2024-01-02",
                },
            ]
        }
        result = await get_dhcp_leases()
        assert len(result) == 1
        assert result[0]["ip"] == "10.0.50.10"
        assert result[0]["mac"] == "aa:bb:cc:dd:ee:ff"
        assert result[0]["hostname"] == "gamehost"
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
            {"name": "lan", "ipv4": "10.0.0.1"}
        ]
        result = await get_interface_status()
        assert result[0]["address"] == "10.0.0.1"

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
                    "content": "10.0.0.1,10.0.0.2",
                    "enabled": "1",
                },
            ]
        }
        result = await get_firewall_aliases()
        assert len(result) == 1
        assert result[0]["name"] == "blocked_hosts"
        assert result[0]["entries"] == ["10.0.0.1", "10.0.0.2"]

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
