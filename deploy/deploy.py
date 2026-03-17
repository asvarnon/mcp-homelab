from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy mcp-homelab to an existing LXC container over SSH.",
    )
    parser.add_argument("--host", required=True, help="Target LXC IP address")
    parser.add_argument(
        "--token",
        required=False,
        default=None,
        help="Bearer token for MCP auth (min 32 chars). Falls back to MCP_DEPLOY_TOKEN env var.",
    )
    parser.add_argument(
        "--branch",
        default="develop",
        help="Git branch to clone or pull",
    )
    parser.add_argument(
        "--ssh-key",
        default=str(Path.home() / ".ssh" / "mcp-server-bootstrap"),
        help="Path to SSH private key",
    )
    parser.add_argument(
        "--ssh-user",
        default="root",
        help="SSH user on target",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Server port",
    )
    parser.add_argument(
        "--repo-url",
        default="https://github.com/asvarnon/mcp-homelab.git",
        help="Git repository URL",
    )
    parser.add_argument(
        "--pve-host",
        default=None,
        help="Proxmox VE host IP/hostname for LXC SSH bootstrap",
    )
    parser.add_argument(
        "--pve-user",
        default=None,
        help="SSH user on Proxmox VE host (required when --pve-host is set)",
    )
    parser.add_argument(
        "--pve-key",
        default=None,
        help="Path to SSH private key for Proxmox VE host (required when --pve-host is set)",
    )
    parser.add_argument(
        "--vmid",
        type=int,
        default=100,
        help="LXC container VMID on Proxmox VE",
    )
    return parser.parse_args()


def _run_command(command: list[str], description: str) -> subprocess.CompletedProcess[str]:
    result: subprocess.CompletedProcess[str] = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        print(f"ERROR: {description}", file=sys.stderr)
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        elif result.stdout:
            print(result.stdout.strip(), file=sys.stderr)
        sys.exit(result.returncode)
    return result


def _build_ssh_command(
    host: str,
    ssh_key: Path,
    ssh_user: str,
    remote_command: str,
) -> list[str]:
    return [
        "ssh",
        "-T",
        "-i",
        str(ssh_key),
        "-o",
        "StrictHostKeyChecking=accept-new",
        f"{ssh_user}@{host}",
        remote_command,
    ]


def _run_ssh_command(
    host: str,
    ssh_key: Path,
    ssh_user: str,
    remote_command: str,
    description: str,
) -> str:
    command: list[str] = _build_ssh_command(host, ssh_key, ssh_user, remote_command)
    result: subprocess.CompletedProcess[str] = _run_command(command, description)
    return result.stdout.strip()


def _transfer_file(
    host: str,
    ssh_key: Path,
    ssh_user: str,
    content: str,
    remote_path: str,
) -> None:
    encoded_content: str = base64.b64encode(content.encode("utf-8")).decode("ascii")
    remote_path_quoted: str = shlex.quote(remote_path)
    remote_command: str = f"echo {encoded_content} | base64 -d > {remote_path_quoted}"
    _run_ssh_command(
        host,
        ssh_key,
        ssh_user,
        remote_command,
        f"transfer file to {remote_path}",
    )


def _ensure_ssh_key(ssh_key: Path) -> None:
    if ssh_key.exists():
        print(f"SSH key found: {ssh_key}")
        return

    ssh_key.parent.mkdir(parents=True, exist_ok=True)
    print(f"SSH key not found, generating: {ssh_key}")
    _run_command(
        ["ssh-keygen", "-t", "ed25519", "-f", str(ssh_key), "-N", ""],
        "generate SSH key",
    )
    print("SSH key generated.")


def _mask_token(token: str) -> str:
    return f"{token[:4]}....{token[-4:]}"


def main() -> int:
    args: argparse.Namespace = parse_args()

    token: str | None = args.token or os.environ.get("MCP_DEPLOY_TOKEN")
    if not token:
        print("ERROR: --token or MCP_DEPLOY_TOKEN env var is required", file=sys.stderr)
        return 2

    if len(token) < 32:
        print("ERROR: --token must be at least 32 characters", file=sys.stderr)
        return 2

    host: str = args.host
    branch: str = args.branch
    ssh_key: Path = Path(args.ssh_key).expanduser()
    ssh_user: str = args.ssh_user
    port: int = args.port
    repo_url: str = args.repo_url
    pve_host: str | None = args.pve_host
    pve_user: str | None = args.pve_user
    pve_key_str: str | None = args.pve_key
    vmid: int = args.vmid
    bootstrap_enabled: bool = pve_host is not None

    if bootstrap_enabled:
        missing_pve: list[str] = []
        if not pve_user:
            missing_pve.append("--pve-user")
        if not pve_key_str:
            missing_pve.append("--pve-key")
        if missing_pve:
            print(f"ERROR: {', '.join(missing_pve)} required when --pve-host is set", file=sys.stderr)
            return 2

    pve_key: Path = Path(pve_key_str).expanduser() if pve_key_str else Path()
    # Narrow types after validation — guaranteed non-None when bootstrap_enabled
    pve_user_resolved: str = pve_user or ""
    total_steps: int = 12 if bootstrap_enabled else 11

    if "\n" in token or "\r" in token:
        print("ERROR: --token must not contain newline characters", file=sys.stderr)
        return 2

    if bootstrap_enabled:
        print(f"Step 0/{total_steps}: Bootstrapping SSH in LXC via Proxmox")
        bootstrap_inner_command: str = (
            "apt-get update && apt-get install -y openssh-server && "
            "systemctl enable --now ssh"
        )
        vmid_quoted: str = shlex.quote(str(vmid))
        bootstrap_command: str = (
            f"sudo pct exec {vmid_quoted} -- bash -c {shlex.quote(bootstrap_inner_command)}"
        )
        _run_ssh_command(
            pve_host,
            pve_key,
            pve_user_resolved,
            bootstrap_command,
            f"bootstrap openssh-server in LXC VMID {vmid}",
        )
        print(f"Bootstrap complete for VMID {vmid} via PVE host {pve_host}.")

    print(f"Step 1/{total_steps}: Checking SSH key")
    _ensure_ssh_key(ssh_key)

    print(f"Step 2/{total_steps}: Verifying SSH connectivity")
    hostname_output: str = _run_ssh_command(
        host,
        ssh_key,
        ssh_user,
        "hostname",
        "verify SSH connectivity",
    )
    print(f"SSH connectivity OK. Remote hostname: {hostname_output}")

    print(f"Step 3/{total_steps}: Installing system packages")
    _run_ssh_command(
        host,
        ssh_key,
        ssh_user,
        "apt-get update && apt-get install -y python3 python3-pip python3-venv git",
        "install system packages",
    )

    print(f"Step 4/{total_steps}: Creating service user")
    _run_ssh_command(
        host,
        ssh_key,
        ssh_user,
        "id -u mcp >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin mcp",
        "create mcp service user",
    )

    print(f"Step 5/{total_steps}: Cloning or updating repository")
    branch_quoted: str = shlex.quote(branch)
    repo_url_quoted: str = shlex.quote(repo_url)
    repo_command: str = (
        "if [ ! -d /opt/mcp-homelab/.git ]; then "
        f"git clone -b {branch_quoted} {repo_url_quoted} /opt/mcp-homelab; "
        "else "
        "cd /opt/mcp-homelab && git fetch && "
        f"git checkout {branch_quoted} && git pull; "
        "fi"
    )
    _run_ssh_command(host, ssh_key, ssh_user, repo_command, "clone or update repository")

    print(f"Step 6/{total_steps}: Creating virtual environment and installing dependencies")
    _run_ssh_command(
        host,
        ssh_key,
        ssh_user,
        "python3 -m venv /opt/mcp-homelab/.venv",
        "create virtual environment",
    )
    _run_ssh_command(
        host,
        ssh_key,
        ssh_user,
        "/opt/mcp-homelab/.venv/bin/pip install -r /opt/mcp-homelab/requirements.txt",
        "install Python dependencies",
    )

    print(f"Step 7/{total_steps}: Writing config.yaml")
    config_content: str = (
        "server:\n"
        "  transport: http\n"
        f"  host: \"{host}\"\n"
        f"  port: {port}\n"
        "\n"
        "hosts: {}\n"
    )
    _transfer_file(
        host,
        ssh_key,
        ssh_user,
        config_content,
        "/opt/mcp-homelab/config.yaml",
    )

    print(f"Step 8/{total_steps}: Writing .env")
    env_content: str = f"MCP_BEARER_TOKEN={token}\n"
    _transfer_file(
        host,
        ssh_key,
        ssh_user,
        env_content,
        "/opt/mcp-homelab/.env",
    )
    _run_ssh_command(
        host,
        ssh_key,
        ssh_user,
        "chown -R mcp:mcp /opt/mcp-homelab && chmod 400 /opt/mcp-homelab/.env",
        "set ownership and permissions",
    )

    print(f"Step 9/{total_steps}: Installing systemd unit")
    service_path: Path = Path(__file__).resolve().parent / "mcp-homelab.service"
    service_content: str = service_path.read_text(encoding="utf-8")
    _transfer_file(
        host,
        ssh_key,
        ssh_user,
        service_content,
        "/etc/systemd/system/mcp-homelab.service",
    )
    _run_ssh_command(
        host,
        ssh_key,
        ssh_user,
        "systemctl daemon-reload",
        "reload systemd daemon",
    )
    _run_ssh_command(
        host,
        ssh_key,
        ssh_user,
        "systemctl enable mcp-homelab",
        "enable mcp-homelab service",
    )

    print(f"Step 10/{total_steps}: Starting service")
    _run_ssh_command(
        host,
        ssh_key,
        ssh_user,
        "systemctl restart mcp-homelab",
        "restart mcp-homelab service",
    )
    time.sleep(2)
    service_state: str = _run_ssh_command(
        host,
        ssh_key,
        ssh_user,
        "systemctl is-active mcp-homelab",
        "verify mcp-homelab service status",
    )
    print(f"Service state: {service_state}")
    logs_output: str = _run_ssh_command(
        host,
        ssh_key,
        ssh_user,
        "journalctl -u mcp-homelab -n 10 --no-pager",
        "fetch recent service logs",
    )
    print("Recent logs:")
    print(logs_output)

    print(f"Step 11/{total_steps}: Deployment summary")
    print(f"Server running at http://{host}:{port}/mcp")
    print(f"Bearer token: {_mask_token(token)}")
    print(f"Logs: ssh root@{host} journalctl -u mcp-homelab -f")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())