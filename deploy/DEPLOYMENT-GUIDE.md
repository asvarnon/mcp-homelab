# mcp-homelab HTTP Deployment Guide

Complete instructions for deploying mcp-homelab with OAuth 2.1 authentication and Cloudflare Tunnel, enabling Claude.ai (web + mobile) and other remote MCP clients to access your homelab infrastructure.

**Recommended:** Deploy to a **VM running Ubuntu Server**. A full VM gives you proper systemd sandboxing (`ProtectHome`, `ProtectSystem`, `PrivateTmp`) and avoids LXC-specific workarounds (QUIC protocol failures, stripped sandbox directives). LXC containers still work if you prefer lightweight — see the notes in each phase.

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
│  VM (Ubuntu Server)  │  VLAN 10
│  ┌────────────────┐  │
│  │  Uvicorn :8000 │  │  OAuth 2.1 + MCP Streamable HTTP
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

- **Proxmox VE** hypervisor (or any host that can run an Ubuntu Server VM)
- **Static IP** available on the management VLAN for the VM
- **SSH access** to the VM (or to the Proxmox host for LXC bootstrap via `pct exec`)
- **SSH access** to each infrastructure host you want tools to reach

### Cloudflare

- **Cloudflare account** with a domain managed by Cloudflare DNS
- **Zero Trust plan** (free tier works)
- **Tunnel** created in the Cloudflare Zero Trust dashboard with a public hostname route

### Credentials (gather before starting)

| Credential                  | Where to get it                                                    | Used for                                 |
| --------------------------- | ------------------------------------------------------------------ | ---------------------------------------- |
| **Cloudflare Tunnel token** | Zero Trust → Tunnels → your tunnel → Configure → Install connector | `cloudflared service install` in Phase 3 |
| **Proxmox API token**       | Datacenter → Permissions → API Tokens → Add                        | `.env` on the server                     |
| **OPNsense API key/secret** | System → Access → Users → API Keys                                 | `.env` on the server                     |

### Developer machine

- **Python 3.10+**
- **SSH client** (OpenSSH)

---

## Phase 1: Create the Server (VM or LXC)

### Option A: VM with Ubuntu Server (recommended)

Create a VM in Proxmox and install Ubuntu Server 24.04 LTS.

1. Download the Ubuntu Server ISO to Proxmox (`local` storage → ISO Images)
2. Create a VM via the Proxmox web UI or CLI:
   - **CPU:** 1 core (2 if budget allows)
   - **Memory:** 1024 MB minimum (2048 MB recommended)
   - **Disk:** 8 GB minimum
   - **Network:** bridge `vmbr0`, VLAN tag matching your management VLAN
3. Boot the VM and install Ubuntu Server:
   - Set a static IP on your management VLAN
   - Enable **OpenSSH server** during install
   - Create a user (e.g., `root` or your admin user)
4. After install, verify SSH access from your workstation:
   ```bash
   ssh root@<SERVER_IP> hostname
   ```

> **Why VM over LXC?** A VM supports full systemd sandboxing (`ProtectHome`, `ProtectSystem`, `PrivateTmp`), avoids QUIC protocol failures with Cloudflare Tunnel, and behaves like a standard Linux server with no container-specific surprises.

### Option B: LXC Container (lightweight alternative)

If you prefer a lighter footprint, create an LXC container instead. Note that you'll need the QUIC→HTTP/2 workaround for Cloudflare Tunnel (covered in Phase 2) and systemd sandbox directives may be silently stripped.

```bash
pct create <VMID> local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst \
  --hostname mcp-server \
  --cores 1 \
  --memory 1024 \
  --swap 512 \
  --rootfs local-lvm:4 \
  --net0 name=eth0,bridge=vmbr0,tag=<VLAN_ID>,ip=<SERVER_IP>/24,gw=<GATEWAY_IP> \
  --unprivileged 1 \
  --start 1
```

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
6. Skip the actual install — you'll install cloudflared manually in Phase 3
7. Click **Next** → add a **Public Hostname**:
   - **Subdomain:** `mcp` (or your preference)
   - **Domain:** select your domain
   - **Service:** `HTTP` → `localhost:8000`
8. Save the tunnel

**Save the token** — you'll need it for the deploy command. Treat it as a secret.

### Rotating or recreating the tunnel token

If you delete and recreate the tunnel, you get a **new token**. To update the server without a full redeploy:

```bash
# SSH to the server as root
cloudflared service uninstall

# Pass token via stdin to avoid shell history / process listing exposure
echo '<new-token>' | cloudflared service install "$(cat /dev/stdin)"
# Or: set CF_TUNNEL_TOKEN env var and use $CF_TUNNEL_TOKEN

systemctl daemon-reload
systemctl restart cloudflared
```

> **LXC only:** If running in an LXC container, QUIC fails due to UDP buffer limits. Force HTTP/2:
> ```bash
> sed -i 's|--no-autoupdate tunnel run|--no-autoupdate --protocol http2 tunnel run|' \
>   /etc/systemd/system/cloudflared.service
> systemctl daemon-reload && systemctl restart cloudflared
> ```

---

## Phase 3: Install mcp-homelab

SSH to the server and install mcp-homelab from PyPI into a dedicated virtualenv.

### 3.1 Install system packages

```bash
ssh root@<SERVER_IP>
apt update && apt install -y python3 python3-venv python3-pip curl ca-certificates gnupg
```

### 3.2 Install cloudflared

Skip this if you've already installed the Cloudflare Tunnel connector.

```bash
# Add Cloudflare GPG key and repository
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | \
  gpg --dearmor -o /usr/share/keyrings/cloudflare-main.gpg
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" | \
  tee /etc/apt/sources.list.d/cloudflared.list

apt update && apt install -y cloudflared

# Install the tunnel service (pass token via stdin to avoid shell history exposure)
echo '<YOUR_TUNNEL_TOKEN>' | cloudflared service install "$(cat /dev/stdin)"
systemctl enable cloudflared
systemctl start cloudflared
```

> **LXC only:** If running in an LXC container, force HTTP/2 to avoid QUIC failures:
> ```bash
> sed -i 's|--no-autoupdate tunnel run|--no-autoupdate --protocol http2 tunnel run|' \
>   /etc/systemd/system/cloudflared.service
> systemctl daemon-reload && systemctl restart cloudflared
> ```

### 3.3 Create the install directory and virtualenv

```bash
mkdir -p /opt/mcp-homelab
python3 -m venv /opt/mcp-homelab/.venv
```

### 3.4 Install mcp-homelab from PyPI

```bash
/opt/mcp-homelab/.venv/bin/pip install mcp-homelab
```

### 3.5 Scaffold configuration templates

```bash
cd /opt/mcp-homelab
.venv/bin/mcp-homelab init
```

This creates `config.yaml` and `.env` templates in `/opt/mcp-homelab/`. You'll fill these in during Phase 4.

### 3.6 Create the service user and set permissions

```bash
useradd --system --no-create-home --shell /usr/sbin/nologin mcp
chown -R mcp:mcp /opt/mcp-homelab
chmod 400 /opt/mcp-homelab/.env
```

### 3.7 Install the systemd service

Create the service unit file:

```bash
cat > /etc/systemd/system/mcp-homelab.service << 'EOF'
[Unit]
Description=mcp-homelab MCP server
After=network.target

[Service]
Type=simple
User=mcp
Group=mcp
WorkingDirectory=/opt/mcp-homelab
Environment=MCP_HOMELAB_CONFIG_DIR=/opt/mcp-homelab
EnvironmentFile=/opt/mcp-homelab/.env
ExecStart=/opt/mcp-homelab/.venv/bin/mcp-homelab serve
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
LockPersonality=true
ReadWritePaths=/opt/mcp-homelab

[Install]
WantedBy=multi-user.target
EOF
```

> **Why `MCP_HOMELAB_CONFIG_DIR`?** When installed via pip, the `mcp-homelab` binary lives in the venv's `bin/` — not in `/opt/mcp-homelab`. This env var tells the server where to find `config.yaml` and `.env`.

> **LXC only:** Strip namespace sandbox directives that fail in unprivileged containers:
> ```bash
> sed -i '/^PrivateTmp\|^PrivateDevices\|^ProtectSystem\|^ProtectHome\|^ProtectKernelTunables\|^ProtectKernelModules\|^ProtectControlGroups\|^ReadWritePaths/d' \
>   /etc/systemd/system/mcp-homelab.service
> ```

### 3.8 Start the service

```bash
systemctl daemon-reload
systemctl enable mcp-homelab
systemctl start mcp-homelab
sleep 2
systemctl is-active mcp-homelab   # should print "active"
```

The server is now running but has no SSH targets or API credentials — configure those in Phase 4.

---

## Phase 4: Post-Deploy Configuration

After Phase 3, the server is running with template configuration files. These steps fill in your infrastructure details, SSH keys, and API credentials.

### 4.1 Configure `config.yaml`

The `mcp-homelab init` command created a template `config.yaml` in Phase 3. Edit it on your dev machine with all host entries, then transfer it to the server.

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

Transfer to the server (base64 method — reliable across Windows/Linux):

```powershell
# PowerShell
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("config.yaml"))
ssh root@<SERVER_IP> "echo $b64 | base64 -d > /opt/mcp-homelab/config.yaml && chown mcp:mcp /opt/mcp-homelab/config.yaml"
```

```bash
# Linux / macOS
b64=$(base64 -w0 config.yaml 2>/dev/null || base64 config.yaml | tr -d '\n')
ssh root@<SERVER_IP> "echo $b64 | base64 -d > /opt/mcp-homelab/config.yaml && chown mcp:mcp /opt/mcp-homelab/config.yaml"
```

### 4.2 Generate SSH keypair for the `mcp` service user

The `mcp` user needs SSH access to each host in `config.yaml`.

```bash
# SSH to the server as root
ssh root@<SERVER_IP>

# Create home directory (service user was created with --no-create-home)
mkdir -p /home/mcp/.ssh
chown -R mcp:mcp /home/mcp
chmod 700 /home/mcp/.ssh

# Generate keypair
ssh-keygen -t ed25519 -f /home/mcp/.ssh/id_ed25519 -N "" -C "mcp@mcp-server"
chown mcp:mcp /home/mcp/.ssh/id_ed25519 /home/mcp/.ssh/id_ed25519.pub
chmod 600 /home/mcp/.ssh/id_ed25519

# Accept new host keys automatically (paramiko uses AutoAddPolicy, but
# also create SSH config for any manual debugging from the server)
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

**Verify connectivity** from the server:
```bash
# SSH to server, then test as mcp user
ssh root@<SERVER_IP>
su -s /bin/bash - mcp -c "ssh -i /home/mcp/.ssh/id_ed25519 <ssh_user>@<host_ip> hostname"
```

### 4.4 Fill in API credentials in `.env`

The `.env` template was created by `mcp-homelab init` in Phase 3. Fill in your Proxmox API token and OPNsense API key.

```bash
# SSH to the server as root
ssh root@<SERVER_IP>

# Edit .env and fill in real values
nano /opt/mcp-homelab/.env

# Or append credentials directly (use your actual values)
cat >> /opt/mcp-homelab/.env << 'EOF'
PROXMOX_TOKEN_ID=admin@pam!mcp-homelab
PROXMOX_TOKEN_SECRET=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
OPNSENSE_API_KEY=your-api-key-here
OPNSENSE_API_SECRET=your-api-secret-here
EOF

chmod 400 /opt/mcp-homelab/.env
chown mcp:mcp /opt/mcp-homelab/.env
```

### 4.5 Lock down OAuth client registration (recommended)

By default, the server auto-approves all OAuth Dynamic Client Registration requests. To restrict access to a single pre-registered client, generate credentials and add them to `.env`:

```bash
# Generate credentials (both must be ≥32 characters)
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# Run twice — once for client ID, once for client secret
# Append to .env on the server
cat >> /opt/mcp-homelab/.env << 'EOF'
MCP_CLIENT_ID=<generated-client-id>
MCP_CLIENT_SECRET=<generated-client-secret>
EOF
```

When both are set, DCR is disabled — only the pre-registered client can authenticate. Enter these values in Claude Desktop's OAuth prompt or store them in your client configuration.

If neither is set, the server falls back to open DCR (backward compatible, suitable for trusted LANs only).

### 4.6 Restart the service

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
4. Claude will perform the OAuth 2.1 flow automatically. If `MCP_CLIENT_ID` / `MCP_CLIENT_SECRET` are set, enter those credentials when prompted. Otherwise, Claude uses Dynamic Client Registration.
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

| Error                                    | Cause                                            | Fix                                                                         |
| ---------------------------------------- | ------------------------------------------------ | --------------------------------------------------------------------------- |
| `Missing required environment variables` | `.env` missing API credentials                   | Add credentials per step 4.4                                                |
| `server.public_url is not set`           | Config has `host: "0.0.0.0"` but no `public_url` | Add `public_url` to config.yaml                                             |
| `ModuleNotFoundError`                    | venv not activated or deps missing               | `systemctl cat mcp-homelab` — verify ExecStart uses `.venv/bin/mcp-homelab` |

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
| Connection timeout         | Cross-VLAN routing not working             | Verify firewall allows server subnet → target subnet  |
| Permission denied (docker) | sudo_docker not set or sudoers missing     | Set `sudo_docker: true` + configure sudoers on target |

### Cloudflare Tunnel issues

```bash
journalctl -u cloudflared -n 50 --no-pager
```

| Symptom                          | Cause                                    | Fix                                                                                                                  |
| -------------------------------- | ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| QUIC timeout / connection failed | QUIC protocol fails in LXC containers    | LXC only: verify HTTP/2 patch: `grep 'protocol http2' /etc/systemd/system/cloudflared.service`. VMs don't need this. |
| Invalid token                    | Tunnel deleted/recreated in CF dashboard | Reinstall with new token (see Phase 2)                                                                               |
| ERR Tunnel not found             | Tunnel UUID changed                      | Uninstall + reinstall cloudflared service                                                                            |

---

## Common Operations

### Update the server code

```bash
ssh root@<SERVER_IP>
/opt/mcp-homelab/.venv/bin/pip install --upgrade mcp-homelab
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

To start from scratch, back up your config, reinstall, and restore:

```bash
ssh root@<SERVER_IP>

# Back up config
cp /opt/mcp-homelab/config.yaml /opt/mcp-homelab/config.yaml.bak
cp /opt/mcp-homelab/.env /opt/mcp-homelab/.env.bak

# Reinstall
/opt/mcp-homelab/.venv/bin/pip install --force-reinstall mcp-homelab

# Restore config and restart
cp /opt/mcp-homelab/config.yaml.bak /opt/mcp-homelab/config.yaml
cp /opt/mcp-homelab/.env.bak /opt/mcp-homelab/.env
chown mcp:mcp /opt/mcp-homelab/config.yaml /opt/mcp-homelab/.env
systemctl restart mcp-homelab
```

---

## Security Notes

- **OAuth tokens are in-memory only** — service restart wipes all sessions. No persistent auth state on disk.
- **`.env` is chmod 400** — only the `mcp` user can read it. Contains API secrets.
- **SSH key** (`/home/mcp/.ssh/id_ed25519`) gives the MCP server access to infrastructure hosts. Treat it as root-equivalent for those hosts.
- **Cloudflare Tunnel token** is a secret. Don't commit it to git or pass it via command-line args (use env var or stdin).
- **The `mcp` user** runs with `NoNewPrivileges=true`, `PrivateTmp=true`, `ProtectSystem=strict` via the systemd unit. It can only write to `/opt/mcp-homelab`.
- **No password auth** — all SSH is key-based. No passwords are stored anywhere.
