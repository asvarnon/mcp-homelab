# MCP Homelab — Design Principles & Agent Keywords

**Last Updated:** 2026-03-11

---

## Core Philosophy

> "The tool should not know what it's connecting to, only how to connect."

Tools describe **capabilities**, not specific hosts. All infrastructure-specific knowledge lives in config. This is the seam that makes the project portable — swap the config, the tools work unchanged.

---

## Prompting Keywords for Coding Agents

Use these phrases when directing agents to write or review tool code:

| Keyword / Phrase | Intent |
|---|---|
| **"generic over specific"** | Avoid hardcoding host names — use parameters |
| **"config-driven"** | Push values to `config.yaml`, not inline in code |
| **"fail explicitly"** | Raise meaningful exceptions, never swallow errors silently |
| **"return structured data"** | Return dicts or Pydantic models, not raw strings |
| **"connection pooling"** | Reuse SSH connections — don't open a new one per call |
| **"timeout on all I/O"** | Every SSH call and HTTP request must have a timeout |
| **"abstract the transport"** | SSH/HTTP client logic must not leak into tool logic — keep layers separate |
| **"single responsibility"** | One tool does one thing — no multi-purpose functions |
| **"hosts registry"** | Single map of `hostname → connection details` in config |
| **"lazy connection"** | Don't connect until a tool is actually called |
| **"credential injection"** | Credentials come from env vars — config holds only non-secret metadata |
| **"validate on startup"** | Check all required env vars exist when server starts — fail fast before any tool is called |

---

## Tool Design Rules

### Bad — brittle, host-specific
```python
def get_gamehost_status():
    # hardcoded knowledge of a specific node
```

### Good — generic, parameterized
```python
def get_node_status(hostname: str):
    # hostname is a parameter
```

### Better — config-driven, no hardcoded infrastructure knowledge
```python
def get_node_status(hostname: str):
    # hostname resolves against config.yaml hosts map
    # SSH credentials, port, user all sourced from config + env
    # tool has zero hardcoded knowledge of any specific node
```

---

## Layer Separation

```
tools/          ← capability definitions only, no transport logic
core/ssh.py     ← SSH transport, connection management
core/config.py  ← loads config.yaml + env vars, validates on startup
config.yaml     ← host metadata (IPs, users, roles) — NO secrets
.env            ← secrets only, never committed
```

Each layer has one job. Tools call core. Core calls config. Config calls env.

---

## Portability Goal

The config is the only place that knows about specific infrastructure.
A different user swaps `config.yaml` and their `.env` — the tools require zero changes.
This is the design target for every tool written in this project.

---

## Versioning Policy

The version in `pyproject.toml` tracks **shipped code only** — what users install via `pip install mcp-homelab`.

| Bump? | Change type |
|---|---|
| **Yes** | Python code (tools, core, setup), dependency changes, config schema changes |
| **No** | Tests, agent files (`.github/`), docs, CI/CD workflows |

- **Patch**: Bug fixes, non-breaking improvements
- **Minor**: New tools, new features, backwards-compatible additions
- **Major**: Breaking changes to config schema, tool signatures, or API
