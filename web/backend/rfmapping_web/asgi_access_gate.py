"""FastAPI/Starlette adapter for the shared access gate."""

from __future__ import annotations

import ipaddress
from pathlib import Path
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .access_gate import (
    CSRF_HEADER_NAME,
    AccessGate,
    cookie_value,
    csrf_cookie_header,
    expired_cookie_header,
    login_page_html,
    normalize_base_path,
    safe_return_url,
    same_origin_request,
    session_cookie_header,
)

LOGIN_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; connect-src 'self'; "
        "font-src 'self'; form-action 'self'; frame-ancestors 'none'; "
        "img-src 'self' data: blob:; object-src 'none'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def _route_path(path: str, base_path: str) -> str:
    if base_path and path == base_path:
        return "/"
    if base_path and path.startswith(f"{base_path}/"):
        return path[len(base_path) :] or "/"
    return path or "/"


def _external_path(path: str, base_path: str) -> str:
    if base_path and (path == base_path or path.startswith(f"{base_path}/")):
        return path
    normalized = path if path.startswith("/") else f"/{path}"
    return f"{base_path}{normalized}" if base_path else normalized


def _is_direct_loopback(scope: Scope, headers: Headers) -> bool:
    if headers.get("forwarded") or headers.get("x-forwarded-for"):
        return False
    client = scope.get("client")
    if not client:
        return False
    try:
        address = ipaddress.ip_address(client[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback


def _secure_request(scope: Scope) -> bool:
    return str(scope.get("scheme", "http")).casefold() == "https"


class AccessGateMiddleware:
    """Require a valid browser session before application routes or APIs."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        gate: AccessGate,
        cookie_name: str,
        csrf_cookie_name: str,
        base_path: str,
        public_paths: frozenset[str],
        loopback_public_paths: frozenset[str],
    ) -> None:
        self.app = app
        self.gate = gate
        self.cookie_name = cookie_name
        self.csrf_cookie_name = csrf_cookie_name
        self.base_path = normalize_base_path(base_path)
        self.cookie_path = self.base_path or "/"
        self.public_paths = public_paths
        self.loopback_public_paths = loopback_public_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", "/"))
        route_path = _route_path(path, self.base_path)
        headers = Headers(scope=scope)
        if (
            route_path == "/login"
            or route_path in self.public_paths
            or (
                route_path in self.loopback_public_paths
                and _is_direct_loopback(scope, headers)
            )
        ):
            await self.app(scope, receive, send)
            return

        token = cookie_value(headers.getlist("cookie"), self.cookie_name)
        if self.gate.validate_session(token):
            if str(scope.get("method", "GET")).upper() in {
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
            }:
                if not same_origin_request(
                    scheme=str(scope.get("scheme", "http")),
                    host=headers.get("host"),
                    origin=headers.get("origin"),
                    referer=headers.get("referer"),
                    sec_fetch_site=headers.get("sec-fetch-site"),
                ):
                    await self._security_error("invalid_origin", scope, receive, send)
                    return
                csrf_candidates = headers.getlist(CSRF_HEADER_NAME)
                if (
                    len(csrf_candidates) != 1
                    or not self.gate.validate_csrf(token, csrf_candidates[0])
                ):
                    await self._security_error("invalid_csrf", scope, receive, send)
                    return
            await self.app(scope, receive, send)
            return

        delete_headers: list[str] = []
        if token is not None:
            delete_headers.extend(
                (
                    expired_cookie_header(
                        self.cookie_name,
                        path=self.cookie_path,
                        secure=_secure_request(scope),
                    ),
                    expired_cookie_header(
                        self.csrf_cookie_name,
                        path=self.cookie_path,
                        secure=_secure_request(scope),
                    ),
                )
            )

        if route_path.startswith("/api/"):
            response = JSONResponse(
                {"detail": "Authentication required", "code": "login_required"},
                status_code=401,
                headers={"Cache-Control": "no-store"},
            )
            for delete_header in delete_headers:
                response.headers.append("Set-Cookie", delete_header)
            await response(scope, receive, send)
            return

        requested_path = _external_path(path, self.base_path)
        query_string = bytes(scope.get("query_string", b"")).decode("latin-1")
        if query_string:
            requested_path = f"{requested_path}?{query_string}"
        response = RedirectResponse(
            f"{self.base_path}/login?{urlencode({'next': requested_path})}",
            status_code=303,
        )
        response.headers["Cache-Control"] = "no-store"
        for delete_header in delete_headers:
            response.headers.append("Set-Cookie", delete_header)
        await response(scope, receive, send)

    @staticmethod
    async def _security_error(
        code: str,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        response = JSONResponse(
            {
                "detail": "The request security check failed.",
                "code": code,
            },
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
        await response(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, wrapped)


def install_access_gate(
    application: FastAPI,
    *,
    app_name: str,
    base_path: str,
    cookie_name: str,
    csrf_cookie_name: str,
    session_db: Path,
    public_paths: frozenset[str] = frozenset(),
    loopback_public_paths: frozenset[str] = frozenset(),
) -> AccessGate:
    normalized_base = normalize_base_path(base_path)
    cookie_path = normalized_base or "/"
    gate = AccessGate.from_environment(session_db=session_db)
    application.state.access_gate = gate

    async def login_page(request: Request) -> Response:
        return_to = safe_return_url(
            request.query_params.get("next", ""),
            base_path=normalized_base,
        )
        token = request.cookies.get(cookie_name)
        if gate.validate_session(token):
            return RedirectResponse(return_to, status_code=303)
        return HTMLResponse(
            login_page_html(
                app_name=app_name,
                action=f"{normalized_base}/login",
                return_to=return_to,
                error=False,
            ),
            headers=LOGIN_PAGE_HEADERS,
        )

    async def submit_login(request: Request) -> Response:
        content_length = request.headers.get("content-length", "0")
        try:
            body_size = int(content_length)
        except ValueError:
            body_size = 4097
        if body_size < 0 or body_size > 4096:
            return Response(status_code=413, headers=LOGIN_PAGE_HEADERS)
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
        if content_type != "application/x-www-form-urlencoded":
            return Response(status_code=415, headers=LOGIN_PAGE_HEADERS)
        try:
            body_buffer = bytearray()
            async for chunk in request.stream():
                body_buffer.extend(chunk)
                if len(body_buffer) > 4096:
                    return Response(status_code=413, headers=LOGIN_PAGE_HEADERS)
            body = bytes(body_buffer)
            fields = parse_qs(
                body.decode("utf-8"),
                keep_blank_values=True,
                max_num_fields=2,
                strict_parsing=False,
            )
        except (UnicodeDecodeError, ValueError):
            return Response(status_code=400, headers=LOGIN_PAGE_HEADERS)
        answer_values = fields.get("answer", [])
        next_values = fields.get("next", [])
        answer = answer_values[0] if len(answer_values) == 1 else ""
        next_value = next_values[0] if len(next_values) == 1 else ""
        return_to = safe_return_url(next_value, base_path=normalized_base)
        if not gate.validate_answer(answer):
            return HTMLResponse(
                login_page_html(
                    app_name=app_name,
                    action=f"{normalized_base}/login",
                    return_to=return_to,
                    error=True,
                ),
                status_code=401,
                headers=LOGIN_PAGE_HEADERS,
            )

        session_token, csrf_token = gate.issue_session()
        response = RedirectResponse(return_to, status_code=303)
        response.headers.append(
            "Set-Cookie",
            session_cookie_header(
                cookie_name,
                session_token,
                path=cookie_path,
                secure=request.url.scheme.casefold() == "https",
            ),
        )
        response.headers.append(
            "Set-Cookie",
            csrf_cookie_header(
                csrf_cookie_name,
                csrf_token,
                path=cookie_path,
                secure=request.url.scheme.casefold() == "https",
            ),
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    async def logout(request: Request) -> Response:
        gate.revoke_session(request.cookies.get(cookie_name))
        response = RedirectResponse(f"{normalized_base}/login", status_code=303)
        response.headers.append(
            "Set-Cookie",
            expired_cookie_header(
                cookie_name,
                path=cookie_path,
                secure=request.url.scheme.casefold() == "https",
            ),
        )
        response.headers.append(
            "Set-Cookie",
            expired_cookie_header(
                csrf_cookie_name,
                path=cookie_path,
                secure=request.url.scheme.casefold() == "https",
            ),
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    application.add_api_route(
        "/login", login_page, methods=["GET"], include_in_schema=False
    )
    application.add_api_route(
        "/login", submit_login, methods=["POST"], include_in_schema=False
    )
    application.add_api_route(
        "/logout", logout, methods=["POST"], include_in_schema=False
    )
    application.add_middleware(
        AccessGateMiddleware,
        gate=gate,
        cookie_name=cookie_name,
        csrf_cookie_name=csrf_cookie_name,
        base_path=normalized_base,
        public_paths=public_paths,
        loopback_public_paths=loopback_public_paths,
    )
    application.add_middleware(SecurityHeadersMiddleware)
    return gate
