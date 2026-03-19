# Guides

Deployment and integration guides for hosting mcp-homelab beyond the default local setup.

The [Quick Start](../README.md#quick-start) in the main README covers the default path — local stdio transport with a single MCP client. The guides in this directory cover **hosted mode**: running the server as a service on a dedicated machine, reachable by multiple clients over HTTP.

## Available Guides

| Guide                                                             | Description                                                                                                         |
| ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| [Hosted Mode Overview](hosted-mode.md)                            | What changes when you go from local to hosted, CLI commands for each step, and what `mcp-homelab install` automates |
| [Proxmox VM + Cloudflare Tunnel](proxmox-vm-cloudflare-tunnel.md) | **Recommended:** deploy to a Proxmox VM with full systemd sandboxing and Cloudflare Tunnel                          |
| [Proxmox LXC + Cloudflare Tunnel](proxmox-cloudflare-tunnel.md)   | Lightweight alternative: deploy to a Proxmox LXC container (sandbox directives stripped automatically)              |
| [Docker Deployment](docker-deployment.md)                         | Container-based deployment with Docker Compose                                                                      |

## Adding Your Own Guide

If you deploy mcp-homelab on a different platform (bare-metal Raspberry Pi, Docker, Tailscale, etc.), add a guide here following the same pattern:

1. Prerequisites specific to your platform
2. Step-by-step setup instructions
3. Validation steps
4. Troubleshooting table
