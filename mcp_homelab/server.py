"""mcp-homelab — MCP server entry point.

Registers all tools from the tools/ package and serves them
via the Anthropic MCP Python SDK.

Can be run directly (``python server.py``) or via the CLI
(``mcp-homelab serve``).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Bootstrap config dir from server.py location so MCP clients
# that spawn from a foreign cwd can find config.yaml and .env.
from mcp_homelab.core.config import bootstrap_config_dir
bootstrap_config_dir(Path(__file__).resolve().parent.parent)

logger = logging.getLogger(__name__)

from mcp.server.fastmcp import FastMCP

from mcp_homelab.tools import nodes, proxmox, opnsense, discovery, context_gen
from mcp_homelab.tools.nodes import NodeSummary, NodeStatus, ContainerInfo
from mcp_homelab.tools.proxmox import VmSummary, VmStatus, VmCreateResult

mcp = FastMCP("homelab")

# ---------------------------------------------------------------------------
# Node tools (SSH)
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_nodes() -> list[NodeSummary]:
    """List all configured homelab nodes. Call this first to discover valid node names for other tools."""
    return await nodes.list_nodes()


@mcp.tool()
async def get_node_status(hostname: str) -> NodeStatus:
    """Get uptime, CPU%, RAM, and disk usage for a homelab node. Use list_nodes to get valid hostname values."""
    return await nodes.get_node_status(hostname)


@mcp.tool()
async def list_containers(hostname: str) -> list[ContainerInfo]:
    """List Docker containers running on a homelab node. Use list_nodes to get valid hostname values."""
    return await nodes.list_containers(hostname)


@mcp.tool()
async def get_container_logs(hostname: str, container: str, lines: int = 50) -> str:
    """Get the last N lines of logs from a Docker container. Use list_nodes to get valid hostname values."""
    return await nodes.get_container_logs(hostname, container, lines)


@mcp.tool()
async def restart_container(hostname: str, container: str) -> str:
    """Restart a Docker container on a homelab node. Use list_nodes to get valid hostname values."""
    return await nodes.restart_container(hostname, container)


# ---------------------------------------------------------------------------
# Proxmox tools (REST API)
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_vms() -> list[VmSummary] | list[dict]:
    """List all VMs on the Proxmox hypervisor with status and resource info."""
    return await proxmox.list_vms()


@mcp.tool()
async def get_vm_status(vmid: int) -> VmStatus | dict:
    """Get detailed status for a specific Proxmox VM."""
    return await proxmox.get_vm_status(vmid)


@mcp.tool()
async def start_vm(vmid: int) -> str:
    """Start a stopped Proxmox VM."""
    return await proxmox.start_vm(vmid)


@mcp.tool()
async def stop_vm(vmid: int) -> str:
    """Gracefully stop a running Proxmox VM."""
    return await proxmox.stop_vm(vmid)


# ---------------------------------------------------------------------------
# Proxmox LXC tools (REST API)
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_lxc() -> list[proxmox.LxcSummary] | list[dict]:
    """List all LXC containers on the Proxmox hypervisor with status and resource info."""
    return await proxmox.list_lxc()


@mcp.tool()
async def get_lxc_status(vmid: int) -> proxmox.LxcStatus | dict:
    """Get detailed status for a specific Proxmox LXC container."""
    return await proxmox.get_lxc_status(vmid)


@mcp.tool()
async def start_lxc(vmid: int) -> str:
    """Start a stopped Proxmox LXC container."""
    return await proxmox.start_lxc(vmid)


@mcp.tool()
async def stop_lxc(vmid: int) -> str:
    """Gracefully stop a running Proxmox LXC container."""
    return await proxmox.stop_lxc(vmid)


# Subset of tools.proxmox.create_lxc params — password intentionally excluded from MCP surface
@mcp.tool()
async def create_lxc(
    node: str,
    ostemplate: str,
    hostname: str | None = None,
    vmid: int | None = None,
    cores: int = 1,
    memory_mb: int = 512,
    swap_mb: int = 512,
    disk_gb: int = 4,
    storage: str | None = None,
    bridge: str | None = None,
    vlan_tag: int | None = None,
    ip_config: str = "ip=dhcp",
    ssh_public_key: str | None = None,
    unprivileged: bool = True,
    start_after_create: bool = False,
) -> proxmox.LxcCreateResult | dict:
    """Create a new LXC container on Proxmox. Use list_templates and list_storage to discover valid ostemplate and storage values first."""
    return await proxmox.create_lxc(
        node=node,
        ostemplate=ostemplate,
        hostname=hostname,
        vmid=vmid,
        cores=cores,
        memory_mb=memory_mb,
        swap_mb=swap_mb,
        disk_gb=disk_gb,
        storage=storage,
        bridge=bridge,
        vlan_tag=vlan_tag,
        ip_config=ip_config,
        ssh_public_key=ssh_public_key,
        unprivileged=unprivileged,
        start_after_create=start_after_create,
    )


@mcp.tool()
async def create_vm(
    node: str,
    iso: str,
    name: str | None = None,
    vmid: int | None = None,
    cores: int = 1,
    sockets: int = 1,
    cpu_type: str = "host",
    memory_mb: int = 1024,
    disk_gb: int = 16,
    storage: str | None = None,
    bridge: str | None = None,
    vlan_tag: int | None = None,
    ostype: str = "l26",
    scsihw: str = "virtio-scsi-single",
    balloon: int = 0,
    start_after_create: bool = False,
) -> VmCreateResult | dict:
    """Create a new VM on Proxmox. Use list_storage to discover valid storage values. ISOs are at local:iso/<filename>."""
    return await proxmox.create_vm(
        node=node,
        iso=iso,
        name=name,
        vmid=vmid,
        cores=cores,
        sockets=sockets,
        cpu_type=cpu_type,
        memory_mb=memory_mb,
        disk_gb=disk_gb,
        storage=storage,
        bridge=bridge,
        vlan_tag=vlan_tag,
        ostype=ostype,
        scsihw=scsihw,
        balloon=balloon,
        start_after_create=start_after_create,
    )


@mcp.tool()
async def get_next_vmid() -> int | dict:
    """Get the next available VM/CT ID from the Proxmox cluster."""
    return await proxmox.get_next_vmid()


@mcp.tool()
async def list_storage(node: str | None = None) -> list[proxmox.StorageInfo] | list[dict]:
    """List available storage on a Proxmox node with capacity info."""
    return await proxmox.list_storage(node)


@mcp.tool()
async def list_templates(node: str | None = None, storage: str | None = None) -> list[proxmox.TemplateInfo] | list[dict]:
    """List available OS templates for LXC container creation on a Proxmox node."""
    return await proxmox.list_templates(node, storage)


# ---------------------------------------------------------------------------
# OPNsense tools (REST API)
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_dhcp_leases() -> list[dict]:
    """Get active DHCP leases from OPNsense."""
    return await opnsense.get_dhcp_leases()


@mcp.tool()
async def get_interface_status() -> list[dict]:
    """Get interface names, IPs, and up/down state from OPNsense."""
    return await opnsense.get_interface_status()


@mcp.tool()
async def get_firewall_aliases() -> list[dict]:
    """Get firewall alias definitions from OPNsense."""
    return await opnsense.get_firewall_aliases()


@mcp.tool()
async def check_ip_available(ip: str) -> opnsense.IpAvailabilityResult | dict:
    """Check if an IP is available by cross-referencing OPNsense DHCP leases and config.yaml hosts.

    Requires OPNsense to be configured. Returns which data sources were checked
    and any warnings if a source was unavailable."""
    return await opnsense.check_ip_available(ip)


# ---------------------------------------------------------------------------
# Discovery tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def scan_infrastructure() -> dict:
    """Scan the entire homelab and return a topology snapshot. Call this first to understand what nodes, VMs, containers, and network interfaces exist. Subsystems that are unreachable will show an error instead of failing the whole scan."""
    return await discovery.scan_infrastructure()


@mcp.tool()
async def generate_context() -> dict:
    """Generate or refresh a documentation workspace from live infrastructure data. Creates Markdown files under context/generated/. Previous versions are archived automatically. Run this after infrastructure changes to keep docs current."""
    scan = await discovery.scan_infrastructure()
    return await context_gen.generate_context(scan)


@mcp.tool()
async def list_context_files() -> dict:
    """List all files in the context directory. Returns a manifest with file paths, sizes, and last-modified dates. Use this to discover what documentation exists — both generated infrastructure docs and any user-curated content — before reading specific files."""
    return await context_gen.list_context_files()


def start_server() -> None:
    """Configure transport from config and start the MCP server.

    Called by both ``mcp-homelab serve`` (CLI) and ``python server.py``
    (dev shim).  Reads config.yaml to decide between stdio and HTTP
    transport, wiring OAuth 2.1 auth when HTTP mode is active.
    """
    from mcp_homelab.core.config import (
        load_config,
        load_env,
        load_from_credentials_dir,
        validate_env,
    )

    load_env()
    load_from_credentials_dir()
    validate_env()

    config = load_config()
    if config.server.transport == "http":
        from pydantic import AnyHttpUrl

        from mcp.server.auth.provider import ProviderTokenVerifier
        from mcp.server.auth.settings import (
            AuthSettings,
            ClientRegistrationOptions,
            RevocationOptions,
        )
        from mcp.server.transport_security import TransportSecuritySettings
        from starlette.requests import Request
        from starlette.responses import Response

        from mcp_homelab.core.oauth_provider import HomelabOAuthProvider

        mcp.settings.host = config.server.host
        mcp.settings.port = config.server.port
        # Claude.ai posts to "/" not "/mcp" — serve the MCP endpoint at root.
        mcp.settings.streamable_http_path = "/"

        def _format_host_for_url(host: str) -> str:
            """Wrap IPv6 hosts in brackets for URL/authority formatting."""
            if ":" in host and not host.startswith("[") and not host.endswith("]"):
                return f"[{host}]"
            return host

        # Derive the public URL used for OAuth metadata and Host header
        # validation.  HTTPS public_url is expected once a TLS terminator
        # (Cloudflare Tunnel / Caddy) is in front of this server.
        formatted_host = _format_host_for_url(config.server.host)
        public_url = (
            str(config.server.public_url) if config.server.public_url
            else f"http://{formatted_host}:{config.server.port}"
        )

        # Override DNS rebinding protection to allow the configured host.
        # FastMCP's constructor auto-enables it for localhost only, but we
        # re-bind to a non-localhost address so we must update allowed_hosts.
        # When public_url is set (TLS terminator in front), also allow that
        # host so OAuth/CORS requests from clients using the public URL work.
        host_with_port = f"{formatted_host}:{config.server.port}"
        allowed_hosts = [host_with_port]
        allowed_origins = [f"http://{host_with_port}"]
        if config.server.public_url:
            from urllib.parse import urlparse
            parsed = urlparse(str(config.server.public_url))
            public_host = parsed.netloc  # includes port if present
            if public_host and public_host != host_with_port:
                allowed_hosts.append(public_host)
                scheme = parsed.scheme or "https"
                allowed_origins.append(f"{scheme}://{public_host}")
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        )

        # Pre-registered OAuth client credentials (set via .env or systemd
        # credentials). Dynamic Client Registration remains enabled so
        # additional clients can self-register.
        client_id = os.environ.get("MCP_CLIENT_ID") or None
        client_secret = os.environ.get("MCP_CLIENT_SECRET") or None
        raw_origins = os.environ.get("MCP_ALLOWED_REDIRECT_ORIGINS", "")
        allowed_redirect_origins: list[str] | None = None
        if raw_origins.strip():
            allowed_redirect_origins = [
                origin.strip().rstrip("/")
                for origin in raw_origins.split(",")
                if origin.strip()
            ]

        if not (client_id and client_secret):
            logger.warning(
                "MCP_CLIENT_ID / MCP_CLIENT_SECRET not set — no static "
                "OAuth client registered.  Only dynamically-registered "
                "clients will be able to authenticate.",
            )

        mcp.settings.auth = AuthSettings(
            issuer_url=AnyHttpUrl(public_url),
            resource_server_url=AnyHttpUrl(public_url),
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
            ),
            revocation_options=RevocationOptions(enabled=True),
        )

        # NOTE: _auth_server_provider and _token_verifier are private
        # ── Admin login gate ──────────────────────────────────────────
        # MCP_ADMIN_PASSWORD_HASH is strongly recommended in HTTP mode to
        # prevent unauthenticated OAuth auto-approve.  If unset, authorize()
        # falls back to auto-approve with a warning.  Generate with:
        #   python -c "import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())"
        admin_hash = os.environ.get("MCP_ADMIN_PASSWORD_HASH", "").strip()
        login_url: str | None = f"{public_url.rstrip('/')}/login" if admin_hash else None

        # FastMCP attributes.  Setting them post-construction is safe
        # because the SDK reads them at run() time when building Starlette
        # routes — not at construction.  Integration tests verify auth is
        # enforced end-to-end, so SDK renames will be caught immediately.
        provider = HomelabOAuthProvider(
            client_id=client_id,
            client_secret=client_secret,
            allowed_redirect_origins=allowed_redirect_origins,
            login_url=login_url,
        )
        mcp._auth_server_provider = provider
        mcp._token_verifier = ProviderTokenVerifier(provider)

        if admin_hash:
            from mcp_homelab.core.login import LoginHandler, validate_bcrypt_hash

            if not validate_bcrypt_hash(admin_hash):
                logger.error(
                    "MCP_ADMIN_PASSWORD_HASH is set but is not a valid bcrypt hash. "
                    "Generate one with: python -c \"import bcrypt; "
                    "print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())\"",
                )
                sys.exit(1)

            login_handler = LoginHandler(provider=provider, password_hash=admin_hash)

            @mcp.custom_route("/login", methods=["GET"])
            async def login_get(request: Request) -> Response:
                return await login_handler.handle_get(request)

            @mcp.custom_route("/login", methods=["POST"])
            async def login_post(request: Request) -> Response:
                return await login_handler.handle_post(request)

            logger.info("Admin login gate enabled for OAuth authorization")
        else:
            logger.warning(
                "MCP_ADMIN_PASSWORD_HASH is not set — OAuth authorization will "
                "auto-approve all requests. Anyone who can reach this server can "
                "obtain tokens. Set this var to require admin login: "
                "MCP_ADMIN_PASSWORD_HASH=<bcrypt hash>",
            )

        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    start_server()
