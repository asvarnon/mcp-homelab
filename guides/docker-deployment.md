# Docker Deployment

Run mcp-homelab as a Docker container with HTTP transport and OAuth 2.1 authentication.

---

## Overview

Docker deployment is one of three supported deployment options:

| Option               | Transport    | Use Case                                               |
| -------------------- | ------------ | ------------------------------------------------------ |
| **Local (stdio)**    | stdio        | Claude Desktop on same machine, `mcp-homelab serve`    |
| **LXC / bare-metal** | HTTP + OAuth | Dedicated server, `mcp-homelab install` → systemd unit |
| **Docker**           | HTTP + OAuth | Container-based deployment, `docker compose up`        |

Docker and LXC/bare-metal both use HTTP transport with OAuth 2.1 — the only difference is packaging.

---

## Prerequisites

- Docker Engine 20.10+
- Docker Compose v2 (included with Docker Desktop)
- SSH keys for target hosts (already generated)
- API credentials for Proxmox/OPNsense (if applicable)

---

## Quick Start

### 1. Clone the repository

```bash
git clone git@github.com:asvarnon/mcp-homelab.git
cd mcp-homelab
```

### 2. Prepare config directory

Create a `config/` directory with your `config.yaml` and `.env`:

```bash
mkdir config
cp config.docker.yaml config/config.yaml
cp .env.example config/.env
```

Edit `config/config.yaml` — add your hosts with **container paths** for SSH keys:

```yaml
server:
  transport: http
  host: 0.0.0.0
  port: 8000
  public_url: http://YOUR_HOST_IP:8000

hosts:
  my-server:
    hostname: my-server
    ip: "192.168.1.100"
    ssh: true
    ssh_user: admin
    ssh_key_path: /keys/id_ed25519    # container path, not host path
    description: "My Linux server"
    os: linux
```

Edit `config/.env` — add your API credentials:

```bash
# Only needed if you configured proxmox/opnsense sections in config.yaml
PROXMOX_TOKEN_ID=user@pam!token-name
PROXMOX_TOKEN_SECRET=your-secret-here
OPNSENSE_API_KEY=your-key
OPNSENSE_API_SECRET=your-secret
```

### 3. Start the container

```bash
docker compose up -d
```

The server starts on port 8000 with OAuth 2.1 authentication.

### 4. Verify

```bash
docker compose logs
curl http://localhost:8000/.well-known/oauth-authorization-server
```

You should see JSON with `issuer`, `authorization_endpoint`, `token_endpoint`, etc.

---

## SSH Keys

SSH key paths in `config.yaml` must reference **container paths**, not host paths.

The default `docker-compose.yml` mounts `~/.ssh` to `/keys` (read-only):

```yaml
volumes:
  - ~/.ssh:/keys:ro
```

Map your host keys accordingly:

| Host Path               | Container Path         |
| ----------------------- | ---------------------- |
| `~/.ssh/gamehost`       | `/keys/gamehost`       |
| `~/.ssh/id_ed25519`     | `/keys/id_ed25519`     |
| `~/.ssh/id_ed25519_pve` | `/keys/id_ed25519_pve` |

If you prefer to mount individual keys instead of the entire `.ssh` directory:

```yaml
volumes:
  - ~/.ssh/gamehost:/keys/gamehost:ro
  - ~/.ssh/id_ed25519_pve:/keys/pve:ro
```

---

## Credential Handling

Three approaches — all work without code changes:

### Volume-mount `.env` (default)

Place `.env` alongside `config.yaml` in the config directory. The application reads it at startup via `python-dotenv`.

```yaml
volumes:
  - ./config:/config:ro    # contains config.yaml + .env
```

This is the default in `docker-compose.yml` and the recommended approach.

### Docker `--env-file`

Pass the env file directly to Docker (not read by the app — Docker injects vars into the container environment):

```bash
docker run --env-file .env -v ./config:/config:ro mcp-homelab
```

With this approach, the `.env` does NOT need to be in the config directory.

### Inline environment variables

Pass credentials directly in `docker-compose.yml` or `docker run`:

```yaml
environment:
  - PROXMOX_TOKEN_ID=user@pam!token
  - PROXMOX_TOKEN_SECRET=your-secret
```

> **Note:** Inline vars are visible in `docker inspect` output. Volume-mounted `.env` files are not.

---

## Connecting Claude Desktop

Once the container is running:

1. Open Claude Desktop → Settings → MCP Servers
2. Add a new server with the URL: `http://YOUR_HOST_IP:8000`
3. Claude Desktop will prompt for OAuth Client ID and Client Secret
4. Complete the OAuth flow — the server currently auto-approves all registrations

> **Security note:** The current OAuth implementation auto-approves all client registrations (Issue #7). On a trusted LAN this is acceptable. Client allowlist support is planned for a future release.

---

## Docker Compose Reference

The included `docker-compose.yml`:

```yaml
services:
  mcp-homelab:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./config:/config:ro        # config.yaml + .env
      - ~/.ssh:/keys:ro            # SSH keys
    environment:
      - MCP_HOMELAB_CONFIG_DIR=/config
    restart: unless-stopped
```

### Customization

**Change the port:**

```yaml
ports:
  - "9000:8000"    # host:container
```

Update `public_url` in `config.yaml` to match.

**Use a pre-built image** (when published):

```yaml
services:
  mcp-homelab:
    image: ghcr.io/asvarnon/mcp-homelab:latest
    # ... rest unchanged
```

---

## Health Check

The Dockerfile includes a health check against the OAuth discovery endpoint:

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/.well-known/oauth-authorization-server')" || exit 1
```

Check container health:

```bash
docker inspect --format='{{.State.Health.Status}}' mcp-homelab-mcp-homelab-1
```

> **Note:** The health check only works when the server is running in HTTP transport mode.

---

## Troubleshooting

### Container exits immediately

Check logs for missing environment variables:

```bash
docker compose logs
```

Common cause: `config.yaml` references Proxmox or OPNsense but `.env` is missing the corresponding credentials.

### SSH connection failures

1. Verify key paths in `config.yaml` use container paths (`/keys/...`), not host paths (`~/.ssh/...`)
2. Check the key file is readable inside the container:
   ```bash
   docker compose exec mcp-homelab ls -la /keys/
   ```
3. Ensure the SSH key has correct permissions on the host (600 or 644)

### OAuth discovery returns connection refused

The server may not be in HTTP mode. Check `config.yaml` has:

```yaml
server:
  transport: http
  host: 0.0.0.0
  port: 8000
  public_url: http://YOUR_HOST_IP:8000
```
