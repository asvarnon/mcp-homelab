"""Dev shim — delegates to the real entry point inside the package.

Allows ``python server.py`` from the repo root during development.
Production installs use ``mcp-homelab serve`` via the CLI entry point.
"""

from mcp_homelab.server import mcp, start_server  # noqa: F401 — re-export

if __name__ == "__main__":
    start_server()
