"""OPNsense REST API client.

Provides an async HTTP client that reads connection info from config
and credentials from environment variables.  The httpx client is
created lazily on first use and reused across calls.
"""

from __future__ import annotations

import logging

import httpx

from mcp_homelab.core.api_client import API_TIMEOUT, APIError, HomelabAPIClient
from mcp_homelab.core.config import get_opnsense_credentials, load_config

logger = logging.getLogger(__name__)


class OPNsenseAPIError(APIError):
    """Raised when the OPNsense API returns a non-2xx response."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(status_code, body, label="OPNsense")


class OPNsenseClient(HomelabAPIClient):
    """Async HTTP client for the OPNsense REST API.

    Connections are lazy — the httpx client is created on first API call.
    Authentication uses HTTP Basic auth with the API key as username
    and API secret as password.
    """

    _label = "OPNsense"

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
            timeout=API_TIMEOUT,
        )

    def _make_error(self, status_code: int, body: str) -> OPNsenseAPIError:
        return OPNsenseAPIError(status_code, body)
