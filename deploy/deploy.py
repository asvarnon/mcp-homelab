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
        "--cf-tunnel-token",
        help="Cloudflare Tunnel connector token from Zero Trust dashboard (or set CF_TUNNEL_TOKEN)",
    )
    parser.add_argument(
        "--public-url",
        required=True,
        help="Public HTTPS URL for the server (e.g. https://mcp.example.com)",
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


def _format_host_for_url(host: str) -> str:
    """Wrap IPv6 hosts in brackets for URL/authority formatting."""
    if ":" in host and not host.startswith("[") and not host.endswith("]"):
        return f"[{host}]"
    return host


def main() -> int:
    args: argparse.Namespace = parse_args()

    host: str = args.host
    cf_tunnel_token: str = (args.cf_tunnel_token or os.environ.get("CF_TUNNEL_TOKEN", "")).strip()
    public_url: str = args.public_url.strip()
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

    if not cf_tunnel_token:
        print("ERROR: set --cf-tunnel-token or CF_TUNNEL_TOKEN", file=sys.stderr)
        return 2

    if "\n" in cf_tunnel_token or "\r" in cf_tunnel_token:
        print("ERROR: --cf-tunnel-token must not contain newline characters", file=sys.stderr)
        return 2

    if not public_url:
        print("ERROR: --public-url must not be empty", file=sys.stderr)
        return 2

    if not public_url.startswith("https://"):
        print("ERROR: --public-url must start with https://", file=sys.stderr)
        return 2

    if '"' in public_url or "\n" in public_url or "\r" in public_url:
        print("ERROR: --public-url contains invalid characters", file=sys.stderr)
        return 2

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
    total_steps: int = 14 if bootstrap_enabled else 13
    current_step: int = 1

    print(f"Step {current_step}/{total_steps}: Checking SSH key")
    _ensure_ssh_key(ssh_key)
    current_step += 1

    if bootstrap_enabled:
        print(f"Step {current_step}/{total_steps}: Bootstrapping SSH in LXC via Proxmox")
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

        pub_key_content: str = ssh_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
        quoted_key: str = shlex.quote(pub_key_content)
        key_install_command: str = (
            f"sudo pct exec {vmid_quoted} -- bash -c "
            + shlex.quote(
                f"mkdir -p /root/.ssh && chmod 700 /root/.ssh && "
                f"grep -qF {quoted_key} /root/.ssh/authorized_keys 2>/dev/null || echo {quoted_key} >> /root/.ssh/authorized_keys && "
                f"chmod 600 /root/.ssh/authorized_keys"
            )
        )
        _run_ssh_command(
            pve_host,
            pve_key,
            pve_user_resolved,
            key_install_command,
            f"install SSH public key in LXC VMID {vmid}",
        )
        print(f"Bootstrap complete for VMID {vmid} via PVE host {pve_host}.")
        current_step += 1

    print(f"Step {current_step}/{total_steps}: Verifying SSH connectivity")
    hostname_output: str = _run_ssh_command(
        host,
        ssh_key,
        ssh_user,
        "hostname",
        "verify SSH connectivity",
    )
    print(f"SSH connectivity OK. Remote hostname: {hostname_output}")
    current_step += 1

    print(f"Step {current_step}/{total_steps}: Installing system packages")
    _run_ssh_command(
        host,
        ssh_key,
        ssh_user,
        "apt-get update && apt-get install -y python3 python3-pip python3-venv git curl gnupg ca-certificates",
        "install system packages",
    )
    current_step += 1

    print(f"Step {current_step}/{total_steps}: Installing cloudflared")
    _run_ssh_command(
        host,
        ssh_key,
        ssh_user,
        "curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | "
        "gpg --batch --yes --dearmor -o /usr/share/keyrings/cloudflare-main.gpg && "
        "echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] "
        "https://pkg.cloudflare.com/cloudflared any main' > "
        "/etc/apt/sources.list.d/cloudflared.list && "
        "apt-get update && apt-get install -y cloudflared",
        "install cloudflared",
    )
    current_step += 1

    print(f"Step {current_step}/{total_steps}: Creating service user")
    _run_ssh_command(
        host,
        ssh_key,
        ssh_user,
        "id -u mcp >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin mcp",
        "create mcp service user",
    )
    current_step += 1

    print(f"Step {current_step}/{total_steps}: Cloning or updating repository")
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
    current_step += 1

    print(f"Step {current_step}/{total_steps}: Creating virtual environment and installing dependencies")
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
    current_step += 1

    print(f"Step {current_step}/{total_steps}: Writing config.yaml")
    config_content: str = (
        "server:\n"
        "  transport: http\n"
        "  host: \"0.0.0.0\"\n"
        f"  port: {port}\n"
        f"  public_url: \"{public_url}\"\n"
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
    current_step += 1

    print(f"Step {current_step}/{total_steps}: Writing .env")
    env_content: str = "MCP_HOMELAB_CONFIG_DIR=/opt/mcp-homelab\n"
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
    current_step += 1

    print(f"Step {current_step}/{total_steps}: Installing systemd unit")
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
    current_step += 1

    print(f"Step {current_step}/{total_steps}: Starting service")
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
    current_step += 1

    print(f"Step {current_step}/{total_steps}: Installing and starting cloudflared service")
    # Token is piped via stdin to avoid exposure in local process listings.
    # The remote cloudflared process briefly shows the token in its argv —
    # acceptable on a single-tenant root-only LXC (cloudflared has no
    # --token-file or env-var alternative for `service install`).
    cf_install_cmd: list[str] = _build_ssh_command(
        host,
        ssh_key,
        ssh_user,
        'read -r CF_TOKEN && cloudflared service install "$CF_TOKEN"',
    )
    cf_result: subprocess.CompletedProcess[str] = subprocess.run(
        cf_install_cmd,
        input=cf_tunnel_token + "\n",
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    # `cloudflared service install` creates the systemd unit AND starts it.
    # In LXC containers the default QUIC transport fails (kernel UDP buffer
    # limits are too low), so the initial start may time out — that's expected.
    # Log the result so token/auth errors aren't silently swallowed on re-runs.
    if cf_result.returncode != 0:
        stderr_msg: str = (cf_result.stderr or cf_result.stdout or "").strip()
        print(f"WARN: cloudflared service install exited {cf_result.returncode}: {stderr_msg}")
    # Verify the unit file was written, then patch it to force HTTP/2.
    _run_ssh_command(
        host,
        ssh_key,
        ssh_user,
        "test -f /etc/systemd/system/cloudflared.service",
        "verify cloudflared service file was created",
    )
    # Force HTTP/2 — QUIC fails in LXC due to restricted UDP buffer sizes
    # (see https://github.com/quic-go/quic-go/wiki/UDP-Buffer-Sizes).
    _run_ssh_command(
        host,
        ssh_key,
        ssh_user,
        "sed -i 's|--no-autoupdate tunnel run|--no-autoupdate --protocol http2 tunnel run|' "
        "/etc/systemd/system/cloudflared.service && "
        "systemctl daemon-reload && "
        "systemctl enable --now cloudflared",
        "patch cloudflared to use HTTP/2 and restart",
    )
    current_step += 1

    print(f"Step {current_step}/{total_steps}: Deployment summary")
    summary_host: str = _format_host_for_url(host)
    print(f"MCP server:     http://{summary_host}:{port}/ (local)")
    print(f"Public URL:     {public_url}")
    print(f"Tunnel status:  ssh {ssh_user}@{host} systemctl status cloudflared")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())