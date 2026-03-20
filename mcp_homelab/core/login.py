"""Admin login gate for OAuth authorization.

Provides a password-protected consent screen that gates the OAuth
authorization flow.  Without this, ``authorize()`` auto-approves every
request — meaning anyone who can reach the server can complete OAuth
and call all MCP tools.

The login page is intentionally minimal: a single password field
(bcrypt-verified) with per-IP rate limiting.  This module produces
Starlette ``Response`` objects for use with ``@mcp.custom_route()``.

Security headers (CSP, X-Frame-Options, etc.) are applied to every
response to prevent clickjacking, embedding, and caching.
"""

from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import bcrypt
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

if TYPE_CHECKING:
    from mcp_homelab.core.oauth_provider import HomelabOAuthProvider

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

SESSION_TOKEN_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_-]{43}$")
"""Validates session token format (base64url, 256-bit / 32-byte → 43 chars)."""

MAX_ATTEMPTS_PER_IP: int = 5
"""Max failed login attempts per IP within the rate-limit window."""

RATE_LIMIT_WINDOW: int = 300
"""Rate-limit window in seconds (5 minutes)."""

_SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'none'; "
        "style-src 'unsafe-inline'; "
        "frame-ancestors 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Pragma": "no-cache",
}


# ── Rate Limiter ───────────────────────────────────────────────────────────

@dataclass
class _IpRecord:
    """Track failed attempts for a single IP."""
    attempts: int = 0
    window_start: float = 0.0


class RateLimiter:
    """Per-IP rate limiter for login attempts.

    Uses ``CF-Connecting-IP`` header when present (cloudflared sends all
    requests from ``127.0.0.1``, so the real client IP is only available
    via this header).  Falls back to ``request.client.host``.

    Note: Restarting the server clears all rate-limit state.  This is
    acceptable for a single-user homelab deployment.
    """

    def __init__(
        self,
        max_attempts: int = MAX_ATTEMPTS_PER_IP,
        window_seconds: int = RATE_LIMIT_WINDOW,
    ) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._records: dict[str, _IpRecord] = {}

    def get_client_ip(self, request: Request) -> str:
        """Extract the real client IP from the request."""
        cf_ip = request.headers.get("CF-Connecting-IP")
        if cf_ip:
            return cf_ip.strip()
        if request.client:
            return request.client.host
        return "unknown"

    def is_rate_limited(self, ip: str) -> bool:
        """Return True if *ip* has exceeded the attempt limit."""
        record = self._records.get(ip)
        if record is None:
            return False
        now = time.time()
        if now - record.window_start > self._window_seconds:
            # Window expired — reset
            del self._records[ip]
            return False
        return record.attempts >= self._max_attempts

    def record_failure(self, ip: str) -> None:
        """Record a failed login attempt for *ip*."""
        now = time.time()
        record = self._records.get(ip)
        if record is None or now - record.window_start > self._window_seconds:
            self._records[ip] = _IpRecord(attempts=1, window_start=now)
        else:
            record.attempts += 1

    def reset(self, ip: str) -> None:
        """Clear rate-limit state for *ip* after successful login."""
        self._records.pop(ip, None)


# ── Login Handler ──────────────────────────────────────────────────────────

class LoginHandler:
    """Handles the admin login gate for OAuth authorization.

    Wired into the MCP server via ``@mcp.custom_route("/login", ...)``.
    The handler validates a session token (created by ``authorize()``),
    presents a password form, verifies the password against a bcrypt hash,
    and completes the pending authorization on success.
    """

    def __init__(
        self,
        provider: HomelabOAuthProvider,
        password_hash: str,
    ) -> None:
        self._provider = provider
        self._password_hash = password_hash.encode("utf-8")
        self._rate_limiter = RateLimiter()

    async def handle_get(self, request: Request) -> Response:
        """Serve the login page for ``GET /login?session=<token>``."""
        session_token = request.query_params.get("session", "")

        if not SESSION_TOKEN_PATTERN.match(session_token):
            return _error_response("Invalid session link.", 400)

        session = self._provider.get_pending_session(session_token)
        if session is None:
            return _error_response(
                "Session expired or invalid. Please reconnect from your MCP client.",
                400,
            )

        client_ip = self._rate_limiter.get_client_ip(request)
        if self._rate_limiter.is_rate_limited(client_ip):
            logger.warning("Rate-limited login page for IP %s", client_ip)
            return _error_response("Too many attempts. Try again later.", 429)

        return _login_page_response(
            session_token=session_token,
            client_name=session.client_name,
            redirect_domain=session.redirect_domain,
        )

    async def handle_post(self, request: Request) -> Response:
        """Validate password for ``POST /login``."""
        client_ip = self._rate_limiter.get_client_ip(request)

        if self._rate_limiter.is_rate_limited(client_ip):
            logger.warning("Rate-limited login POST for IP %s", client_ip)
            return _error_response("Too many attempts. Try again later.", 429)

        form = await request.form()
        session_token = str(form.get("session", ""))
        password = str(form.get("password", ""))

        if not SESSION_TOKEN_PATTERN.match(session_token):
            return _error_response("Invalid session.", 400)

        session = self._provider.get_pending_session(session_token)
        if session is None:
            return _error_response(
                "Session expired or invalid. Please reconnect from your MCP client.",
                400,
            )

        if not bcrypt.checkpw(password.encode("utf-8"), self._password_hash):
            self._rate_limiter.record_failure(client_ip)
            logger.warning("Failed login attempt from %s", client_ip)
            return _login_page_response(
                session_token=session_token,
                client_name=session.client_name,
                redirect_domain=session.redirect_domain,
                error="Incorrect password.",
            )

        # Password correct — complete the authorization
        self._rate_limiter.reset(client_ip)
        redirect_url = self._provider.complete_authorization(session_token)
        if redirect_url is None:
            return _error_response(
                "Session expired during login. Please reconnect from your MCP client.",
                400,
            )

        logger.info("Admin login successful from %s", client_ip)
        return _redirect_response(redirect_url)


# ── Helpers ────────────────────────────────────────────────────────────────

def validate_bcrypt_hash(hash_str: str) -> bool:
    """Return True if *hash_str* looks like a valid bcrypt hash."""
    return bool(re.match(r"^\$2[aby]?\$\d{2}\$.{53}$", hash_str))


def _apply_security_headers(response: Response) -> Response:
    """Apply hardening headers to a response."""
    for header, value in _SECURITY_HEADERS.items():
        response.headers[header] = value
    return response


def _login_page_response(
    session_token: str,
    client_name: str,
    redirect_domain: str,
    error: str | None = None,
) -> Response:
    """Build and return the login HTML page."""
    escaped_client = html.escape(client_name)
    escaped_domain = html.escape(redirect_domain)
    escaped_token = html.escape(session_token)

    error_html = ""
    if error:
        escaped_error = html.escape(error)
        error_html = f'<div class="error">{escaped_error}</div>'

    page = _LOGIN_TEMPLATE.format(
        client_name=escaped_client,
        redirect_domain=escaped_domain,
        session_token=escaped_token,
        error_html=error_html,
    )
    return _apply_security_headers(HTMLResponse(content=page, status_code=200))


def _error_response(message: str, status_code: int) -> Response:
    """Build a simple error HTML page."""
    escaped = html.escape(message)
    page = _ERROR_TEMPLATE.format(message=escaped)
    return _apply_security_headers(HTMLResponse(content=page, status_code=status_code))


def _redirect_response(url: str) -> Response:
    """Build a redirect response with security headers."""
    response = Response(status_code=302, headers={"Location": url})
    return _apply_security_headers(response)


# ── HTML Templates ─────────────────────────────────────────────────────────

_LOGIN_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>mcp-homelab — Admin Login</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #0f172a;
      color: #e2e8f0;
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 100vh;
    }}
    .card {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 12px;
      padding: 2rem;
      width: 100%;
      max-width: 400px;
      box-shadow: 0 4px 24px rgba(0, 0, 0, 0.3);
    }}
    h1 {{
      font-size: 1.25rem;
      margin-bottom: 0.25rem;
    }}
    .subtitle {{
      color: #94a3b8;
      font-size: 0.85rem;
      margin-bottom: 1.5rem;
    }}
    .info {{
      background: #0f172a;
      border: 1px solid #334155;
      border-radius: 8px;
      padding: 0.75rem 1rem;
      margin-bottom: 1.5rem;
      font-size: 0.85rem;
    }}
    .info strong {{ color: #e2e8f0; }}
    .info span {{ color: #94a3b8; }}
    label {{
      display: block;
      font-size: 0.85rem;
      color: #94a3b8;
      margin-bottom: 0.5rem;
    }}
    input[type="password"] {{
      width: 100%;
      padding: 0.625rem 0.75rem;
      background: #0f172a;
      border: 1px solid #475569;
      border-radius: 6px;
      color: #e2e8f0;
      font-size: 1rem;
      outline: none;
    }}
    input[type="password"]:focus {{
      border-color: #3b82f6;
      box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.3);
    }}
    button {{
      width: 100%;
      margin-top: 1rem;
      padding: 0.625rem;
      background: #3b82f6;
      color: white;
      border: none;
      border-radius: 6px;
      font-size: 1rem;
      cursor: pointer;
    }}
    button:hover {{ background: #2563eb; }}
    .error {{
      background: #451a1a;
      border: 1px solid #7f1d1d;
      color: #fca5a5;
      border-radius: 6px;
      padding: 0.625rem 0.75rem;
      margin-bottom: 1rem;
      font-size: 0.85rem;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>mcp-homelab</h1>
    <div class="subtitle">Admin authorization required</div>
    <div class="info">
      <div><span>Client:</span> <strong>{client_name}</strong></div>
      <div><span>Redirect:</span> <strong>{redirect_domain}</strong></div>
    </div>
    {error_html}
    <form method="POST" action="/login" autocomplete="off">
      <input type="hidden" name="session" value="{session_token}">
      <label for="password">Admin Password</label>
      <input type="password" id="password" name="password" required autofocus>
      <button type="submit">Authorize</button>
    </form>
  </div>
</body>
</html>"""

_ERROR_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>mcp-homelab — Error</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #0f172a;
      color: #e2e8f0;
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 100vh;
    }}
    .card {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 12px;
      padding: 2rem;
      width: 100%;
      max-width: 400px;
      text-align: center;
    }}
    h1 {{ font-size: 1.25rem; margin-bottom: 1rem; }}
    p {{ color: #94a3b8; font-size: 0.9rem; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Error</h1>
    <p>{message}</p>
  </div>
</body>
</html>"""
