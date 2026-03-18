"""Static bearer-token verifier for HTTP transport."""
from __future__ import annotations

import hmac
import logging

from mcp.server.auth.provider import AccessToken, TokenVerifier


logger = logging.getLogger(__name__)


class StaticBearerVerifier(TokenVerifier):
    """Verify bearer tokens against a static secret from the environment.

    Uses hmac.compare_digest to prevent timing attacks.
    """

    def __init__(self, expected_token: str) -> None:
        self._expected = expected_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if hmac.compare_digest(token, self._expected):
            return AccessToken(
                token=token,
                client_id="mcp-client",
                scopes=["*"],
                expires_at=None,
            )
        logger.warning("Bearer token authentication failed")
        return None
