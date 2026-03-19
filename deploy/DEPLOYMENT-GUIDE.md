# mcp-homelab HTTP Deployment Guide

Complete instructions for deploying mcp-homelab with OAuth 2.1 authentication and Cloudflare Tunnel to an LXC container, enabling Claude.ai (web + mobile) and other remote MCP clients to access your homelab infrastructure.

## Architecture Overview

```
Claude.ai / Mobile
       │
       ▼ HTTPS
┌──────────────────────┐
│  Cloudflare Tunnel   │  mcp.example.com → HTTP/2 → localhost:8000
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  LXC Container       │  Debian 12, VLAN 10
│  ┌────────────────┐  │
│  │  Uvicorn :8000 │  │  OAuth 2.1 auto-approve + MCP Streamable HTTP
│  │  mcp-homelab   │  │
│  └──────┬─────────┘  │
│         │ SSH/REST    │
└─────────┼────────────┘
          ▼
  ┌───────────────┐
  │ Infrastructure│  Proxmox VE, OPNsense, Docker hosts, VMs
  └───────────────┘
```

**Transport chain:** Claude.ai → HTTPS (Cloudflare edge) → HTTP/2 tunnel → Uvicorn (0.0.0.0:8000) → OAuth verification → MCP tool execution → SSH/REST to infrastructure → structured response

## Prerequisites

### Infrastructure

- **Proxmox VE** hypervisor with an LXC template (e.g., `debian-12-standard`)
- **Static IP** available on the management VLAN for the LXC
- **SSH access** to the Proxmox host (for LXC bootstrap — user must be `root` or have passwordless `sudo` for `pct exec`)
- **SSH access** to each infrastructure host you want tools to reach

### Cloudflare

- **Cloudflare account** with a domain managed by Cloudflare DNS
- **Zero Trust plan** (free tier works)
- **Tunnel** created in the Cloudflare Zero Trust dashboard with a public hostname route

### Credentials (gather before starting)

| Credential                  | Where to get it                                                    | Used for                                  |
| --------------------------- | ------------------------------------------------------------------ | ----------------------------------------- |
| **Cloudflare Tunnel token** | Zero Trust → Tunnels → your tunnel → Configure → Install connector | `CF_TUNNEL_TOKEN` env var for `deploy.py` |
| **Proxmox API token**       | Datacenter → Permissions → API Tokens → Add                        | `.env` on LXC                             |
| **OPNsense API key/secret** | System → Access → Users → API Keys                                 | `.env` on LXC                             |

### Developer machine

- **Python 3.10+**
- **SSH client** (OpenSSH)
- **Git** with access to the mcp-homelab repository

---

## Phase 1: Create the LXC Container

Create the LXC on Proxmox. You can use the Proxmox web UI, `pct create`, or the mcp-homelab `create_lxc` tool if you already have a working MCP instance.

Example via Proxmox CLI:
```bash
pct create <VMID> local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst \
  --hostname mcp-server \
  --cores 1 \
  --memory 1024 \
  --swap 512 \
  --rootfs local-lvm:4 \
  --net0 name=eth0,bridge=vmbr0,tag=<VLAN_ID>,ip=<LXC_IP>/24,gw=<GATEWAY_IP> \
  --unprivileged 1 \
  --start 1
```

**Key settings:**
- **Memory:** 1024 MB recommended (512 MB works but tight under load)
- **VLAN tag:** Match your management VLAN
- **Static IP:** Required — the deploy script needs a known IP
- **Start after create:** Yes — the container must be running for deploy

Verify the container is running and has network connectivity:
```bash
pct exec <VMID> -- ping -c1 8.8.8.8
```

---

## Phase 2: Create a Cloudflare Tunnel

> Skip this if you already have a tunnel configured.

1. Log into [Cloudflare Zero Trust](https://one.dash.cloudflare.com/)
2. Navigate to **Networks → Tunnels**
3. Click **Create a tunnel** → choose **Cloudflared** connector type
4. Name the tunnel (e.g., `mcp-homelab`)
5. On the connector install page, **copy the token** from the install command (the base64 blob after `cloudflared service install`)
6. Skip the actual install — `deploy.py` handles that
7. Click **Next** → add a **Public Hostname**:
   - **Subdomain:** `mcp` (or your preference)
   - **Domain:** select your domain
   - **Service:** `HTTP` → `localhost:8000`
8. Save the tunnel

**Save the token** — you'll need it for the deploy command. Treat it as a secret.

### Rotating or recreating the tunnel token

If you delete and recreate the tunnel, you get a **new token**. To update the LXC without a full redeploy:

```bash
# SSH to the LXC as root
cloudflared service uninstall

# Pass token via stdin to avoid shell history / process listing exposure
echo '<new-token>' | cloudflared service install "$(cat /dev/stdin)"
# Or: set CF_TUNNEL_TOKEN env var and use $CF_TUNNEL_TOKEN

# Patch for HTTP/2 (QUIC fails in LXC containers due to UDP buffer limits)
sed -i 's|--no-autoupdate tunnel run|--no-autoupdate --protocol http2 tunnel run|' \
  /etc/systemd/system/cloudflared.service

systemctl daemon-reload
systemctl restart cloudflared
```

---

## Phase 3: Run the Deploy Script

The deploy script automates: system package installation, cloudflared, repo clone, Python venv, systemd services, and Cloudflare Tunnel setup.

### First deploy (with Proxmox bootstrap)

On your **first deploy** to a fresh LXC, use `--pve-host` to bootstrap SSH access. This installs `openssh-server` in the LXC and injects your SSH key via `pct exec`.

```powershell
# Windows PowerShell
$env:CF_TUNNEL_TOKEN = "eyJhIjoi..."

python deploy/deploy.py `
  --host <LXC_IP> `
  --public-url "https://mcp.example.com" `
  --pve-host <PVE_IP> `
  --pve-user <PVE_SSH_USER> `
  --pve-key "$env:USERPROFILE\.ssh\<PVE_KEY>" `
  --vmid <VMID>
```

```bash
# Linux / macOS
export CF_TUNNEL_TOKEN="eyJhIjoi..."

python deploy/deploy.py \
  --host <LXC_IP> \
  --public-url "https://mcp.example.com" \
  --pve-host <PVE_IP> \
  --pve-user <PVE_SSH_USER> \
  --pve-key ~/.ssh/<PVE_KEY> \
  --vmid <VMID>
```

### Subsequent deploys (SSH already working)

If SSH to the LXC already works, omit the `--pve-*` flags:

```bash
python deploy/deploy.py \
  --host <LXC_IP> \
  --public-url "https://mcp.example.com"
```

### What deploy.py does (13 steps, 14 with bootstrap)

| Step | Action                                  | Notes                                                                  |
| ---- | --------------------------------------- | ---------------------------------------------------------------------- |
| 1    | Generate/locate SSH bootstrap key       | `~/.ssh/mcp-server-bootstrap`                                          |
| 2*   | Bootstrap SSH in LXC via PVE `pct exec` | Only with `--pve-host`. Installs openssh-server + injects key          |
| 3    | Verify SSH connectivity                 | Runs `hostname` on target                                              |
| 4    | Install system packages                 | python3, git, curl, gnupg, ca-certificates                             |
| 5    | Install cloudflared                     | Via Cloudflare apt repo                                                |
| 6    | Create `mcp` service user               | System user for running the service                                    |
| 7    | Clone/update repository                 | From GitHub, specified branch                                          |
| 8    | Create venv + install dependencies      | `/opt/mcp-homelab/.venv`                                               |
| 9    | Write `config.yaml`                     | Minimal: `transport: http`, `host: 0.0.0.0`, `public_url`, `hosts: {}` |
| 10   | Write `.env`                            | Minimal: `MCP_HOMELAB_CONFIG_DIR` only                                 |
| 11   | Install systemd unit                    | `mcp-homelab.service` → enabled                                        |
| 12   | Start service                           | `systemctl restart mcp-homelab` + verify active                        |
| 13   | Install cloudflared tunnel              | Token via stdin, HTTP/2 patch applied                                  |

### What deploy.py does NOT do

These are completed manually in Phase 4:

- ❌ Write host entries to `config.yaml` (SSH targets)
- ❌ Generate SSH keypair for the `mcp` service user
- ❌ Deploy `mcp` public key to infrastructure hosts
- ❌ Write Proxmox/OPNsense API credentials to `.env`
- ❌ Verify end-to-end tool connectivity

---

## Phase 4: Post-Deploy Configuration

After `deploy.py` completes, the server is running but has no SSH targets or API credentials. These steps configure it for your infrastructure.

### 4.1 Write the full `config.yaml`

Create a `config.yaml` on your dev machine with all host entries, then transfer it to the LXC.

Example `config.yaml`:
```yaml
server:
  transport: http
  host: "0.0.0.0"
  port: 8000
  public_url: "https://mcp.example.com"    # must match your Cloudflare hostname

hosts:
  my-server:
    hostname: my-server
    ip: 192.168.1.10
    vlan: 10
    ssh: true
    ssh_user: myuser
    ssh_key_path: /home/mcp/.ssh/id_ed25519
    docker: true                              # set true if Docker is installed
    sudo_docker: true                         # set true if ssh_user needs sudo for docker
    description: Example Linux server
    type: baremetal                            # baremetal, vm, or container
    os: linux                                 # linux or freebsd
  # Add more hosts as needed...

# Optional — only include if you have Proxmox VE
proxmox:
  host: "192.168.1.50"                        # Proxmox VE IP
  port: 8006
  verify_ssl: false
  default_node: pve                           # your Proxmox node name
  default_storage: local                      # local, local-lvm, etc.
  default_bridge: vmbr0

# Optional — only include if you have OPNsense
opnsense:
  host: "192.168.1.1"                         # OPNsense IP
  verify_ssl: false
```

Transfer to the LXC (base64 method — reliable across Windows/Linux):

```powershell
# PowerShell
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("config.yaml"))
ssh -i "$env:USERPROFILE\.ssh\mcp-server-bootstrap" root@<LXC_IP> "echo $b64 | base64 -d > /opt/mcp-homelab/config.yaml && chown mcp:mcp /opt/mcp-homelab/config.yaml"
```

```bash
# Linux / macOS
b64=$(base64 -w0 config.yaml 2>/dev/null || base64 config.yaml | tr -d '\n')
ssh -i ~/.ssh/mcp-server-bootstrap root@<LXC_IP> "echo $b64 | base64 -d > /opt/mcp-homelab/config.yaml && chown mcp:mcp /opt/mcp-homelab/config.yaml"
```

### 4.2 Generate SSH keypair for the `mcp` service user

The `mcp` user needs SSH access to each host in `config.yaml`.

```bash
# SSH to the LXC as root
ssh -i ~/.ssh/mcp-server-bootstrap root@<LXC_IP>

# Create home directory (deploy.py uses --no-create-home)
mkdir -p /home/mcp/.ssh
chown -R mcp:mcp /home/mcp
chmod 700 /home/mcp/.ssh

# Generate keypair
ssh-keygen -t ed25519 -f /home/mcp/.ssh/id_ed25519 -N "" -C "mcp@mcp-server"
chown mcp:mcp /home/mcp/.ssh/id_ed25519 /home/mcp/.ssh/id_ed25519.pub
chmod 600 /home/mcp/.ssh/id_ed25519

# Accept new host keys automatically (paramiko uses AutoAddPolicy, but
# also create SSH config for any manual debugging from the LXC)
cat > /home/mcp/.ssh/config << 'EOF'
Host *
    StrictHostKeyChecking accept-new
EOF
chown mcp:mcp /home/mcp/.ssh/config
chmod 600 /home/mcp/.ssh/config

# Print the public key — you'll need this for the next step
cat /home/mcp/.ssh/id_ed25519.pub
```

### 4.3 Deploy the public key to target hosts

For **each** host in your `config.yaml`, append the `mcp` user's public key to the SSH user's `authorized_keys`.

```bash
# Replace <pubkey> with the output from step 4.2
# Repeat for each host in your config.yaml

ssh <ssh_user>@<host_ip> 'mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo "<pubkey>" >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'
```

**Verify connectivity** from the LXC:
```bash
# SSH to LXC, then test as mcp user
ssh -i ~/.ssh/mcp-server-bootstrap root@<LXC_IP>
su -s /bin/bash - mcp -c "ssh -i /home/mcp/.ssh/id_ed25519 <ssh_user>@<host_ip> hostname"
```

### 4.4 Add API credentials to `.env`

Add your Proxmox API token and OPNsense API key to the `.env` file on the LXC.

```bash
# SSH to the LXC as root
ssh -i ~/.ssh/mcp-server-bootstrap root@<LXC_IP>

# Append credentials (use your actual values)
cat >> /opt/mcp-homelab/.env << 'EOF'
PROXMOX_TOKEN_ID=admin@pam!mcp-homelab
PROXMOX_TOKEN_SECRET=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
OPNSENSE_API_KEY=your-api-key-here
OPNSENSE_API_SECRET=your-api-secret-here
EOF

chmod 400 /opt/mcp-homelab/.env
chown mcp:mcp /opt/mcp-homelab/.env
```

### 4.5 Restart the service

```bash
systemctl restart mcp-homelab
sleep 2
systemctl is-active mcp-homelab   # should print "active"
journalctl -u mcp-homelab -n 20   # check for errors
```

> **Note:** Restarting the service **invalidates all OAuth tokens** (they're stored in memory). Any connected Claude.ai sessions will get 401 errors and must disconnect/reconnect the MCP server to trigger a fresh OAuth flow.

---

## Phase 5: Validate

### Connect Claude.ai

1. Go to [Claude.ai Settings → Integrations](https://claude.ai/settings/integrations) (or Claude mobile → Settings → Integrations)
2. Click **Add Integration** → **Custom MCP Server**
3. Enter your public URL (e.g., `https://mcp.example.com`)
4. Claude will perform the OAuth 2.1 flow automatically (Dynamic Client Registration → authorize → token exchange)
5. You should see all tools listed (scan_infrastructure, list_nodes, get_node_status, etc.)

### Run validation checks

From Claude.ai, ask the assistant to:

1. **`scan_infrastructure`** — should return nodes, VMs, DHCP leases, interfaces, firewall aliases
2. **`get_node_status`** for each configured host — verifies SSH connectivity
3. **`list_containers`** on Docker hosts — verifies Docker access
4. **`list_vms`** and **`list_lxc`** — verifies Proxmox API access
5. **`get_dhcp_leases`** — verifies OPNsense API access

---

## Troubleshooting

### Service won't start

```bash
journalctl -u mcp-homelab -n 50 --no-pager
```

| Error                                    | Cause                                            | Fix                                                                    |
| ---------------------------------------- | ------------------------------------------------ | ---------------------------------------------------------------------- |
| `Missing required environment variables` | `.env` missing API credentials                   | Add credentials per step 4.4                                           |
| `server.public_url is not set`           | Config has `host: "0.0.0.0"` but no `public_url` | Add `public_url` to config.yaml                                        |
| `ModuleNotFoundError`                    | venv not activated or deps missing               | `systemctl cat mcp-homelab` — verify ExecStart uses `.venv/bin/python` |

### Claude.ai can't connect

| Symptom                      | Cause                                     | Fix                                                                |
| ---------------------------- | ----------------------------------------- | ------------------------------------------------------------------ |
| Connection refused / timeout | Cloudflare Tunnel not running             | `systemctl status cloudflared`                                     |
| 404 on POST /                | `streamable_http_path` not set to `/`     | Verify `server.py` has `mcp.settings.streamable_http_path = "/"`   |
| 401 Unauthorized             | OAuth tokens expired or service restarted | Disconnect + reconnect MCP server in Claude settings               |
| 421 Invalid Host             | DNS rebinding protection mismatch         | Check `public_url` in config.yaml matches your Cloudflare hostname |

### SSH tools fail

| Symptom                    | Cause                                      | Fix                                                   |
| -------------------------- | ------------------------------------------ | ----------------------------------------------------- |
| All hosts unreachable      | `hosts: {}` in config.yaml                 | Write full config.yaml (step 4.1)                     |
| Auth failed                | mcp pubkey not in target's authorized_keys | Deploy key per step 4.3                               |
| Connection timeout         | Cross-VLAN routing not working             | Verify firewall allows LXC subnet → target subnet     |
| Permission denied (docker) | sudo_docker not set or sudoers missing     | Set `sudo_docker: true` + configure sudoers on target |

### Cloudflare Tunnel issues

```bash
journalctl -u cloudflared -n 50 --no-pager
```

| Symptom                          | Cause                                    | Fix                                                                                  |
| -------------------------------- | ---------------------------------------- | ------------------------------------------------------------------------------------ |
| QUIC timeout / connection failed | QUIC protocol fails in LXC               | Verify HTTP/2 patch: `grep 'protocol http2' /etc/systemd/system/cloudflared.service` |
| Invalid token                    | Tunnel deleted/recreated in CF dashboard | Reinstall with new token (see Phase 2)                                               |
| ERR Tunnel not found             | Tunnel UUID changed                      | Uninstall + reinstall cloudflared service                                            |

---

## Common Operations

### Update the server code

```bash
ssh -i ~/.ssh/mcp-server-bootstrap root@<LXC_IP>
cd /opt/mcp-homelab
git pull
.venv/bin/pip install -r requirements.txt   # if deps changed
systemctl restart mcp-homelab
```

### View logs

```bash
# MCP server logs
journalctl -u mcp-homelab -f

# Cloudflare Tunnel logs
journalctl -u cloudflared -f

# Last 50 lines
journalctl -u mcp-homelab -n 50 --no-pager
```

### Check service status

```bash
systemctl status mcp-homelab
systemctl status cloudflared
```

### Full redeploy

Re-run `deploy.py` — it's idempotent. It will `git pull` instead of re-cloning, reinstall deps, and restart services. Note: this **overwrites** `config.yaml` and `.env` with minimal versions. Back up your config first:

```bash
ssh -i ~/.ssh/mcp-server-bootstrap root@<LXC_IP> \
  "cp /opt/mcp-homelab/config.yaml /opt/mcp-homelab/config.yaml.bak && cp /opt/mcp-homelab/.env /opt/mcp-homelab/.env.bak"
```

Then re-run `deploy.py` and restore your config:

```bash
ssh -i ~/.ssh/mcp-server-bootstrap root@<LXC_IP> \
  "cp /opt/mcp-homelab/config.yaml.bak /opt/mcp-homelab/config.yaml && cp /opt/mcp-homelab/.env.bak /opt/mcp-homelab/.env && systemctl restart mcp-homelab"
```

---

## Security Notes

- **OAuth tokens are in-memory only** — service restart wipes all sessions. No persistent auth state on disk.
- **`.env` is chmod 400** — only the `mcp` user can read it. Contains API secrets.
- **SSH key** (`/home/mcp/.ssh/id_ed25519`) gives the MCP server access to infrastructure hosts. Treat it as root-equivalent for those hosts.
- **Cloudflare Tunnel token** is a secret. Don't commit it to git or pass it via command-line args (use env var or stdin).
- **The `mcp` user** runs with `NoNewPrivileges=true`, `PrivateTmp=true`, `ProtectSystem=strict` via the systemd unit. It can only write to `/opt/mcp-homelab`.
- **No password auth** — all SSH is key-based. No passwords are stored anywhere.
