# Proxmox LXC + Cloudflare Tunnel

Reference architecture for hosting mcp-homelab on a Proxmox LXC container with Cloudflare Tunnel for public HTTPS access. This enables Claude.ai (web + mobile) and other remote MCP clients to reach your homelab.

> **Prerequisite:** Read [hosted-mode.md](hosted-mode.md) first for the general hosted mode workflow. This guide covers the platform-specific steps only.

## Architecture

```
Claude.ai / Mobile
       │
       ▼ HTTPS
┌──────────────────────┐
│  Cloudflare Tunnel   │  mcp.example.com → HTTP/2 → localhost:8000
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  LXC Container       │  Debian 12, management VLAN
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

## Prerequisites

| Requirement            | Detail                                                    |
| ---------------------- | --------------------------------------------------------- |
| **Proxmox VE**         | Hypervisor with LXC template (e.g., `debian-12-standard`) |
| **Static IP**          | Available on your management VLAN for the LXC             |
| **SSH to Proxmox**     | `root` or user with passwordless `sudo` for `pct exec`    |
| **Cloudflare account** | Domain managed by Cloudflare DNS                          |
| **Zero Trust plan**    | Free tier works                                           |

## Step 1: Create the LXC Container

Use the Proxmox web UI, `pct create`, or the `create_lxc` MCP tool if you already have a working instance.

### Via Proxmox CLI

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
- **Static IP:** Required — SSH and tunnel need a known address

Verify the container is running:
```bash
pct exec <VMID> -- ping -c1 8.8.8.8
```

### Via `create_lxc` MCP tool

If you already have a local MCP instance running, ask your AI assistant:

> "Create an LXC container with hostname mcp-server, 1024MB RAM, 1 core, 4GB disk on local-lvm, with IP <LXC_IP>/24 on VLAN <VLAN_ID>"

The tool handles `pct create` + start automatically.

## Step 2: Bootstrap SSH Access

Install SSH in the LXC and inject your key so you can connect directly:

```bash
# From Proxmox host (requires root or sudo for pct)
pct exec <VMID> -- bash -c "apt-get update && apt-get install -y openssh-server && systemctl enable --now ssh"

# Generate a bootstrap key on your dev machine (if you don't have one)
ssh-keygen -t ed25519 -f ~/.ssh/mcp-server-bootstrap -N "" -C "mcp-bootstrap"

# Inject the key into the LXC
PUBKEY=$(cat ~/.ssh/mcp-server-bootstrap.pub)
pct exec <VMID> -- bash -c "mkdir -p /root/.ssh && chmod 700 /root/.ssh && echo '$PUBKEY' >> /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys"

# Verify direct SSH works
ssh -i ~/.ssh/mcp-server-bootstrap root@<LXC_IP> hostname
```

## Step 3: Install mcp-homelab on the LXC

SSH to the LXC and install the project:

```bash
ssh -i ~/.ssh/mcp-server-bootstrap root@<LXC_IP>

# Install system dependencies
apt-get update && apt-get install -y python3 python3-pip python3-venv git curl

# Clone the repository
git clone https://github.com/asvarnon/mcp-homelab.git /opt/mcp-homelab
cd /opt/mcp-homelab

# Create venv and install
python3 -m venv .venv
.venv/bin/pip install -e .

# Generate config templates
.venv/bin/mcp-homelab init
```

Then configure using the standard CLI commands — same as local setup:

```bash
.venv/bin/mcp-homelab setup node          # Add each SSH-accessible host
.venv/bin/mcp-homelab setup proxmox       # Proxmox API credentials (optional)
.venv/bin/mcp-homelab setup opnsense      # OPNsense API credentials (optional)
```

## Step 4: Install as a Service

```bash
sudo .venv/bin/mcp-homelab install --public-url "https://mcp.example.com"
```

This creates the `mcp` service user, sets ownership, updates config.yaml for HTTP transport, installs the systemd unit, and starts the service.

> **LXC containers:** `install` auto-detects unprivileged containers via `systemd-detect-virt` and strips sandbox directives (`PrivateTmp`, `ProtectSystem`, etc.) that require mount namespaces. Look for the `⚠` marker in step 8 output. If detection fails, the full sandbox is preserved (safe default).

### Lock down OAuth client registration (recommended)

Add redirect URI allowlisting to restrict which clients can register:

```bash
echo 'MCP_ALLOWED_REDIRECT_ORIGINS=https://claude.ai,http://localhost' >> /opt/mcp-homelab/.env
sudo systemctl restart mcp-homelab
```

See [DEPLOYMENT-GUIDE.md Phase 4.5](../deploy/DEPLOYMENT-GUIDE.md) for additional options (IPv6 loopback, static clients).

If you prefer to do each step manually (or need to customize), the manual steps are below.

<details>
<summary>Manual steps (expand if needed)</summary>

### Create service user

```bash
useradd --system --create-home --shell /usr/sbin/nologin mcp
chown -R mcp:mcp /opt/mcp-homelab
```

### Update config for HTTP transport

Edit `/opt/mcp-homelab/config.yaml`:

```yaml
server:
  transport: http
  host: "0.0.0.0"
  port: 8000
  public_url: "https://mcp.example.com"    # must match your Cloudflare hostname
```

### Generate SSH keypair for the `mcp` service user

```bash
mkdir -p /home/mcp/.ssh
ssh-keygen -t ed25519 -f /home/mcp/.ssh/id_ed25519 -N "" -C "mcp@mcp-server"
chown -R mcp:mcp /home/mcp/.ssh
chmod 700 /home/mcp/.ssh
chmod 600 /home/mcp/.ssh/id_ed25519

# Print the public key — deploy to each target host's authorized_keys
cat /home/mcp/.ssh/id_ed25519.pub
```

Or use `mcp-homelab setup ssh` on each target host to automate key provisioning.

### Install systemd unit

The recommended approach is `mcp-homelab install`, which handles the systemd unit automatically. If you need to install manually:

```bash
# Locate the bundled template
python3 -c "import importlib.resources; print(importlib.resources.files('mcp_homelab.data').joinpath('mcp-homelab.service'))"

# Copy it to systemd
cp $(python3 -c "import importlib.resources; print(importlib.resources.files('mcp_homelab.data').joinpath('mcp-homelab.service'))") /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now mcp-homelab

# Verify
systemctl is-active mcp-homelab
journalctl -u mcp-homelab -n 20
```

### Secure `.env`

```bash
chmod 400 /opt/mcp-homelab/.env
chown mcp:mcp /opt/mcp-homelab/.env
```

</details>

## Step 5: Set Up Cloudflare Tunnel

### Create the tunnel

1. Log into [Cloudflare Zero Trust](https://one.dash.cloudflare.com/)
2. Navigate to **Networks → Tunnels**
3. Click **Create a tunnel** → choose **Cloudflared** connector type
4. Name the tunnel (e.g., `mcp-homelab`)
5. **Copy the connector token** (the base64 blob from the install command)
6. Click **Next** → add a **Public Hostname**:
   - **Subdomain:** `mcp` (or your preference)
   - **Domain:** select your domain
   - **Service:** `HTTP` → `localhost:8000`
7. Save the tunnel

### Install cloudflared on the LXC

```bash
ssh -i ~/.ssh/mcp-server-bootstrap root@<LXC_IP>

# Add Cloudflare apt repo
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | \
  gpg --batch --yes --dearmor -o /usr/share/keyrings/cloudflare-main.gpg
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" \
  > /etc/apt/sources.list.d/cloudflared.list
apt-get update && apt-get install -y cloudflared

# Install the tunnel service (pass token via stdin to avoid shell history exposure)
echo '<YOUR_TUNNEL_TOKEN>' | cloudflared service install "$(cat /dev/stdin)"

# Patch QUIC → HTTP/2 (QUIC fails in unprivileged LXC containers)
sed -i 's|--no-autoupdate tunnel run|--no-autoupdate --protocol http2 tunnel run|' \
  /etc/systemd/system/cloudflared.service

systemctl daemon-reload
systemctl restart cloudflared

# Verify
systemctl is-active cloudflared
```

### Rotating the tunnel token

If you delete and recreate the tunnel, update the LXC:

```bash
cloudflared service uninstall
echo '<new-token>' | cloudflared service install "$(cat /dev/stdin)"
sed -i 's|--no-autoupdate tunnel run|--no-autoupdate --protocol http2 tunnel run|' \
  /etc/systemd/system/cloudflared.service
systemctl daemon-reload
systemctl restart cloudflared
```

## Step 6: Validate

### Service health

```bash
systemctl status mcp-homelab      # should be active
systemctl status cloudflared       # should be active
```

### Connect Claude.ai

1. Go to [Claude.ai Settings → Integrations](https://claude.ai/settings/integrations)
2. Click **Add Integration** → **Custom MCP Server**
3. Enter your public URL (e.g., `https://mcp.example.com`)
4. Claude performs OAuth 2.1 automatically
5. All tools should be listed

### Run tool checks

Ask Claude.ai to run:
1. `scan_infrastructure` — full topology snapshot
2. `get_node_status` for each host — SSH connectivity
3. `list_containers` on Docker hosts — Docker access
4. `list_vms` / `list_lxc` — Proxmox API
5. `get_dhcp_leases` — OPNsense API

---

## Troubleshooting

### Service won't start

| Error                                    | Cause                                                     | Fix                                                                                       |
| ---------------------------------------- | --------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `Missing required environment variables` | `.env` has empty placeholders from `init`                 | Fill in real values: `PROXMOX_TOKEN_ID`, `PROXMOX_TOKEN_SECRET`, etc. in `.env`           |
| `status=226/NAMESPACE`                   | Systemd sandbox directives need mount namespaces (no LXC) | Re-run `mcp-homelab install` — auto-detects containers and strips incompatible directives |
| `server.public_url is not set`           | Config has `host: "0.0.0.0"` but no `public_url`          | Add `public_url` to config.yaml                                                           |
| `ModuleNotFoundError`                    | venv not activated or deps missing                        | Check `systemctl cat mcp-homelab` — ExecStart should use `.venv/bin/python`               |
| `Input should be a valid dictionary`     | `hosts:` key in config.yaml is null (commented-out only)  | Add at least one host, or delete the `hosts:` key entirely                                |

### Claude.ai can't connect

| Symptom                      | Cause                                      | Fix                                                              |
| ---------------------------- | ------------------------------------------ | ---------------------------------------------------------------- |
| Connection refused / timeout | Cloudflare Tunnel not running              | `systemctl status cloudflared`                                   |
| 404 on POST /                | `streamable_http_path` not set to `/`      | Verify `server.py` has `mcp.settings.streamable_http_path = "/"` |
| 401 Unauthorized             | Service restarted (in-memory tokens wiped) | Disconnect + reconnect in Claude settings                        |
| 421 Invalid Host             | DNS rebinding protection mismatch          | `public_url` in config.yaml must match Cloudflare hostname       |

### SSH tools fail

| Symptom                    | Cause                                      | Fix                                                   |
| -------------------------- | ------------------------------------------ | ----------------------------------------------------- |
| All hosts unreachable      | No hosts in config.yaml                    | Run `mcp-homelab setup node` for each host            |
| Auth failed                | mcp pubkey not in target's authorized_keys | Run `mcp-homelab setup ssh` or deploy key manually    |
| Connection timeout         | Cross-VLAN routing blocked                 | Check firewall allows LXC subnet → target subnet      |
| Permission denied (docker) | sudo_docker misconfigured                  | Set `sudo_docker: true` in config + configure sudoers |

### Cloudflare Tunnel

| Symptom              | Cause                      | Fix                                                                                  |
| -------------------- | -------------------------- | ------------------------------------------------------------------------------------ |
| QUIC timeout         | QUIC protocol fails in LXC | Verify HTTP/2 patch: `grep 'protocol http2' /etc/systemd/system/cloudflared.service` |
| Invalid token        | Tunnel deleted/recreated   | Reinstall with new token (see Rotating section above)                                |
| ERR Tunnel not found | Tunnel UUID changed        | `cloudflared service uninstall` + reinstall                                          |

---

## Common Operations

### Update server code

```bash
ssh -i ~/.ssh/mcp-server-bootstrap root@<LXC_IP>
cd /opt/mcp-homelab
git pull
.venv/bin/pip install -e .     # if deps changed
systemctl restart mcp-homelab
```

### View logs

```bash
journalctl -u mcp-homelab -f        # MCP server (live)
journalctl -u cloudflared -f        # Cloudflare Tunnel (live)
journalctl -u mcp-homelab -n 50     # Last 50 lines
```

### Check status

```bash
systemctl status mcp-homelab
systemctl status cloudflared
```

---

## Security Notes

- **OAuth tokens are in-memory only** — service restart wipes all sessions
- **`.env` is chmod 400** — only the `mcp` user can read it
- **SSH key** (`/home/mcp/.ssh/id_ed25519`) gives the MCP server access to infrastructure hosts — treat as sensitive
- **Tunnel token is a secret** — never commit to git or pass via command-line args (use env var or stdin)
- **systemd hardening** — `NoNewPrivileges=true`, `PrivateTmp=true`, `ProtectSystem=strict`, writes only to `/opt/mcp-homelab`. In unprivileged LXC containers, `install` auto-strips namespace-dependent directives (`PrivateTmp`, `ProtectSystem`, etc.) and logs a `⚠` warning
- **No password auth** — all SSH is key-based
