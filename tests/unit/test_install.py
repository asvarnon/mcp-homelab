"""Unit tests for mcp_homelab/setup/install.py."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from ruamel.yaml import YAML

from mcp_homelab.setup.install import (
    _detect_container,
    _ensure_linux,
    _resolve_public_url,
    _run_command,
    _strip_namespace_directives,
    _update_server_config,
    _write_systemd_unit,
    run_install,
)


class TestEnsureLinux:
    def test_exits_on_non_linux(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr("mcp_homelab.setup.install.platform.system", lambda: "Windows")

        with pytest.raises(SystemExit, match="1"):
            _ensure_linux()

        err = capsys.readouterr().err
        assert "Linux only" in err

    def test_passes_on_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("mcp_homelab.setup.install.platform.system", lambda: "Linux")
        _ensure_linux()  # should not raise


class TestRunCommand:
    def test_catches_missing_binary(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        def raise_fnf(*args: object, **kwargs: object) -> None:
            raise FileNotFoundError("[Errno 2] No such file or directory: 'nosuchcmd'")

        monkeypatch.setattr("mcp_homelab.setup.install.subprocess.run", raise_fnf)

        with pytest.raises(SystemExit, match="1"):
            _run_command(["nosuchcmd"], "test step")

        err = capsys.readouterr().err
        assert "test step failed" in err


class TestRunInstall:
    def test_requires_root(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr("mcp_homelab.setup.install.platform.system", lambda: "Linux")
        monkeypatch.setattr("mcp_homelab.setup.install.os.geteuid", lambda: 1000, raising=False)

        with pytest.raises(SystemExit, match="1"):
            run_install(public_url="https://mcp.example.com")

        err = capsys.readouterr().err
        assert "must be run as root" in err

    def test_creates_service_user(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        install_path = _seed_install_tree(tmp_path)
        calls: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            if command[:2] == ["id", "mcp"]:
                return subprocess.CompletedProcess(command, 1, "", "")
            if command[0] == "systemctl" and command[1] == "is-active":
                return subprocess.CompletedProcess(command, 0, "active\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        monkeypatch.setattr("mcp_homelab.setup.install.platform.system", lambda: "Linux")
        monkeypatch.setattr("mcp_homelab.setup.install.os.geteuid", lambda: 0, raising=False)
        monkeypatch.setattr("mcp_homelab.setup.install._detect_install_path", lambda: install_path)
        monkeypatch.setattr("mcp_homelab.setup.install._write_systemd_unit", lambda *a, **kw: None)
        monkeypatch.setattr("mcp_homelab.setup.install._detect_container", lambda: None)
        monkeypatch.setattr("mcp_homelab.setup.install.subprocess.run", fake_run)

        run_install(public_url="https://mcp.example.com")

        assert ["id", "mcp"] in calls
        assert [
            "useradd",
            "--system",
            "--create-home",
            "--shell",
            "/usr/sbin/nologin",
            "mcp",
        ] in calls

    def test_skips_existing_user(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        install_path = _seed_install_tree(tmp_path)
        calls: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            if command[:2] == ["id", "mcp"]:
                return subprocess.CompletedProcess(command, 0, "uid=999(mcp)", "")
            if command[0] == "systemctl" and command[1] == "is-active":
                return subprocess.CompletedProcess(command, 0, "active\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        monkeypatch.setattr("mcp_homelab.setup.install.platform.system", lambda: "Linux")
        monkeypatch.setattr("mcp_homelab.setup.install.os.geteuid", lambda: 0, raising=False)
        monkeypatch.setattr("mcp_homelab.setup.install._detect_install_path", lambda: install_path)
        monkeypatch.setattr("mcp_homelab.setup.install._write_systemd_unit", lambda *a, **kw: None)
        monkeypatch.setattr("mcp_homelab.setup.install._detect_container", lambda: None)
        monkeypatch.setattr("mcp_homelab.setup.install.subprocess.run", fake_run)

        run_install(public_url="https://mcp.example.com")

        assert ["id", "mcp"] in calls
        assert not any(command and command[0] == "useradd" for command in calls)

    def test_updates_config_yaml(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text("hosts: {}\n", encoding="utf-8")

        _update_server_config(config_path, "https://mcp.example.com")

        yaml = YAML()
        with open(config_path, encoding="utf-8") as file:
            data = yaml.load(file)

        assert data["server"]["transport"] == "http"
        assert data["server"]["host"] == "0.0.0.0"
        assert data["server"]["port"] == 8000
        assert data["server"]["public_url"] == "https://mcp.example.com"

    def test_renders_systemd_unit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        install_path = tmp_path / "custom-install"
        output_path = tmp_path / "mcp-homelab.service"

        from unittest.mock import MagicMock
        fake_ref = MagicMock()
        fake_ref.read_text.return_value = (
            "WorkingDirectory=/opt/mcp-homelab\n"
            "EnvironmentFile=/opt/mcp-homelab/.env\n"
            "ExecStart=/opt/mcp-homelab/.venv/bin/mcp-homelab serve\n"
        )
        fake_files = MagicMock(return_value=MagicMock())
        fake_files.return_value.joinpath.return_value = fake_ref
        monkeypatch.setattr("mcp_homelab.setup.install.importlib.resources.files", fake_files)

        _write_systemd_unit(install_path, output_path)

        rendered = output_path.read_text(encoding="utf-8")
        assert "/opt/mcp-homelab" not in rendered
        assert f"WorkingDirectory={install_path}" in rendered
        assert f"EnvironmentFile={install_path}/.env" in rendered
        assert f"ExecStart={install_path}/.venv/bin/mcp-homelab serve" in rendered

    def test_public_url_from_arg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        input_mock = MagicMock(side_effect=RuntimeError("input should not be called"))
        monkeypatch.setattr("builtins.input", input_mock)

        resolved = _resolve_public_url("https://mcp.example.com")

        assert resolved == "https://mcp.example.com"
        input_mock.assert_not_called()

    def test_public_url_from_prompt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "https://prompted.example.com")

        resolved = _resolve_public_url(None)

        assert resolved == "https://prompted.example.com"


class TestDetectContainer:
    def test_returns_lxc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "mcp_homelab.setup.install.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "lxc\n", ""),
        )
        assert _detect_container() == "lxc"

    def test_returns_docker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "mcp_homelab.setup.install.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "docker\n", ""),
        )
        assert _detect_container() == "docker"

    def test_returns_none_on_bare_metal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "mcp_homelab.setup.install.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "none\n", ""),
        )
        assert _detect_container() is None

    def test_returns_none_on_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "mcp_homelab.setup.install.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, "", ""),
        )
        assert _detect_container() is None

    def test_returns_none_on_missing_binary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def raise_fnf(*args: object, **kwargs: object) -> None:
            raise FileNotFoundError("systemd-detect-virt")

        monkeypatch.setattr("mcp_homelab.setup.install.subprocess.run", raise_fnf)
        assert _detect_container() is None

    def test_returns_none_on_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def raise_timeout(*args: object, **kwargs: object) -> None:
            raise subprocess.TimeoutExpired("systemd-detect-virt", 5)

        monkeypatch.setattr("mcp_homelab.setup.install.subprocess.run", raise_timeout)
        assert _detect_container() is None


class TestStripNamespaceDirectives:
    def test_strips_sandbox_lines(self) -> None:
        unit = (
            "[Service]\n"
            "User=mcp\n"
            "NoNewPrivileges=true\n"
            "LockPersonality=true\n"
            "PrivateTmp=true\n"
            "ProtectSystem=strict\n"
            "ProtectHome=true\n"
            "ReadWritePaths=/opt/mcp-homelab\n"
            "\n"
            "[Install]\n"
        )
        result = _strip_namespace_directives(unit)
        assert "PrivateTmp" not in result
        assert "ProtectSystem" not in result
        assert "ProtectHome" not in result
        assert "ReadWritePaths" not in result
        # NoNewPrivileges and LockPersonality use prctl(), not namespaces
        assert "NoNewPrivileges=true" in result
        assert "LockPersonality=true" in result
        assert "User=mcp" in result
        assert "[Install]" in result

    def test_preserves_unit_without_sandbox(self) -> None:
        unit = "[Service]\nUser=mcp\nExecStart=/bin/true\n"
        assert _strip_namespace_directives(unit) == unit


class TestWriteSystemdUnitContainer:
    def test_strips_directives_when_in_container(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        install_path = tmp_path / "mcp-homelab"
        output_path = tmp_path / "mcp-homelab.service"

        fake_ref = MagicMock()
        fake_ref.read_text.return_value = (
            "[Service]\n"
            "WorkingDirectory=/opt/mcp-homelab\n"
            "PrivateTmp=true\n"
            "ProtectSystem=strict\n"
            "NoNewPrivileges=true\n"
        )
        fake_files = MagicMock(return_value=MagicMock())
        fake_files.return_value.joinpath.return_value = fake_ref
        monkeypatch.setattr("mcp_homelab.setup.install.importlib.resources.files", fake_files)

        _write_systemd_unit(install_path, output_path, in_container=True)

        rendered = output_path.read_text(encoding="utf-8")
        assert "PrivateTmp" not in rendered
        assert "ProtectSystem" not in rendered
        assert "NoNewPrivileges=true" in rendered

    def test_keeps_directives_on_bare_metal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        install_path = tmp_path / "mcp-homelab"
        output_path = tmp_path / "mcp-homelab.service"

        fake_ref = MagicMock()
        fake_ref.read_text.return_value = (
            "[Service]\n"
            "WorkingDirectory=/opt/mcp-homelab\n"
            "PrivateTmp=true\n"
            "ProtectSystem=strict\n"
        )
        fake_files = MagicMock(return_value=MagicMock())
        fake_files.return_value.joinpath.return_value = fake_ref
        monkeypatch.setattr("mcp_homelab.setup.install.importlib.resources.files", fake_files)

        _write_systemd_unit(install_path, output_path, in_container=False)

        rendered = output_path.read_text(encoding="utf-8")
        assert "PrivateTmp=true" in rendered
        assert "ProtectSystem=strict" in rendered


def _seed_install_tree(base_path: Path) -> Path:
    """Create a minimal install tree used by run_install tests."""
    install_path = base_path / "mcp-homelab"
    install_path.mkdir(parents=True)
    (install_path / "pyproject.toml").write_text("[project]\nname = 'mcp-homelab'\n", encoding="utf-8")
    (install_path / "config.yaml").write_text("hosts: {}\n", encoding="utf-8")
    return install_path
