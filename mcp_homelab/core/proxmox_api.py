"""Proxmox VE REST API client.

Provides an async HTTP client that reads connection info from config
and credentials from environment variables.  The httpx client is
created lazily on first use and reused across calls.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from mcp_homelab.core.config import get_proxmox_token, load_config

logger = logging.getLogger(__name__)

_API_TIMEOUT = 15  # seconds — all HTTP requests


class ProxmoxAPIError(Exception):
    """Raised when the Proxmox API returns a non-2xx response."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"Proxmox API error {status_code}: {body}")


class ProxmoxClient:
    """Async HTTP client for the Proxmox VE REST API.

    Connections are lazy — the httpx client is created on first API call.
    Node names are auto-discovered on first use and cached.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
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
            timeout=_API_TIMEOUT,
        )

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Return the httpx client, creating it lazily on first call."""
        if self._client is None:
            self._client = self._build_client()
            logger.debug("Proxmox HTTP client created")
        return self._client

    async def get(self, path: str) -> Any:
        """GET request to Proxmox API.

        Args:
            path: Relative API path, e.g. '/nodes'.

        Returns:
            Parsed JSON response (the 'data' field if present, else full body).

        Raises:
            ProxmoxAPIError: On non-2xx responses.
        """
        client = await self._ensure_client()
        try:
            response = await client.get(path)
        except httpx.ConnectError as exc:
            raise ProxmoxAPIError(0, f"Cannot reach Proxmox at {client.base_url}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise ProxmoxAPIError(0, f"Proxmox request timed out ({_API_TIMEOUT}s): {exc}") from exc

        if not response.is_success:
            raise ProxmoxAPIError(response.status_code, response.text)

        body = response.json()
        # Proxmox wraps most responses in {"data": ...}
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body

    async def post(self, path: str, data: dict | None = None) -> Any:
        """POST request to Proxmox API.

        Args:
            path: Relative API path.
            data: Optional form data to send.

        Returns:
            Parsed JSON response (the 'data' field if present, else full body).

        Raises:
            ProxmoxAPIError: On non-2xx responses.
        """
        client = await self._ensure_client()
        try:
            response = await client.post(path, data=data)
        except httpx.ConnectError as exc:
            raise ProxmoxAPIError(0, f"Cannot reach Proxmox at {client.base_url}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise ProxmoxAPIError(0, f"Proxmox request timed out ({_API_TIMEOUT}s): {exc}") from exc

        if not response.is_success:
            raise ProxmoxAPIError(response.status_code, response.text)

        body = response.json()
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
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            self._nodes = None
            logger.debug("Proxmox HTTP client closed")
