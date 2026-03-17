"""Unit tests for tools/proxmox.py.

Tests VM listing, status conversion, and node discovery with mocked
ProxmoxClient responses. No real HTTP calls.

Java comparison: Testing a Spring Service layer with a mocked repository.
"""

from __future__ import annotations

import pytest
from core.config import ProxmoxConfig

from tools.proxmox import (
    _find_ct_node,
    _resolve_default_node,
    _find_resource_node,
    _find_vm_node,
    create_lxc,
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
            [{"vmid": 100, "node": "pve", "type": "qemu"}],
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
            [{"vmid": 100, "node": "pve", "type": "qemu"}],
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
            {"vmid": 100, "node": "pve", "type": "qemu"},
            {"vmid": 200, "node": "pve2", "type": "qemu"},
        ]
        assert await _find_vm_node(200) == "pve2"

    @pytest.mark.asyncio
    async def test_raises_for_missing_vm(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [{"vmid": 100, "node": "pve", "type": "qemu"}]
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


# ===========================================================================
# _find_ct_node
# ===========================================================================


class TestFindCtNode:
    @pytest.mark.asyncio
    async def test_finds_node(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [
            {"vmid": 100, "node": "pve", "type": "lxc"},
            {"vmid": 200, "node": "pve2", "type": "lxc"},
        ]
        assert await _find_ct_node(200) == "pve2"

    @pytest.mark.asyncio
    async def test_raises_for_missing_ct(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [{"vmid": 100, "node": "pve", "type": "lxc"}]
        with pytest.raises(ValueError, match="LXC container 999 not found"):
            await _find_ct_node(999)

    @pytest.mark.asyncio
    async def test_ignores_qemu_with_same_vmid(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [
            {"vmid": 100, "node": "pve", "type": "qemu"},
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
            {"vmid": 100, "node": "pve", "type": "qemu"},
        ]
        assert await _find_resource_node(100, "qemu") == "pve"

    @pytest.mark.asyncio
    async def test_finds_lxc_node(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [
            {"vmid": 300, "node": "pve2", "type": "lxc"},
        ]
        assert await _find_resource_node(300, "lxc") == "pve2"

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
            {"vmid": 100, "node": "pve", "type": "qemu"},
        ]
        with pytest.raises(ValueError, match="LXC container 100 not found"):
            await _find_resource_node(100, "lxc")


# ===========================================================================
# _resolve_default_node
# ===========================================================================


class TestResolveDefaultNode:
    @pytest.mark.asyncio
    async def test_uses_configured_default_when_present(self, monkeypatch: pytest.MonkeyPatch, mock_proxmox_client) -> None:
        mock_proxmox_client.get_nodes.return_value = ["pve", "pve2"]
        monkeypatch.setattr(
            "tools.proxmox.get_proxmox_config",
            lambda: ProxmoxConfig(host="10.10.10.50", default_node="pve2"),
        )
        result = await _resolve_default_node()
        assert result == "pve2"

    @pytest.mark.asyncio
    async def test_falls_back_when_configured_default_missing(self, monkeypatch: pytest.MonkeyPatch, mock_proxmox_client) -> None:
        mock_proxmox_client.get_nodes.return_value = ["pve", "pve2"]
        monkeypatch.setattr(
            "tools.proxmox.get_proxmox_config",
            lambda: ProxmoxConfig(host="10.10.10.50", default_node="pve3"),
        )
        result = await _resolve_default_node()
        assert result == "pve"

    @pytest.mark.asyncio
    async def test_falls_back_when_default_node_not_set(self, monkeypatch: pytest.MonkeyPatch, mock_proxmox_client) -> None:
        mock_proxmox_client.get_nodes.return_value = ["pve", "pve2"]
        monkeypatch.setattr(
            "tools.proxmox.get_proxmox_config",
            lambda: ProxmoxConfig(host="10.10.10.50", default_node=None),
        )
        result = await _resolve_default_node()
        assert result == "pve"

    @pytest.mark.asyncio
    async def test_raises_when_cluster_has_no_nodes(self, monkeypatch: pytest.MonkeyPatch, mock_proxmox_client) -> None:
        mock_proxmox_client.get_nodes.return_value = []
        monkeypatch.setattr(
            "tools.proxmox.get_proxmox_config",
            lambda: ProxmoxConfig(host="10.10.10.50", default_node="pve"),
        )
        with pytest.raises(ValueError, match="No Proxmox nodes found in cluster"):
            await _resolve_default_node()


# ===========================================================================
# list_lxc
# ===========================================================================


class TestListLxc:
    @pytest.mark.asyncio
    async def test_aggregates_across_nodes(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get_nodes.return_value = ["pve", "pve2"]
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
        mock_proxmox_client.get_nodes.return_value = ["pve"]
        mock_proxmox_client.get.return_value = []
        result = await list_lxc()
        assert result == []

    @pytest.mark.asyncio
    async def test_memory_conversion(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get_nodes.return_value = ["pve"]
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
            [{"vmid": 300, "node": "pve", "type": "lxc"}],
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
            [{"vmid": 300, "node": "pve", "type": "lxc"}],
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
        mock_proxmox_client.get.return_value = [{"vmid": 300, "node": "pve", "type": "lxc"}]
        mock_proxmox_client.post.return_value = "UPID:pve:00001234:00000000:65F00000:vzstart:300:root@pam:"
        result = await start_lxc(300)
        assert "LXC 300 start initiated" in result
        assert "UPID" in result


# ===========================================================================
# stop_lxc
# ===========================================================================


class TestStopLxc:
    @pytest.mark.asyncio
    async def test_returns_confirmation(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [{"vmid": 300, "node": "pve", "type": "lxc"}]
        mock_proxmox_client.post.return_value = "UPID:pve:00001234:00000000:65F00000:vzstop:300:root@pam:"
        result = await stop_lxc(300)
        assert "LXC 300 stop initiated" in result
        assert "UPID" in result


# ===========================================================================
# create_lxc
# ===========================================================================


class TestCreateLxc:
    @pytest.mark.asyncio
    async def test_builds_correct_payload(self, mock_proxmox_client) -> None:
        mock_proxmox_client.post.return_value = "UPID:pve:create:300"
        result = await create_lxc(
            node="pve",
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
        assert result["node"] == "pve"
        assert result["task_id"] == "UPID:pve:create:300"

        # Verify the POST was called with the right data
        call_args = mock_proxmox_client.post.call_args
        assert call_args[0][0] == "/nodes/pve/lxc"
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
        mock_proxmox_client.post.return_value = "UPID:pve:create:400"
        result = await create_lxc(
            node="pve",
            ostemplate="local:vztmpl/debian-12.tar.zst",
        )
        assert result["vmid"] == 400
        mock_proxmox_client.get.assert_called_with("/cluster/nextid")

    @pytest.mark.asyncio
    async def test_net0_with_vlan(self, mock_proxmox_client) -> None:
        mock_proxmox_client.post.return_value = "UPID:pve:create:300"
        await create_lxc(
            node="pve",
            ostemplate="local:vztmpl/debian-12.tar.zst",
            vmid=300,
            vlan_tag=50,
        )
        posted_data = mock_proxmox_client.post.call_args[1].get("data") or mock_proxmox_client.post.call_args[0][1]
        assert "tag=50" in posted_data["net0"]

    @pytest.mark.asyncio
    async def test_net0_without_vlan(self, mock_proxmox_client) -> None:
        mock_proxmox_client.post.return_value = "UPID:pve:create:300"
        await create_lxc(
            node="pve",
            ostemplate="local:vztmpl/debian-12.tar.zst",
            vmid=300,
        )
        posted_data = mock_proxmox_client.post.call_args[1].get("data") or mock_proxmox_client.post.call_args[0][1]
        assert "tag=" not in posted_data["net0"]
        assert posted_data["net0"] == "name=eth0,bridge=vmbr0,ip=dhcp"

    @pytest.mark.asyncio
    async def test_optional_ssh_key(self, mock_proxmox_client) -> None:
        mock_proxmox_client.post.return_value = "UPID:pve:create:300"
        await create_lxc(
            node="pve",
            ostemplate="local:vztmpl/debian-12.tar.zst",
            vmid=300,
            ssh_public_key="ssh-ed25519 AAAA... user@host",
        )
        posted_data = mock_proxmox_client.post.call_args[1].get("data") or mock_proxmox_client.post.call_args[0][1]
        assert posted_data["ssh-public-keys"] == "ssh-ed25519 AAAA... user@host"

    @pytest.mark.asyncio
    async def test_optional_password(self, mock_proxmox_client) -> None:
        mock_proxmox_client.post.return_value = "UPID:pve:create:300"
        await create_lxc(
            node="pve",
            ostemplate="local:vztmpl/debian-12.tar.zst",
            vmid=300,
            password="s3cret",
        )
        posted_data = mock_proxmox_client.post.call_args[1].get("data") or mock_proxmox_client.post.call_args[0][1]
        assert posted_data["password"] == "s3cret"

    @pytest.mark.asyncio
    async def test_uses_config_defaults_when_storage_and_bridge_are_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_proxmox_client,
    ) -> None:
        mock_proxmox_client.post.return_value = "UPID:pve:create:300"
        monkeypatch.setattr(
            "tools.proxmox.get_proxmox_config",
            lambda: ProxmoxConfig(
                host="10.10.10.50",
                default_storage="local",
                default_bridge="vmbr1",
            ),
        )

        await create_lxc(
            node="pve",
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
                node="pve",
                ostemplate="local:vztmpl/debian-12.tar.zst",
                vmid=300,
                cores=0,
            )

    @pytest.mark.asyncio
    async def test_rejects_negative_memory(self) -> None:
        with pytest.raises(ValueError, match=r"memory_mb must be >= 16, got -1"):
            await create_lxc(
                node="pve",
                ostemplate="local:vztmpl/debian-12.tar.zst",
                vmid=300,
                memory_mb=-1,
            )

    @pytest.mark.asyncio
    async def test_rejects_negative_swap(self) -> None:
        with pytest.raises(ValueError, match=r"swap_mb must be >= 0, got -1"):
            await create_lxc(
                node="pve",
                ostemplate="local:vztmpl/debian-12.tar.zst",
                vmid=300,
                swap_mb=-1,
            )

    @pytest.mark.asyncio
    async def test_rejects_zero_disk(self) -> None:
        with pytest.raises(ValueError, match=r"disk_gb must be >= 1, got 0"):
            await create_lxc(
                node="pve",
                ostemplate="local:vztmpl/debian-12.tar.zst",
                vmid=300,
                disk_gb=0,
            )

    @pytest.mark.asyncio
    async def test_rejects_vlan_below_range(self) -> None:
        with pytest.raises(ValueError, match=r"vlan_tag must be in range 1-4094 when set, got 0"):
            await create_lxc(
                node="pve",
                ostemplate="local:vztmpl/debian-12.tar.zst",
                vmid=300,
                vlan_tag=0,
            )

    @pytest.mark.asyncio
    async def test_rejects_vlan_above_range(self) -> None:
        with pytest.raises(ValueError, match=r"vlan_tag must be in range 1-4094 when set, got 4095"):
            await create_lxc(
                node="pve",
                ostemplate="local:vztmpl/debian-12.tar.zst",
                vmid=300,
                vlan_tag=4095,
            )

    @pytest.mark.asyncio
    async def test_accepts_valid_vlan(self, mock_proxmox_client) -> None:
        mock_proxmox_client.post.return_value = "UPID:pve:create:300"

        result = await create_lxc(
            node="pve",
            ostemplate="local:vztmpl/debian-12.tar.zst",
            vmid=300,
            vlan_tag=50,
        )

        assert result["vmid"] == 300
        posted_data = mock_proxmox_client.post.call_args[1].get("data") or mock_proxmox_client.post.call_args[0][1]
        assert "tag=50" in posted_data["net0"]


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
        result = await list_storage(node="pve")
        assert len(result) == 1
        assert result[0]["storage"] == "local-lvm"
        assert result[0]["total_gb"] == 100.0
        assert result[0]["used_gb"] == 50.0
        assert result[0]["avail_gb"] == 50.0
        assert result[0]["active"] is True

    @pytest.mark.asyncio
    async def test_defaults_to_first_node(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get_nodes.return_value = ["pve"]
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
        result = await list_templates(node="pve", storage="local")
        assert len(result) == 1
        assert result[0]["volid"] == "local:vztmpl/debian-12.tar.zst"
        assert result[0]["format"] == "tar.zst"
        assert result[0]["size_mb"] == 125.0

    @pytest.mark.asyncio
    async def test_defaults_to_first_storage_with_vztmpl(self, mock_proxmox_client) -> None:
        # First call: get_nodes, second call: list_storage (node/storage), third call: content
        mock_proxmox_client.get_nodes.return_value = ["pve"]
        mock_proxmox_client.get.side_effect = [
            # get_nodes already mocked above, first get = list_storage
            [
                {"storage": "local-lvm", "type": "lvmthin", "content": "images,rootdir", "total": 0, "used": 0, "avail": 0, "active": 1},
                {"storage": "local", "type": "dir", "content": "vztmpl,iso,backup", "total": 0, "used": 0, "avail": 0, "active": 1},
            ],
            # content query
            [{"volid": "local:vztmpl/ubuntu-22.tar.zst", "format": "tar.zst", "size": 262144000}],
        ]
        result = await list_templates(node="pve")
        assert len(result) == 1
        assert result[0]["volid"] == "local:vztmpl/ubuntu-22.tar.zst"

    @pytest.mark.asyncio
    async def test_explicit_storage(self, mock_proxmox_client) -> None:
        mock_proxmox_client.get.return_value = [
            {"volid": "nfs:vztmpl/alpine.tar.gz", "format": "tar.gz", "size": 5242880},
        ]
        result = await list_templates(node="pve", storage="nfs")
        assert len(result) == 1
        assert result[0]["volid"] == "nfs:vztmpl/alpine.tar.gz"


# ===========================================================================
# LXC not-configured guards
# ===========================================================================


class TestLxcNotConfigured:
    """When Proxmox section is absent, LXC tools return error dicts/strings."""

    @pytest.fixture(autouse=True)
    def disable_proxmox(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("tools.proxmox.proxmox_configured", lambda: False)

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
        result = await create_lxc(node="pve", ostemplate="local:vztmpl/debian.tar.zst")
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
