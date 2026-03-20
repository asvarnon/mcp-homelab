"""In-memory OAuth 2.1 authorization server provider for mcp-homelab.

Implements the MCP SDK's OAuthAuthorizationServerProvider protocol with
in-memory storage suited for a single-user homelab. All state (registered
clients, auth codes, tokens) lives in memory and is cleared on restart.

Security controls:
- Max registered clients cap (prevents DoS via unbounded DCR)
- Max outstanding auth codes cap
- Auth codes are single-use and time-limited (5 min)
- Access tokens expire after 1 hour
- Refresh tokens expire after 30 days with rotation on use
- Token entropy: 256 bits (auth codes), 384 bits (access/refresh)
"""

from __future__ import annotations

import logging
import secrets
import time

from pydantic import AnyUrl

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    construct_redirect_uri,
)
from mcp.shared.auth import (
    InvalidRedirectUriError,
    OAuthClientInformationFull,
    OAuthToken,
)

logger = logging.getLogger(__name__)

# ── Limits ─────────────────────────────────────────────────────────────────
MAX_CLIENTS: int = 5
MAX_AUTH_CODES: int = 50

# ── Token lifetimes (seconds) ─────────────────────────────────────────────
AUTH_CODE_TTL: int = 5 * 60            # 5 minutes
ACCESS_TOKEN_TTL: int = 60 * 60        # 1 hour
REFRESH_TOKEN_TTL: int = 30 * 24 * 3600  # 30 days


class FlexibleRedirectClient(OAuthClientInformationFull):
    """Pre-registered client that accepts any localhost or HTTPS redirect URI.

    The MCP SDK validates redirect URIs via exact list membership. Claude
    Desktop sends dynamic ``http://localhost:PORT/callback`` URIs (the port
    changes each session), so exact matching is impossible. This subclass
    overrides ``validate_redirect_uri`` to accept any URI that is localhost
    or HTTPS — matching the MCP spec requirement: *"Redirect URIs MUST be
    either localhost URLs or HTTPS URLs."*

    PKCE S256 is required by MCP, so authorization code interception risk
    is mitigated even with flexible redirect URI matching.
    """

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        if redirect_uri is None:
            raise InvalidRedirectUriError(
                "redirect_uri is required for pre-registered client",
            )
        uri_str = str(redirect_uri)
        if not (
            uri_str.startswith("http://localhost")
            or uri_str.startswith("http://127.0.0.1")
            or uri_str.startswith("https://")
        ):
            raise InvalidRedirectUriError(
                f"Redirect URI must be localhost or HTTPS, got: {uri_str}",
            )
        return redirect_uri


class HomelabOAuthProvider:
    """Single-user, in-memory OAuth 2.1 authorization server.

    Implements ``OAuthAuthorizationServerProvider`` with auto-approve
    authorization (no consent screen). All tokens are cleared on process
    restart — this is a feature, not a bug, for a homelab deployment.

    When *client_id* and *client_secret* are provided, the provider starts
    with a pre-registered client and disables Dynamic Client Registration.
    When omitted, DCR remains enabled (backward-compatible).
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._auth_codes: dict[str, AuthorizationCode] = {}
        self._access_tokens: dict[str, AccessToken] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}
        # Map access token → refresh token for paired revocation.
        self._token_pairs: dict[str, str] = {}

        self._dcr_enabled: bool = True
        if client_id and client_secret:
            self._register_static_client(client_id, client_secret)
            self._dcr_enabled = False

    def _register_static_client(
        self, client_id: str, client_secret: str,
    ) -> None:
        """Pre-populate ``_clients`` with a static credential pair.

        Uses ``FlexibleRedirectClient`` to accept dynamic localhost redirect
        URIs from Claude Desktop.  ``token_endpoint_auth_method`` is set
        explicitly to ``client_secret_post`` so the SDK's
        ``ClientAuthenticator`` validates the secret via
        ``hmac.compare_digest``.
        """
        client = FlexibleRedirectClient(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uris=[AnyUrl("http://localhost/callback")],
            token_endpoint_auth_method="client_secret_post",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        )
        self._clients[client_id] = client
        logger.info("Pre-registered static OAuth client: %s", client_id)

    # ── Client registration (RFC 7591) ────────────────────────────────────

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(
        self, client_info: OAuthClientInformationFull,
    ) -> None:
        if not self._dcr_enabled:
            raise RegistrationError(
                error="invalid_client_metadata",
                error_description=(
                    "Dynamic Client Registration is disabled. "
                    "Use pre-registered credentials."
                ),
            )
        if len(self._clients) >= MAX_CLIENTS:
            raise RegistrationError(
                error="invalid_client_metadata",
                error_description=(
                    f"Maximum number of registered clients ({MAX_CLIENTS}) reached. "
                    "Restart the server to clear registrations."
                ),
            )
        if client_info.client_id is None:
            raise RegistrationError(
                error="invalid_client_metadata",
                error_description="client_id is required",
            )
        self._clients[client_info.client_id] = client_info
        logger.info(
            "Registered OAuth client: %s (%s)",
            client_info.client_id,
            client_info.client_name or "unnamed",
        )

    # ── Authorization (auto-approve) ──────────────────────────────────────

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        self._evict_expired_codes()

        if len(self._auth_codes) >= MAX_AUTH_CODES:
            logger.warning("Auth code cap reached (%d); rejecting", MAX_AUTH_CODES)
            return construct_redirect_uri(
                str(params.redirect_uri),
                error="server_error",
                error_description="Too many outstanding authorization codes",
                state=params.state,
            )

        # 256-bit code (spec requires ≥128 bits)
        code = secrets.token_urlsafe(32)
        now = time.time()

        self._auth_codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=now + AUTH_CODE_TTL,
            client_id=client.client_id or "",
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )

        logger.info("Issued auth code for client %s", client.client_id)
        return construct_redirect_uri(
            str(params.redirect_uri),
            code=code,
            state=params.state,
        )

    # ── Authorization code exchange ───────────────────────────────────────

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        entry = self._auth_codes.get(authorization_code)
        if entry is None:
            return None
        # Reject expired codes
        if entry.expires_at < time.time():
            del self._auth_codes[authorization_code]
            return None
        # Reject cross-client code redemption
        if entry.client_id != (client.client_id or ""):
            return None
        return entry

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        # Validate client binding and expiry before consuming
        if authorization_code.client_id != (client.client_id or ""):
            raise ValueError("Authorization code was not issued to this client")
        if authorization_code.expires_at < time.time():
            self._auth_codes.pop(authorization_code.code, None)
            raise ValueError("Authorization code has expired")

        # Consume the code (single-use)
        self._auth_codes.pop(authorization_code.code, None)

        access_token_str = secrets.token_urlsafe(48)
        refresh_token_str = secrets.token_urlsafe(48)
        now = int(time.time())

        self._access_tokens[access_token_str] = AccessToken(
            token=access_token_str,
            client_id=client.client_id or "",
            scopes=authorization_code.scopes,
            expires_at=now + ACCESS_TOKEN_TTL,
            resource=authorization_code.resource,
        )
        self._refresh_tokens[refresh_token_str] = RefreshToken(
            token=refresh_token_str,
            client_id=client.client_id or "",
            scopes=authorization_code.scopes,
            expires_at=now + REFRESH_TOKEN_TTL,
        )
        self._token_pairs[access_token_str] = refresh_token_str

        logger.info("Exchanged auth code for tokens (client: %s)", client.client_id)
        return OAuthToken(
            access_token=access_token_str,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL,
            refresh_token=refresh_token_str,
            scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
        )

    # ── Refresh token exchange (with rotation) ────────────────────────────

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        entry = self._refresh_tokens.get(refresh_token)
        if entry is None:
            return None
        # Reject expired refresh tokens
        if entry.expires_at is not None and entry.expires_at < time.time():
            self._refresh_tokens.pop(refresh_token, None)
            return None
        # Reject cross-client refresh
        if entry.client_id != (client.client_id or ""):
            return None
        return entry

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Rotate: delete old refresh token
        self._refresh_tokens.pop(refresh_token.token, None)

        # Also revoke any access token paired to the old refresh token
        for at, rt in list(self._token_pairs.items()):
            if rt == refresh_token.token:
                self._access_tokens.pop(at, None)
                del self._token_pairs[at]
                break

        new_access = secrets.token_urlsafe(48)
        new_refresh = secrets.token_urlsafe(48)
        now = int(time.time())

        self._access_tokens[new_access] = AccessToken(
            token=new_access,
            client_id=client.client_id or "",
            scopes=scopes,
            expires_at=now + ACCESS_TOKEN_TTL,
        )
        self._refresh_tokens[new_refresh] = RefreshToken(
            token=new_refresh,
            client_id=client.client_id or "",
            scopes=scopes,
            expires_at=now + REFRESH_TOKEN_TTL,
        )
        self._token_pairs[new_access] = new_refresh

        logger.info("Rotated tokens for client %s", client.client_id)
        return OAuthToken(
            access_token=new_access,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL,
            refresh_token=new_refresh,
            scope=" ".join(scopes) if scopes else None,
        )

    # ── Access token validation ───────────────────────────────────────────

    async def load_access_token(self, token: str) -> AccessToken | None:
        entry = self._access_tokens.get(token)
        if entry is None:
            return None
        # Reject expired access tokens
        if entry.expires_at is not None and entry.expires_at < time.time():
            self._access_tokens.pop(token, None)
            return None
        return entry

    # ── Revocation ────────────────────────────────────────────────────────

    async def revoke_token(
        self, token: AccessToken | RefreshToken,
    ) -> None:
        if isinstance(token, AccessToken):
            self._access_tokens.pop(token.token, None)
            # Also revoke the paired refresh token
            paired_refresh = self._token_pairs.pop(token.token, None)
            if paired_refresh:
                self._refresh_tokens.pop(paired_refresh, None)
        elif isinstance(token, RefreshToken):
            self._refresh_tokens.pop(token.token, None)
            # Also revoke the paired access token
            for at, rt in list(self._token_pairs.items()):
                if rt == token.token:
                    self._access_tokens.pop(at, None)
                    del self._token_pairs[at]
                    break

    # ── Internal helpers ──────────────────────────────────────────────────

    def _evict_expired_codes(self) -> None:
        """Remove expired authorization codes."""
        now = time.time()
        expired = [
            code for code, entry in self._auth_codes.items()
            if entry.expires_at < now
        ]
        for code in expired:
            del self._auth_codes[code]
