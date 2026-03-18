# Hosted Mode — Multi-Client MCP Server

Run mcp-homelab as a persistent service on a dedicated machine so multiple clients (Claude Desktop, VS Code, Claude.ai, mobile) can connect over HTTP.

---

## Local vs. Hosted

| Aspect             | Local (default)                               | Hosted                                            |
| ------------------ | --------------------------------------------- | ------------------------------------------------- |
| Transport          | stdio                                         | HTTP                                              |
| Auth               | None (process-level)                          | OAuth 2.1 (PKCE, dynamic client registration)     |
| Process management | Client spawns server                          | systemd service                                   |
| Network access     | localhost only                                | LAN or internet-reachable                         |
| Clients            | One (the spawning client)                     | Many, each points at server URL                   |
| Config             | `mcp-homelab setup client` writes stdio entry | Each client connects to `https://your-server-url` |

---

## Step-by-Step: Local to Hosted

### Step 1 — Set up locally first (existing CLI)

Complete the standard setup on the machine that will host the server. Everything below assumes you've already done this:

```bash
git clone git@github.com:asvarnon/mcp-homelab.git
cd mcp-homelab
python -m venv .venv && source .venv/bin/activate
pip install -e .
mcp-homelab init
```

| Command            | What it does                               | Status     |
| ------------------ | ------------------------------------------ | ---------- |
| `mcp-homelab init` | Generates `config.yaml` + `.env` templates | **Exists** |

### Step 2 — Configure your infrastructure (existing CLI)

Add hosts, API credentials, and SSH keys using the interactive setup commands:

```bash
mcp-homelab setup node                # Add each SSH-accessible host
mcp-homelab setup proxmox             # Configure Proxmox API (optional)
mcp-homelab setup opnsense            # Configure OPNsense API (optional)
mcp-homelab setup ssh --host <name>   # Provision SSH service account per host
mcp-homelab setup check               # Validate all connections
```

| Command                      | What it does                                                                      | Status     |
| ---------------------------- | --------------------------------------------------------------------------------- | ---------- |
| `mcp-homelab setup node`     | Interactive: add hostname, IP, SSH user, key path, detect Docker/Proxmox/OPNsense | **Exists** |
| `mcp-homelab setup proxmox`  | Interactive: Proxmox IP, port, API token → writes config.yaml + .env              | **Exists** |
| `mcp-homelab setup opnsense` | Interactive: OPNsense IP, API key/secret → writes config.yaml + .env              | **Exists** |
| `mcp-homelab setup ssh`      | Generate SSH keypair, create service account on target, apply role-based sudoers  | **Exists** |
| `mcp-homelab setup check`    | Read-only validation: SSH connectivity, Docker, Proxmox API, OPNsense API         | **Exists** |

### Step 3 — Install as a service

Convert the local installation into a systemd service running in HTTP mode:

```bash
sudo mcp-homelab install --public-url "https://mcp.example.com"
```

Or omit `--public-url` and the command will prompt for it interactively.

| Command               | What it does                                                                                      | Status     |
| --------------------- | ------------------------------------------------------------------------------------------------- | ---------- |
| `mcp-homelab install` | Creates service user, installs systemd unit, switches transport to HTTP, enables + starts service | **Exists** |

#### What `install` automates (10 steps)

1. Verify Linux + root privileges
2. Detect install path (from running code location)
3. Create `mcp` service user (`useradd --system --create-home`)
4. Set file ownership (`chown -R mcp:mcp`)
5. Resolve public HTTPS URL (from `--public-url` arg or interactive prompt)
6. Update `config.yaml` — `transport: http`, `host: 0.0.0.0`, `port: 8000`, `public_url`
7. Render and install systemd unit (`deploy/mcp-homelab.service` template)
8. `systemctl daemon-reload` + `enable` + `start`
9. Verify service is active
10. Print next steps (network accessibility, guides reference)

#### What `install` does NOT do

- Create the machine (VM, LXC, bare-metal provisioning)
- Install/configure Cloudflare Tunnel, Tailscale, Caddy, nginx, etc.
- Set up DNS records
- Configure firewall rules
- Generate SSL certificates

### Step 4 — Make the server reachable (platform-specific)

This is where the [platform guides](./) come in. Choose your method:

| Method                      | Guide                                                        | Use case                                      |
| --------------------------- | ------------------------------------------------------------ | --------------------------------------------- |
| Cloudflare Tunnel           | [proxmox-cloudflare-tunnel.md](proxmox-cloudflare-tunnel.md) | Public internet access, no port forwarding    |
| Tailscale / WireGuard       | *(not yet written)*                                          | Private mesh network access                   |
| Reverse proxy (Caddy/nginx) | *(not yet written)*                                          | LAN-only or with your own TLS                 |
| Direct LAN                  | No guide needed                                              | Just use `http://<server-ip>:8000` as the URL |

### Step 5 — Connect clients (existing CLI)

Point each MCP client at the server's HTTP URL:

```bash
# On each client machine — use --url for remote HTTP mode:
mcp-homelab setup client --url "https://mcp.example.com"

# Or omit --url for local stdio mode (the default):
mcp-homelab setup client
```

| Command                                | What it does                                             | Status     |
| -------------------------------------- | -------------------------------------------------------- | ---------- |
| `mcp-homelab setup client`             | Writes stdio transport entry (local server)              | **Exists** |
| `mcp-homelab setup client --url <url>` | Writes HTTP transport entry (remote server at given URL) | **Exists** |

For Claude.ai (web/mobile), no client config is needed — add the server as a custom MCP integration at `https://claude.ai/settings/integrations` using your public URL.

### Step 6 — Validate

```bash
mcp-homelab setup check               # On the server — validates config + connectivity
```

For remote validation, connect from a client and run a tool (e.g., `list_nodes`).

---

## Function Scope Summary

### Existing (no changes needed)

| Function                  | CLI Command                  | Coverage |
| ------------------------- | ---------------------------- | -------- |
| Generate config templates | `mcp-homelab init`           | Full     |
| Add SSH hosts             | `mcp-homelab setup node`     | Full     |
| Configure Proxmox API     | `mcp-homelab setup proxmox`  | Full     |
| Configure OPNsense API    | `mcp-homelab setup opnsense` | Full     |
| Provision SSH accounts    | `mcp-homelab setup ssh`      | Full     |
| Validate config           | `mcp-homelab setup check`    | Full     |
| Start server (dev)        | `mcp-homelab serve`          | Full     |

### Newly implemented

| Function                   | CLI Command                            | Scope                                                                |
| -------------------------- | -------------------------------------- | -------------------------------------------------------------------- |
| Install as systemd service | `mcp-homelab install`                  | Service user, systemd unit, transport switch, enable + start         |
| HTTP client config         | `mcp-homelab setup client --url <url>` | Write HTTP transport entry to client config (vs. current stdio-only) |

### Out of scope for CLI (documentation only)

| Function                                    | Where documented                                      |
| ------------------------------------------- | ----------------------------------------------------- |
| Create infrastructure (LXC, VM, bare-metal) | Platform guides                                       |
| Network tunneling (Cloudflare, Tailscale)   | Platform guides                                       |
| DNS / TLS / firewall                        | Platform guides                                       |
| Proxmox LXC creation                        | `create_lxc` MCP tool (AI-assisted) or platform guide |

---

## deploy.py Disposition

`deploy.py` combined generic install steps with environment-specific provisioning (Cloudflare Tunnel, Proxmox LXC bootstrap). With the new structure:

| deploy.py responsibility  | New owner                                                             |
| ------------------------- | --------------------------------------------------------------------- |
| Install system packages   | User runs `apt-get install` per platform guide (or prereqs in README) |
| Create service user       | `mcp-homelab install`                                                 |
| Clone repo + venv + pip   | User follows README Quick Start                                       |
| Write config.yaml / .env  | `mcp-homelab init` + `setup *` commands                               |
| Install systemd unit      | `mcp-homelab install`                                                 |
| Start service             | `mcp-homelab install`                                                 |
| Cloudflare Tunnel setup   | [proxmox-cloudflare-tunnel.md](proxmox-cloudflare-tunnel.md) guide    |
| Proxmox LXC SSH bootstrap | [proxmox-cloudflare-tunnel.md](proxmox-cloudflare-tunnel.md) guide    |

**Action:** Now that `mcp-homelab install` exists, `deploy.py` can be moved to `examples/deploy-reference.py` as a historical reference. The `deploy/mcp-homelab.service` template stays — the `install` command reads it.

> **Note:** The systemd unit at `deploy/mcp-homelab.service` assumes `/opt/mcp-homelab/` as the install path. If installing elsewhere, edit the `WorkingDirectory`, `EnvironmentFile`, and `ExecStart` paths before copying.
