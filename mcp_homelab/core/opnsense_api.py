"""OPNsense REST API client.

Provides an async HTTP client that reads connection info from config
and credentials from environment variables.  The httpx client is
created lazily on first use and reused across calls.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from mcp_homelab.core.config import get_opnsense_credentials, load_config

logger = logging.getLogger(__name__)

_API_TIMEOUT = 15  # seconds — all HTTP requests


class OPNsenseAPIError(Exception):
    """Raised when the OPNsense API returns a non-2xx response."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"OPNsense API error {status_code}: {body}")


class OPNsenseClient:
    """Async HTTP client for the OPNsense REST API.

    Connections are lazy — the httpx client is created on first API call.
    Authentication uses HTTP Basic auth with the API key as username
    and API secret as password.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _build_client(self) -> httpx.AsyncClient:
        """Create the httpx.AsyncClient with Basic auth and SSL settings."""
        config = load_config()
        opn = config.opnsense
        if opn is None:
            raise RuntimeError(
                "OPNsense is not configured. Add an 'opnsense' section to config.yaml."
            )
        api_key, api_secret = get_opnsense_credentials()

        base_url = f"https://{opn.host}/api"

        return httpx.AsyncClient(
            base_url=base_url,
            auth=(api_key, api_secret),
            verify=opn.verify_ssl,
            timeout=_API_TIMEOUT,
        )

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Return the httpx client, creating it lazily on first call."""
        if self._client is None:
            self._client = self._build_client()
            logger.debug("OPNsense HTTP client created")
        return self._client

    async def get(self, path: str) -> Any:
        """GET request to OPNsense API.

        Args:
            path: Relative API path, e.g. '/dhcpv4/leases/searchLease'.

        Returns:
            Parsed JSON response body.

        Raises:
            OPNsenseAPIError: On non-2xx responses.
        """
        client = await self._ensure_client()
        try:
            response = await client.get(path)
        except httpx.ConnectError as exc:
            raise OPNsenseAPIError(0, f"Cannot reach OPNsense at {client.base_url}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise OPNsenseAPIError(0, f"OPNsense request timed out ({_API_TIMEOUT}s): {exc}") from exc
        if not response.is_success:
            raise OPNsenseAPIError(response.status_code, response.text)
        return response.json()

    async def post(self, path: str, data: dict | None = None) -> Any:
        """POST request to OPNsense API.

        Args:
            path: Relative API path.
            data: Optional form data to include in the request body.

        Returns:
            Parsed JSON response body.

        Raises:
            OPNsenseAPIError: On non-2xx responses.
        """
        client = await self._ensure_client()
        try:
            response = await client.post(path, data=data)
        except httpx.ConnectError as exc:
            raise OPNsenseAPIError(0, f"Cannot reach OPNsense at {client.base_url}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise OPNsenseAPIError(0, f"OPNsense request timed out ({_API_TIMEOUT}s): {exc}") from exc
        if not response.is_success:
            raise OPNsenseAPIError(response.status_code, response.text)
        return response.json()
