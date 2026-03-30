"""Proxmox VE REST API client.

Provides an async HTTP client that reads connection info from config
and credentials from environment variables.  The httpx client is
created lazily on first use and reused across calls.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from mcp_homelab.core.api_client import API_TIMEOUT, APIError, HomelabAPIClient
from mcp_homelab.core.config import get_proxmox_token, load_config

logger = logging.getLogger(__name__)


class ProxmoxAPIError(APIError):
    """Raised when the Proxmox API returns a non-2xx response."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(status_code, body, label="Proxmox")


class ProxmoxClient(HomelabAPIClient):
    """Async HTTP client for the Proxmox VE REST API.

    Connections are lazy — the httpx client is created on first API call.
    Node names are auto-discovered on first use and cached.
    """

    _label = "Proxmox"

    def __init__(self) -> None:
        super().__init__()
        self._nodes: list[str] | None = None

    def _build_client(self) -> httpx.AsyncClient:
        """Create the httpx.AsyncClient with auth headers and SSL settings."""
        config = load_config()
        pve = config.proxmox
        if pve is None:
            raise RuntimeError(
                "Proxmox is not configured. Add a 'proxmox' section to config.yaml."
            )
        token_id, token_secret = get_proxmox_token()

        base_url = f"https://{pve.host}:{pve.port}/api2/json"

        return httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"PVEAPIToken={token_id}={token_secret}",
            },
            verify=pve.verify_ssl,
            timeout=API_TIMEOUT,
        )

    def _make_error(self, status_code: int, body: str) -> ProxmoxAPIError:
        return ProxmoxAPIError(status_code, body)

    def _extract_data(self, body: Any) -> Any:
        """Proxmox wraps most responses in {"data": ...}."""
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body

    async def get_nodes(self) -> list[str]:
        """Return list of PVE node names (auto-discovered, cached).

        On first call, queries GET /nodes and caches the result.

        Returns:
            List of node name strings, e.g. ['pve'].
        """
        if self._nodes is None:
            data = await self.get("/nodes")
            self._nodes = [node["node"] for node in data]
            logger.debug("Discovered Proxmox nodes: %s", self._nodes)
        return self._nodes

    async def close(self) -> None:
        """Close the underlying httpx client."""
        self._nodes = None
        await super().close()
