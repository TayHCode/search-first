"""Optional static bearer-token gate for the HTTP transport (pure ASGI, no extra deps)."""

from __future__ import annotations

import hmac
from typing import Any

MIN_TOKEN_LENGTH = 16


class BearerAuth:
    """Reject HTTP requests lacking ``Authorization: Bearer <token>``.

    A shared secret protects an endpoint from casual use and from burning the
    server's GitHub quota. It is not OAuth; use a gateway for per-user access.
    """

    def __init__(self, app: Any, token: str):
        if len(token) < MIN_TOKEN_LENGTH:
            raise ValueError(f"auth token must be at least {MIN_TOKEN_LENGTH} characters")
        self.app = app
        self.expected = token.encode()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            # No WebSocket or other protocol is served; never let them bypass the check.
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        scheme, _, supplied = (
            dict(scope["headers"]).get(b"authorization", b"").decode("latin-1").partition(" ")
        )
        if scheme.lower() == "bearer" and hmac.compare_digest(
            supplied.strip().encode(), self.expected
        ):
            await self.app(scope, receive, send)
            return
        body = b'{"error":"unauthorized"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
