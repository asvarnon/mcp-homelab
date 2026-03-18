"""Install mcp-homelab as a systemd service in HTTP transport mode."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from mcp_homelab.setup.config_writer import _load_yaml


def _ensure_linux() -> None:
    """Exit if the command is not running on Linux."""
    if platform.system() != "Linux":
        print("  ✗ install is supported on Linux only (systemd required)", file=sys.stderr)
        sys.exit(1)


def _ensure_root() -> None:
    """Exit if the current process is not running as root."""
    if os.geteuid() != 0:
        print("  ✗ this command must be run as root (sudo)", file=sys.stderr)
        sys.exit(1)


def _detect_install_path() -> Path:
    """Resolve and validate the local installation root path."""
    install_path = Path(__file__).resolve().parent.parent.parent
    server_path = install_path / "server.py"
    if not server_path.exists():
        print(f"  ✗ could not find server.py at {server_path}", file=sys.stderr)
        sys.exit(1)
    return install_path


def _run_command(command: list[str], step_name: str) -> subprocess.CompletedProcess[str]:
    """Run a command and exit with a descriptive error if it fails."""
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else "(no stderr output)"
        print(f"  ✗ {step_name} failed", file=sys.stderr)
        print(f"    command: {' '.join(command)}", file=sys.stderr)
        print(f"    error: {stderr}", file=sys.stderr)
        sys.exit(1)
    return result


def _resolve_public_url(public_url: str | None) -> str:
    """Get public HTTPS URL from argument or interactive prompt."""
    if public_url is not None and public_url.strip():
        resolved_url = public_url.strip()
    else:
        resolved_url = input("Public HTTPS URL for this server (e.g., https://mcp.example.com): ").strip()

    if not resolved_url:
        print("  ✗ public URL cannot be empty", file=sys.stderr)
        sys.exit(1)
    if not resolved_url.startswith("https://"):
        print("  ✗ public URL must start with https://", file=sys.stderr)
        sys.exit(1)

    return resolved_url


def _update_server_config(config_path: Path, public_url: str) -> None:
    """Update config.yaml server section for HTTP transport mode."""
    yaml, data = _load_yaml(config_path)

    server_section = data.get("server")
    if not isinstance(server_section, CommentedMap):
        server_section = CommentedMap()
        data["server"] = server_section

    server_section["transport"] = "http"
    server_section["host"] = "0.0.0.0"
    server_section["port"] = 8000
    server_section["public_url"] = public_url

    with open(config_path, "wb") as file:
        yaml.dump(data, file)


def _write_systemd_unit(template_path: Path, install_path: Path, output_path: Path) -> None:
    """Render and write the systemd unit file using the install path."""
    template = template_path.read_text(encoding="utf-8")
    rendered = template.replace("/opt/mcp-homelab", str(install_path))
    output_path.write_text(rendered, encoding="utf-8")


def run_install(public_url: str | None = None) -> None:
    """Convert a local mcp-homelab checkout into a systemd HTTP service."""
    print("\n--- mcp-homelab install ---\n")

    print("[1/10] Checking platform...")
    _ensure_linux()
    print("  → Linux detected ✓")

    print("[2/10] Checking privileges...")
    _ensure_root()
    print("  → root privileges confirmed ✓")

    print("[3/10] Detecting install path...")
    install_path = _detect_install_path()
    print(f"  → install path: {install_path} ✓")

    print("[4/10] Ensuring service user exists...")
    id_result = subprocess.run(["id", "mcp"], check=False, capture_output=True, text=True)
    if id_result.returncode == 0:
        print("  → service user mcp already exists ✓")
    else:
        _run_command(
            [
                "useradd",
                "--system",
                "--create-home",
                "--shell",
                "/usr/sbin/nologin",
                "mcp",
            ],
            "create service user",
        )
        print("  → created service user mcp ✓")

    print("[5/10] Setting ownership...")
    _run_command(["chown", "-R", "mcp:mcp", str(install_path)], "set ownership")
    print("  → ownership set to mcp:mcp ✓")

    print("[6/10] Resolving public URL...")
    resolved_public_url = _resolve_public_url(public_url)
    print(f"  → public URL set to {resolved_public_url} ✓")

    print("[7/10] Updating config.yaml...")
    config_path = install_path / "config.yaml"
    if not config_path.exists():
        print(f"  ✗ config.yaml not found at {config_path}", file=sys.stderr)
        sys.exit(1)
    _update_server_config(config_path, resolved_public_url)
    print("  → config.yaml updated (HTTP mode) ✓")

    print("[8/10] Installing systemd unit...")
    template_path = install_path / "deploy" / "mcp-homelab.service"
    if not template_path.exists():
        print(f"  ✗ service template not found at {template_path}", file=sys.stderr)
        sys.exit(1)
    service_path = Path("/etc/systemd/system/mcp-homelab.service")
    _write_systemd_unit(template_path, install_path, service_path)
    print(f"  → wrote {service_path} ✓")

    print("[9/10] Enabling and starting service...")
    _run_command(["systemctl", "daemon-reload"], "systemd daemon reload")
    _run_command(["systemctl", "enable", "mcp-homelab"], "enable service")
    _run_command(["systemctl", "start", "mcp-homelab"], "start service")
    print("  → service enabled and started ✓")

    print("[10/10] Verifying service status...")
    status_result = _run_command(["systemctl", "is-active", "mcp-homelab"], "verify service status")
    status = status_result.stdout.strip() or "unknown"
    print(f"  → mcp-homelab status: {status} ✓")

    print("\nNext steps:")
    print("  1. Make the service reachable from your MCP client environment.")
    print("  2. Configure a secure ingress path (reverse proxy, Cloudflare Tunnel, etc.).")
    print("  3. See guides/ for hosted-mode and tunnel setup details.")
