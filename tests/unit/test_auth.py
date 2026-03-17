"""Unit tests for HTTP auth and server transport configuration."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl
from pydantic import ValidationError
from ruamel.yaml import YAML
from starlette.applications import Starlette

from core.auth import StaticBearerVerifier
from core.config import ServerConfig, validate_env


class TestStaticBearerVerifier:
    @pytest.mark.asyncio
    async def test_valid_token_returns_access_token(self) -> None:
        verifier = StaticBearerVerifier("a" * 40)

        result = await verifier.verify_token("a" * 40)

        assert result is not None
        assert result.token == "a" * 40
        assert result.client_id == "mcp-client"
        assert result.scopes == ["*"]
        assert result.expires_at is None

    @pytest.mark.asyncio
    async def test_invalid_token_returns_none(self) -> None:
        verifier = StaticBearerVerifier("a" * 40)

        result = await verifier.verify_token("b" * 40)

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_token_returns_none(self) -> None:
        verifier = StaticBearerVerifier("a" * 40)

        result = await verifier.verify_token("")

        assert result is None


class TestServerConfig:
    def test_defaults(self) -> None:
        config = ServerConfig()

        assert config.transport == "stdio"
        assert config.host == "127.0.0.1"
        assert config.port == 8000
        assert config.public_url is None

    def test_http_config(self) -> None:
        config = ServerConfig(transport="http")

        assert config.transport == "http"

    def test_invalid_transport_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ServerConfig(transport="websocket")  # type: ignore[arg-type]


class TestValidateEnvHttpTransport:
    @staticmethod
    def _write_http_only_config(
        config_dir: Path,
        host: str = "127.0.0.1",
        public_url: str | None = None,
    ) -> None:
        yaml = YAML()
        config_path = config_dir / "config.yaml"
        server_config: dict[str, str | int] = {
            "transport": "http",
            "host": host,
            "port": 8000,
        }
        if public_url is not None:
            server_config["public_url"] = public_url

        with open(config_path, "w", encoding="utf-8") as handle:
            yaml.dump(
                {
                    "server": server_config,
                    "hosts": {
                        "test-node-1": {
                            "hostname": "test-node-1",
                            "ip": "198.51.100.10",
                        }
                    },
                },
                handle,
            )

    def test_validate_env_requires_bearer_token_for_http(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._write_http_only_config(tmp_path)
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)

        with pytest.raises(EnvironmentError, match="MCP_BEARER_TOKEN"):
            validate_env()

    def test_validate_env_requires_min_token_length(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._write_http_only_config(tmp_path)
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("MCP_BEARER_TOKEN", "short-token")

        with pytest.raises(EnvironmentError, match="at least 32 characters"):
            validate_env()

    def test_validate_env_accepts_valid_http_config(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._write_http_only_config(tmp_path)
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("MCP_BEARER_TOKEN", "a" * 32)

        validate_env()


class TestPublicUrlValidation:
    def test_wildcard_host_without_public_url_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """validate_env() raises when host=0.0.0.0 and no public_url is set."""
        TestValidateEnvHttpTransport._write_http_only_config(tmp_path, host="0.0.0.0")
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("MCP_BEARER_TOKEN", "a" * 32)

        with pytest.raises(EnvironmentError, match="server.public_url"):
            validate_env()

    def test_wildcard_host_with_public_url_passes(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """validate_env() succeeds when host=0.0.0.0 and public_url is set."""
        TestValidateEnvHttpTransport._write_http_only_config(
            tmp_path,
            host="0.0.0.0",
            public_url="http://203.0.113.111:8000",
        )
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("MCP_BEARER_TOKEN", "a" * 32)

        validate_env()

    def test_specific_host_without_public_url_passes(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """validate_env() succeeds when host is a specific IP and no public_url."""
        TestValidateEnvHttpTransport._write_http_only_config(tmp_path, host="203.0.113.111")
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("MCP_BEARER_TOKEN", "a" * 32)

        validate_env()

    def test_ipv6_wildcard_without_public_url_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """validate_env() raises when host='::' and no public_url is set."""
        TestValidateEnvHttpTransport._write_http_only_config(tmp_path, host="::")
        monkeypatch.setenv("MCP_HOMELAB_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("MCP_BEARER_TOKEN", "a" * 32)

        with pytest.raises(EnvironmentError, match="server.public_url"):
            validate_env()


class TestAuthEnforcement:
    @staticmethod
    def _build_asgi_app(expected_token: str) -> tuple[Starlette, str]:
        mcp = FastMCP("auth-test")

        @mcp.tool()
        async def ping() -> dict[str, str]:
            return {"status": "ok"}

        mcp.settings.auth = AuthSettings(
            issuer_url=AnyHttpUrl("http://127.0.0.1:8000"),
            resource_server_url=AnyHttpUrl("http://127.0.0.1:8000"),
        )
        mcp._token_verifier = StaticBearerVerifier(expected_token)
        return mcp.streamable_http_app(), mcp.settings.streamable_http_path

    @pytest.mark.asyncio
    async def test_streamable_http_requires_valid_bearer_token(self) -> None:
        expected_token = "a" * 40
        app, endpoint = self._build_asgi_app(expected_token)

        headers = {
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
        }
        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "0.0.1"}},
        }

        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                no_auth = await client.post(endpoint, headers=headers, json=payload)
                assert no_auth.status_code == 401

                wrong_auth = await client.post(
                    endpoint,
                    headers={**headers, "authorization": "Bearer wrong-token"},
                    json=payload,
                )
                assert wrong_auth.status_code == 401

                valid_auth = await client.post(
                    endpoint,
                    headers={**headers, "authorization": f"Bearer {expected_token}"},
                    json=payload,
                )
                assert valid_auth.status_code != 401
