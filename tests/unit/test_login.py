"""Tests for mcp_homelab.core.login — admin login gate."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import bcrypt
import pytest

from mcp_homelab.core.login import (
    LoginHandler,
    RateLimiter,
    SESSION_TOKEN_PATTERN,
    _SECURITY_HEADERS,
    _error_response,
    _login_page_response,
    validate_bcrypt_hash,
)
from mcp_homelab.core.oauth_provider import (
    HomelabOAuthProvider,
    PendingSession,
)


# ── Fixtures ───────────────────────────────────────────────────────────────

_TEST_PASSWORD = "test-admin-password"
_TEST_HASH = bcrypt.hashpw(_TEST_PASSWORD.encode(), bcrypt.gensalt()).decode()
_VALID_SESSION_TOKEN = "A" * 43  # 43 chars of base64url


def _make_pending_session(
    session_token: str = _VALID_SESSION_TOKEN,
    client_name: str = "Claude.ai",
    redirect_domain: str = "claude.ai",
    ttl: float = 600.0,
) -> PendingSession:
    """Build a PendingSession for testing."""
    return PendingSession(
        session_token=session_token,
        client=MagicMock(),
        params=MagicMock(),
        client_name=client_name,
        redirect_domain=redirect_domain,
        expires_at=time.time() + ttl,
    )


def _make_provider_mock(
    pending_session: PendingSession | None = None,
    complete_url: str | None = "http://localhost:3000/callback?code=abc&state=xyz",
) -> MagicMock:
    """Build a mock provider with get_pending_session and complete_authorization."""
    provider = MagicMock()
    provider.get_pending_session.return_value = pending_session
    provider.complete_authorization.return_value = complete_url
    return provider


def _make_request(
    method: str = "GET",
    path: str = "/login",
    query_string: str = "",
    form_data: dict | None = None,
    client_ip: str = "192.168.1.100",
    cf_ip: str | None = None,
) -> MagicMock:
    """Build a mock Starlette Request."""
    request = MagicMock()
    request.method = method
    request.url = MagicMock()
    request.url.path = path
    request.query_params = {}
    if query_string:
        from urllib.parse import parse_qs
        params = parse_qs(query_string, keep_blank_values=True)
        request.query_params = {k: v[0] for k, v in params.items()}

    headers = {}
    if cf_ip:
        headers["CF-Connecting-IP"] = cf_ip
    request.headers = headers

    request.client = MagicMock()
    request.client.host = client_ip

    async def mock_form():
        return form_data or {}
    request.form = mock_form

    return request


# ── Session Token Pattern ──────────────────────────────────────────────────

class TestSessionTokenPattern:
    def test_valid_43_char_base64url(self) -> None:
        assert SESSION_TOKEN_PATTERN.match("A" * 43)

    def test_valid_mixed_base64url(self) -> None:
        assert SESSION_TOKEN_PATTERN.match("aB3_-cD4eF5gH6iJ7kL8mN9oP0qR1sT2uV3wX4yZZab")

    def test_rejects_42_chars(self) -> None:
        assert SESSION_TOKEN_PATTERN.match("A" * 42) is None

    def test_rejects_44_chars(self) -> None:
        assert SESSION_TOKEN_PATTERN.match("A" * 44) is None

    def test_rejects_empty(self) -> None:
        assert SESSION_TOKEN_PATTERN.match("") is None

    def test_rejects_special_chars(self) -> None:
        assert SESSION_TOKEN_PATTERN.match("A" * 42 + "!") is None

    def test_rejects_spaces(self) -> None:
        assert SESSION_TOKEN_PATTERN.match("A" * 42 + " ") is None


# ── Bcrypt Hash Validation ─────────────────────────────────────────────────

class TestValidateBcryptHash:
    def test_valid_2b_hash(self) -> None:
        h = bcrypt.hashpw(b"test", bcrypt.gensalt()).decode()
        assert validate_bcrypt_hash(h) is True

    def test_rejects_plaintext(self) -> None:
        assert validate_bcrypt_hash("not-a-hash") is False

    def test_rejects_empty(self) -> None:
        assert validate_bcrypt_hash("") is False

    def test_rejects_short_hash(self) -> None:
        assert validate_bcrypt_hash("$2b$12$short") is False


# ── Rate Limiter ───────────────────────────────────────────────────────────

class TestRateLimiter:
    def test_not_limited_initially(self) -> None:
        rl = RateLimiter()
        assert rl.is_rate_limited("1.2.3.4") is False

    def test_limited_after_max_attempts(self) -> None:
        rl = RateLimiter(max_attempts=3)
        for _ in range(3):
            rl.record_failure("1.2.3.4")
        assert rl.is_rate_limited("1.2.3.4") is True

    def test_not_limited_below_max(self) -> None:
        rl = RateLimiter(max_attempts=3)
        rl.record_failure("1.2.3.4")
        rl.record_failure("1.2.3.4")
        assert rl.is_rate_limited("1.2.3.4") is False

    def test_different_ips_independent(self) -> None:
        rl = RateLimiter(max_attempts=2)
        rl.record_failure("1.1.1.1")
        rl.record_failure("1.1.1.1")
        assert rl.is_rate_limited("1.1.1.1") is True
        assert rl.is_rate_limited("2.2.2.2") is False

    def test_window_expiry_resets(self) -> None:
        rl = RateLimiter(max_attempts=1, window_seconds=1)
        rl.record_failure("1.2.3.4")
        assert rl.is_rate_limited("1.2.3.4") is True

        # Manually expire the window
        rl._records["1.2.3.4"].window_start = time.time() - 2
        assert rl.is_rate_limited("1.2.3.4") is False

    def test_reset_clears_ip(self) -> None:
        rl = RateLimiter(max_attempts=1)
        rl.record_failure("1.2.3.4")
        assert rl.is_rate_limited("1.2.3.4") is True
        rl.reset("1.2.3.4")
        assert rl.is_rate_limited("1.2.3.4") is False

    def test_cf_connecting_ip_header(self) -> None:
        rl = RateLimiter()
        request = _make_request(client_ip="127.0.0.1", cf_ip="203.0.113.5")
        assert rl.get_client_ip(request) == "203.0.113.5"

    def test_cf_header_ignored_from_non_loopback(self) -> None:
        """CF-Connecting-IP must be ignored when peer is not loopback."""
        rl = RateLimiter()
        request = _make_request(client_ip="192.168.1.50", cf_ip="203.0.113.5")
        assert rl.get_client_ip(request) == "192.168.1.50"

    def test_fallback_to_client_host(self) -> None:
        rl = RateLimiter()
        request = _make_request(client_ip="10.0.0.1")
        assert rl.get_client_ip(request) == "10.0.0.1"


# ── Security Headers ──────────────────────────────────────────────────────

class TestSecurityHeaders:
    def test_login_page_has_security_headers(self) -> None:
        response = _login_page_response(
            session_token=_VALID_SESSION_TOKEN,
            client_name="Test",
            redirect_domain="example.com",
        )
        for header, value in _SECURITY_HEADERS.items():
            assert response.headers.get(header) == value

    def test_error_page_has_security_headers(self) -> None:
        response = _error_response("test error", 400)
        for header, value in _SECURITY_HEADERS.items():
            assert response.headers.get(header) == value


# ── Login Page Rendering ──────────────────────────────────────────────────

class TestLoginPageRendering:
    def test_renders_client_name(self) -> None:
        response = _login_page_response(
            session_token=_VALID_SESSION_TOKEN,
            client_name="Claude Desktop",
            redirect_domain="claude.ai",
        )
        body = bytes(response.body).decode()
        assert "Claude Desktop" in body

    def test_renders_redirect_domain(self) -> None:
        response = _login_page_response(
            session_token=_VALID_SESSION_TOKEN,
            client_name="Test",
            redirect_domain="claude.ai",
        )
        body = bytes(response.body).decode()
        assert "claude.ai" in body

    def test_session_token_in_hidden_field(self) -> None:
        response = _login_page_response(
            session_token=_VALID_SESSION_TOKEN,
            client_name="Test",
            redirect_domain="example.com",
        )
        body = bytes(response.body).decode()
        assert f'value="{_VALID_SESSION_TOKEN}"' in body

    def test_error_message_rendered(self) -> None:
        response = _login_page_response(
            session_token=_VALID_SESSION_TOKEN,
            client_name="Test",
            redirect_domain="example.com",
            error="Incorrect password.",
        )
        body = bytes(response.body).decode()
        assert "Incorrect password." in body

    def test_html_escapes_client_name(self) -> None:
        response = _login_page_response(
            session_token=_VALID_SESSION_TOKEN,
            client_name='<script>alert("xss")</script>',
            redirect_domain="example.com",
        )
        body = bytes(response.body).decode()
        assert "<script>" not in body
        assert "&lt;script&gt;" in body

    def test_no_error_by_default(self) -> None:
        response = _login_page_response(
            session_token=_VALID_SESSION_TOKEN,
            client_name="Test",
            redirect_domain="example.com",
        )
        body = bytes(response.body).decode()
        assert "error" not in body.lower() or 'class="error"' not in body


# ── LoginHandler GET ───────────────────────────────────────────────────────

class TestLoginHandlerGet:
    async def test_valid_session_returns_login_page(self) -> None:
        session = _make_pending_session()
        provider = _make_provider_mock(pending_session=session)
        handler = LoginHandler(provider=provider, password_hash=_TEST_HASH)

        request = _make_request(query_string=f"session={_VALID_SESSION_TOKEN}")
        response = await handler.handle_get(request)

        assert response.status_code == 200
        assert b"Admin Password" in response.body

    async def test_invalid_token_format_returns_400(self) -> None:
        provider = _make_provider_mock()
        handler = LoginHandler(provider=provider, password_hash=_TEST_HASH)

        request = _make_request(query_string="session=bad!token")
        response = await handler.handle_get(request)

        assert response.status_code == 400
        assert b"Invalid session" in response.body

    async def test_missing_token_returns_400(self) -> None:
        provider = _make_provider_mock()
        handler = LoginHandler(provider=provider, password_hash=_TEST_HASH)

        request = _make_request(query_string="")
        response = await handler.handle_get(request)

        assert response.status_code == 400

    async def test_expired_session_returns_400(self) -> None:
        provider = _make_provider_mock(pending_session=None)
        handler = LoginHandler(provider=provider, password_hash=_TEST_HASH)

        request = _make_request(query_string=f"session={_VALID_SESSION_TOKEN}")
        response = await handler.handle_get(request)

        assert response.status_code == 400
        assert b"expired" in bytes(response.body).lower()

    async def test_rate_limited_returns_429(self) -> None:
        session = _make_pending_session()
        provider = _make_provider_mock(pending_session=session)
        handler = LoginHandler(provider=provider, password_hash=_TEST_HASH)

        # Exhaust rate limit
        for _ in range(5):
            handler._rate_limiter.record_failure("192.168.1.100")

        request = _make_request(query_string=f"session={_VALID_SESSION_TOKEN}")
        response = await handler.handle_get(request)

        assert response.status_code == 429


# ── LoginHandler POST ─────────────────────────────────────────────────────

class TestLoginHandlerPost:
    async def test_correct_password_redirects(self) -> None:
        session = _make_pending_session()
        redirect_url = "http://localhost:3000/callback?code=abc&state=xyz"
        provider = _make_provider_mock(
            pending_session=session, complete_url=redirect_url,
        )
        handler = LoginHandler(provider=provider, password_hash=_TEST_HASH)

        request = _make_request(
            method="POST",
            form_data={"session": _VALID_SESSION_TOKEN, "password": _TEST_PASSWORD},
        )
        response = await handler.handle_post(request)

        assert response.status_code == 302
        assert response.headers["Location"] == redirect_url

    async def test_wrong_password_shows_error(self) -> None:
        session = _make_pending_session()
        provider = _make_provider_mock(pending_session=session)
        handler = LoginHandler(provider=provider, password_hash=_TEST_HASH)

        request = _make_request(
            method="POST",
            form_data={"session": _VALID_SESSION_TOKEN, "password": "wrong"},
        )
        response = await handler.handle_post(request)

        assert response.status_code == 200
        assert b"Incorrect password" in response.body

    async def test_wrong_password_records_rate_limit(self) -> None:
        session = _make_pending_session()
        provider = _make_provider_mock(pending_session=session)
        handler = LoginHandler(provider=provider, password_hash=_TEST_HASH)

        request = _make_request(
            method="POST",
            form_data={"session": _VALID_SESSION_TOKEN, "password": "wrong"},
        )
        await handler.handle_post(request)

        assert handler._rate_limiter._records.get("192.168.1.100") is not None

    async def test_invalid_session_token_returns_400(self) -> None:
        provider = _make_provider_mock()
        handler = LoginHandler(provider=provider, password_hash=_TEST_HASH)

        request = _make_request(
            method="POST",
            form_data={"session": "bad", "password": _TEST_PASSWORD},
        )
        response = await handler.handle_post(request)

        assert response.status_code == 400

    async def test_expired_session_returns_400(self) -> None:
        provider = _make_provider_mock(pending_session=None)
        handler = LoginHandler(provider=provider, password_hash=_TEST_HASH)

        request = _make_request(
            method="POST",
            form_data={"session": _VALID_SESSION_TOKEN, "password": _TEST_PASSWORD},
        )
        response = await handler.handle_post(request)

        assert response.status_code == 400

    async def test_rate_limited_returns_429(self) -> None:
        session = _make_pending_session()
        provider = _make_provider_mock(pending_session=session)
        handler = LoginHandler(provider=provider, password_hash=_TEST_HASH)

        for _ in range(5):
            handler._rate_limiter.record_failure("192.168.1.100")

        request = _make_request(
            method="POST",
            form_data={"session": _VALID_SESSION_TOKEN, "password": _TEST_PASSWORD},
        )
        response = await handler.handle_post(request)

        assert response.status_code == 429

    async def test_correct_password_resets_rate_limit(self) -> None:
        session = _make_pending_session()
        provider = _make_provider_mock(pending_session=session)
        handler = LoginHandler(provider=provider, password_hash=_TEST_HASH)

        handler._rate_limiter.record_failure("192.168.1.100")

        request = _make_request(
            method="POST",
            form_data={"session": _VALID_SESSION_TOKEN, "password": _TEST_PASSWORD},
        )
        await handler.handle_post(request)

        assert not handler._rate_limiter.is_rate_limited("192.168.1.100")

    async def test_session_expired_during_login(self) -> None:
        """Session found for form render but expired when completing."""
        session = _make_pending_session()
        provider = _make_provider_mock(
            pending_session=session, complete_url=None,
        )
        handler = LoginHandler(provider=provider, password_hash=_TEST_HASH)

        request = _make_request(
            method="POST",
            form_data={"session": _VALID_SESSION_TOKEN, "password": _TEST_PASSWORD},
        )
        response = await handler.handle_post(request)

        assert response.status_code == 400
        assert b"expired" in bytes(response.body).lower()
