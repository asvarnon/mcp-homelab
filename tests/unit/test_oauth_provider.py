"""Unit tests for HomelabOAuthProvider."""

from __future__ import annotations

import time

import pytest
from pydantic import AnyUrl

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    RegistrationError,
)
from mcp.shared.auth import InvalidRedirectUriError, OAuthClientInformationFull

from mcp_homelab.core.oauth_provider import (
    ACCESS_TOKEN_TTL,
    AUTH_CODE_TTL,
    MAX_AUTH_CODES,
    MAX_CLIENTS,
    REFRESH_TOKEN_TTL,
    FlexibleRedirectClient,
    HomelabOAuthProvider,
)


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_client(client_id: str = "test-client") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        client_secret="test-secret",
        redirect_uris=[AnyUrl("http://localhost:3000/callback")],
        client_name="Test Client",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="client_secret_post",
    )


def _make_auth_params(
    redirect_uri: str = "http://localhost:3000/callback",
    state: str = "test-state",
    scopes: list[str] | None = None,
) -> AuthorizationParams:
    return AuthorizationParams(
        state=state,
        scopes=scopes or [],
        code_challenge="dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk",
        redirect_uri=AnyUrl(redirect_uri),
        redirect_uri_provided_explicitly=True,
    )


def _extract_code(redirect_url: str) -> str:
    """Extract the 'code' query parameter from a redirect URL."""
    from urllib.parse import parse_qs, urlparse
    parsed = urlparse(redirect_url)
    params = parse_qs(parsed.query)
    codes = params.get("code")
    assert codes is not None, f"No 'code' param in redirect: {redirect_url}"
    return codes[0]


# ── Client Registration ──────────────────────────────────────────────────

class TestClientRegistration:
    async def test_register_and_get_client(self) -> None:
        provider = HomelabOAuthProvider()
        client = _make_client("client-1")

        await provider.register_client(client)
        result = await provider.get_client("client-1")

        assert result is not None
        assert result.client_id == "client-1"
        assert result.client_name == "Test Client"

    async def test_get_unknown_client_returns_none(self) -> None:
        provider = HomelabOAuthProvider()

        result = await provider.get_client("nonexistent")

        assert result is None

    async def test_register_enforces_max_clients(self) -> None:
        provider = HomelabOAuthProvider()

        for i in range(MAX_CLIENTS):
            await provider.register_client(_make_client(f"client-{i}"))

        with pytest.raises(RegistrationError) as exc_info:
            await provider.register_client(_make_client("one-too-many"))
        assert "Maximum number" in (exc_info.value.error_description or "")

    async def test_register_requires_client_id(self) -> None:
        provider = HomelabOAuthProvider()
        client = _make_client()
        client.client_id = None

        with pytest.raises(RegistrationError) as exc_info:
            await provider.register_client(client)
        assert "client_id" in (exc_info.value.error_description or "")


# ── Authorization Code Flow ──────────────────────────────────────────────

class TestAuthorization:
    async def test_authorize_returns_redirect_with_code(self) -> None:
        provider = HomelabOAuthProvider()
        client = _make_client()
        await provider.register_client(client)
        params = _make_auth_params()

        redirect_url = await provider.authorize(client, params)

        assert "code=" in redirect_url
        assert "state=test-state" in redirect_url
        assert redirect_url.startswith("http://localhost:3000/callback")

    async def test_authorize_stores_code_for_loading(self) -> None:
        provider = HomelabOAuthProvider()
        client = _make_client()
        await provider.register_client(client)
        params = _make_auth_params()

        redirect_url = await provider.authorize(client, params)
        code = _extract_code(redirect_url)

        loaded = await provider.load_authorization_code(client, code)
        assert loaded is not None
        assert loaded.code == code
        assert loaded.client_id == "test-client"

    async def test_authorize_enforces_max_codes(self) -> None:
        provider = HomelabOAuthProvider()
        client = _make_client()
        await provider.register_client(client)
        params = _make_auth_params()

        for _ in range(MAX_AUTH_CODES):
            await provider.authorize(client, params)

        redirect_url = await provider.authorize(client, params)

        assert "error=server_error" in redirect_url

    async def test_load_unknown_code_returns_none(self) -> None:
        provider = HomelabOAuthProvider()
        client = _make_client()

        result = await provider.load_authorization_code(client, "bogus-code")

        assert result is None

    async def test_load_expired_code_returns_none(self) -> None:
        provider = HomelabOAuthProvider()
        client = _make_client()
        await provider.register_client(client)
        params = _make_auth_params()

        redirect_url = await provider.authorize(client, params)
        code = _extract_code(redirect_url)

        # Manually expire the code
        provider._auth_codes[code].expires_at = time.time() - 1

        result = await provider.load_authorization_code(client, code)
        assert result is None

    async def test_load_code_rejects_wrong_client(self) -> None:
        provider = HomelabOAuthProvider()
        client_a = _make_client("client-a")
        client_b = _make_client("client-b")
        await provider.register_client(client_a)
        await provider.register_client(client_b)
        params = _make_auth_params()

        redirect_url = await provider.authorize(client_a, params)
        code = _extract_code(redirect_url)

        # client_b should not be able to load client_a's code
        result = await provider.load_authorization_code(client_b, code)
        assert result is None


# ── Token Exchange ────────────────────────────────────────────────────────

class TestTokenExchange:
    async def _do_auth_code_flow(
        self, provider: HomelabOAuthProvider,
    ) -> tuple[OAuthClientInformationFull, str]:
        """Register client, authorize, return (client, auth_code)."""
        client = _make_client()
        await provider.register_client(client)
        params = _make_auth_params()
        redirect_url = await provider.authorize(client, params)
        code = _extract_code(redirect_url)
        return client, code

    async def test_exchange_code_returns_tokens(self) -> None:
        provider = HomelabOAuthProvider()
        client, code = await self._do_auth_code_flow(provider)
        auth_code = await provider.load_authorization_code(client, code)
        assert auth_code is not None

        token = await provider.exchange_authorization_code(client, auth_code)

        assert token.access_token is not None
        assert token.refresh_token is not None
        assert token.token_type == "Bearer"
        assert token.expires_in == ACCESS_TOKEN_TTL

    async def test_exchange_code_is_single_use(self) -> None:
        provider = HomelabOAuthProvider()
        client, code = await self._do_auth_code_flow(provider)
        auth_code = await provider.load_authorization_code(client, code)
        assert auth_code is not None

        await provider.exchange_authorization_code(client, auth_code)

        # Code should be consumed
        second_load = await provider.load_authorization_code(client, code)
        assert second_load is None

    async def test_access_token_is_loadable(self) -> None:
        provider = HomelabOAuthProvider()
        client, code = await self._do_auth_code_flow(provider)
        auth_code = await provider.load_authorization_code(client, code)
        assert auth_code is not None

        token = await provider.exchange_authorization_code(client, auth_code)
        loaded = await provider.load_access_token(token.access_token)

        assert loaded is not None
        assert loaded.client_id == "test-client"
        assert loaded.expires_at is not None

    async def test_unknown_access_token_returns_none(self) -> None:
        provider = HomelabOAuthProvider()

        result = await provider.load_access_token("nonexistent-token")

        assert result is None

    async def test_exchange_rejects_wrong_client(self) -> None:
        provider = HomelabOAuthProvider()
        client_a = _make_client("client-a")
        client_b = _make_client("client-b")
        await provider.register_client(client_a)
        await provider.register_client(client_b)
        params = _make_auth_params()

        redirect_url = await provider.authorize(client_a, params)
        code = _extract_code(redirect_url)
        auth_code = await provider.load_authorization_code(client_a, code)
        assert auth_code is not None

        with pytest.raises(ValueError, match="not issued to this client"):
            await provider.exchange_authorization_code(client_b, auth_code)

    async def test_exchange_rejects_expired_code(self) -> None:
        provider = HomelabOAuthProvider()
        client, code = await self._do_auth_code_flow(provider)
        auth_code = await provider.load_authorization_code(client, code)
        assert auth_code is not None

        # Manually expire the code
        auth_code.expires_at = time.time() - 1

        with pytest.raises(ValueError, match="expired"):
            await provider.exchange_authorization_code(client, auth_code)

    async def test_expired_access_token_returns_none(self) -> None:
        provider = HomelabOAuthProvider()
        client, code = await self._do_auth_code_flow(provider)
        auth_code = await provider.load_authorization_code(client, code)
        assert auth_code is not None

        token = await provider.exchange_authorization_code(client, auth_code)

        # Manually expire the access token
        entry = provider._access_tokens[token.access_token]
        entry.expires_at = int(time.time()) - 1

        result = await provider.load_access_token(token.access_token)
        assert result is None


# ── Refresh Token Rotation ────────────────────────────────────────────────

class TestRefreshTokenRotation:
    async def _get_tokens(
        self, provider: HomelabOAuthProvider,
    ) -> tuple[OAuthClientInformationFull, str, str]:
        """Full flow: register → authorize → exchange → (client, access, refresh)."""
        client = _make_client()
        await provider.register_client(client)
        params = _make_auth_params()
        redirect_url = await provider.authorize(client, params)
        code = _extract_code(redirect_url)
        auth_code = await provider.load_authorization_code(client, code)
        assert auth_code is not None
        token = await provider.exchange_authorization_code(client, auth_code)
        assert token.refresh_token is not None
        return client, token.access_token, token.refresh_token

    async def test_refresh_returns_new_tokens(self) -> None:
        provider = HomelabOAuthProvider()
        client, access, refresh = await self._get_tokens(provider)

        old_refresh = await provider.load_refresh_token(client, refresh)
        assert old_refresh is not None

        new_token = await provider.exchange_refresh_token(
            client, old_refresh, old_refresh.scopes,
        )

        assert new_token.access_token != access
        assert new_token.refresh_token != refresh
        assert new_token.token_type == "Bearer"

    async def test_refresh_revokes_old_tokens(self) -> None:
        provider = HomelabOAuthProvider()
        client, access, refresh = await self._get_tokens(provider)

        old_refresh = await provider.load_refresh_token(client, refresh)
        assert old_refresh is not None
        await provider.exchange_refresh_token(
            client, old_refresh, old_refresh.scopes,
        )

        # Old refresh token should be consumed
        assert await provider.load_refresh_token(client, refresh) is None
        # Old access token should also be revoked
        assert await provider.load_access_token(access) is None

    async def test_unknown_refresh_token_returns_none(self) -> None:
        provider = HomelabOAuthProvider()
        client = _make_client()

        result = await provider.load_refresh_token(client, "bogus")

        assert result is None

    async def test_expired_refresh_token_returns_none(self) -> None:
        provider = HomelabOAuthProvider()
        client, access, refresh = await self._get_tokens(provider)

        # Manually expire the refresh token
        provider._refresh_tokens[refresh].expires_at = int(time.time()) - 1

        result = await provider.load_refresh_token(client, refresh)
        assert result is None

    async def test_refresh_rejects_wrong_client(self) -> None:
        provider = HomelabOAuthProvider()
        client_a, access, refresh = await self._get_tokens(provider)
        client_b = _make_client("other-client")
        await provider.register_client(client_b)

        result = await provider.load_refresh_token(client_b, refresh)
        assert result is None


# ── Revocation ────────────────────────────────────────────────────────────

class TestRevocation:
    async def _get_tokens(
        self, provider: HomelabOAuthProvider,
    ) -> tuple[str, str]:
        """Full flow, returns (access_token, refresh_token)."""
        client = _make_client()
        await provider.register_client(client)
        params = _make_auth_params()
        redirect_url = await provider.authorize(client, params)
        code = _extract_code(redirect_url)
        auth_code = await provider.load_authorization_code(client, code)
        assert auth_code is not None
        token = await provider.exchange_authorization_code(client, auth_code)
        assert token.refresh_token is not None
        return token.access_token, token.refresh_token

    async def test_revoke_access_token_also_revokes_refresh(self) -> None:
        provider = HomelabOAuthProvider()
        access, refresh = await self._get_tokens(provider)

        access_obj = await provider.load_access_token(access)
        assert access_obj is not None
        await provider.revoke_token(access_obj)

        assert await provider.load_access_token(access) is None
        assert await provider.load_refresh_token(_make_client(), refresh) is None

    async def test_revoke_refresh_token_also_revokes_access(self) -> None:
        provider = HomelabOAuthProvider()
        access, refresh = await self._get_tokens(provider)

        refresh_obj = await provider.load_refresh_token(_make_client(), refresh)
        assert refresh_obj is not None
        await provider.revoke_token(refresh_obj)

        assert await provider.load_refresh_token(_make_client(), refresh) is None
        assert await provider.load_access_token(access) is None

    async def test_revoke_unknown_token_is_noop(self) -> None:
        provider = HomelabOAuthProvider()

        # Should not raise
        fake_token = AccessToken(
            token="nonexistent",
            client_id="x",
            scopes=[],
            expires_at=None,
        )
        await provider.revoke_token(fake_token)


# ── Expired Code Eviction ─────────────────────────────────────────────────

class TestExpiredCodeEviction:
    async def test_expired_codes_are_evicted(self) -> None:
        provider = HomelabOAuthProvider()
        client = _make_client()
        await provider.register_client(client)
        params = _make_auth_params()

        redirect_url = await provider.authorize(client, params)
        code = _extract_code(redirect_url)

        # Manually expire the code
        provider._auth_codes[code].expires_at = time.time() - 1

        # Trigger eviction via a new authorize call
        await provider.authorize(client, params)

        assert await provider.load_authorization_code(client, code) is None


# ── Pre-registered client (DCR lockdown) ──────────────────────────────────

_STATIC_ID = "a" * 32
_STATIC_SECRET = "b" * 32


class TestPreRegisteredClient:
    """Tests for static client pre-registration and DCR lockdown."""

    async def test_pre_registered_client_available_on_init(self) -> None:
        provider = HomelabOAuthProvider(
            client_id=_STATIC_ID, client_secret=_STATIC_SECRET,
        )
        client = await provider.get_client(_STATIC_ID)

        assert client is not None
        assert client.client_id == _STATIC_ID
        assert client.client_secret == _STATIC_SECRET
        assert client.token_endpoint_auth_method == "client_secret_post"

    async def test_dcr_disabled_when_credentials_provided(self) -> None:
        provider = HomelabOAuthProvider(
            client_id=_STATIC_ID, client_secret=_STATIC_SECRET,
        )

        with pytest.raises(RegistrationError) as exc_info:
            await provider.register_client(_make_client("intruder"))
        assert "disabled" in (exc_info.value.error_description or "").lower()

    async def test_dcr_enabled_when_no_credentials(self) -> None:
        provider = HomelabOAuthProvider()
        client = _make_client("dynamic-client")

        await provider.register_client(client)
        result = await provider.get_client("dynamic-client")

        assert result is not None
        assert result.client_id == "dynamic-client"

    async def test_pre_registered_client_full_auth_flow(self) -> None:
        """Static client can authorize, exchange code, and get tokens."""
        provider = HomelabOAuthProvider(
            client_id=_STATIC_ID, client_secret=_STATIC_SECRET,
        )
        client = await provider.get_client(_STATIC_ID)
        assert client is not None

        params = _make_auth_params()
        redirect_url = await provider.authorize(client, params)
        code = _extract_code(redirect_url)

        auth_code = await provider.load_authorization_code(client, code)
        assert auth_code is not None

        token = await provider.exchange_authorization_code(client, auth_code)
        assert token.access_token
        assert token.refresh_token


class TestFlexibleRedirectClient:
    """Tests for FlexibleRedirectClient redirect URI validation."""

    def test_accepts_localhost_with_port(self) -> None:
        client = FlexibleRedirectClient(
            client_id="test",
            redirect_uris=[AnyUrl("http://localhost/callback")],
        )
        result = client.validate_redirect_uri(AnyUrl("http://localhost:9876/callback"))
        assert str(result).startswith("http://localhost:9876")

    def test_accepts_127_0_0_1(self) -> None:
        client = FlexibleRedirectClient(
            client_id="test",
            redirect_uris=[AnyUrl("http://localhost/callback")],
        )
        result = client.validate_redirect_uri(AnyUrl("http://127.0.0.1:5555/cb"))
        assert str(result).startswith("http://127.0.0.1")

    def test_accepts_https(self) -> None:
        client = FlexibleRedirectClient(
            client_id="test",
            redirect_uris=[AnyUrl("http://localhost/callback")],
        )
        result = client.validate_redirect_uri(AnyUrl("https://example.com/callback"))
        assert str(result).startswith("https://")

    def test_rejects_non_localhost_http(self) -> None:
        client = FlexibleRedirectClient(
            client_id="test",
            redirect_uris=[AnyUrl("http://localhost/callback")],
        )
        with pytest.raises(InvalidRedirectUriError, match="localhost or HTTPS"):
            client.validate_redirect_uri(AnyUrl("http://evil.com/callback"))

    def test_rejects_none(self) -> None:
        client = FlexibleRedirectClient(
            client_id="test",
            redirect_uris=[AnyUrl("http://localhost/callback")],
        )
        with pytest.raises(InvalidRedirectUriError, match="required"):
            client.validate_redirect_uri(None)
