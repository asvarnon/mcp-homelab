"""SSH connection manager using paramiko.

Provides a reusable SSH client that reads host info from config
and credentials from environment variables.  Connections are cached
per-host and reused across calls.
"""

from __future__ import annotations

import asyncio
import logging
import os
import stat
from typing import TYPE_CHECKING

import paramiko

from pathlib import Path

from mcp_homelab.core.config import get_ssh_key_path, get_ssh_user, load_config

if TYPE_CHECKING:
    from mcp_homelab.core.config import AppConfig

logger = logging.getLogger(__name__)

_SSH_TIMEOUT = 10  # seconds — connect + command timeout
_IS_POSIX = os.name != "nt"


def _validate_key_permissions(key_path: str) -> None:
    """Refuse to use an SSH key with overly permissive file permissions.

    Mirrors OpenSSH behavior: key files must not be readable by
    group or others.  Skipped on Windows where POSIX permission
    bits don't apply.
    """
    if not _IS_POSIX:
        return
    path = Path(key_path)
    if not path.is_file():
        return  # let paramiko report the missing file
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & (stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP
               | stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH):
        raise SSHError(
            f"SSH key {key_path} has mode {mode:04o} — too open. "
            f"Keys must not be accessible by group or others. "
            f"Run: chmod 600 {key_path}"
        )


class SSHError(Exception):
    """Raised when an SSH command fails (non-zero exit or connection error)."""


class SSHManager:
    """Manages SSH connections to homelab nodes.

    Connections are lazy — nothing happens until execute() is called.
    Connections are cached per-host and reused for subsequent calls.
    """

    def __init__(self) -> None:
        self._config: AppConfig | None = None
        self._connections: dict[str, paramiko.SSHClient] = {}

    def _get_config(self) -> AppConfig:
        """Lazy-load config on first use instead of at import time."""
        if self._config is None:
            self._config = load_config()
        return self._config

    def _connect(self, hostname: str) -> paramiko.SSHClient:
        """Create or return a cached SSH client for the given node.

        Connects using the IP from config.yaml and credentials from env vars.
        Does NOT rely on ~/.ssh/config.

        Args:
            hostname: Logical node name as defined in config.yaml.

        Returns:
            Connected paramiko.SSHClient.

        Raises:
            KeyError: If hostname is not in config.
            SSHError: If connection fails.
        """
        # Return cached connection if still active
        if hostname in self._connections:
            client = self._connections[hostname]
            transport = client.get_transport()
            if transport is not None and transport.is_active():
                return client
            # Stale connection — clean up and reconnect
            client.close()
            del self._connections[hostname]

        config = self._get_config()
        node = config.hosts.get(hostname)
        if node is None:
            raise KeyError(f"Unknown host: {hostname}")

        if not node.ssh:
            raise SSHError(f"Node '{hostname}' does not have SSH enabled in config")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Per-node credentials override global env var fallbacks
        username = node.ssh_user or get_ssh_user()
        key_path = str(Path(node.ssh_key_path).expanduser()) if node.ssh_key_path else str(get_ssh_key_path())

        _validate_key_permissions(key_path)

        try:
            client.connect(
                hostname=node.ip,
                username=username,
                key_filename=key_path,
                timeout=_SSH_TIMEOUT,
            )
        except Exception as exc:
            client.close()
            raise SSHError(f"Failed to connect to {hostname} ({node.ip}): {exc}") from exc

        self._connections[hostname] = client
        logger.debug("SSH connected to %s (%s)", hostname, node.ip)
        return client

    def execute(self, hostname: str, command: str) -> str:
        """Execute a command on a remote node via SSH.

        Args:
            hostname: Logical node name as defined in config.yaml.
            command: Shell command to execute.

        Returns:
            stdout output as a string (stripped).

        Raises:
            KeyError: If hostname is not defined in config.
            SSHError: If connection fails or command returns non-zero exit.
        """
        client = self._connect(hostname)

        try:
            _, stdout, stderr = client.exec_command(command, timeout=_SSH_TIMEOUT)
            exit_code = stdout.channel.recv_exit_status()
            out = stdout.read().decode().strip()
            err = stderr.read().decode().strip()
        except Exception as exc:
            # Connection may have gone stale mid-call — evict and close
            stale = self._connections.pop(hostname, None)
            if stale:
                stale.close()
            raise SSHError(f"SSH command failed on {hostname}: {exc}") from exc

        if exit_code != 0:
            raise SSHError(
                f"Command '{command}' on {hostname} exited {exit_code}: {err or out}"
            )

        return out

    async def execute_async(self, hostname: str, command: str) -> str:
        """Async wrapper around execute() — runs in a thread to avoid blocking."""
        return await asyncio.to_thread(self.execute, hostname, command)

    def execute_docker(self, hostname: str, docker_args: str) -> str:
        """Execute a docker command on a node, prefixing sudo if configured.

        Args:
            hostname: Logical node name as defined in config.yaml.
            docker_args: Arguments to pass to docker (e.g. 'ps --format json').

        Returns:
            stdout output as a string (stripped).
        """
        config = self._get_config()
        node = config.hosts[hostname]
        prefix = "sudo " if node.sudo_docker else ""
        return self.execute(hostname, f"{prefix}docker {docker_args}")

    async def execute_docker_async(self, hostname: str, docker_args: str) -> str:
        """Async wrapper around execute_docker()."""
        return await asyncio.to_thread(self.execute_docker, hostname, docker_args)

    def close(self) -> None:
        """Close all cached connections."""
        for name, client in self._connections.items():
            logger.debug("Closing SSH connection to %s", name)
            client.close()
        self._connections.clear()
