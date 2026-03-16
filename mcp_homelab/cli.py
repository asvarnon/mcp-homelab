"""CLI entry point for mcp-homelab.

Provides subcommands:
    mcp-homelab serve           — Start the MCP server (stdio transport)
    mcp-homelab init            — Generate config.yaml and .env templates in cwd
    mcp-homelab setup node      — Add or reconfigure a host
    mcp-homelab setup check     — Validate existing config (read-only)
    mcp-homelab setup client    — Configure an MCP client (Claude Desktop, VS Code)
    mcp-homelab setup proxmox   — Configure Proxmox VE API connection
    mcp-homelab setup opnsense  — Configure OPNsense API connection
"""

from __future__ import annotations

import argparse
import logging
import sys
from importlib.metadata import version as pkg_version
from pathlib import Path

_CONFIG_TEMPLATE = """\
# mcp-homelab configuration
# Secrets (API keys, tokens, SSH credentials) are loaded from environment variables.
# See .env for required vars.

hosts:
  # Add your SSH-accessible hosts here.
  # Example:
  # my-server:
  #   hostname: my-server
  #   ip: "192.168.1.100"
  #   vlan: 1
  #   ssh: true
  #   ssh_user: admin
  #   ssh_key_path: "~/.ssh/id_ed25519"
  #   sudo_docker: false
  #   description: "My Linux server"
  #   type: baremetal  # optional: baremetal, vm, container

proxmox:
  host: "0.0.0.0"       # Replace with your Proxmox IP
  port: 8006
  verify_ssl: false

opnsense:
  host: "0.0.0.0"       # Replace with your OPNsense IP
  verify_ssl: false

"""

_ENV_TEMPLATE = """\
# mcp-homelab environment variables
# Fill in real values. NEVER commit this file.

# --- Proxmox API ---
PROXMOX_TOKEN_ID=
PROXMOX_TOKEN_SECRET=

# --- OPNsense API ---
OPNSENSE_API_KEY=
OPNSENSE_API_SECRET=
"""


def _cmd_init(args: argparse.Namespace) -> None:
    """Generate config.yaml and .env in the current directory."""
    cwd = Path.cwd()
    created: list[str] = []

    config_path = cwd / "config.yaml"
    if config_path.exists():
        print(f"  skip  {config_path} (already exists)")
    else:
        config_path.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
        created.append(str(config_path))
        print(f"  created  {config_path}")

    env_path = cwd / ".env"
    if env_path.exists():
        print(f"  skip  {env_path} (already exists)")
    else:
        env_path.write_text(_ENV_TEMPLATE, encoding="utf-8")
        created.append(str(env_path))
        print(f"  created  {env_path}")

    if created:
        print("\nEdit these files with your infrastructure details, then run:")
        print("  mcp-homelab serve")
    else:
        print("\nConfig files already exist. Edit them if needed, then run:")
        print("  mcp-homelab serve")


def _configure_logging(args: argparse.Namespace) -> None:
    """Set up logging based on --verbose / --debug flags."""
    if getattr(args, "debug", False):
        level = logging.DEBUG
        fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    elif getattr(args, "verbose", False):
        level = logging.INFO
        fmt = "%(levelname)s %(message)s"
    else:
        return  # no logging config — default (WARNING) applies

    logging.basicConfig(level=level, format=fmt, stream=sys.stderr)

    # In debug mode, also enable paramiko and httpx transport logging
    if level == logging.DEBUG:
        logging.getLogger("paramiko").setLevel(logging.DEBUG)
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        logging.getLogger("httpcore").setLevel(logging.DEBUG)


def _cmd_serve(args: argparse.Namespace) -> None:
    """Start the MCP server."""
    _configure_logging(args)

    from core.config import bootstrap_config_dir, load_env, validate_env

    bootstrap_config_dir(Path(__file__).resolve().parent.parent)
    load_env()
    validate_env()

    from server import mcp

    mcp.run()


def _cmd_setup(args: argparse.Namespace) -> None:
    """Route setup subcommands."""
    sub = args.setup_command

    if sub == "node":
        from mcp_homelab.setup.node_setup import run_node_setup
        run_node_setup(name=args.name)
    elif sub == "check":
        from mcp_homelab.setup.check import run_check
        run_check()
    elif sub == "client":
        from mcp_homelab.setup.client_setup import run_client_setup
        run_client_setup(dry_run=args.dry_run)
    elif sub == "proxmox":
        from mcp_homelab.setup.proxmox_setup import run_proxmox_setup
        run_proxmox_setup()
    elif sub == "opnsense":
        from mcp_homelab.setup.opnsense_setup import run_opnsense_setup
        run_opnsense_setup()
    else:
        print("Usage: mcp-homelab setup {node,check,client,proxmox,opnsense}")
        sys.exit(1)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="mcp-homelab",
        description="MCP server for homelab infrastructure management",
    )
    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"%(prog)s {pkg_version('mcp-homelab')}",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("init", help="Generate config.yaml and .env templates in the current directory")

    serve_parser = subparsers.add_parser("serve", help="Start the MCP server (stdio transport)")
    serve_parser.add_argument("--verbose", "-v", action="store_true", help="Enable info-level logging to stderr")
    serve_parser.add_argument("--debug", action="store_true", help="Enable debug-level logging (includes paramiko/httpx)")

    # setup subcommand with its own sub-subcommands
    setup_parser = subparsers.add_parser("setup", help="Guided setup wizard for configuring nodes and integrations")
    setup_sub = setup_parser.add_subparsers(dest="setup_command")

    node_parser = setup_sub.add_parser("node", help="Add or reconfigure a node")
    node_parser.add_argument("name", nargs="?", default=None, help="Node name (prompted if omitted)")

    setup_sub.add_parser("check", help="Validate existing config (read-only health check)")

    client_parser = setup_sub.add_parser("client", help="Configure an MCP client (Claude Desktop, VS Code)")
    client_parser.add_argument("--dry-run", action="store_true", help="Print config snippet without writing")

    setup_sub.add_parser("proxmox", help="Configure Proxmox VE API connection")
    setup_sub.add_parser("opnsense", help="Configure OPNsense API connection")

    args = parser.parse_args()

    try:
        if args.command == "init":
            _cmd_init(args)
        elif args.command == "serve":
            _cmd_serve(args)
        elif args.command == "setup":
            _cmd_setup(args)
        else:
            parser.print_help()
            sys.exit(1)
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
    except (FileNotFoundError, EnvironmentError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # Catch-all for unexpected errors — show the message without a traceback
        # unless --debug was used (in which case logging already captured it)
        if getattr(args, "debug", False):
            raise
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
