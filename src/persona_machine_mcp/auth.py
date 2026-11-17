"""Short-lived private_key_jwt assertions and in-memory bearer lifecycle."""

from __future__ import annotations

import logging
import math
import secrets
import time

import anyio
import httpx
import jwt

from .config import validate_client_id
from .keys import load_private_key

ASSERTION_TTL_S = 300
REFRESH_SKEW_S = 90
log = logging.getLogger(__name__)


class TokenError(Exception):
    """Safe diagnostic: never include endpoint response bodies or credentials."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Persona token endpoint failed ({code})")


class TokenMinter:
    """Serialize minting and cache tokens until 90 seconds before expiry."""

    def __init__(
        self,
        client_id: str,
        pem: bytes | str,
        token_url: str,
        resource: str | None,
        scope: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.client_id = validate_client_id(client_id)
        self.token_url = token_url
        self.resource = resource
        self.scope = scope
        self._key, self.alg = load_private_key(pem)
        self._transport = transport
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = anyio.Lock()

    def _assertion(self) -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "iss": self.client_id,
                "sub": self.client_id,
                "aud": self.token_url,
                "iat": now,
                "exp": now + ASSERTION_TTL_S,
                "jti": secrets.token_urlsafe(32),
            },
            self._key,
            algorithm=self.alg,
        )

    async def get(self, *, force: bool = False) -> str:
        async with self._lock:
            if not force and self._token and time.time() < self._expires_at - REFRESH_SKEW_S:
                return self._token
            data = {
                "grant_type": "client_credentials",
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": self._assertion(),
            }
            if self.resource is not None:
                data["resource"] = self.resource
            if self.scope:
                data["scope"] = self.scope
            try:
                async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
                    response = await client.post(self.token_url, data=data)
            except httpx.HTTPError:
                raise TokenError("network") from None
            try:
                body = response.json()
            except ValueError:
                raise TokenError(f"http_{response.status_code}") from None
            if response.status_code != 200:
                error = body.get("error") if isinstance(body, dict) else None
                code = (
                    error
                    if error in ("invalid_client", "invalid_grant")
                    else (f"http_{response.status_code}")
                )
                raise TokenError(code)
            try:
                token = body["access_token"]
                ttl = float(body.get("expires_in", 900))
                if not isinstance(token, str) or not token or not math.isfinite(ttl) or ttl <= 0:
                    raise ValueError
                if any(c.isspace() for c in token) or not token.isascii():
                    raise ValueError
            except (KeyError, TypeError, ValueError, OverflowError):
                raise TokenError("http_200") from None
            self._token = token
            self._expires_at = time.time() + ttl
            log.info("minted access token (alg=%s, expires_in=%ss)", self.alg, ttl)
            return token


class PersonaAuth(httpx.Auth):
    """Fresh bearer per request, one forced mint and retry on 401."""

    requires_response_body = False

    def __init__(self, minter: TokenMinter):
        self.minter = minter

    def sync_auth_flow(self, request):
        raise RuntimeError("PersonaAuth is async-only")

    async def async_auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {await self.minter.get()}"
        response = yield request
        if response.status_code == 401:
            log.warning("upstream 401; re-minting token and retrying once")
            request.headers["Authorization"] = f"Bearer {await self.minter.get(force=True)}"
            yield request
