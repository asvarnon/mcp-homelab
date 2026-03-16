"""Unit tests for core/ssh.py — SSHManager.

Tests connection caching, stale connection eviction, credential resolution,
and error handling. All paramiko interactions are mocked.

Java comparison: Testing a connection pool manager with mocked JDBC connections.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.config import AppConfig, HostConfig, OPNsenseConfig, ProxmoxConfig
from core.ssh import SSHError, SSHManager


@pytest.fixture()
def ssh_manager() -> SSHManager:
    """Return a fresh SSHManager with mocked config."""
    mgr = SSHManager()
    mgr._config = AppConfig(
        hosts={
            "gamehost": HostConfig(
                hostname="gamehost",
                ip="10.0.50.10",
                vlan=50,
                ssh=True,
                ssh_user="admin",
                ssh_key_path="~/.ssh/id_ed25519",
                sudo_docker=True,
                description="test node",
            ),
            "no-ssh": HostConfig(
                hostname="no-ssh",
                ip="10.0.50.99",
                ssh=False,
                description="SSH disabled",
            ),
        },
        proxmox=ProxmoxConfig(host="10.0.50.20"),
        opnsense=OPNsenseConfig(host="10.0.50.1"),
    )
    return mgr


class TestGetConfig:
    def test_lazy_loads_on_first_call(self) -> None:
        mgr = SSHManager()
        assert mgr._config is None

        with patch("core.ssh.load_config") as mock_load:
            mock_load.return_value = MagicMock()
            result = mgr._get_config()
            mock_load.assert_called_once()
            assert result is not None

    def test_caches_after_first_call(self) -> None:
        mgr = SSHManager()
        fake_config = MagicMock()

        with patch("core.ssh.load_config") as mock_load:
            mock_load.return_value = fake_config
            mgr._get_config()
            mgr._get_config()
            # Should only load once
            mock_load.assert_called_once()


class TestConnect:
    @patch("core.ssh.paramiko.SSHClient")
    def test_creates_connection(self, mock_ssh_cls: MagicMock, ssh_manager: SSHManager) -> None:
        mock_client = MagicMock()
        mock_ssh_cls.return_value = mock_client

        result = ssh_manager._connect("gamehost")
        mock_client.connect.assert_called_once_with(
            hostname="10.0.50.10",
            username="admin",
            key_filename=str(Path("~/.ssh/id_ed25519").expanduser()),
            timeout=10,
        )
        assert result is mock_client

    @patch("core.ssh.paramiko.SSHClient")
    def test_caches_connection(self, mock_ssh_cls: MagicMock, ssh_manager: SSHManager) -> None:
        mock_client = MagicMock()
        mock_transport = MagicMock()
        mock_transport.is_active.return_value = True
        mock_client.get_transport.return_value = mock_transport
        mock_ssh_cls.return_value = mock_client

        first = ssh_manager._connect("gamehost")
        second = ssh_manager._connect("gamehost")

        # Should reuse the same connection
        assert first is second
        assert mock_ssh_cls.call_count == 1

    @patch("core.ssh.paramiko.SSHClient")
    def test_evicts_stale_connection(self, mock_ssh_cls: MagicMock, ssh_manager: SSHManager) -> None:
        stale_client = MagicMock()
        stale_transport = MagicMock()
        stale_transport.is_active.return_value = False
        stale_client.get_transport.return_value = stale_transport

        fresh_client = MagicMock()
        # SSHClient() is called once inside _connect after eviction
        mock_ssh_cls.return_value = fresh_client

        # Inject stale connection into cache
        ssh_manager._connections["gamehost"] = stale_client

        # Should evict stale and create fresh
        result = ssh_manager._connect("gamehost")
        stale_client.close.assert_called_once()
        assert result is fresh_client

    def test_unknown_host_raises(self, ssh_manager: SSHManager) -> None:
        with pytest.raises(KeyError, match="Unknown host"):
            ssh_manager._connect("nonexistent")

    def test_ssh_disabled_raises(self, ssh_manager: SSHManager) -> None:
        with pytest.raises(SSHError, match="does not have SSH enabled"):
            ssh_manager._connect("no-ssh")


class TestExecute:
    def test_returns_stdout(self, ssh_manager: SSHManager) -> None:
        mock_client = MagicMock()
        stdout = MagicMock()
        stdout.read.return_value = b"output text"
        stdout.channel.recv_exit_status.return_value = 0
        stderr = MagicMock()
        stderr.read.return_value = b""
        mock_client.exec_command.return_value = (MagicMock(), stdout, stderr)

        # Inject the mock connection
        transport = MagicMock()
        transport.is_active.return_value = True
        mock_client.get_transport.return_value = transport
        ssh_manager._connections["gamehost"] = mock_client

        result = ssh_manager.execute("gamehost", "whoami")
        assert result == "output text"

    def test_nonzero_exit_raises(self, ssh_manager: SSHManager) -> None:
        mock_client = MagicMock()
        stdout = MagicMock()
        stdout.read.return_value = b""
        stdout.channel.recv_exit_status.return_value = 1
        stderr = MagicMock()
        stderr.read.return_value = b"command not found"
        mock_client.exec_command.return_value = (MagicMock(), stdout, stderr)

        transport = MagicMock()
        transport.is_active.return_value = True
        mock_client.get_transport.return_value = transport
        ssh_manager._connections["gamehost"] = mock_client

        with pytest.raises(SSHError, match="exited 1"):
            ssh_manager.execute("gamehost", "bad-command")


class TestExecuteDocker:
    def test_with_sudo(self, ssh_manager: SSHManager) -> None:
        """gamehost has sudo_docker=True, so command should be prefixed."""
        mock_client = MagicMock()
        stdout = MagicMock()
        stdout.read.return_value = b"container output"
        stdout.channel.recv_exit_status.return_value = 0
        stderr = MagicMock()
        stderr.read.return_value = b""
        mock_client.exec_command.return_value = (MagicMock(), stdout, stderr)

        transport = MagicMock()
        transport.is_active.return_value = True
        mock_client.get_transport.return_value = transport
        ssh_manager._connections["gamehost"] = mock_client

        result = ssh_manager.execute_docker("gamehost", "ps")
        mock_client.exec_command.assert_called_once()
        cmd = mock_client.exec_command.call_args[0][0]
        assert cmd.startswith("sudo docker")
        assert result == "container output"


class TestClose:
    def test_closes_all_connections(self, ssh_manager: SSHManager) -> None:
        mock1 = MagicMock()
        mock2 = MagicMock()
        ssh_manager._connections = {"host1": mock1, "host2": mock2}

        ssh_manager.close()
        mock1.close.assert_called_once()
        mock2.close.assert_called_once()
        assert len(ssh_manager._connections) == 0
