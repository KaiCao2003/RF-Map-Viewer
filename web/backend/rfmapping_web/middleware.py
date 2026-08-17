from __future__ import annotations

import ipaddress
from typing import Any, Awaitable, Callable

from starlette.responses import JSONResponse


AsgiApp = Callable[
    [dict[str, Any], Callable[[], Awaitable[dict[str, Any]]], Callable[[dict[str, Any]], Awaitable[None]]],
    Awaitable[None],
]


class DirectAccessMiddleware:
    """Enforce the direct-port allowlist and accept an optional URL prefix."""

    def __init__(
        self,
        app: AsgiApp,
        *,
        allowed_networks: tuple[str, ...],
        prefix: str = "/rfmapping",
    ) -> None:
        self.app = app
        self.networks = tuple(
            ipaddress.ip_network(value, strict=False) for value in allowed_networks
        )
        self.prefix = prefix.rstrip("/")

    @staticmethod
    async def _error(
        scope: dict[str, Any], receive: Any, send: Any, status: int, detail: str
    ) -> None:
        await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)

    def _client_allowed(self, scope: dict[str, Any]) -> bool:
        client = scope.get("client")
        if not client:
            return False
        host = client[0]
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return False
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return any(
            address.version == network.version and address in network
            for network in self.networks
        )

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        if not self._client_allowed(scope):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await self._error(
                    scope, receive, send, 403, "Client network is not allowed"
                )
            return

        path = scope.get("path", "")
        if path == self.prefix:
            stripped = "/"
        elif path.startswith(self.prefix + "/"):
            stripped = path[len(self.prefix) :]
        else:
            stripped = path
        if stripped != path:
            scope = dict(scope)
            scope["path"] = stripped
            scope["raw_path"] = stripped.encode("utf-8")
        await self.app(scope, receive, send)
