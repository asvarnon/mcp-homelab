"""MCP client configuration writer.

Generates or updates MCP client config files (Claude Desktop, VS Code)
so users don't need to manually wire the server entry.
"""

from __future__ import annotations

import json
import platform
import re
import sys
from pathlib import Path
from typing import Callable, NamedTuple

from core.config import get_config_dir


# ---------------------------------------------------------------------------
# Client config locations
# ---------------------------------------------------------------------------
def _windows_claude_config_path() -> Path:
    """Return the Claude Desktop config directory on Windows.

    Checks for a Windows Store (MSIX) install first by scanning
    ``AppData/Local/Packages/`` for a ``Claude_*`` directory. Falls
    back to the traditional ``AppData/Roaming/Claude`` path.

    The Store package directory (e.g. ``Claude_pzs8sxrjxfjjc``) uses a
    hash suffix derived from the publisher's signing certificate. Scanning
    with a glob avoids hardcoding a value that could change if Anthropic
    rotates their certificate.
    """
    packages_dir = Path.home() / "AppData" / "Local" / "Packages"
    matches = sorted(packages_dir.glob("Claude_*")) if packages_dir.is_dir() else []
    if matches:
        return matches[0] / "LocalCache" / "Roaming" / "Claude"
    return Path.home() / "AppData" / "Roaming" / "Claude"


def _claude_desktop_config_path() -> Path | None:
    """Return the Claude Desktop config path for the current OS, or None."""
    system = platform.system()
    if system == "Windows":
        appdata = _windows_claude_config_path()
    elif system == "Darwin":
        appdata = Path.home() / "Library" / "Application Support" / "Claude"
    else:
        return None
    return appdata / "claude_desktop_config.json"


def _vscode_mcp_config_path() -> Path | None:
    """Return the VS Code workspace .vscode/mcp.json path, or None.

    Only returns a path if a .vscode/ directory exists in the config dir
    (indicating a VS Code workspace).
    """
    vscode_dir = get_config_dir() / ".vscode"
    if vscode_dir.is_dir():
        return vscode_dir / "mcp.json"
    return None


# ---------------------------------------------------------------------------
# Server entry builders
# ---------------------------------------------------------------------------

def _server_entry_stdio() -> dict:
    """Build the MCP server entry for stdio transport."""
    config_dir = get_config_dir()

    # Prefer the venv python if it exists, otherwise fall back to sys.executable
    venv_python = config_dir / ".venv" / ("Scripts" if platform.system() == "Windows" else "bin") / "python"
    if platform.system() == "Windows":
        venv_python = venv_python.with_suffix(".exe")

    python_path = str(venv_python) if venv_python.exists() else sys.executable
    server_path = str(config_dir / "server.py")

    return {
        "command": python_path,
        "args": [server_path],
        "env": {
            "MCP_HOMELAB_CONFIG_DIR": str(config_dir),
        },
    }


# ---------------------------------------------------------------------------
# Config file writers
# ---------------------------------------------------------------------------

# Matches either a double-quoted JSON string (kept) or a JSONC comment (removed).
# Using a single alternation avoids the classic bug where https:// inside a
# string value is treated as a // comment.
_JSONC_RE = re.compile(
    r'"(?:[^"\\]|\\.)*"'        # double-quoted string (skip)
    r"|"
    r"(//[^\n]*"                # single-line // comment  (capture)
    r"|/\*.*?\*/)",             # block /* */ comment      (capture)
    re.DOTALL,
)


def _strip_jsonc_comments(text: str) -> str:
    """Strip ``//`` and ``/* */`` comments from JSONC without mangling strings.

    Uses a single regex that matches either a double-quoted JSON string
    (which is kept) or a comment (which is removed).  This avoids the
    classic bug where ``https://`` inside a string is treated as a ``//``
    comment.
    """

    def _replacer(match: re.Match[str]) -> str:
        if match.group(1) is not None:
            return ""              # comment — remove
        return match.group(0)      # string  — keep

    return _JSONC_RE.sub(_replacer, text)


def _load_json(path: Path) -> dict:
    """Load a JSON file, returning empty dict if missing or empty.

    Handles JSONC (JSON with comments) by stripping // and /* */ comments
    before parsing — VS Code config files commonly use these.
    """
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            text = _strip_jsonc_comments(text)
            return json.loads(text)
    return {}


def _write_json(path: Path, data: dict) -> None:
    """Write JSON atomically with consistent formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def upsert_claude_desktop(dry_run: bool = False) -> str | None:
    """Add or update the homelab server entry in Claude Desktop config.

    Returns the generated JSON snippet if dry_run=True, otherwise writes
    the file and returns None.
    """
    entry = _server_entry_stdio()

    if dry_run:
        snippet = {"mcpServers": {"homelab": entry}}
        return json.dumps(snippet, indent=2)

    config_path = _claude_desktop_config_path()
    if config_path is None:
        raise RuntimeError("Claude Desktop config path not found for this OS")

    data = _load_json(config_path)
    if "mcpServers" not in data:
        data["mcpServers"] = {}

    data["mcpServers"]["homelab"] = entry
    _write_json(config_path, data)
    return None


def upsert_vscode(dry_run: bool = False) -> str | None:
    """Add or update the homelab server entry in .vscode/mcp.json.

    Returns the generated JSON snippet if dry_run=True, otherwise writes
    the file and returns None.
    """
    entry = _server_entry_stdio()

    if dry_run:
        snippet = {"servers": {"homelab": entry}}
        return json.dumps(snippet, indent=2)

    config_path = _vscode_mcp_config_path()
    if config_path is None:
        config_path = get_config_dir() / ".vscode" / "mcp.json"
        print(f"  Note: creating {config_path} (.vscode/ did not exist)")

    data = _load_json(config_path)
    if "servers" not in data:
        data["servers"] = {}

    data["servers"]["homelab"] = entry
    _write_json(config_path, data)
    return None


# ---------------------------------------------------------------------------
# Interactive client setup
# ---------------------------------------------------------------------------

# Type alias for writer functions (upsert_claude_desktop, upsert_vscode)
ClientWriter = Callable[..., str | None]


class ClientTarget(NamedTuple):
    name: str
    config_path_fn: Callable[[], Path | None]
    writer_fn: ClientWriter


_CLIENTS: list[ClientTarget] = [
    ClientTarget("Claude Desktop", _claude_desktop_config_path, upsert_claude_desktop),
    ClientTarget("VS Code (Copilot)", _vscode_mcp_config_path, upsert_vscode),
]


def run_client_setup(dry_run: bool = False) -> None:
    """Interactive MCP client configuration."""
    print()
    print("─── MCP Client Setup ───")
    print()

    # Detect available clients
    available: list[tuple[str, Path | None, ClientWriter]] = []
    for client in _CLIENTS:
        path = client.config_path_fn()
        available.append((client.name, path, client.writer_fn))

    # Display options
    print("MCP clients:")
    for i, (name, path, _) in enumerate(available, 1):
        status = str(path) if path else "(not detected)"
        print(f"  [{i}] {name}  — {status}")
    print(f"  [{len(available) + 1}] All detected clients")
    print()

    # Prompt for selection
    while True:
        try:
            choice = input(f"Select client [1-{len(available) + 1}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return

        if not choice.isdigit():
            continue
        idx = int(choice)
        if 1 <= idx <= len(available) + 1:
            break

    if dry_run:
        print()

    # Apply selection
    if idx == len(available) + 1:
        # All clients
        targets = available
    else:
        targets = [available[idx - 1]]

    for name, path, writer_fn in targets:
        if dry_run:
            snippet = writer_fn(dry_run=True)
            print(f"── {name} ──")
            print(snippet)
            print()
        else:
            try:
                writer_fn()

                print(f"  → Updated {name} config ✓")
            except Exception as e:
                print(f"  ✗ {name}: {e}")

    if not dry_run:
        print()
        print("Restart your MCP client to pick up the changes.")
