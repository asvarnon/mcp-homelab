"""Unit tests for tools/proxmox.py.

Tests VM listing, status conversion, and node discovery with mocked
ProxmoxClient responses. No real HTTP calls.

Java comparison: Testing a Spring Service layer with a mocked repository.
"""

from __future__ import annotations

import pytest

from tools.proxmox import _find_vm_node, get_vm_status, list_vms, start_vm, stop_vm


@pytest.fixture(autouse=True)
def mock_proxmox_client(monkeypatch: pytest.MonkeyPatch):
    """Replace the module-level _client with a mock for all tests."""
    from unittest.mock import AsyncMock, MagicMock

    mock_client = MagicMock()
    mock_client.get = AsyncMock()
    mock_client.post = AsyncMock()
    mock_client.get_nodes = AsyncMock()
    monkeypatch.setattr("tools.proxmox._client", mock_client)
    # Default: Proxmox is configured (happy path)
    monkeypatch.setattr("tools.proxmox.proxmox_configured", lambda: True)
    return mock_client


# ===========================================================================
# list_vms
# ===========================================================================


class TestListVms:
    @pytest.mark.asyncio
    async def test_aggregates_across_nodes(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get_nodes.return_value = ["pve"]
        mock_proxmox_client.get.return_value = [
            {"vmid": 100, "name": "test-vm", "status": "running", "cpus": 2, "maxmem": 4294967296},
            {"vmid": 101, "name": "stopped-vm", "status": "stopped", "cpus": 1, "maxmem": 2147483648},
        ]

        result = await list_vms()
        assert len(result) == 2
        assert result[0]["vmid"] == 100
        assert result[0]["name"] == "test-vm"
        assert result[0]["memory_mb"] == 4096
        assert result[1]["memory_mb"] == 2048

    @pytest.mark.asyncio
    async def test_empty_cluster(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get_nodes.return_value = ["pve"]
        mock_proxmox_client.get.return_value = []
        result = await list_vms()
        assert result == []

    @pytest.mark.asyncio
    async def test_memory_conversion(self, mock_proxmox_client) -> None:
        """Proxmox returns memory in bytes — we convert to MB."""
        mock_proxmox_client.get_nodes.return_value = ["pve"]
        mock_proxmox_client.get.return_value = [
            {"vmid": 100, "name": "vm", "status": "running", "cpus": 1, "maxmem": 1073741824},
        ]
        result = await list_vms()
        assert result[0]["memory_mb"] == 1024  # 1 GB in MB


# ===========================================================================
# get_vm_status
# ===========================================================================


class TestGetVmStatus:
    @pytest.mark.asyncio
    async def test_returns_formatted_status(self, mock_proxmox_client) -> None:
        # First call: _find_vm_node uses /cluster/resources
        mock_proxmox_client.get.side_effect = [
            # /cluster/resources?type=vm
            [{"vmid": 100, "node": "pve"}],
            # /nodes/pve/qemu/100/status/current
            {
                "vmid": 100,
                "name": "test-vm",
                "status": "running",
                "uptime": 86400,
                "cpu": 0.25,
                "mem": 2147483648,
                "maxmem": 4294967296,
            },
        ]

        result = await get_vm_status(100)
        assert result["vmid"] == 100
        assert result["status"] == "running"
        assert result["uptime_seconds"] == 86400
        assert result["cpu_usage_percent"] == 25.0
        assert result["memory_used_mb"] == 2048
        assert result["memory_total_mb"] == 4096

    @pytest.mark.asyncio
    async def test_cpu_percentage_conversion(self, mock_proxmox_client) -> None:
        """Proxmox returns CPU as 0.0–1.0 float — we convert to percentage."""
        mock_proxmox_client.get.side_effect = [
            [{"vmid": 100, "node": "pve"}],
            {"vmid": 100, "name": "vm", "status": "running", "cpu": 0.75, "mem": 0, "maxmem": 0},
        ]
        result = await get_vm_status(100)
        assert result["cpu_usage_percent"] == 75.0


# ===========================================================================
# _find_vm_node
# ===========================================================================


class TestFindVmNode:
    @pytest.mark.asyncio
    async def test_finds_node(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [
            {"vmid": 100, "node": "pve"},
            {"vmid": 200, "node": "pve2"},
        ]
        assert await _find_vm_node(200) == "pve2"

    @pytest.mark.asyncio
    async def test_raises_for_missing_vm(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [{"vmid": 100, "node": "pve"}]
        with pytest.raises(ValueError, match="VM 999 not found"):
            await _find_vm_node(999)


# ===========================================================================
# Not-configured guards
# ===========================================================================


class TestProxmoxNotConfigured:
    """When Proxmox section is absent from config.yaml, tools return error dicts."""

    @pytest.fixture(autouse=True)
    def disable_proxmox(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("tools.proxmox.proxmox_configured", lambda: False)

    @pytest.mark.asyncio
    async def test_list_vms_returns_error(self) -> None:
        result = await list_vms()
        assert len(result) == 1
        assert "error" in result[0]
        assert "not configured" in result[0]["error"].lower()

    @pytest.mark.asyncio
    async def test_get_vm_status_returns_error(self) -> None:
        result = await get_vm_status(100)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_start_vm_returns_error_string(self) -> None:
        result = await start_vm(100)
        assert "not configured" in result.lower()

    @pytest.mark.asyncio
    async def test_stop_vm_returns_error_string(self) -> None:
        result = await stop_vm(100)
        assert "not configured" in result.lower()
