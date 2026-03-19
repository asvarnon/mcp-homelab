"""Unit tests for tools/proxmox.py.

Tests VM listing, status conversion, and node discovery with mocked
ProxmoxClient responses. No real HTTP calls.

Java comparison: Testing a Spring Service layer with a mocked repository.
"""

from __future__ import annotations

import pytest
from mcp_homelab.core.config import ProxmoxConfig

from mcp_homelab.tools.proxmox import (
    _find_ct_node,
    _resolve_default_node,
    _find_resource_node,
    _find_vm_node,
    _validate_node,
    _validate_safe_field,
    _validate_vmid,
    create_lxc,
    create_vm,
    get_lxc_status,
    get_next_vmid,
    get_vm_status,
    list_lxc,
    list_storage,
    list_templates,
    list_vms,
    start_lxc,
    start_vm,
    stop_lxc,
    stop_vm,
)


@pytest.fixture(autouse=True)
def mock_proxmox_client(monkeypatch: pytest.MonkeyPatch):
    """Replace the module-level _client with a mock for all tests."""
    from unittest.mock import AsyncMock, MagicMock

    mock_client = MagicMock()
    mock_client.get = AsyncMock()
    mock_client.post = AsyncMock()
    mock_client.get_nodes = AsyncMock()
    monkeypatch.setattr("mcp_homelab.tools.proxmox._client", mock_client)
    # Default: Proxmox is configured (happy path)
    monkeypatch.setattr("mcp_homelab.tools.proxmox.proxmox_configured", lambda: True)
    return mock_client


# ===========================================================================
# list_vms
# ===========================================================================


class TestListVms:
    @pytest.mark.asyncio
    async def test_aggregates_across_nodes(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get_nodes.return_value = ["test-node-2"]
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
        mock_proxmox_client.get_nodes.return_value = ["test-node-2"]
        mock_proxmox_client.get.return_value = []
        result = await list_vms()
        assert result == []

    @pytest.mark.asyncio
    async def test_memory_conversion(self, mock_proxmox_client) -> None:
        """Proxmox returns memory in bytes — we convert to MB."""
        mock_proxmox_client.get_nodes.return_value = ["test-node-2"]
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
            [{"vmid": 100, "node": "test-node-2", "type": "qemu"}],
            # /nodes/test-node-2/qemu/100/status/current
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
            [{"vmid": 100, "node": "test-node-2", "type": "qemu"}],
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
            {"vmid": 100, "node": "test-node-2", "type": "qemu"},
            {"vmid": 200, "node": "test-node-4", "type": "qemu"},
        ]
        assert await _find_vm_node(200) == "test-node-4"

    @pytest.mark.asyncio
    async def test_raises_for_missing_vm(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [{"vmid": 100, "node": "test-node-2", "type": "qemu"}]
        with pytest.raises(ValueError, match="VM 999 not found"):
            await _find_vm_node(999)


# ===========================================================================
# Not-configured guards
# ===========================================================================


class TestProxmoxNotConfigured:
    """When Proxmox section is absent from config.yaml, tools return error dicts."""

    @pytest.fixture(autouse=True)
    def disable_proxmox(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("mcp_homelab.tools.proxmox.proxmox_configured", lambda: False)

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


# ===========================================================================
# _find_ct_node
# ===========================================================================


class TestFindCtNode:
    @pytest.mark.asyncio
    async def test_finds_node(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [
            {"vmid": 100, "node": "test-node-2", "type": "lxc"},
            {"vmid": 200, "node": "test-node-4", "type": "lxc"},
        ]
        assert await _find_ct_node(200) == "test-node-4"

    @pytest.mark.asyncio
    async def test_raises_for_missing_ct(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [{"vmid": 100, "node": "test-node-2", "type": "lxc"}]
        with pytest.raises(ValueError, match="LXC container 999 not found"):
            await _find_ct_node(999)

    @pytest.mark.asyncio
    async def test_ignores_qemu_with_same_vmid(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [
            {"vmid": 100, "node": "test-node-2", "type": "qemu"},
        ]
        with pytest.raises(ValueError, match="LXC container 100 not found"):
            await _find_ct_node(100)


# ===========================================================================
# _find_resource_node (shared helper)
# ===========================================================================


class TestFindResourceNode:
    @pytest.mark.asyncio
    async def test_finds_qemu_node(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [
            {"vmid": 100, "node": "test-node-2", "type": "qemu"},
        ]
        assert await _find_resource_node(100, "qemu") == "test-node-2"

    @pytest.mark.asyncio
    async def test_finds_lxc_node(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [
            {"vmid": 300, "node": "test-node-4", "type": "lxc"},
        ]
        assert await _find_resource_node(300, "lxc") == "test-node-4"

    @pytest.mark.asyncio
    async def test_raises_for_missing_qemu(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = []
        with pytest.raises(ValueError, match="VM 999 not found"):
            await _find_resource_node(999, "qemu")

    @pytest.mark.asyncio
    async def test_raises_for_missing_lxc(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = []
        with pytest.raises(ValueError, match="LXC container 999 not found"):
            await _find_resource_node(999, "lxc")

    @pytest.mark.asyncio
    async def test_does_not_cross_match_types(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [
            {"vmid": 100, "node": "test-node-2", "type": "qemu"},
        ]
        with pytest.raises(ValueError, match="LXC container 100 not found"):
            await _find_resource_node(100, "lxc")


# ===========================================================================
# _resolve_default_node
# ===========================================================================


class TestResolveDefaultNode:
    @pytest.mark.asyncio
    async def test_uses_configured_default_when_present(self, monkeypatch: pytest.MonkeyPatch, mock_proxmox_client) -> None:
        mock_proxmox_client.get_nodes.return_value = ["test-node-2", "test-node-4"]
        monkeypatch.setattr(
            "mcp_homelab.tools.proxmox.get_proxmox_config",
            lambda: ProxmoxConfig(host="203.0.113.50", default_node="test-node-4"),
        )
        result = await _resolve_default_node()
        assert result == "test-node-4"

    @pytest.mark.asyncio
    async def test_falls_back_when_configured_default_missing(self, monkeypatch: pytest.MonkeyPatch, mock_proxmox_client) -> None:
        mock_proxmox_client.get_nodes.return_value = ["test-node-2", "test-node-4"]
        monkeypatch.setattr(
            "mcp_homelab.tools.proxmox.get_proxmox_config",
            lambda: ProxmoxConfig(host="203.0.113.50", default_node="test-node-3"),
        )
        result = await _resolve_default_node()
        assert result == "test-node-2"

    @pytest.mark.asyncio
    async def test_falls_back_when_default_node_not_set(self, monkeypatch: pytest.MonkeyPatch, mock_proxmox_client) -> None:
        mock_proxmox_client.get_nodes.return_value = ["test-node-2", "test-node-4"]
        monkeypatch.setattr(
            "mcp_homelab.tools.proxmox.get_proxmox_config",
            lambda: ProxmoxConfig(host="203.0.113.50", default_node=None),
        )
        result = await _resolve_default_node()
        assert result == "test-node-2"

    @pytest.mark.asyncio
    async def test_raises_when_cluster_has_no_nodes(self, monkeypatch: pytest.MonkeyPatch, mock_proxmox_client) -> None:
        mock_proxmox_client.get_nodes.return_value = []
        monkeypatch.setattr(
            "mcp_homelab.tools.proxmox.get_proxmox_config",
            lambda: ProxmoxConfig(host="203.0.113.50", default_node="test-node-2"),
        )
        with pytest.raises(ValueError, match="No Proxmox nodes found in cluster"):
            await _resolve_default_node()


# ===========================================================================
# list_lxc
# ===========================================================================


class TestListLxc:
    @pytest.mark.asyncio
    async def test_aggregates_across_nodes(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get_nodes.return_value = ["test-node-2", "test-node-4"]
        mock_proxmox_client.get.side_effect = [
            [{"vmid": 300, "name": "ct-a", "status": "running", "cpus": 2, "maxmem": 1073741824}],
            [{"vmid": 301, "name": "ct-b", "status": "stopped", "cpus": 1, "maxmem": 536870912}],
        ]
        result = await list_lxc()
        assert len(result) == 2
        assert result[0]["vmid"] == 300
        assert result[0]["type"] == "lxc"
        assert result[1]["vmid"] == 301

    @pytest.mark.asyncio
    async def test_empty_cluster(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get_nodes.return_value = ["test-node-2"]
        mock_proxmox_client.get.return_value = []
        result = await list_lxc()
        assert result == []

    @pytest.mark.asyncio
    async def test_memory_conversion(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get_nodes.return_value = ["test-node-2"]
        mock_proxmox_client.get.return_value = [
            {"vmid": 300, "name": "ct", "status": "running", "cpus": 1, "maxmem": 2147483648},
        ]
        result = await list_lxc()
        assert result[0]["memory_mb"] == 2048


# ===========================================================================
# get_lxc_status
# ===========================================================================


class TestGetLxcStatus:
    @pytest.mark.asyncio
    async def test_returns_formatted_status(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.side_effect = [
            [{"vmid": 300, "node": "test-node-2", "type": "lxc"}],
            {
                "vmid": 300,
                "name": "test-ct",
                "status": "running",
                "uptime": 3600,
                "cpu": 0.15,
                "mem": 536870912,
                "maxmem": 1073741824,
                "swap": 104857600,
                "maxswap": 536870912,
                "disk": 2147483648,
                "maxdisk": 8589934592,
            },
        ]
        result = await get_lxc_status(300)
        assert result["vmid"] == 300
        assert result["name"] == "test-ct"
        assert result["status"] == "running"
        assert result["uptime_seconds"] == 3600
        assert result["cpu_usage_percent"] == 15.0
        assert result["memory_used_mb"] == 512
        assert result["memory_total_mb"] == 1024
        assert result["swap_used_mb"] == 100
        assert result["swap_total_mb"] == 512
        assert result["disk_used_gb"] == 2.0
        assert result["disk_total_gb"] == 8.0
        assert result["type"] == "lxc"

    @pytest.mark.asyncio
    async def test_cpu_percentage_conversion(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.side_effect = [
            [{"vmid": 300, "node": "test-node-2", "type": "lxc"}],
            {"vmid": 300, "name": "ct", "status": "running", "cpu": 0.85, "mem": 0, "maxmem": 0, "swap": 0, "maxswap": 0, "disk": 0, "maxdisk": 0},
        ]
        result = await get_lxc_status(300)
        assert result["cpu_usage_percent"] == 85.0


# ===========================================================================
# start_lxc
# ===========================================================================


class TestStartLxc:
    @pytest.mark.asyncio
    async def test_returns_confirmation(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [{"vmid": 300, "node": "test-node-2", "type": "lxc"}]
        mock_proxmox_client.post.return_value = "UPID:test-node-2:00001234:00000000:65F00000:vzstart:300:root@pam:"
        result = await start_lxc(300)
        assert "LXC 300 start initiated" in result
        assert "UPID" in result


# ===========================================================================
# stop_lxc
# ===========================================================================


class TestStopLxc:
    @pytest.mark.asyncio
    async def test_returns_confirmation(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [{"vmid": 300, "node": "test-node-2", "type": "lxc"}]
        mock_proxmox_client.post.return_value = "UPID:test-node-2:00001234:00000000:65F00000:vzstop:300:root@pam:"
        result = await stop_lxc(300)
        assert "LXC 300 stop initiated" in result
        assert "UPID" in result


# ===========================================================================
# create_lxc
# ===========================================================================


class TestCreateLxc:
    @pytest.mark.asyncio
    async def test_builds_correct_payload(self, mock_proxmox_client) -> None:
        mock_proxmox_client.post.return_value = "UPID:test-node-2:create:300"
        result = await create_lxc(
            node="test-node-2",
            ostemplate="local:vztmpl/debian-12.tar.zst",
            vmid=300,
            hostname="test-ct",
            cores=2,
            memory_mb=1024,
            swap_mb=256,
            disk_gb=8,
            storage="local-lvm",
        )
        assert result["vmid"] == 300
        assert result["node"] == "test-node-2"
        assert result["task_id"] == "UPID:test-node-2:create:300"

        # Verify the POST was called with the right data
        call_args = mock_proxmox_client.post.call_args
        assert call_args[0][0] == "/nodes/test-node-2/lxc"
        posted_data = call_args[1]["data"] if "data" in call_args[1] else call_args[0][1]
        assert posted_data["vmid"] == 300
        assert posted_data["ostemplate"] == "local:vztmpl/debian-12.tar.zst"
        assert posted_data["cores"] == 2
        assert posted_data["memory"] == 1024
        assert posted_data["swap"] == 256
        assert posted_data["rootfs"] == "local-lvm:8"
        assert posted_data["hostname"] == "test-ct"
        assert posted_data["unprivileged"] == 1
        assert posted_data["start"] == 0

    @pytest.mark.asyncio
    async def test_auto_assigns_vmid(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = 400
        mock_proxmox_client.post.return_value = "UPID:test-node-2:create:400"
        result = await create_lxc(
            node="test-node-2",
            ostemplate="local:vztmpl/debian-12.tar.zst",
        )
        assert result["vmid"] == 400
        mock_proxmox_client.get.assert_called_with("/cluster/nextid")

    @pytest.mark.asyncio
    async def test_net0_with_vlan(self, mock_proxmox_client) -> None:
        mock_proxmox_client.post.return_value = "UPID:test-node-2:create:300"
        await create_lxc(
            node="test-node-2",
            ostemplate="local:vztmpl/debian-12.tar.zst",
            vmid=300,
            vlan_tag=50,
        )
        posted_data = mock_proxmox_client.post.call_args[1].get("data") or mock_proxmox_client.post.call_args[0][1]
        assert "tag=50" in posted_data["net0"]

    @pytest.mark.asyncio
    async def test_net0_without_vlan(self, mock_proxmox_client) -> None:
        mock_proxmox_client.post.return_value = "UPID:test-node-2:create:300"
        await create_lxc(
            node="test-node-2",
            ostemplate="local:vztmpl/debian-12.tar.zst",
            vmid=300,
        )
        posted_data = mock_proxmox_client.post.call_args[1].get("data") or mock_proxmox_client.post.call_args[0][1]
        assert "tag=" not in posted_data["net0"]
        assert posted_data["net0"] == "name=eth0,bridge=vmbr0,ip=dhcp"

    @pytest.mark.asyncio
    async def test_optional_ssh_key(self, mock_proxmox_client) -> None:
        mock_proxmox_client.post.return_value = "UPID:test-node-2:create:300"
        await create_lxc(
            node="test-node-2",
            ostemplate="local:vztmpl/debian-12.tar.zst",
            vmid=300,
            ssh_public_key="ssh-ed25519 AAAA... user@host",
        )
        posted_data = mock_proxmox_client.post.call_args[1].get("data") or mock_proxmox_client.post.call_args[0][1]
        assert posted_data["ssh-public-keys"] == "ssh-ed25519 AAAA... user@host"

    @pytest.mark.asyncio
    async def test_optional_password(self, mock_proxmox_client) -> None:
        mock_proxmox_client.post.return_value = "UPID:test-node-2:create:300"
        await create_lxc(
            node="test-node-2",
            ostemplate="local:vztmpl/debian-12.tar.zst",
            vmid=300,
            password="s3cret",
        )
        posted_data = mock_proxmox_client.post.call_args[1].get("data") or mock_proxmox_client.post.call_args[0][1]
        assert posted_data["password"] == "s3cret"

    @pytest.mark.asyncio
    async def test_optional_features(self, mock_proxmox_client) -> None:
        mock_proxmox_client.post.return_value = "UPID:test-node-2:create:300"
        await create_lxc(
            node="test-node-2",
            ostemplate="local:vztmpl/debian-12.tar.zst",
            vmid=300,
            features="nesting=1,keyctl=1",
        )
        posted_data = mock_proxmox_client.post.call_args[1].get("data") or mock_proxmox_client.post.call_args[0][1]
        assert posted_data["features"] == "nesting=1,keyctl=1"

    @pytest.mark.asyncio
    async def test_features_omitted_when_none(self, mock_proxmox_client) -> None:
        mock_proxmox_client.post.return_value = "UPID:test-node-2:create:300"
        await create_lxc(
            node="test-node-2",
            ostemplate="local:vztmpl/debian-12.tar.zst",
            vmid=300,
        )
        posted_data = mock_proxmox_client.post.call_args[1].get("data") or mock_proxmox_client.post.call_args[0][1]
        assert "features" not in posted_data

    @pytest.mark.asyncio
    async def test_uses_config_defaults_when_storage_and_bridge_are_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_proxmox_client,
    ) -> None:
        mock_proxmox_client.post.return_value = "UPID:test-node-2:create:300"
        monkeypatch.setattr(
            "mcp_homelab.tools.proxmox.get_proxmox_config",
            lambda: ProxmoxConfig(
                host="203.0.113.50",
                default_storage="local",
                default_bridge="vmbr1",
            ),
        )

        await create_lxc(
            node="test-node-2",
            ostemplate="local:vztmpl/debian-12.tar.zst",
            vmid=300,
            storage=None,
            bridge=None,
        )

        posted_data = mock_proxmox_client.post.call_args[1].get("data") or mock_proxmox_client.post.call_args[0][1]
        assert posted_data["rootfs"] == "local:4"
        assert posted_data["net0"] == "name=eth0,bridge=vmbr1,ip=dhcp"


class TestCreateLxcValidation:
    @pytest.mark.asyncio
    async def test_rejects_zero_cores(self) -> None:
        with pytest.raises(ValueError, match=r"cores must be >= 1, got 0"):
            await create_lxc(
                node="test-node-2",
                ostemplate="local:vztmpl/debian-12.tar.zst",
                vmid=300,
                cores=0,
            )

    @pytest.mark.asyncio
    async def test_rejects_negative_memory(self) -> None:
        with pytest.raises(ValueError, match=r"memory_mb must be >= 16, got -1"):
            await create_lxc(
                node="test-node-2",
                ostemplate="local:vztmpl/debian-12.tar.zst",
                vmid=300,
                memory_mb=-1,
            )

    @pytest.mark.asyncio
    async def test_rejects_negative_swap(self) -> None:
        with pytest.raises(ValueError, match=r"swap_mb must be >= 0, got -1"):
            await create_lxc(
                node="test-node-2",
                ostemplate="local:vztmpl/debian-12.tar.zst",
                vmid=300,
                swap_mb=-1,
            )

    @pytest.mark.asyncio
    async def test_rejects_zero_disk(self) -> None:
        with pytest.raises(ValueError, match=r"disk_gb must be >= 1, got 0"):
            await create_lxc(
                node="test-node-2",
                ostemplate="local:vztmpl/debian-12.tar.zst",
                vmid=300,
                disk_gb=0,
            )

    @pytest.mark.asyncio
    async def test_rejects_vlan_below_range(self) -> None:
        with pytest.raises(ValueError, match=r"vlan_tag must be in range 1-4094 when set, got 0"):
            await create_lxc(
                node="test-node-2",
                ostemplate="local:vztmpl/debian-12.tar.zst",
                vmid=300,
                vlan_tag=0,
            )

    @pytest.mark.asyncio
    async def test_rejects_vlan_above_range(self) -> None:
        with pytest.raises(ValueError, match=r"vlan_tag must be in range 1-4094 when set, got 4095"):
            await create_lxc(
                node="test-node-2",
                ostemplate="local:vztmpl/debian-12.tar.zst",
                vmid=300,
                vlan_tag=4095,
            )

    @pytest.mark.asyncio
    async def test_accepts_valid_vlan(self, mock_proxmox_client) -> None:
        mock_proxmox_client.post.return_value = "UPID:test-node-2:create:300"

        result = await create_lxc(
            node="test-node-2",
            ostemplate="local:vztmpl/debian-12.tar.zst",
            vmid=300,
            vlan_tag=50,
        )

        assert result["vmid"] == 300
        posted_data = mock_proxmox_client.post.call_args[1].get("data") or mock_proxmox_client.post.call_args[0][1]
        assert "tag=50" in posted_data["net0"]


class TestCreateVm:
    @pytest.mark.asyncio
    async def test_builds_correct_payload(self, mock_proxmox_client) -> None:
        mock_proxmox_client.post.return_value = "UPID:test-node-2:create:901"

        result = await create_vm(
            node="test-node-2",
            iso="local:iso/debian-13.3.0-amd64-netinst.iso",
            name="Monitoring-layer",
            vmid=901,
            cores=1,
            sockets=1,
            cpu_type="host",
            memory_mb=1024,
            disk_gb=16,
            storage="local",
            bridge="vmbr0",
            ostype="l26",
            scsihw="virtio-scsi-single",
            balloon=0,
            start_after_create=False,
        )

        assert result["vmid"] == 901
        assert result["node"] == "test-node-2"
        assert result["task_id"] == "UPID:test-node-2:create:901"

        call_args = mock_proxmox_client.post.call_args
        assert call_args[0][0] == "/nodes/test-node-2/qemu"
        posted_data = call_args[1]["data"] if "data" in call_args[1] else call_args[0][1]
        assert posted_data["vmid"] == 901
        assert posted_data["cores"] == 1
        assert posted_data["sockets"] == 1
        assert posted_data["cpu"] == "host"
        assert posted_data["memory"] == 1024
        assert posted_data["ostype"] == "l26"
        assert posted_data["scsihw"] == "virtio-scsi-single"
        assert posted_data["balloon"] == 0
        assert posted_data["ide2"] == "local:iso/debian-13.3.0-amd64-netinst.iso,media=cdrom"
        assert posted_data["scsi0"] == "local:16,iothread=1"
        assert posted_data["net0"] == "model=virtio,bridge=vmbr0"
        assert posted_data["boot"] == "order=scsi0;ide2;net0"
        assert posted_data["start"] == 0
        assert posted_data["numa"] == 0
        assert posted_data["name"] == "Monitoring-layer"

    @pytest.mark.asyncio
    async def test_auto_assigns_vmid(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = 902
        mock_proxmox_client.post.return_value = "UPID:test-node-2:create:902"

        result = await create_vm(
            node="test-node-2",
            iso="local:iso/debian-13.3.0-amd64-netinst.iso",
            vmid=None,
        )

        assert result["vmid"] == 902
        mock_proxmox_client.get.assert_called_with("/cluster/nextid")

    @pytest.mark.asyncio
    async def test_net0_with_vlan(self, mock_proxmox_client) -> None:
        mock_proxmox_client.post.return_value = "UPID:test-node-2:create:903"

        await create_vm(
            node="test-node-2",
            iso="local:iso/debian-13.3.0-amd64-netinst.iso",
            vmid=903,
            vlan_tag=10,
        )

        posted_data = mock_proxmox_client.post.call_args[1].get("data") or mock_proxmox_client.post.call_args[0][1]
        assert posted_data["net0"] == "model=virtio,bridge=vmbr0,tag=10"

    @pytest.mark.asyncio
    async def test_net0_without_vlan(self, mock_proxmox_client) -> None:
        mock_proxmox_client.post.return_value = "UPID:test-node-2:create:904"

        await create_vm(
            node="test-node-2",
            iso="local:iso/debian-13.3.0-amd64-netinst.iso",
            vmid=904,
        )

        posted_data = mock_proxmox_client.post.call_args[1].get("data") or mock_proxmox_client.post.call_args[0][1]
        assert posted_data["net0"] == "model=virtio,bridge=vmbr0"
        assert "tag=" not in posted_data["net0"]

    @pytest.mark.asyncio
    async def test_optional_name(self, mock_proxmox_client) -> None:
        mock_proxmox_client.post.return_value = "UPID:test-node-2:create:905"

        await create_vm(
            node="test-node-2",
            iso="local:iso/debian-13.3.0-amd64-netinst.iso",
            vmid=905,
            name="vm-with-name",
        )
        posted_data_with_name = mock_proxmox_client.post.call_args[1].get("data") or mock_proxmox_client.post.call_args[0][1]
        assert posted_data_with_name["name"] == "vm-with-name"

        mock_proxmox_client.post.return_value = "UPID:test-node-2:create:906"
        await create_vm(
            node="test-node-2",
            iso="local:iso/debian-13.3.0-amd64-netinst.iso",
            vmid=906,
            name=None,
        )
        posted_data_without_name = mock_proxmox_client.post.call_args[1].get("data") or mock_proxmox_client.post.call_args[0][1]
        assert "name" not in posted_data_without_name

    @pytest.mark.asyncio
    async def test_uses_config_defaults_when_storage_and_bridge_are_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_proxmox_client,
    ) -> None:
        mock_proxmox_client.post.return_value = "UPID:test-node-2:create:907"
        monkeypatch.setattr(
            "mcp_homelab.tools.proxmox.get_proxmox_config",
            lambda: ProxmoxConfig(
                host="203.0.113.50",
                default_storage="local-zfs",
                default_bridge="vmbr1",
            ),
        )

        await create_vm(
            node="test-node-2",
            iso="local:iso/debian-13.3.0-amd64-netinst.iso",
            vmid=907,
            storage=None,
            bridge=None,
        )

        posted_data = mock_proxmox_client.post.call_args[1].get("data") or mock_proxmox_client.post.call_args[0][1]
        assert posted_data["scsi0"] == "local-zfs:16,iothread=1"
        assert posted_data["net0"] == "model=virtio,bridge=vmbr1"

    @pytest.mark.asyncio
    async def test_not_configured_returns_sentinel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("mcp_homelab.tools.proxmox.proxmox_configured", lambda: False)

        result = await create_vm(
            node="test-node-2",
            iso="local:iso/debian-13.3.0-amd64-netinst.iso",
            vmid=908,
        )

        assert "error" in result
        assert "not configured" in result["error"].lower()


class TestCreateVmValidation:
    @pytest.mark.asyncio
    async def test_cores_below_minimum(self) -> None:
        with pytest.raises(ValueError, match=r"cores must be >= 1, got 0"):
            await create_vm(
                node="test-node-2",
                iso="local:iso/debian-13.3.0-amd64-netinst.iso",
                vmid=909,
                cores=0,
            )

    @pytest.mark.asyncio
    async def test_sockets_below_minimum(self) -> None:
        with pytest.raises(ValueError, match=r"sockets must be >= 1, got 0"):
            await create_vm(
                node="test-node-2",
                iso="local:iso/debian-13.3.0-amd64-netinst.iso",
                vmid=910,
                sockets=0,
            )

    @pytest.mark.asyncio
    async def test_memory_below_minimum(self) -> None:
        with pytest.raises(ValueError, match=r"memory_mb must be >= 64, got 0"):
            await create_vm(
                node="test-node-2",
                iso="local:iso/debian-13.3.0-amd64-netinst.iso",
                vmid=911,
                memory_mb=0,
            )

    @pytest.mark.asyncio
    async def test_disk_below_minimum(self) -> None:
        with pytest.raises(ValueError, match=r"disk_gb must be >= 1, got 0"):
            await create_vm(
                node="test-node-2",
                iso="local:iso/debian-13.3.0-amd64-netinst.iso",
                vmid=912,
                disk_gb=0,
            )

    @pytest.mark.asyncio
    async def test_vlan_tag_out_of_range(self) -> None:
        with pytest.raises(ValueError, match=r"vlan_tag must be in range 1-4094 when set, got 0"):
            await create_vm(
                node="test-node-2",
                iso="local:iso/debian-13.3.0-amd64-netinst.iso",
                vmid=913,
                vlan_tag=0,
            )

        with pytest.raises(ValueError, match=r"vlan_tag must be in range 1-4094 when set, got 4095"):
            await create_vm(
                node="test-node-2",
                iso="local:iso/debian-13.3.0-amd64-netinst.iso",
                vmid=914,
                vlan_tag=4095,
            )


# ===========================================================================
# get_next_vmid
# ===========================================================================


class TestGetNextVmid:
    @pytest.mark.asyncio
    async def test_returns_integer(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = "500"
        result = await get_next_vmid()
        assert result == 500
        assert isinstance(result, int)


# ===========================================================================
# list_storage
# ===========================================================================


class TestListStorage:
    @pytest.mark.asyncio
    async def test_returns_storage_info(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [
            {
                "storage": "local-lvm",
                "type": "lvmthin",
                "content": "images,rootdir",
                "total": 107374182400,
                "used": 53687091200,
                "avail": 53687091200,
                "active": 1,
            },
        ]
        result = await list_storage(node="test-node-2")
        assert len(result) == 1
        assert result[0]["storage"] == "local-lvm"
        assert result[0]["total_gb"] == 100.0
        assert result[0]["used_gb"] == 50.0
        assert result[0]["avail_gb"] == 50.0
        assert result[0]["active"] is True

    @pytest.mark.asyncio
    async def test_defaults_to_first_node(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get_nodes.return_value = ["test-node-2"]
        mock_proxmox_client.get.return_value = []
        result = await list_storage()
        assert result == []
        mock_proxmox_client.get_nodes.assert_called_once()


# ===========================================================================
# list_templates
# ===========================================================================


class TestListTemplates:
    @pytest.mark.asyncio
    async def test_returns_template_info(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [
            {"volid": "local:vztmpl/debian-12.tar.zst", "format": "tar.zst", "size": 131072000},
        ]
        result = await list_templates(node="test-node-2", storage="local")
        assert len(result) == 1
        assert result[0]["volid"] == "local:vztmpl/debian-12.tar.zst"
        assert result[0]["format"] == "tar.zst"
        assert result[0]["size_mb"] == 125.0

    @pytest.mark.asyncio
    async def test_defaults_to_first_storage_with_vztmpl(self, mock_proxmox_client) -> None:
        # First call: get_nodes, second call: list_storage (node/storage), third call: content
        mock_proxmox_client.get_nodes.return_value = ["test-node-2"]
        mock_proxmox_client.get.side_effect = [
            # get_nodes already mocked above, first get = list_storage
            [
                {"storage": "local-lvm", "type": "lvmthin", "content": "images,rootdir", "total": 0, "used": 0, "avail": 0, "active": 1},
                {"storage": "local", "type": "dir", "content": "vztmpl,iso,backup", "total": 0, "used": 0, "avail": 0, "active": 1},
            ],
            # content query
            [{"volid": "local:vztmpl/ubuntu-22.tar.zst", "format": "tar.zst", "size": 262144000}],
        ]
        result = await list_templates(node="test-node-2")
        assert len(result) == 1
        assert result[0]["volid"] == "local:vztmpl/ubuntu-22.tar.zst"

    @pytest.mark.asyncio
    async def test_explicit_storage(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [
            {"volid": "nfs:vztmpl/alpine.tar.gz", "format": "tar.gz", "size": 5242880},
        ]
        result = await list_templates(node="test-node-2", storage="nfs")
        assert len(result) == 1
        assert result[0]["volid"] == "nfs:vztmpl/alpine.tar.gz"


# ===========================================================================
# LXC not-configured guards
# ===========================================================================


class TestLxcNotConfigured:
    """When Proxmox section is absent, LXC tools return error dicts/strings."""

    @pytest.fixture(autouse=True)
    def disable_proxmox(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("mcp_homelab.tools.proxmox.proxmox_configured", lambda: False)

    @pytest.mark.asyncio
    async def test_list_lxc_returns_error(self) -> None:
        result = await list_lxc()
        assert len(result) == 1
        assert "error" in result[0]

    @pytest.mark.asyncio
    async def test_get_lxc_status_returns_error(self) -> None:
        result = await get_lxc_status(300)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_start_lxc_returns_error_string(self) -> None:
        result = await start_lxc(300)
        assert "not configured" in result.lower()

    @pytest.mark.asyncio
    async def test_stop_lxc_returns_error_string(self) -> None:
        result = await stop_lxc(300)
        assert "not configured" in result.lower()

    @pytest.mark.asyncio
    async def test_create_lxc_returns_error(self) -> None:
        result = await create_lxc(node="test-node-2", ostemplate="local:vztmpl/debian.tar.zst")
        assert isinstance(result, dict)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_next_vmid_returns_error(self) -> None:
        result = await get_next_vmid()
        assert isinstance(result, dict)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_list_storage_returns_error(self) -> None:
        result = await list_storage()
        assert len(result) == 1
        assert "error" in result[0]

    @pytest.mark.asyncio
    async def test_list_templates_returns_error(self) -> None:
        result = await list_templates()
        assert len(result) == 1
        assert "error" in result[0]


# ===========================================================================
# Input validation helpers (unit tests)
# ===========================================================================


class TestValidateSafeField:
    """Comma injection prevention — _validate_safe_field rejects dangerous chars."""

    def test_accepts_simple_name(self) -> None:
        _validate_safe_field("local-lvm", "storage")

    def test_accepts_iso_path(self) -> None:
        _validate_safe_field("local:iso/debian-13.3.0-amd64-netinst.iso", "iso")

    def test_accepts_template_volid(self) -> None:
        _validate_safe_field("local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst", "ostemplate")

    def test_rejects_comma(self) -> None:
        with pytest.raises(ValueError, match="contains invalid characters"):
            _validate_safe_field("local:iso/debian.iso,cache=none", "iso")

    def test_rejects_space(self) -> None:
        with pytest.raises(ValueError, match="contains invalid characters"):
            _validate_safe_field("vmbr0 ,model=e1000", "bridge")

    def test_rejects_semicolon(self) -> None:
        with pytest.raises(ValueError, match="contains invalid characters"):
            _validate_safe_field("local;rm -rf /", "storage")

    def test_rejects_equals(self) -> None:
        with pytest.raises(ValueError, match="contains invalid characters"):
            _validate_safe_field("vmbr0=evil", "bridge")

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError, match="contains invalid characters"):
            _validate_safe_field("", "storage")


class TestValidateNode:
    """Node parameter validation — prevents path traversal."""

    def test_accepts_simple_name(self) -> None:
        _validate_node("pve")

    def test_accepts_hyphenated_name(self) -> None:
        _validate_node("pve-node-1")

    def test_accepts_numeric_name(self) -> None:
        _validate_node("node01")

    def test_rejects_path_traversal(self) -> None:
        with pytest.raises(ValueError, match="must be a valid hostname"):
            _validate_node("pve/../../../cluster/config")

    def test_rejects_slash(self) -> None:
        with pytest.raises(ValueError, match="must be a valid hostname"):
            _validate_node("pve/evil")

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="must be a valid hostname"):
            _validate_node("")

    def test_rejects_starts_with_hyphen(self) -> None:
        with pytest.raises(ValueError, match="must be a valid hostname"):
            _validate_node("-pve")


class TestValidateVmid:
    """VMID validation — Proxmox reserves IDs below 100."""

    def test_accepts_100(self) -> None:
        _validate_vmid(100)  # should not raise

    def test_accepts_999(self) -> None:
        _validate_vmid(999)  # should not raise

    def test_rejects_99(self) -> None:
        with pytest.raises(ValueError, match="vmid must be >= 100"):
            _validate_vmid(99)

    def test_rejects_1(self) -> None:
        with pytest.raises(ValueError, match="vmid must be >= 100"):
            _validate_vmid(1)

    def test_rejects_zero(self) -> None:
        with pytest.raises(ValueError, match="vmid must be >= 100"):
            _validate_vmid(0)


# ===========================================================================
# Security validation — create_vm injection/boundary tests
# ===========================================================================


class TestCreateVmSecurityValidation:
    """Tests for comma injection, path traversal, enum allowlisting, and bounds."""

    @pytest.mark.asyncio
    async def test_rejects_iso_with_comma(self) -> None:
        with pytest.raises(ValueError, match="iso contains invalid characters"):
            await create_vm(
                node="pve",
                iso="local:iso/debian.iso,cache=none",
                vmid=200,
            )

    @pytest.mark.asyncio
    async def test_rejects_bridge_with_comma(self) -> None:
        with pytest.raises(ValueError, match="bridge contains invalid characters"):
            await create_vm(
                node="pve",
                iso="local:iso/debian.iso",
                vmid=200,
                bridge="vmbr0,model=e1000",
            )

    @pytest.mark.asyncio
    async def test_rejects_storage_with_comma(self) -> None:
        with pytest.raises(ValueError, match="storage contains invalid characters"):
            await create_vm(
                node="pve",
                iso="local:iso/debian.iso",
                vmid=200,
                storage="local,discard=on",
            )

    @pytest.mark.asyncio
    async def test_rejects_node_path_traversal(self) -> None:
        with pytest.raises(ValueError, match="must be a valid hostname"):
            await create_vm(
                node="pve/../../../cluster/config",
                iso="local:iso/debian.iso",
                vmid=200,
            )

    @pytest.mark.asyncio
    async def test_rejects_vmid_below_100(self) -> None:
        with pytest.raises(ValueError, match="vmid must be >= 100"):
            await create_vm(
                node="pve",
                iso="local:iso/debian.iso",
                vmid=99,
            )

    @pytest.mark.asyncio
    async def test_rejects_negative_balloon(self) -> None:
        with pytest.raises(ValueError, match="balloon must be >= 0"):
            await create_vm(
                node="pve",
                iso="local:iso/debian.iso",
                vmid=200,
                balloon=-1,
            )

    @pytest.mark.asyncio
    async def test_rejects_invalid_ostype(self) -> None:
        with pytest.raises(ValueError, match="ostype must be one of"):
            await create_vm(
                node="pve",
                iso="local:iso/debian.iso",
                vmid=200,
                ostype="evil",
            )

    @pytest.mark.asyncio
    async def test_rejects_invalid_scsihw(self) -> None:
        with pytest.raises(ValueError, match="scsihw must be one of"):
            await create_vm(
                node="pve",
                iso="local:iso/debian.iso",
                vmid=200,
                scsihw="badcontroller",
            )

    @pytest.mark.asyncio
    async def test_rejects_invalid_cpu_type(self) -> None:
        with pytest.raises(ValueError, match="cpu_type must be one of"):
            await create_vm(
                node="pve",
                iso="local:iso/debian.iso",
                vmid=200,
                cpu_type="exploit-cpu",
            )


# ===========================================================================
# Security validation — create_lxc injection/boundary tests
# ===========================================================================


class TestCreateLxcSecurityValidation:
    """Tests for comma injection, path traversal, and vmid bounds on create_lxc."""

    @pytest.mark.asyncio
    async def test_rejects_ostemplate_with_comma(self) -> None:
        with pytest.raises(ValueError, match="ostemplate contains invalid characters"):
            await create_lxc(
                node="pve",
                ostemplate="local:vztmpl/debian.tar.zst,evil=1",
                vmid=200,
            )

    @pytest.mark.asyncio
    async def test_rejects_node_path_traversal(self) -> None:
        with pytest.raises(ValueError, match="must be a valid hostname"):
            await create_lxc(
                node="pve/../etc",
                ostemplate="local:vztmpl/debian-12.tar.zst",
                vmid=200,
            )

    @pytest.mark.asyncio
    async def test_rejects_vmid_below_100(self) -> None:
        with pytest.raises(ValueError, match="vmid must be >= 100"):
            await create_lxc(
                node="pve",
                ostemplate="local:vztmpl/debian-12.tar.zst",
                vmid=50,
            )
