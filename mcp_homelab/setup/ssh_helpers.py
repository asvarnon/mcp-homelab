"""Shared SSH helpers for setup commands.

Low-level SSH connection and command execution used by both
node_setup (interactive) and check (read-only validation).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import paramiko

_SSH_TIMEOUT = 10


class SSHConnectError(Exception):
    """Raised when SSH connection fails during setup."""


@dataclass
class Capabilities:
    docker: bool = False
    docker_needs_sudo: bool = False
    proxmox: bool = False
    opnsense: bool = False
    dmidecode: bool = False
    dmidecode_needs_sudo_fix: bool = False


class CommandResult(NamedTuple):
    exit_code: int
    stdout: str
    stderr: str


def connect(ip: str, user: str, key_path: str) -> paramiko.SSHClient:
    """Test SSH connectivity, returning the connected client on success.

    Raises SSHConnectError with a clear message on failure.
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    resolved_key = str(Path(key_path).expanduser())

    try:
        client.connect(
            hostname=ip,
            username=user,
            key_filename=resolved_key,
            timeout=_SSH_TIMEOUT,
        )
    except FileNotFoundError:
        client.close()
        raise SSHConnectError(f"SSH key not found: {resolved_key}")
    except Exception as exc:
        client.close()
        raise SSHConnectError(f"SSH connection failed: {exc}")

    return client


def run_command(client: paramiko.SSHClient, command: str) -> CommandResult:
    """Execute a command and return (exit_code, stdout, stderr)."""
    _, stdout, stderr = client.exec_command(command, timeout=_SSH_TIMEOUT)
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    return CommandResult(exit_code, out, err)


def detect_capabilities(client: paramiko.SSHClient) -> Capabilities:
    """Detect what the node supports: Docker, Proxmox, OPNsense, dmidecode."""
    caps = Capabilities()

    # Docker: try without sudo first, then with sudo
    exit_code, _, _ = run_command(client, "docker info >/dev/null 2>&1")
    if exit_code == 0:
        caps.docker = True
    else:
        exit_code, _, _ = run_command(client, "sudo -n docker info >/dev/null 2>&1")
        if exit_code == 0:
            caps.docker = True
            caps.docker_needs_sudo = True

    # Proxmox: check for pvesh binary
    exit_code, _, _ = run_command(client, "command -v pvesh >/dev/null 2>&1")
    caps.proxmox = exit_code == 0

    # OPNsense: FreeBSD + opnsense config dir
    exit_code, _, _ = run_command(client, "test -d /usr/local/etc/inc && uname | grep -qi freebsd")
    caps.opnsense = exit_code == 0

    # dmidecode: try passwordless sudo
    exit_code, _, _ = run_command(client, "sudo -n dmidecode --version >/dev/null 2>&1")
    if exit_code == 0:
        caps.dmidecode = True
    else:
        # Check if dmidecode binary exists at all
        exit_code, _, _ = run_command(client, "command -v dmidecode >/dev/null 2>&1")
        if exit_code == 0:
            caps.dmidecode_needs_sudo_fix = True

    return caps
