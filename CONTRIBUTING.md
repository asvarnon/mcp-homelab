# Contributing to mcp-homelab

Thanks for your interest in contributing! Whether it's a bug fix, a new tool, or a documentation improvement — all contributions are welcome.

This project is an MCP server for homelab infrastructure management, built with Python and FastMCP. Below is everything you need to get started.

---

## Getting Started

### Prerequisites

- Python 3.10+
- Git
- A Unix-like SSH target for integration testing (or just use mock data — most tests don't need real hardware)

### Dev Setup

```bash
git clone https://github.com/asvarnon/mcp-homelab.git
cd mcp-homelab
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

pip install -e .
```

### Configuration

```bash
mcp-homelab init              # Creates config.yaml interactively
mcp-homelab setup check       # Validates config + env vars
```

Copy `.env.example` to `.env` and fill in your credentials. Never commit `.env`.

### Running Tests

```bash
pytest tests/ -q
```

All tests should pass before submitting a PR. If something's failing on `master`, open an issue.

---

## Code Style

- **Type hints on all function signatures.** Always annotate parameters and return types — it helps reviewers and keeps the codebase consistent.
- **Follow existing patterns.** Read the file you're modifying before changing it. Match the docstring style, naming conventions, and structure you see.
- **Read the design principles.** Check out `docs/design-principles.md` before writing tool code. The key ideas: generic over specific, config-driven, fail explicitly, lazy connections, abstract the transport.

---

## Branch Model

```
master          ← stable releases only (tagged versions, published to PyPI)
  └─ develop    ← integration staging (features tested here before release)
       ├─ feature/xxx
       └─ fix/xxx
```

| Branch | Purpose | Merges into |
|---|---|---|
| `master` | Production releases. Every commit is a tested, versioned release. | — |
| `develop` | Integration staging. Features land here first for validation. | `master` (via PR) |
| `feature/*` | New features and enhancements. | `develop` (via PR) |
| `fix/*` | Bug fixes. Hotfixes for production branch from `master`. | `develop` or `master` |

## Pull Request Process

1. **Branch from `develop`** — use `feature/<name>` or `fix/<name>`.
2. **PR into `develop`** for integration testing. Only validated changes get promoted to `master`.
3. **One logical change per PR.** Keep things focused — it makes review faster for everyone.
4. **Include tests** for any behavioral change. New tools need unit tests. Bug fixes need a regression test.
5. **All tests should pass** (`pytest tests/ -q` — all green).
6. **Explain what and why** in the PR description — not just what changed, but why this approach was chosen.

---

## Versioning Policy

This project uses [Semantic Versioning](https://semver.org/). The version in `pyproject.toml` represents what gets **published to PyPI** — what users install with `pip install mcp-homelab`.

**Only bump the version when shipped code changes:**

| Change type                              | Bump version? | Why                                    |
| ---------------------------------------- | ------------- | -------------------------------------- |
| Python code (tools, core, setup)         | **Yes**       | Changes runtime behavior               |
| `pyproject.toml` dependency changes      | **Yes**       | Changes installed dependencies         |
| `config.yaml` schema changes             | **Yes**       | Changes runtime behavior               |
| Tests                                    | No            | No effect on installed runtime         |
| Agent/instruction files (`.github/`)     | No            | Repo tooling, no runtime effect        |
| Docs (README, design docs, CONTRIBUTING) | No            | No effect on installed runtime         |
| CI/CD workflows                          | No            | No effect on installed runtime         |

**Bump levels:**
- **Patch** (1.3.1 → 1.3.2): Bug fixes, non-breaking improvements
- **Minor** (1.3.x → 1.4.0): New tools, new features, backwards-compatible additions
- **Major** (1.x → 2.0.0): Breaking changes to config schema, tool signatures, or API

---

## AI-Assisted Contributions

This project actively uses AI tools to increase development productivity. If your PR involved AI assistance (Copilot, Claude, ChatGPT, Cursor, etc.), please mention it in the PR description. This isn't about gatekeeping — it helps reviewers focus their attention in the right places.

A quick note like this is plenty:

> Built with GitHub Copilot. Manually verified the API endpoint against Proxmox docs and added the edge case test.

For larger AI-driven contributions, it's helpful to include:
- Which tool(s) you used
- A summary of the key prompts or agent instructions
- What you reviewed or changed after generation

### Why this helps

AI-generated code has different failure modes than hand-written code (confident-but-wrong patterns, hallucinated APIs, over-engineering). Knowing the source helps reviewers calibrate what to look for. It also helps maintainers diagnose issues later if a bug traces back to a misunderstood constraint.

---

## Test Philosophy

Tests protect **current design intent**, not historical decisions.

When a test breaks during your changes:
- **Mechanical breakage** (import path change, library update) → fix the test
- **Design drift** (test enforces a pattern we've moved away from) → rewrite or delete the test
- **Real regression** (your code broke something that should still work) → fix your code

Not sure which category it falls into? Just ask in the PR — that's what code review is for.

---

## Architecture Quick Reference

```
server.py              ← Entry point, thin @mcp.tool() wrappers
├── core/config.py     ← Pydantic models, YAML loader, env var accessors
├── core/ssh.py        ← SSHManager (paramiko) — shared SSH transport
├── tools/nodes.py     ← SSH: system stats, docker ps/logs/restart
├── tools/proxmox.py   ← Proxmox REST: VM list/status/start/stop
├── tools/opnsense.py  ← OPNsense REST: DHCP, interfaces, aliases
├── tools/discovery.py ← Composite: scan_infrastructure
└── tools/context_gen.py ← Markdown: generate_infrastructure_context
```

Config schema uses `hosts` (not `nodes`) as the top-level key for SSH-reachable machines. Legacy configs with `nodes` are accepted with a deprecation warning.

Each layer has one job. Tools call core. Core calls config. Config calls env. Read `docs/design-principles.md` for the full explanation.

---

## Security & Local Tooling

This project gives AI agents SSH access to real infrastructure — security matters. The following protections are in place:

### CI (automatic — runs on every push and PR)

- **pytest** across Python 3.10–3.12
- **gitleaks** secret scanning (full history)
- **CodeQL** static analysis (GitHub-managed)
- **Dependabot** weekly dependency and Actions version updates

### Pre-commit hooks (optional — local only)

Pre-commit runs checks before each `git commit`. It's opt-in per developer:

```bash
pip install pre-commit
pre-commit install
```

This enables local gitleaks scanning so secrets are caught before they ever reach a commit. If you skip this step, the CI gitleaks workflow still catches them on push.

### What to keep out of commits

- **`.env`** — secrets only. Already in `.gitignore`.
- **`config.yaml`** — contains real IPs and hostnames. Already in `.gitignore`.
- **`context/`** — generated infrastructure data. Already in `.gitignore`.
- **`tests/integration/`** — integration tests that hit real infrastructure. Already in `.gitignore`.

If you're adding a new file that could contain sensitive data, add it to `.gitignore` before committing anything else.

---

## Integration Testing

Unit tests in `tests/unit/` use monkeypatch and mock data — they never touch real infrastructure. Integration tests are different: they make real API calls and SSH connections against your personal homelab.

Because integration tests contain environment-specific details (node names, storage pools, VLAN tags, templates), the entire `tests/integration/` directory is gitignored. Each developer writes their own integration tests locally.

### Writing an integration test

1. Create your test in `tests/integration/` (e.g. `test_lxc_integration.py`)
2. Call `load_env()` before any tool calls — this loads your `.env` credentials
3. Use the real tool functions from `tools/` (not mocks)
4. Include confirmation prompts before destructive operations (creating/deleting resources)
5. Always include a cleanup step

### Running integration tests

```bash
# Integration tests are gitignored — they won't exist in CI or cloned repos
# Run them explicitly:
python tests/integration/test_lxc_integration.py
```

### What you need

- A working `config.yaml` with your infrastructure details
- A `.env` file with valid API tokens / credentials
- Network access to the target systems (Proxmox, OPNsense, SSH hosts)
