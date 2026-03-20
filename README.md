# mcp-homelab

An [MCP](https://modelcontextprotocol.io/) server that gives AI assistants real-time access to your homelab infrastructure. Connect it to Claude Desktop, VS Code Copilot, or any MCP-compatible client and your assistant can query node status, manage Docker containers, control Proxmox VMs, and inspect OPNsense firewall state — all through natural conversation.

## Why

AI assistants are useful for infrastructure work, but they can't see your environment. You end up copy-pasting `docker ps` output, describing your network layout, and manually feeding context. mcp-homelab fixes that by giving the assistant direct access to live infrastructure data over SSH and REST APIs.

**What it supports:**
- **Any Linux host** accessible via SSH (bare-metal, VMs, LXC containers)
- **Docker** container listing, logs, and restart
- **Proxmox VE** VM and LXC container management (optional)
- **OPNsense** firewall inspection (optional)

Proxmox and OPNsense are optional — the server works with just SSH-accessible hosts.

## Requirements

- **Python 3.10+**
- **SSH key access** to at least one Linux host
- **MCP client**: Claude Desktop, VS Code with Copilot, or any MCP-compatible client
- *(Optional)* Proxmox VE with an API token
- *(Optional)* OPNsense with API key/secret

## Quick Start

```bash
git clone git@github.com:asvarnon/mcp-homelab.git
cd mcp-homelab
python -m venv .venv && .venv/Scripts/activate  # or source .venv/bin/activate on Linux/macOS
pip install -e .
mcp-homelab init                # creates config.yaml + .env from templates
# Edit config.yaml with your hosts, .env with API credentials
mcp-homelab setup check         # validates config and tests connectivity
mcp-homelab setup client        # auto-configures Claude Desktop / VS Code
```

That's it. Your MCP client will now spawn the server automatically when it needs homelab data.

## Tools

| Category  | Tool                   | Description                                                     |
| --------- | ---------------------- | --------------------------------------------------------------- |
| Discovery | `scan_infrastructure`  | Full topology snapshot (all nodes, VMs, containers, interfaces) |
| Discovery | `generate_context`     | Auto-generate Markdown docs from live scan data                 |
| Discovery | `list_context_files`   | Manifest of all files in the context directory                  |
| Nodes     | `list_nodes`           | All configured nodes from config                                |
| Nodes     | `get_node_status`      | Uptime, CPU, RAM, disk via SSH                                  |
| Nodes     | `list_containers`      | Docker containers on a node                                     |
| Nodes     | `get_container_logs`   | Last N lines of container logs                                  |
| Nodes     | `restart_container`    | Restart a container                                             |
| Proxmox   | `list_vms`             | All VMs with status and resources                               |
| Proxmox   | `get_vm_status`        | Detailed status for one VM                                      |
| Proxmox   | `start_vm`             | Start a stopped VM                                              |
| Proxmox   | `stop_vm`              | Gracefully stop a VM                                            |
| Proxmox   | `list_lxc`             | All LXC containers with status and resources                    |
| Proxmox   | `get_lxc_status`       | Detailed status for one LXC container                           |
| Proxmox   | `start_lxc`            | Start a stopped LXC container                                   |
| Proxmox   | `stop_lxc`             | Gracefully stop an LXC container                                |
| Proxmox   | `create_lxc`           | Create a new LXC container from a template                      |
| Proxmox   | `get_next_vmid`        | Next available VM/CT ID from the cluster                        |
| Proxmox   | `list_storage`         | Storage pools with capacity info                                |
| Proxmox   | `list_templates`       | Available OS templates for LXC creation                         |
| OPNsense  | `get_dhcp_leases`      | Active DHCP leases                                              |
| OPNsense  | `get_interface_status` | Interface state                                                 |
| OPNsense  | `get_firewall_aliases` | Alias definitions                                               |

## Detailed Setup

The Quick Start above covers the basics. This section has details for specific integrations.

### Configure

```bash
cp config.yaml.example config.yaml
cp .env.example .env
# Edit config.yaml with your hosts, then .env with API credentials
```

### Proxmox API token (optional)

In the Proxmox web UI (`https://your-pve-host:8006`):

1. **Datacenter → Permissions → API Tokens → Add**
2. **User:** your PVE user (e.g. `admin@pam`)
3. **Token ID:** `mcp-homelab` (or any label)
4. **Privilege Separation:** Uncheck (token inherits user permissions)
5. Copy the token secret — it's shown only once

Add to `.env`:
```
PROXMOX_TOKEN_ID=admin@pam!mcp-homelab
PROXMOX_TOKEN_SECRET=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

The Proxmox host/port/SSL settings are in `config.yaml` under the `proxmox:` section. You can also configure LXC creation defaults:

```yaml
proxmox:
  host: "10.10.10.50"
  port: 8006
  verify_ssl: false
  default_node: pve           # preferred node for storage/template queries
  default_storage: local      # rootfs storage for create_lxc ("local" = dir, "local-lvm" = LVM thin)
  default_bridge: vmbr0       # network bridge for create_lxc
```

### Node access

Each node's SSH credentials are defined in `config.yaml` (per-node `ssh_user` and `ssh_key_path` fields). Edit these to match your environment.

#### Docker sudo (if required)

If the SSH user is **not** in the `docker` group, set `sudo_docker: true` for that node in `config.yaml`. This requires **passwordless sudo** for the docker binary on the target host:

```bash
# On the remote node:
echo '<username> ALL=(ALL) NOPASSWD: /usr/bin/docker' | sudo tee /etc/sudoers.d/docker-nopasswd
sudo chmod 440 /etc/sudoers.d/docker-nopasswd
sudo visudo -cf /etc/sudoers.d/docker-nopasswd   # must print "parsed OK"
```

If the SSH user is already in the `docker` group, set `sudo_docker: false` (the default) and no sudoers changes are needed.

#### Hardware memory details (optional)

`generate_context` and `scan_infrastructure` gather hardware specs via SSH. Most data comes from unprivileged commands (`lscpu`, `/proc/meminfo`, `lsblk`), but **per-DIMM memory details** (type, speed, manufacturer) require `dmidecode`, which needs root access.

If you want memory module details in your generated docs, grant passwordless sudo for dmidecode:

```bash
# On each remote node:
echo '<username> ALL=(ALL) NOPASSWD: /usr/sbin/dmidecode' | sudo tee /etc/sudoers.d/dmidecode-nopasswd
sudo chmod 440 /etc/sudoers.d/dmidecode-nopasswd
sudo visudo -cf /etc/sudoers.d/dmidecode-nopasswd   # must print "parsed OK"
```

Without this, everything else still works — the memory modules section is simply omitted from generated docs.

### MCP client connection

The `setup client` command auto-configures Claude Desktop and VS Code to use your server. It detects your venv Python, resolves paths, and merges the entry into existing config files without overwriting other servers.

```bash
# Interactive — picks Claude Desktop, VS Code, or both
mcp-homelab setup client

# Preview what would be written (no changes)
mcp-homelab setup client --dry-run
```

This writes the correct stdio transport entry including the `MCP_HOMELAB_CONFIG_DIR` env var, so the server can find `config.yaml` and `.env` regardless of the client's working directory.

<details>
<summary>Manual configuration (if you prefer editing JSON directly)</summary>

**Claude Desktop** — edit `%APPDATA%/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "homelab": {
      "command": "C:/path/to/mcp-homelab/.venv/Scripts/python.exe",
      "args": ["C:/path/to/mcp-homelab/server.py"],
      "env": {
        "MCP_HOMELAB_CONFIG_DIR": "C:/path/to/mcp-homelab"
      }
    }
  }
}
```

**VS Code (Copilot)** — add `.vscode/mcp.json` in your workspace:

```json
{
  "servers": {
    "homelab": {
      "command": "C:/path/to/mcp-homelab/.venv/Scripts/python.exe",
      "args": ["C:/path/to/mcp-homelab/server.py"],
      "env": {
        "MCP_HOMELAB_CONFIG_DIR": "C:/path/to/mcp-homelab"
      }
    }
  }
}
```

The `MCP_HOMELAB_CONFIG_DIR` env var is required — without it, MCP clients that spawn from a different working directory won't find your config files.
</details>

### OAuth client lockdown (hosted mode)

When running in HTTP mode, mcp-homelab uses OAuth 2.1 for authentication. By default, it accepts Dynamic Client Registration (DCR) from any client. To restrict access to a single pre-registered client, set these env vars in `.env`:

```bash
# Generate credentials (both must be ≥32 characters)
python -c "import secrets; print(secrets.token_urlsafe(48))"

# Add to .env
MCP_CLIENT_ID=<generated-value>
MCP_CLIENT_SECRET=<generated-value>
```

When both are set, this static client is pre-registered alongside Dynamic Client Registration (DCR). To restrict which clients can register dynamically, set `MCP_ALLOWED_REDIRECT_ORIGINS` (comma-separated origins). When connecting from Claude Desktop, enter these values in the OAuth Client ID / Client Secret prompt.

If neither is set, the server uses open DCR (backward compatible, suitable for trusted LANs).

### Hosted mode (multi-client)

Want to run mcp-homelab as a shared service accessible from multiple machines, Claude.ai, or mobile? See the [Hosted Mode Guide](guides/hosted-mode.md) for the full workflow, including the platform-specific [Proxmox LXC + Cloudflare Tunnel](guides/proxmox-cloudflare-tunnel.md) reference architecture.

### Running directly

```bash
python server.py
```

The server starts on stdio transport (standard for MCP). Normally you don't run this directly — the MCP client spawns it automatically.

### Custom context notes

mcp-homelab generates infrastructure docs into `context/generated/` from live scan data. If you also have your own curated notes (architecture docs, runbooks, etc.) that you want your AI assistant to reference, **don't put them in this project**.

Instead, connect your documentation workspace to the MCP client directly:

- **Claude Desktop / ChatGPT:** Add a separate filesystem MCP server pointing at your docs folder
- **VS Code / Copilot:** Add your docs folder to the workspace — the agent sees all workspace files automatically

This keeps concerns separated: mcp-homelab handles live infrastructure state, your docs workspace handles curated knowledge, and the MCP client aggregates both.

## Testing

Unit tests use pytest with full mocking — no real infrastructure required.

### Install test dependencies

```bash
pip install pytest pytest-asyncio
```

### Run tests

```bash
# All tests
python -m pytest tests/ -v

# Single module
python -m pytest tests/unit/test_node_parsers.py -v

# Single test class
python -m pytest tests/unit/test_ssh.py::TestConnect -v

# Single test
python -m pytest tests/unit/test_config.py::TestBootstrapConfigDir::test_sets_env_when_unset -v
```

### Test structure

```
tests/
├── conftest.py                  # Shared fixtures (sample configs, mock SSH, env helpers)
└── unit/
    ├── test_config.py           # Config loading, bootstrap, env validation, Pydantic models
    ├── test_ssh.py              # Connection caching, stale eviction, credential resolution
    ├── test_node_parsers.py     # All SSH output parsers (uptime, CPU, memory, disk, Docker,
    │                            #   lscpu, meminfo, lsblk, dmidecode, labels, rounding)
    ├── test_proxmox_tools.py    # VM/LXC tools, storage, templates, node discovery, input validation
    ├── test_opnsense_tools.py   # DHCP leases, interfaces, firewall aliases
    ├── test_prompts.py          # Input validation (IP, int, path, yes/no, node names)
    ├── test_config_writer.py    # YAML round-trip, .env upserts, section preservation
    └── test_client_setup.py     # JSONC stripping, OS detection, atomic writes, config merging
```

## Project Structure

```
mcp-homelab/
├── server.py              # MCP entry point — registers all tools
├── tools/
│   ├── discovery.py       # Composite scan tool for agent bootstrapping
│   ├── context_gen.py     # Documentation generation from scan data
│   ├── nodes.py           # SSH-based node tools + output parsers
│   ├── proxmox.py         # Proxmox REST API tools
│   └── opnsense.py        # OPNsense REST API tools
├── core/
│   ├── config.py          # Config loader, bootstrap, env var accessors
│   ├── ssh.py             # SSH connection manager (paramiko)
│   ├── proxmox_api.py     # Proxmox REST API client (httpx)
│   └── opnsense_api.py    # OPNsense REST API client (httpx)
├── mcp_homelab/
│   ├── cli.py             # CLI entry point (init, serve, setup)
│   └── setup/
│       ├── prompts.py     # Validated input prompts
│       ├── config_writer.py # YAML/env round-trip writer
│       ├── node_setup.py  # Interactive node configuration
│       ├── client_setup.py # MCP client config writer (Claude Desktop, VS Code)
│       ├── check.py       # Read-only health check
│       └── ssh_helpers.py # SSH test + capability detection
├── tests/                 # pytest unit tests (see Testing section)
├── config.yaml.example    # Example host definitions (copy to config.yaml)
├── .env.example           # Required env vars template
├── requirements.txt       # Runtime dependencies
├── pyproject.toml         # Package metadata + pytest config
└── Dockerfile             # Container packaging
```

## Future Updates

- **Dockerfile packaging** — current Dockerfile is a stub; needs design work for stdio transport
- **FreeBSD memory accuracy** — BSD memory reporting could include inactive/cached pages for more precise used-memory values
- **Config validation warnings** — detect likely misconfigurations (e.g., `sudo_docker` without `docker`) at startup
