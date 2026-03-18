# Deployment

Automated deployment of mcp-homelab to an LXC container with Cloudflare Tunnel for HTTPS.

## Prerequisites

| Requirement                 | Detail                                                                                                           |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| **SSH access**              | Root SSH to the target LXC (key-based, or bootstrapped via `--pve-host`)                                         |
| **Passwordless sudo**       | The SSH user must have passwordless `sudo` for `apt-get install`, `systemctl`, and `cloudflared service install` |
| **Cloudflare Tunnel token** | Create a tunnel in Cloudflare Zero Trust dashboard → Tunnels → Create → get the connector token                  |
| **Public URL**              | The HTTPS hostname you configure in the tunnel's public hostname tab (e.g. `https://mcp.example.com`)            |

## Usage

```bash
python deploy/deploy.py \
  --host 10.10.10.111 \
  --cf-tunnel-token "eyJhIjoi..." \
  --public-url "https://mcp.example.com"
```

### With Proxmox bootstrap (first deploy)

```bash
python deploy/deploy.py \
  --host 10.10.10.111 \
  --cf-tunnel-token "eyJhIjoi..." \
  --public-url "https://mcp.example.com" \
  --pve-host 10.10.10.10 \
  --pve-user root \
  --pve-key ~/.ssh/id_ed25519 \
  --vmid 100
```

### All arguments

| Argument            | Required | Default                       | Description                                   |
| ------------------- | -------- | ----------------------------- | --------------------------------------------- |
| `--host`            | Yes      | —                             | Target LXC IP address                         |
| `--cf-tunnel-token` | Yes      | —                             | Cloudflare Tunnel connector token             |
| `--public-url`      | Yes      | —                             | Public HTTPS URL (must start with `https://`) |
| `--branch`          | No       | `develop`                     | Git branch to deploy                          |
| `--ssh-key`         | No       | `~/.ssh/mcp-server-bootstrap` | SSH private key path                          |
| `--ssh-user`        | No       | `root`                        | SSH user on target                            |
| `--port`            | No       | `8000`                        | Server listen port                            |
| `--repo-url`        | No       | GitHub repo                   | Git repository URL                            |
| `--pve-host`        | No       | —                             | Proxmox VE host for LXC SSH bootstrap         |
| `--pve-user`        | No       | —                             | SSH user on PVE (required with `--pve-host`)  |
| `--pve-key`         | No       | —                             | SSH key for PVE (required with `--pve-host`)  |
| `--vmid`            | No       | `100`                         | LXC container VMID                            |

## What it deploys

1. System packages (python3, git)
2. `cloudflared` (via Cloudflare apt repo)
3. Service user (`mcp`)
4. Git clone/pull of mcp-homelab
5. Python venv + dependencies
6. `config.yaml` with `public_url` set to the tunnel URL
7. `mcp-homelab` systemd service
8. `cloudflared` systemd service (tunnel connector)

## Common commands

```bash
systemctl status mcp-homelab
systemctl status cloudflared
journalctl -u mcp-homelab -f
journalctl -u cloudflared -f
```
