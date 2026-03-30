"""Abstract base class for REST API clients.

Provides the shared pattern used by ProxmoxClient and OPNsenseClient:
lazy httpx.AsyncClient creation, get/post with connect/timeout error
wrapping, and a custom exception class.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API_TIMEOUT = 15  # seconds — default for all HTTP requests


class APIError(Exception):
    """Base exception for REST API errors."""

    def __init__(self, status_code: int, body: str, label: str = "API") -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"{label} error {status_code}: {body}")


class HomelabAPIClient(ABC):
    """Abstract async HTTP client with lazy initialisation.

    Subclasses implement ``_build_client()`` to configure auth, base URL,
    and SSL settings.  The base class handles:

    - Lazy client creation (``_ensure_client``)
    - GET / POST with ``ConnectError`` and ``TimeoutException`` wrapping
    - Graceful close
    """

    _label: str = "API"  # Human-readable name for error messages

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    @abstractmethod
    def _build_client(self) -> httpx.AsyncClient:
        """Create and return a configured ``httpx.AsyncClient``."""

    def _make_error(self, status_code: int, body: str) -> APIError:
        """Create the appropriate APIError subclass.

        Override in subclasses to return a more specific exception type.
        """
        return APIError(status_code, body, label=self._label)

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Return the httpx client, creating it lazily on first call."""
        if self._client is None:
            self._client = self._build_client()
            logger.debug("%s HTTP client created", self._label)
        return self._client

    def _extract_data(self, body: Any) -> Any:
        """Post-process the parsed JSON response.

        Override to unwrap envelope formats (e.g. Proxmox ``{"data": ...}``).
        Default implementation returns the body as-is.
        """
        return body

    async def get(self, path: str) -> Any:
        """GET request with connect/timeout error wrapping.

        Args:
            path: Relative API path.

        Returns:
            Parsed JSON response, optionally unwrapped by ``_extract_data``.
        """
        client = await self._ensure_client()
        try:
            response = await client.get(path)
        except httpx.ConnectError as exc:
            raise self._make_error(
                0, f"Cannot reach {self._label} at {client.base_url}: {exc}",
            ) from exc
        except httpx.TimeoutException as exc:
            raise self._make_error(
                0, f"{self._label} request timed out ({API_TIMEOUT}s): {exc}",
            ) from exc

        if not response.is_success:
            raise self._make_error(response.status_code, response.text)

        return self._extract_data(response.json())

    async def post(self, path: str, data: dict | None = None) -> Any:
        """POST request with connect/timeout error wrapping.

        Args:
            path: Relative API path.
            data: Optional form data to send.

        Returns:
            Parsed JSON response, optionally unwrapped by ``_extract_data``.
        """
        client = await self._ensure_client()
        try:
            response = await client.post(path, data=data)
        except httpx.ConnectError as exc:
            raise self._make_error(
                0, f"Cannot reach {self._label} at {client.base_url}: {exc}",
            ) from exc
        except httpx.TimeoutException as exc:
            raise self._make_error(
                0, f"{self._label} request timed out ({API_TIMEOUT}s): {exc}",
            ) from exc

        if not response.is_success:
            raise self._make_error(response.status_code, response.text)

        return self._extract_data(response.json())

    async def close(self) -> None:
        """Close the underlying httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.debug("%s HTTP client closed", self._label)
