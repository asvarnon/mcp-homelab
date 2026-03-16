---
applyTo: "**"
---

# mcp-homelab Code Review Instructions

## Project Summary

mcp-homelab is a Python MCP server (built on Anthropic's FastMCP SDK) that gives AI assistants real-time access to homelab infrastructure via SSH and REST APIs. It supports Linux and FreeBSD hosts, Docker container management, Proxmox VE, and OPNsense firewalls.

## Tech Stack

- **Python 3.10+** — minimum version, use `from __future__ import annotations` for modern syntax
- **Pydantic v2** — config models with strict validation (`Literal`, `BaseModel`)
- **TypedDict** — all tool return types are `TypedDict` subclasses, not raw dicts
- **paramiko** — SSH transport (wrapped in `core/ssh.py`, never used directly in tools)
- **httpx** — async HTTP for Proxmox/OPNsense APIs (wrapped in core modules)
- **pytest + pytest-asyncio** — test framework

## Design Principles (enforce these in review)

1. **Generic over specific** — no hardcoded host names; tools take `hostname: str` parameters that resolve against `config.yaml`
2. **Config-driven** — infrastructure knowledge lives in `config.yaml`, not in code
3. **Fail explicitly** — raise meaningful exceptions, never swallow errors silently
4. **Return structured data** — return TypedDicts or Pydantic models, not raw strings
5. **Abstract the transport** — SSH/HTTP logic stays in `core/`, tool functions in `tools/` never import paramiko or httpx directly
6. **Timeout on all I/O** — every SSH call and HTTP request must have a timeout
7. **Lazy connection** — don't connect until a tool is actually called
8. **Credential injection** — secrets come from env vars (`.env`), config holds only non-secret metadata
9. **Validate on startup** — check required env vars exist when server starts, fail fast

## Layer Separation

```
tools/          ← Tool functions only — no transport logic
core/ssh.py     ← SSH transport, connection pooling
core/config.py  ← Config loading, Pydantic models, env validation
config.yaml     ← Host metadata (IPs, users, roles) — NO secrets
.env            ← Secrets only, never committed
```

Tools call core. Core calls config. Config calls env. Don't skip layers.

## Type Hint Requirements

- **All function signatures** must have type hints (parameters and return types)
- **Variables** need type hints where the type isn't obvious from context
- Use `list[str]` not `List[str]` (lowercase generics, Python 3.10+)
- Use `X | None` not `Optional[X]`
- Use `Literal["a", "b"]` for constrained string fields

## Testing Conventions

- Tests live in `tests/unit/` — one test file per source module
- Parser functions are tested exhaustively (happy path, edge cases, empty input)
- Integration-level tests use `monkeypatch` to stub SSH calls, not real connections
- Test classes group related tests: `class TestParseUptime`, `class TestGetNodeStatus`
- Async tool tests use `pytest-asyncio` with `async def test_*` methods

## Common Review Issues to Flag

- Missing type hints on function signatures
- Raw dict returns instead of TypedDict
- Transport logic (paramiko, httpx) leaking into `tools/` layer
- Hardcoded hostnames or IPs in tool code
- Missing timeouts on I/O operations
- Swallowed exceptions (bare `except:` or `except Exception: pass`)
- Tests that require real SSH/API connections instead of mocking
- `Optional[X]` or `List[X]` instead of `X | None` or `list[X]`

## FreeBSD/Linux Dual Support

The `HostConfig.os` field (`Literal["linux", "freebsd"]`) controls command dispatch. When reviewing changes to `tools/nodes.py`:
- Verify both OS paths are covered (Linux and FreeBSD commands differ)
- FreeBSD uses: `uptime`, `vmstat`, `sysctl`, `df -g`
- Linux uses: `uptime -p`, `top`, `free -m`, `df -BG`, `lscpu`, `/proc/meminfo`
- New node-level tools must handle both paths or document why they don't
