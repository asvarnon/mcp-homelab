"""Unit tests for mcp_homelab/setup/install.py."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from ruamel.yaml import YAML

from mcp_homelab.setup.install import (
    _resolve_public_url,
    _update_server_config,
    _write_systemd_unit,
    run_install,
)


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
        # Stub out the service template write (writes to /etc/systemd which needs root)
        monkeypatch.setattr("mcp_homelab.setup.install._write_systemd_unit", lambda *args: None)
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
        monkeypatch.setattr("mcp_homelab.setup.install._write_systemd_unit", lambda *args: None)
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


def _seed_install_tree(base_path: Path) -> Path:
    """Create a minimal install tree used by run_install tests."""
    install_path = base_path / "mcp-homelab"
    install_path.mkdir(parents=True)
    (install_path / "pyproject.toml").write_text("[project]\nname = 'mcp-homelab'\n", encoding="utf-8")
    (install_path / "config.yaml").write_text("hosts: {}\n", encoding="utf-8")
    return install_path
