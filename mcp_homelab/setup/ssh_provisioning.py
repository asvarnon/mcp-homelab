"""SSH service-account provisioning for mcp-homelab.

Creates a dedicated service account on target hosts with a generated
ed25519 keypair and role-based permissions.  Two modes:

- **Automated** (``--bootstrap-user``): SSH in as an existing admin user,
  create the service account, deploy the key, and apply role permissions.
- **Manual** (``--manual``): Generate the keypair locally and print
  step-by-step commands for the user to run on the target host.

Designed to be called from ``mcp-homelab setup ssh``.
"""

from __future__ import annotations

import io
import platform
import re
import stat
import sys
from pathlib import Path
from typing import Literal

import paramiko
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.config import HostConfig
from mcp_homelab.setup.config_writer import upsert_node
from mcp_homelab.setup.roles import RoleTemplate, get_role
from mcp_homelab.setup.ssh_helpers import (
    SSHConnectError,
    connect,
    run_command,
)

_DEFAULT_KEY_DIR = Path.home() / ".mcp-homelab" / "keys"
_SERVICE_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]*$")


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------


def _create_ed25519_key() -> paramiko.Ed25519Key:
    """Generate a new ed25519 key using cryptography and return as paramiko key."""
    priv = Ed25519PrivateKey.generate()
    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    )
    return paramiko.Ed25519Key(file_obj=io.StringIO(pem.decode("utf-8")))


def generate_keypair(key_path: Path, force: bool = False) -> Path:
    """Generate an ed25519 keypair at *key_path*.

    Args:
        key_path: Path for the **private** key.  The public key is written
                  to ``key_path.with_suffix('.pub')``.
        force: If ``True`` overwrite an existing keypair.

    Returns:
        Path to the private key file.

    Raises:
        FileExistsError: If the key already exists and *force* is ``False``.
    """
    pub_path = key_path.with_suffix(".pub")

    if key_path.exists() and not force:
        raise FileExistsError(
            f"Key already exists: {key_path}\n"
            "Use --force to overwrite."
        )

    key_path.parent.mkdir(parents=True, exist_ok=True)

    key = _create_ed25519_key()
    key.write_private_key_file(str(key_path))

    # Set 0600 on POSIX (not enforceable on Windows)
    if platform.system() != "Windows":
        key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    # Write public key in OpenSSH format
    pub_line = f"{key.get_name()} {key.get_base64()}"
    pub_path.write_text(pub_line + "\n", encoding="utf-8")

    return key_path


def _read_public_key(key_path: Path) -> str:
    """Read the public key string from the ``.pub`` companion file."""
    pub_path = key_path.with_suffix(".pub")
    return pub_path.read_text(encoding="utf-8").strip()


# ---------------------------------------------------------------------------
# Remote provisioning helpers
# ---------------------------------------------------------------------------


def deploy_public_key(
    client: paramiko.SSHClient,
    public_key: str,
    service_user: str,
    os_type: Literal["linux", "freebsd"] = "linux",
) -> None:
    """Create the service account and deploy *public_key* on the remote host.

    Runs as the bootstrap (admin) user.  Uses ``useradd`` on Linux and
    ``pw useradd`` on FreeBSD.
    """
    # Create the user (ignore: user may already exist)
    if os_type == "freebsd":
        create_cmd = (
            f"pw useradd {service_user} -m -s /bin/sh "
            f"-d /home/{service_user} 2>/dev/null || true"
        )
    elif os_type == "linux":
        create_cmd = (
            f"useradd -m -s /bin/bash {service_user} 2>/dev/null || true"
        )
    else:
        raise ValueError(f"Unsupported os_type: {os_type!r}")
    run_command(client, f"sudo {create_cmd}")

    # Deploy authorized_keys
    ssh_dir = f"/home/{service_user}/.ssh"
    commands = [
        f"sudo mkdir -p {ssh_dir}",
        f"sudo tee {ssh_dir}/authorized_keys <<'MCPEOF'\n{public_key}\nMCPEOF",
        f"sudo chmod 700 {ssh_dir}",
        f"sudo chmod 600 {ssh_dir}/authorized_keys",
        f"sudo chown -R {service_user}:{service_user} {ssh_dir}",
    ]
    for cmd in commands:
        result = run_command(client, cmd)
        if result.exit_code != 0:
            raise RuntimeError(
                f"Failed deploying public key — command: {cmd!r}\n"
                f"stderr: {result.stderr}"
            )


def apply_role(
    client: paramiko.SSHClient,
    role: RoleTemplate,
    service_user: str,
) -> None:
    """Apply role permissions on the remote host.

    Adds the service user to required groups and writes a sudoers
    drop-in file validated with ``visudo -cf``.
    """
    # Add to groups
    for group in role.groups:
        result = run_command(
            client,
            f"sudo usermod -aG {group} {service_user}",
        )
        if result.exit_code != 0:
            raise RuntimeError(
                f"Failed to add {service_user} to group {group}: "
                f"{result.stderr}"
            )

    # Write sudoers drop-in
    sudoers_content = role.sudoers_file_content(service_user)
    sudoers_path = "/etc/sudoers.d/mcp-homelab"

    # Write to a temp file, validate, then move into place
    tmp_sudoers = "/tmp/mcp-homelab-sudoers"
    write_result = run_command(
        client,
        f"sudo tee {tmp_sudoers} <<'MCPEOF'\n{sudoers_content}\nMCPEOF",
    )
    if write_result.exit_code != 0:
        raise RuntimeError(
            f"Failed to write sudoers file: {write_result.stderr}"
        )

    # Validate with visudo
    check_result = run_command(
        client,
        f"sudo visudo -cf {tmp_sudoers}",
    )
    if check_result.exit_code != 0:
        # Clean up and fail
        run_command(client, f"sudo rm -f {tmp_sudoers}")
        raise RuntimeError(
            f"Sudoers validation failed: {check_result.stderr}"
        )

    # Move validated file into place with correct permissions
    run_command(client, f"sudo chmod 440 {tmp_sudoers}")
    move_result = run_command(
        client,
        f"sudo mv {tmp_sudoers} {sudoers_path}",
    )
    if move_result.exit_code != 0:
        raise RuntimeError(
            f"Failed to install sudoers file: {move_result.stderr}"
        )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_connection(ip: str, service_user: str, key_path: Path) -> bool:
    """Test SSH connectivity with the newly provisioned credentials.

    Returns ``True`` on success, ``False`` on failure.
    """
    try:
        client = connect(ip, service_user, str(key_path))
        result = run_command(client, "echo ok")
        client.close()
        return result.exit_code == 0 and result.stdout.strip() == "ok"
    except SSHConnectError:
        return False


# ---------------------------------------------------------------------------
# Manual (offline) mode
# ---------------------------------------------------------------------------


def print_manual_instructions(
    hostname: str,
    public_key: str,
    role: RoleTemplate,
    service_user: str,
    os_type: Literal["linux", "freebsd"] = "linux",
) -> None:
    """Print step-by-step commands for manual provisioning."""
    print(f"\n{'=' * 60}")
    print(f"Manual provisioning commands for: {hostname}")
    print(f"{'=' * 60}\n")

    step = 1
    print(f"{step}. Create the service account:")
    if os_type == "freebsd":
        print(f"   sudo pw useradd {service_user} -m -s /bin/sh -d /home/{service_user}\n")
    else:
        print(f"   sudo useradd -m -s /bin/bash {service_user}\n")

    step += 1
    print(f"{step}. Deploy the SSH public key:")
    print(f"   sudo mkdir -p /home/{service_user}/.ssh")
    print(f"   echo '{public_key}' | sudo tee /home/{service_user}/.ssh/authorized_keys")
    print(f"   sudo chmod 700 /home/{service_user}/.ssh")
    print(f"   sudo chmod 600 /home/{service_user}/.ssh/authorized_keys")
    print(f"   sudo chown -R {service_user}:{service_user} /home/{service_user}/.ssh\n")

    if role.groups:
        step += 1
        print(f"{step}. Add to groups:")
        for group in role.groups:
            print(f"   sudo usermod -aG {group} {service_user}")
        print()

    if role.sudoers:
        step += 1
        print(f"{step}. Write sudoers drop-in:")
        print("   sudo tee /etc/sudoers.d/mcp-homelab <<'EOF'")
        print(role.sudoers_file_content(service_user), end="")
        print("EOF")
        print("   sudo chmod 440 /etc/sudoers.d/mcp-homelab")
        print("   sudo visudo -cf /etc/sudoers.d/mcp-homelab\n")

    step += 1
    print(f"{step}. Test the connection from your workstation:")
    print(f"   ssh -i <private-key-path> {service_user}@<host-ip>\n")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_ssh_provisioning(
    hostname: str,
    bootstrap_user: str | None = None,
    manual: bool = False,
    role_name: str | None = None,
    service_user: str = "mcp-homelab",
    key_dir: Path | None = None,
    force: bool = False,
) -> None:
    """Provision an SSH service account on *hostname*.

    If *bootstrap_user* is set, uses automated mode (SSH into host,
    create user, deploy key, apply role).  If *manual* is ``True``,
    generates the keypair locally and prints commands.

    Raises:
        ValueError: If neither ``bootstrap_user`` nor ``manual`` is set.
        KeyError: If *role_name* is not a valid built-in role.
        FileNotFoundError: If *hostname* is not in ``config.yaml``.
    """
    if not bootstrap_user and not manual:
        raise ValueError(
            "Must specify either --bootstrap-user (automated) or --manual."
        )

    if not _SERVICE_USER_RE.match(service_user):
        raise ValueError(
            f"Invalid service_user: {service_user!r}. "
            "Must match [a-z_][a-z0-9_-]* (lowercase, no spaces or special chars)."
        )

    # Resolve key directory
    effective_key_dir = key_dir or _DEFAULT_KEY_DIR
    key_path = effective_key_dir / hostname

    # Load host config for IP and OS
    from core.config import load_config

    config = load_config()
    if hostname not in config.hosts:
        available = ", ".join(sorted(config.hosts))
        raise FileNotFoundError(
            f"Host {hostname!r} not found in config.yaml. "
            f"Available: {available}"
        )

    host_cfg = config.hosts[hostname]
    host_ip = host_cfg.ip
    os_type = host_cfg.os

    # Resolve role — use role_name if given, else infer from host type
    role: RoleTemplate | None = None
    if role_name:
        role = get_role(role_name)

    # ---- Generate keypair ----
    print(f"Generating ed25519 keypair at: {key_path}")
    generate_keypair(key_path, force=force)
    public_key = _read_public_key(key_path)
    print(f"  Key generated: {key_path}")

    if manual:
        # ---- Manual mode ----
        if role:
            print_manual_instructions(hostname, public_key, role, service_user, os_type=os_type)
        else:
            # Minimal instructions without role
            print_manual_instructions(
                hostname,
                public_key,
                RoleTemplate(name="(none)", description="No role"),
                service_user,
                os_type=os_type,
            )

        # Update config.yaml with new ssh_user + ssh_key_path
        _update_config(hostname, host_cfg, service_user, key_path)
        print(f"Updated config.yaml: {hostname}.ssh_user = {service_user}")
        return

    # ---- Automated mode (bootstrap_user guaranteed non-None by guard above) ----

    # Get bootstrap credentials from config
    bootstrap_key = host_cfg.ssh_key_path
    if not bootstrap_key:
        raise ValueError(
            f"Host {hostname!r} has no ssh_key_path in config.yaml. "
            "Cannot connect as bootstrap user."
        )

    print(f"Connecting to {host_ip} as {bootstrap_user}...")
    client = connect(host_ip, bootstrap_user, bootstrap_key)

    try:
        print(f"Creating service account: {service_user}")
        deploy_public_key(client, public_key, service_user, os_type=os_type)

        if role:
            print(f"Applying role: {role.name}")
            apply_role(client, role, service_user)
    finally:
        client.close()

    # ---- Verify ----
    print(f"Verifying connectivity as {service_user}...")
    if verify_connection(host_ip, service_user, key_path):
        print("  Verification successful!")
    else:
        print(
            "  WARNING: Verification failed. The account may not be "
            "fully provisioned. Check the target host manually.",
            file=sys.stderr,
        )

    # Update config.yaml
    _update_config(hostname, host_cfg, service_user, key_path)
    print(f"Updated config.yaml: {hostname}.ssh_user = {service_user}")


def _update_config(
    hostname: str,
    host_cfg: HostConfig,
    service_user: str,
    key_path: Path,
) -> None:
    """Update config.yaml with the new service account credentials."""
    from core.config import get_config_dir

    config_path = get_config_dir() / "config.yaml"

    # Build the updated node data preserving existing fields
    node_data = host_cfg.model_dump(exclude_none=True)
    node_data["ssh_user"] = service_user
    node_data["ssh_key_path"] = str(key_path)

    upsert_node(config_path, hostname, node_data)
