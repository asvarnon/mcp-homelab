"""Unit tests for mcp_homelab/setup/ssh_provisioning.py.

All SSH operations are mocked — no real connections are made.
Tests cover key generation, remote deployment, role application,
connection verification, manual output, and the main entry point.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mcp_homelab.setup.roles import RoleTemplate
from mcp_homelab.setup.ssh_helpers import CommandResult
from mcp_homelab.setup.ssh_provisioning import (
    apply_role,
    deploy_public_key,
    generate_keypair,
    print_manual_instructions,
    run_ssh_provisioning,
    verify_connection,
)


# ---------------------------------------------------------------------------
# Helpers for mocking key generation
# ---------------------------------------------------------------------------

def _mock_create_key() -> tuple:
    """Return (patcher, mock_key) for stubbing _create_ed25519_key."""
    mock_key = MagicMock()
    mock_key.get_name.return_value = "ssh-ed25519"
    mock_key.get_base64.return_value = "AAAA1234"
    mock_pem = b"-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----\n"
    patcher = patch(
        "mcp_homelab.setup.ssh_provisioning._create_ed25519_key",
        return_value=(mock_pem, mock_key),
    )
    return patcher, mock_key


# ===========================================================================
# TestGenerateKeypair
# ===========================================================================


class TestGenerateKeypair:
    """Tests for generate_keypair()."""

    def test_generates_key_at_path(self, tmp_path: Path) -> None:
        key_path = tmp_path / "testhost"
        patcher, mock_key = _mock_create_key()
        with patcher:
            result = generate_keypair(key_path)

        assert result == key_path
        assert key_path.exists()
        assert b"OPENSSH PRIVATE KEY" in key_path.read_bytes()
        pub_path = key_path.with_suffix(".pub")
        assert pub_path.exists()
        assert "ssh-ed25519 AAAA1234" in pub_path.read_text(encoding="utf-8")

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        key_path = tmp_path / "subdir" / "deep" / "testhost"
        patcher, mock_key = _mock_create_key()
        with patcher:
            generate_keypair(key_path)

        assert key_path.parent.exists()

    def test_raises_file_exists_without_force(self, tmp_path: Path) -> None:
        key_path = tmp_path / "existing"
        key_path.write_text("dummy", encoding="utf-8")

        with pytest.raises(FileExistsError, match="already exists"):
            generate_keypair(key_path, force=False)

    def test_overwrites_with_force(self, tmp_path: Path) -> None:
        key_path = tmp_path / "existing"
        key_path.write_text("old-key", encoding="utf-8")

        patcher, mock_key = _mock_create_key()
        with patcher:
            result = generate_keypair(key_path, force=True)

        assert result == key_path
        assert b"OPENSSH PRIVATE KEY" in key_path.read_bytes()


# ===========================================================================
# TestDeployPublicKey
# ===========================================================================


def _mock_run_command_success(client: MagicMock, cmd: str) -> CommandResult:
    """Stub run_command that always succeeds."""
    return CommandResult(exit_code=0, stdout="", stderr="")


class TestDeployPublicKey:
    """Tests for deploy_public_key() — mock SSH client."""

    @patch("mcp_homelab.setup.ssh_provisioning.run_command")
    def test_linux_user_creation(self, mock_run: MagicMock) -> None:
        mock_run.return_value = CommandResult(0, "", "")
        client = MagicMock()

        deploy_public_key(client, "ssh-ed25519 KEY", "mcp-homelab", os_type="linux")

        # First call should be useradd
        first_cmd = mock_run.call_args_list[0][0][1]
        assert "useradd" in first_cmd
        assert "mcp-homelab" in first_cmd

    @patch("mcp_homelab.setup.ssh_provisioning.run_command")
    def test_freebsd_user_creation(self, mock_run: MagicMock) -> None:
        mock_run.return_value = CommandResult(0, "", "")
        client = MagicMock()

        deploy_public_key(client, "ssh-ed25519 KEY", "mcp-homelab", os_type="freebsd")

        first_cmd = mock_run.call_args_list[0][0][1]
        assert "pw useradd" in first_cmd

    @patch("mcp_homelab.setup.ssh_provisioning.run_command")
    def test_deploys_authorized_keys(self, mock_run: MagicMock) -> None:
        mock_run.return_value = CommandResult(0, "", "")
        client = MagicMock()
        pub_key = "ssh-ed25519 TESTKEY123"

        deploy_public_key(client, pub_key, "svc")

        all_cmds = [c[0][1] for c in mock_run.call_args_list]
        # Should have mkdir, tee, chmod x2, chown
        assert any("mkdir" in cmd for cmd in all_cmds)
        assert any("authorized_keys" in cmd for cmd in all_cmds)
        assert any("chmod 700" in cmd for cmd in all_cmds)
        assert any("chmod 600" in cmd for cmd in all_cmds)
        assert any("chown" in cmd for cmd in all_cmds)

    @patch("mcp_homelab.setup.ssh_provisioning.run_command")
    def test_raises_on_command_failure(self, mock_run: MagicMock) -> None:
        # First call (useradd) succeeds, second (mkdir) fails
        mock_run.side_effect = [
            CommandResult(0, "", ""),
            CommandResult(0, "", ""),  # mkdir
            CommandResult(1, "", "permission denied"),
        ]
        client = MagicMock()

        with pytest.raises(RuntimeError, match="Failed deploying public key"):
            deploy_public_key(client, "ssh-ed25519 KEY", "svc")

    def test_raises_on_invalid_os_type(self) -> None:
        client = MagicMock()
        with pytest.raises(ValueError, match="Unsupported os_type"):
            deploy_public_key(client, "ssh-ed25519 KEY", "svc", os_type="windows")  # type: ignore[arg-type]


# ===========================================================================
# TestApplyRole
# ===========================================================================


class TestApplyRole:
    """Tests for apply_role() — mock SSH client."""

    @patch("mcp_homelab.setup.ssh_provisioning.run_command")
    def test_adds_to_groups(self, mock_run: MagicMock) -> None:
        mock_run.return_value = CommandResult(0, "", "")
        client = MagicMock()
        role = RoleTemplate(
            name="test",
            description="test",
            groups=["docker", "adm"],
            sudoers=[],
        )

        apply_role(client, role, "svc")

        cmds = [c[0][1] for c in mock_run.call_args_list]
        assert any("usermod -aG docker svc" in cmd for cmd in cmds)
        assert any("usermod -aG adm svc" in cmd for cmd in cmds)

    @patch("mcp_homelab.setup.ssh_provisioning.run_command")
    def test_writes_sudoers_and_validates(self, mock_run: MagicMock) -> None:
        mock_run.return_value = CommandResult(0, "", "")
        client = MagicMock()
        role = RoleTemplate(
            name="test",
            description="test",
            sudoers=["/usr/bin/docker ps"],
        )

        apply_role(client, role, "svc")

        cmds = [c[0][1] for c in mock_run.call_args_list]
        # Should write sudoers content via tee
        assert any("tee" in cmd and "mcp-homelab-sudoers" in cmd for cmd in cmds)
        # Should validate with visudo
        assert any("visudo -cf" in cmd for cmd in cmds)
        # Should move into place
        assert any("mv" in cmd and "/etc/sudoers.d/mcp-homelab" in cmd for cmd in cmds)

    @patch("mcp_homelab.setup.ssh_provisioning.run_command")
    def test_group_add_failure_raises(self, mock_run: MagicMock) -> None:
        mock_run.return_value = CommandResult(1, "", "group not found")
        client = MagicMock()
        role = RoleTemplate(name="test", description="test", groups=["badgroup"])

        with pytest.raises(RuntimeError, match="Failed to add"):
            apply_role(client, role, "svc")

    @patch("mcp_homelab.setup.ssh_provisioning.run_command")
    def test_visudo_failure_cleans_up(self, mock_run: MagicMock) -> None:
        # tee succeeds, visudo fails, rm cleanup
        mock_run.side_effect = [
            CommandResult(0, "", ""),  # tee
            CommandResult(1, "", "syntax error"),  # visudo
            CommandResult(0, "", ""),  # rm cleanup
        ]
        client = MagicMock()
        role = RoleTemplate(
            name="test",
            description="test",
            sudoers=["/usr/bin/bad"],
        )

        with pytest.raises(RuntimeError, match="Sudoers validation failed"):
            apply_role(client, role, "svc")

        # Verify cleanup rm was called
        last_cmd = mock_run.call_args_list[-1][0][1]
        assert "rm -f" in last_cmd


# ===========================================================================
# TestVerifyConnection
# ===========================================================================


class TestVerifyConnection:
    """Tests for verify_connection()."""

    @patch("mcp_homelab.setup.ssh_provisioning.run_command")
    @patch("mcp_homelab.setup.ssh_provisioning.connect")
    def test_success(self, mock_connect: MagicMock, mock_run: MagicMock) -> None:
        mock_client = MagicMock()
        mock_connect.return_value = mock_client
        mock_run.return_value = CommandResult(0, "ok", "")

        result = verify_connection("10.0.0.1", "svc", Path("/tmp/key"))

        assert result is True
        mock_client.close.assert_called_once()

    @patch("mcp_homelab.setup.ssh_provisioning.connect")
    def test_connection_failure(self, mock_connect: MagicMock) -> None:
        from mcp_homelab.setup.ssh_helpers import SSHConnectError

        mock_connect.side_effect = SSHConnectError("timeout")

        result = verify_connection("10.0.0.1", "svc", Path("/tmp/key"))

        assert result is False

    @patch("mcp_homelab.setup.ssh_provisioning.run_command")
    @patch("mcp_homelab.setup.ssh_provisioning.connect")
    def test_command_failure(self, mock_connect: MagicMock, mock_run: MagicMock) -> None:
        mock_client = MagicMock()
        mock_connect.return_value = mock_client
        mock_run.return_value = CommandResult(1, "", "error")

        result = verify_connection("10.0.0.1", "svc", Path("/tmp/key"))

        assert result is False


# ===========================================================================
# TestPrintManualInstructions
# ===========================================================================


class TestPrintManualInstructions:
    """Tests for print_manual_instructions()."""

    def test_outputs_hostname(self, capsys: pytest.CaptureFixture[str]) -> None:
        role = RoleTemplate(name="test", description="test")
        print_manual_instructions("myhost", "ssh-ed25519 KEY", role, "svc")
        output = capsys.readouterr().out
        assert "myhost" in output

    def test_outputs_useradd_command(self, capsys: pytest.CaptureFixture[str]) -> None:
        role = RoleTemplate(name="test", description="test")
        print_manual_instructions("myhost", "ssh-ed25519 KEY", role, "mcp-homelab")
        output = capsys.readouterr().out
        assert "useradd" in output
        assert "mcp-homelab" in output

    def test_outputs_public_key(self, capsys: pytest.CaptureFixture[str]) -> None:
        role = RoleTemplate(name="test", description="test")
        print_manual_instructions("myhost", "ssh-ed25519 TESTKEY", role, "svc")
        output = capsys.readouterr().out
        assert "ssh-ed25519 TESTKEY" in output

    def test_outputs_group_commands(self, capsys: pytest.CaptureFixture[str]) -> None:
        role = RoleTemplate(name="test", description="test", groups=["docker", "adm"])
        print_manual_instructions("myhost", "ssh-ed25519 KEY", role, "svc")
        output = capsys.readouterr().out
        assert "usermod -aG docker" in output
        assert "usermod -aG adm" in output

    def test_outputs_sudoers_content(self, capsys: pytest.CaptureFixture[str]) -> None:
        role = RoleTemplate(
            name="test",
            description="test",
            sudoers=["/usr/bin/docker ps"],
        )
        print_manual_instructions("myhost", "ssh-ed25519 KEY", role, "svc")
        output = capsys.readouterr().out
        assert "sudoers" in output.lower() or "/etc/sudoers.d" in output
        assert "visudo" in output

    def test_freebsd_uses_pw_useradd(self, capsys: pytest.CaptureFixture[str]) -> None:
        role = RoleTemplate(name="test", description="test")
        print_manual_instructions("myhost", "ssh-ed25519 KEY", role, "svc", os_type="freebsd")
        output = capsys.readouterr().out
        assert "pw useradd" in output

    def test_linux_uses_useradd(self, capsys: pytest.CaptureFixture[str]) -> None:
        role = RoleTemplate(name="test", description="test")
        print_manual_instructions("myhost", "ssh-ed25519 KEY", role, "svc", os_type="linux")
        output = capsys.readouterr().out
        assert "useradd -m -s /bin/bash" in output

    def test_step_numbers_sequential_with_groups_and_sudoers(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        role = RoleTemplate(
            name="full",
            description="test",
            groups=["docker"],
            sudoers=["/usr/bin/x"],
        )
        print_manual_instructions("h", "ssh-ed25519 K", role, "svc")
        output = capsys.readouterr().out
        # Steps should be 1, 2, 3 (groups), 4 (sudoers), 5 (test)
        assert "1." in output
        assert "2." in output
        assert "3." in output
        assert "4." in output
        assert "5." in output

    def test_step_numbers_sequential_without_groups(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        role = RoleTemplate(
            name="nogroups",
            description="test",
            sudoers=["/usr/bin/x"],
        )
        print_manual_instructions("h", "ssh-ed25519 K", role, "svc")
        output = capsys.readouterr().out
        # Steps: 1 (create), 2 (key), 3 (sudoers), 4 (test)
        assert "1." in output
        assert "2." in output
        assert "3." in output
        assert "4." in output


# ===========================================================================
# TestRunSshProvisioning
# ===========================================================================


class TestRunSshProvisioning:
    """Integration tests for run_ssh_provisioning() with all SSH mocked."""

    def _make_config(self, tmp_path: Path, os_type: str = "linux") -> Path:
        """Create a minimal config.yaml and return the config dir."""
        from ruamel.yaml import YAML

        config = {
            "hosts": {
                "testhost": {
                    "hostname": "testhost",
                    "ip": "10.0.0.99",
                    "vlan": 10,
                    "ssh": True,
                    "ssh_user": "admin",
                    "ssh_key_path": "~/.ssh/id_ed25519",
                    "os": os_type,
                },
            },
        }
        yaml = YAML()
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f)
        return tmp_path

    def test_raises_without_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = self._make_config(tmp_path)
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(config_dir))

        with pytest.raises(ValueError, match="Must specify"):
            run_ssh_provisioning("testhost")

    def test_raises_for_unknown_host(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = self._make_config(tmp_path)
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(config_dir))

        with pytest.raises(FileNotFoundError, match="not found in config.yaml"):
            run_ssh_provisioning("bogus", manual=True)

    @pytest.mark.parametrize("bad_name", ["root!", "MCP", "has space", "../evil", ""])
    def test_raises_for_invalid_service_user(
        self,
        bad_name: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config_dir = self._make_config(tmp_path)
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(config_dir))

        with pytest.raises(ValueError, match="Invalid service_user"):
            run_ssh_provisioning("testhost", manual=True, service_user=bad_name)

    @patch("mcp_homelab.setup.ssh_provisioning.upsert_node")
    @patch("mcp_homelab.setup.ssh_provisioning.generate_keypair")
    @patch("mcp_homelab.setup.ssh_provisioning._read_public_key")
    def test_manual_mode_generates_key_and_updates_config(
        self,
        mock_read_pub: MagicMock,
        mock_gen: MagicMock,
        mock_upsert: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        config_dir = self._make_config(tmp_path)
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(config_dir))
        mock_gen.return_value = tmp_path / "testhost"
        mock_read_pub.return_value = "ssh-ed25519 PUBKEY"

        key_dir = tmp_path / "keys"
        run_ssh_provisioning(
            "testhost",
            manual=True,
            role_name="readonly",
            key_dir=key_dir,
        )

        mock_gen.assert_called_once()
        mock_upsert.assert_called_once()
        # upsert_node(config_path, name, node_data) — positional args
        upsert_args = mock_upsert.call_args[0]
        assert upsert_args[1] == "testhost"

    @patch("mcp_homelab.setup.ssh_provisioning._update_config")
    @patch("mcp_homelab.setup.ssh_provisioning.verify_connection")
    @patch("mcp_homelab.setup.ssh_provisioning.apply_role")
    @patch("mcp_homelab.setup.ssh_provisioning.deploy_public_key")
    @patch("mcp_homelab.setup.ssh_provisioning.connect")
    @patch("mcp_homelab.setup.ssh_provisioning._read_public_key")
    @patch("mcp_homelab.setup.ssh_provisioning.generate_keypair")
    def test_automated_mode_full_flow(
        self,
        mock_gen: MagicMock,
        mock_read_pub: MagicMock,
        mock_connect: MagicMock,
        mock_deploy: MagicMock,
        mock_apply: MagicMock,
        mock_verify: MagicMock,
        mock_update: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config_dir = self._make_config(tmp_path)
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(config_dir))

        mock_gen.return_value = tmp_path / "testhost"
        mock_read_pub.return_value = "ssh-ed25519 PUBKEY"
        mock_client = MagicMock()
        mock_connect.return_value = mock_client
        mock_verify.return_value = True

        key_dir = tmp_path / "keys"
        run_ssh_provisioning(
            "testhost",
            bootstrap_user="admin",
            role_name="docker-host",
            key_dir=key_dir,
        )

        mock_gen.assert_called_once()
        mock_connect.assert_called_once_with("10.0.0.99", "admin", "~/.ssh/id_ed25519")
        mock_deploy.assert_called_once()
        mock_apply.assert_called_once()
        mock_verify.assert_called_once()
        mock_client.close.assert_called_once()
        mock_update.assert_called_once()

    @patch("mcp_homelab.setup.ssh_provisioning._update_config")
    @patch("mcp_homelab.setup.ssh_provisioning.verify_connection")
    @patch("mcp_homelab.setup.ssh_provisioning.deploy_public_key")
    @patch("mcp_homelab.setup.ssh_provisioning.connect")
    @patch("mcp_homelab.setup.ssh_provisioning._read_public_key")
    @patch("mcp_homelab.setup.ssh_provisioning.generate_keypair")
    def test_automated_mode_without_role(
        self,
        mock_gen: MagicMock,
        mock_read_pub: MagicMock,
        mock_connect: MagicMock,
        mock_deploy: MagicMock,
        mock_verify: MagicMock,
        mock_update: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Automated mode works even when no role is specified."""
        config_dir = self._make_config(tmp_path)
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(config_dir))

        mock_gen.return_value = tmp_path / "testhost"
        mock_read_pub.return_value = "ssh-ed25519 PUBKEY"
        mock_client = MagicMock()
        mock_connect.return_value = mock_client
        mock_verify.return_value = True

        run_ssh_provisioning(
            "testhost",
            bootstrap_user="admin",
            key_dir=tmp_path / "keys",
        )

        # deploy_public_key is still called; apply_role is not
        mock_deploy.assert_called_once()
        mock_update.assert_called_once()

    @patch("mcp_homelab.setup.ssh_provisioning._update_config")
    @patch("mcp_homelab.setup.ssh_provisioning.verify_connection")
    @patch("mcp_homelab.setup.ssh_provisioning.deploy_public_key")
    @patch("mcp_homelab.setup.ssh_provisioning.connect")
    @patch("mcp_homelab.setup.ssh_provisioning._read_public_key")
    @patch("mcp_homelab.setup.ssh_provisioning.generate_keypair")
    def test_automated_mode_verify_failure_warns(
        self,
        mock_gen: MagicMock,
        mock_read_pub: MagicMock,
        mock_connect: MagicMock,
        mock_deploy: MagicMock,
        mock_verify: MagicMock,
        mock_update: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When verification fails, a warning is printed but no exception raised."""
        config_dir = self._make_config(tmp_path)
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(config_dir))

        mock_gen.return_value = tmp_path / "testhost"
        mock_read_pub.return_value = "ssh-ed25519 PUBKEY"
        mock_client = MagicMock()
        mock_connect.return_value = mock_client
        mock_verify.return_value = False

        run_ssh_provisioning(
            "testhost",
            bootstrap_user="admin",
            key_dir=tmp_path / "keys",
        )

        captured = capsys.readouterr()
        assert "WARNING" in captured.err or "Verification failed" in captured.err
