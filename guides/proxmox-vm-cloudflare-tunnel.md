# Proxmox VM + Cloudflare Tunnel

Deploy mcp-homelab to a Proxmox VM with Cloudflare Tunnel for public HTTPS access. This is the recommended production deployment — a full VM gives you systemd sandboxing (ProtectHome, ProtectSystem, PrivateTmp) that unprivileged LXC containers can't support.

> **Alternative:** If you prefer a lightweight container, see [proxmox-cloudflare-tunnel.md](proxmox-cloudflare-tunnel.md) (LXC variant — sandbox directives are stripped automatically).

---

## Architecture

```
Claude Desktop / Claude.ai / VS Code
        │
        ▼ HTTPS (OAuth 2.1)
┌──────────────────────────┐
│   Cloudflare Tunnel      │
│   mcp.your-domain.dev    │
└──────────┬───────────────┘
           │ http://localhost:8000
           ▼
┌──────────────────────────┐
│   VM (Ubuntu 24.04)      │
│   mcp-homelab service    │
│   User: mcp (systemd)    │
│   /opt/mcp-homelab/      │
└──────────┬───────────────┘
           │ SSH / REST API
           ▼
   ┌───────────────┐
   │  homelab hosts │
   │  (your hosts)  │
   └───────────────┘
```

---

## Prerequisites

| Requirement         | Details                                                      |
| ------------------- | ------------------------------------------------------------ |
| Proxmox VE          | Any recent version with storage for a VM disk                |
| Ubuntu 24.04 ISO    | Downloaded to Proxmox storage (or use cloud-init image)      |
| Cloudflare account  | Free tier works; you need a domain managed by Cloudflare DNS |
| SSH key for Proxmox | So you can SSH to the VM from your workstation               |
| Git + Python 3.10+  | Installed on the VM (Ubuntu 24.04 ships Python 3.12)         |

---

## Step 1 — Create the VM

Create a lightweight VM on Proxmox. The MCP server is I/O-bound (SSH + HTTP), not compute-heavy.

| Setting        | Value                          |
| -------------- | ------------------------------ |
| VM ID          | Pick next available (e.g. 102) |
| Name           | `mcp-homelab`                  |
| OS             | Ubuntu 24.04 Server ISO        |
| CPU            | 2 cores                        |
| RAM            | 2 GB                           |
| Disk           | 20 GB (local-lvm or similar)   |
| Network bridge | `vmbr0` (or your mgmt bridge)  |
| VLAN tag       | Your management VLAN (e.g. 10) |

Install Ubuntu with a regular admin user. Minimal server install is fine — no desktop, no snap.

After install, note the VM's IP address.

### Configure SSH access from your workstation

Add an entry to your local `~/.ssh/config` so you can `ssh mcp-homelab` easily:

```
Host mcp-homelab
    HostName <VM-IP>
    User <ADMIN-USER>
    IdentityFile ~/.ssh/your-key
```

Copy your public key:

```bash
ssh-copy-id -i ~/.ssh/your-key mcp-homelab
```

---

## Step 2 — Bootstrap the VM

SSH in and install prerequisites:

```bash
ssh mcp-homelab
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3-venv python3-pip
```

### Clone and install mcp-homelab

```bash
sudo mkdir -p /opt/mcp-homelab
sudo chown $USER:$USER /opt/mcp-homelab
git clone https://github.com/asvarnon/mcp-homelab.git /opt/mcp-homelab
cd /opt/mcp-homelab
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Initialize config

```bash
mcp-homelab init
```

This creates `config.yaml` and `.env` templates in `/opt/mcp-homelab/`.

---

## Step 3 — Configure hosts and credentials

### Edit config.yaml

Add your infrastructure hosts. Each host needs an IP, SSH user, and key path:

```yaml
hosts:
  my-server:
    hostname: my-server
    ip: "<HOST-IP>"
    vlan: 10
    ssh: true
    ssh_user: <SSH-USER>
    ssh_key_path: /opt/mcp-homelab/.ssh/mcp_id_ed25519
    description: "Example host"
    os: linux

  # Add more hosts as needed — see config.yaml reference in README

proxmox:
  host: "<PROXMOX-IP>"
  port: 8006
  verify_ssl: false

opnsense:
  host: "<OPNSENSE-IP>"
  verify_ssl: false
```

### Edit .env

Add your API secrets (never commit this file):

```bash
PROXMOX_TOKEN_ID=user@pam!token-name
PROXMOX_TOKEN_SECRET=your-secret-uuid
OPNSENSE_API_KEY=your-key
OPNSENSE_API_SECRET=your-secret

# Restrict which clients can register via OAuth (recommended)
# See DEPLOYMENT-GUIDE.md Phase 4.5 for details
MCP_ALLOWED_REDIRECT_ORIGINS=https://claude.ai,http://localhost
```

### Generate and distribute SSH keys

Generate a dedicated keypair for the MCP service:

```bash
ssh-keygen -t ed25519 -C "mcp-homelab-service" -f /opt/mcp-homelab/.ssh/mcp_id_ed25519 -N ""
```

Copy the public key to each target host:

```bash
ssh-copy-id -i /opt/mcp-homelab/.ssh/mcp_id_ed25519.pub user@target-host
```

Repeat for every host in your config.

---

## Step 4 — Install as a systemd service

This is the key step. `mcp-homelab install` automates 10 things: creates the `mcp` service user, sets ownership, updates config for HTTP transport, writes the systemd unit, and starts the service.

```bash
sudo /opt/mcp-homelab/.venv/bin/mcp-homelab install --public-url "https://mcp.your-domain.dev"
```

What it does:

1. Verifies Linux + root
2. Detects install path from code location
3. Creates `mcp` system user (nologin shell)
4. Sets `chown -R mcp:mcp /opt/mcp-homelab/`
5. Updates `config.yaml` → `transport: http`, `host: 0.0.0.0`, `port: 8000`
6. Writes systemd unit to `/etc/systemd/system/mcp-homelab.service`
7. Enables and starts the service

### Verify

```bash
sudo systemctl status mcp-homelab
curl -s http://localhost:8000/.well-known/oauth-authorization-server | head -1
```

You should see `active (running)` and JSON with OAuth endpoints.

---

## Step 5 — Fix SSH key permissions (ProtectHome gotcha)

**This is the most common issue with VM deployments.**

The systemd unit includes `ProtectHome=true`, which makes `/home/` appear empty to the `mcp` service user. If your SSH keys are in `/home/mcp/.ssh/`, the service **cannot read them**.

The fix: store keys under `/opt/mcp-homelab/.ssh/` (which is within `ReadWritePaths`):

```bash
# Create the key directory
sudo mkdir -p /opt/mcp-homelab/.ssh
sudo chown mcp:mcp /opt/mcp-homelab/.ssh
sudo chmod 700 /opt/mcp-homelab/.ssh

# Move or copy your keys there
sudo cp /home/mcp/.ssh/mcp_id_ed25519* /opt/mcp-homelab/.ssh/
sudo chown mcp:mcp /opt/mcp-homelab/.ssh/*
sudo chmod 600 /opt/mcp-homelab/.ssh/mcp_id_ed25519
sudo chmod 644 /opt/mcp-homelab/.ssh/mcp_id_ed25519.pub
```

Update **every** host entry in `config.yaml` to use the new path:

```yaml
ssh_key_path: /opt/mcp-homelab/.ssh/mcp_id_ed25519
```

> **Why not just disable ProtectHome?** It's a security hardening measure — the service has no business reading user home directories. Moving keys to the app directory is the correct fix.

Restart after the change:

```bash
sudo systemctl restart mcp-homelab
```

---

## Step 6 — Install Cloudflare Tunnel

### Install cloudflared

```bash
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install -y cloudflared
```

### Authenticate and create tunnel

```bash
cloudflared tunnel login          # Opens browser, authorizes your Cloudflare account
cloudflared tunnel create mcp-homelab
```

Note the tunnel UUID printed.

### Configure the tunnel

Create `/etc/cloudflared/config.yml`:

```yaml
tunnel: <TUNNEL-UUID>
credentials-file: /root/.cloudflared/<TUNNEL-UUID>.json

ingress:
  - hostname: mcp.your-domain.dev
    service: http://localhost:8000
  - service: http_status:404
```

### Add DNS record

```bash
cloudflared tunnel route dns mcp-homelab mcp.your-domain.dev
```

### Install as a service

```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

### Verify end-to-end

```bash
curl -s https://mcp.your-domain.dev/.well-known/oauth-authorization-server | head -1
```

You should see JSON with `issuer`, `authorization_endpoint`, `token_endpoint`, etc. If you get a connection error or 404, check that `cloudflared` is running and routing to `localhost:8000`.

---

## Step 7 — Connect clients

### Claude Desktop

1. On your workstation, install mcp-homelab (just for the CLI): `pip install mcp-homelab`
2. Run: `mcp-homelab setup client --url "https://mcp.your-domain.dev"`
3. Restart Claude Desktop
4. Complete the OAuth flow when prompted — if `MCP_CLIENT_ID` / `MCP_CLIENT_SECRET` are set on the server, enter those values in Claude Desktop's credential prompt

### Claude.ai (web/mobile)

1. Go to `https://claude.ai/settings/integrations`
2. Add custom MCP integration
3. Enter your server URL: `https://mcp.your-domain.dev`

---

## Troubleshooting

| Symptom                                      | Cause                                      | Fix                                                                       |
| -------------------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------- |
| Service starts then crashes in loop          | `public_url` in config.yaml uses `http://` | Change to `https://` — OAuth requires HTTPS                               |
| SSH tools return "Permission denied"         | `ProtectHome=true` blocks `/home/.ssh/`    | Move keys to `/opt/mcp-homelab/.ssh/` (Step 5)                            |
| Proxmox tools return 401                     | Wrong `PROXMOX_TOKEN_ID` in `.env`         | Token format is `user@pam!token-name` — verify in Proxmox UI → API Tokens |
| `curl localhost:8000` hangs                  | Service not running                        | `sudo systemctl status mcp-homelab` → check journal                       |
| Cloudflare tunnel 502                        | cloudflared can't reach localhost:8000     | Verify service is listening: `ss -tlnp \| grep 8000`                      |
| `build/` directory appears after pip install | Setuptools build artifact                  | Harmless, gitignored. Delete with `rm -rf build/`                         |

## Updating

To deploy new code from GitHub:

```bash
ssh mcp-homelab
cd /opt/mcp-homelab
git pull
source .venv/bin/activate
pip install -e .
sudo systemctl restart mcp-homelab
```

---

## File Layout Reference

After a complete deployment, `/opt/mcp-homelab/` looks like this:

```
/opt/mcp-homelab/
├── mcp_homelab/          ← Python package (source code, via git)
│   ├── cli.py            ← CLI entry point
│   ├── server.py         ← MCP server
│   ├── core/             ← SSH, config, API clients
│   ├── tools/            ← MCP tool implementations
│   ├── setup/            ← Setup wizards, install logic
│   └── data/             ← Bundled files (systemd template)
├── .venv/                ← Python virtual environment
│   └── bin/mcp-homelab   ← Entry point (calls cli.py:main)
├── config.yaml           ← Host definitions, server settings (gitignored)
├── .env                  ← API secrets (gitignored)
├── .ssh/                 ← SSH keys for target hosts
│   ├── mcp_id_ed25519
│   └── mcp_id_ed25519.pub
├── pyproject.toml        ← Build config
└── .git/                 ← Git repo (for updates via git pull)
```

### systemd unit location

`/etc/systemd/system/mcp-homelab.service` — auto-generated by `mcp-homelab install`.

### Key systemd sandbox directives (VM only)

| Directive                         | Effect                                                          |
| --------------------------------- | --------------------------------------------------------------- |
| `ProtectHome=true`                | `/home/` is empty to the service — keys must live under `/opt/` |
| `ProtectSystem=strict`            | Filesystem is read-only except `ReadWritePaths`                 |
| `ReadWritePaths=/opt/mcp-homelab` | The only writable path for the service                          |
| `PrivateTmp=true`                 | Isolated `/tmp` per service                                     |
| `NoNewPrivileges=true`            | Cannot escalate privileges                                      |
