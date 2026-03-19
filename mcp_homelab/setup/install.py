"""Install mcp-homelab as a systemd service in HTTP transport mode."""

from __future__ import annotations

import importlib.resources
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
    # geteuid() is Unix-only; _ensure_linux() guards this call at runtime.
    # Use getattr to satisfy Pylance on Windows where the attr doesn't exist.
    euid: int = getattr(os, "geteuid", lambda: -1)()
    if euid != 0:
        print("  ✗ this command must be run as root (sudo)", file=sys.stderr)
        sys.exit(1)


def _detect_install_path() -> Path:
    """Resolve and validate the local installation root path.

    Walks up from this file's location looking for ``pyproject.toml``.
    Does NOT fall back to cwd — the project root must be unambiguous
    to avoid chown -R on an unintended directory.
    """
    candidate = Path(__file__).resolve().parent.parent.parent
    if (candidate / "pyproject.toml").exists():
        return candidate
    print("  ✗ could not locate project root (no pyproject.toml found)", file=sys.stderr)
    print("    run this command from a git clone of mcp-homelab", file=sys.stderr)
    sys.exit(1)


def _validate_path_safe(path: Path) -> None:
    """Reject paths containing characters unsafe for systemd unit substitution."""
    import re
    path_str = str(path)
    if re.search(r'[\n\r\0]', path_str):
        print("  ✗ install path contains control characters", file=sys.stderr)
        sys.exit(1)
    if not re.match(r'^[A-Za-z0-9/_.\\ :-]+$', path_str):
        print(f"  ✗ install path contains unsafe characters: {path_str}", file=sys.stderr)
        sys.exit(1)
    # Spaces in paths break systemd unit fields like WorkingDirectory=
    # without quoting.  Reject them rather than attempting systemd escaping.
    if ' ' in path_str:
        print("  ✗ install path contains spaces (unsupported by systemd units)", file=sys.stderr)
        sys.exit(1)


def _run_command(command: list[str], step_name: str) -> subprocess.CompletedProcess[str]:
    """Run a command and exit with a descriptive error if it fails."""
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as exc:
        print(f"  ✗ {step_name} failed", file=sys.stderr)
        print(f"    command: {' '.join(command)}", file=sys.stderr)
        print(f"    error: {exc}", file=sys.stderr)
        sys.exit(1)
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else "(no stderr output)"
        print(f"  ✗ {step_name} failed", file=sys.stderr)
        print(f"    command: {' '.join(command)}", file=sys.stderr)
        print(f"    error: {stderr}", file=sys.stderr)
        sys.exit(1)
    return result


# Systemd sandbox directives that require mount namespaces.
# These fail inside unprivileged LXC containers (status 226/NAMESPACE).
# LockPersonality and NoNewPrivileges use prctl(), not namespaces — kept.
# ReadWritePaths is a no-op without ProtectSystem, but stripped for clarity.
_NAMESPACE_DIRECTIVES: set[str] = {
    "PrivateTmp",
    "PrivateDevices",
    "ProtectSystem",
    "ProtectHome",
    "ProtectKernelTunables",
    "ProtectKernelModules",
    "ProtectControlGroups",
    "ReadWritePaths",
}


def _detect_container() -> str | None:
    """Return the container runtime name (e.g. 'lxc', 'docker') or None.

    Returns None (assume bare metal) if the detection binary is missing
    or times out, so install continues with full sandbox directives.
    """
    try:
        result = subprocess.run(
            ["systemd-detect-virt", "--container"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    virt = result.stdout.strip() if result.returncode == 0 else None
    # systemd-detect-virt returns "none" when not in a container
    if virt == "none":
        return None
    return virt


def _strip_namespace_directives(unit_content: str) -> str:
    """Remove systemd directives that need mount namespaces."""
    lines = unit_content.splitlines(keepends=True)
    return "".join(
        line for line in lines
        if line.split("=", 1)[0].strip() not in _NAMESPACE_DIRECTIVES
    )


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


def _write_systemd_unit(
    install_path: Path,
    output_path: Path,
    *,
    in_container: bool = False,
) -> None:
    """Load the bundled service template, render it, and write to output_path."""
    _validate_path_safe(install_path)
    service_ref = importlib.resources.files("mcp_homelab.data").joinpath("mcp-homelab.service")
    template = service_ref.read_text(encoding="utf-8")
    rendered = template.replace("/opt/mcp-homelab", str(install_path))
    if in_container:
        rendered = _strip_namespace_directives(rendered)
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
    container_type = _detect_container()
    service_path = Path("/etc/systemd/system/mcp-homelab.service")
    _write_systemd_unit(install_path, service_path, in_container=container_type is not None)
    if container_type:
        print(f"  ⚠ wrote {service_path} (sandbox directives stripped — {container_type} container detected)")
    else:
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
