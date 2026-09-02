"""Streamable HTTP transport + static bearer auth for remote MCP clients.

Stdio remains the default (see ``brain_mcp.server:main``). This module is only
used when HTTP mode is selected via ``--http`` / ``--transport`` /
``BRAIN_MCP_TRANSPORT``.
"""
from __future__ import annotations

import argparse
import hmac
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from starlette.types import ASGIApp, Receive, Scope, Send

from mcp.server.transport_security import TransportSecuritySettings


logger = logging.getLogger(__name__)

TOKEN_ENV = "BRAIN_MCP_TOKEN"
TRANSPORT_ENV = "BRAIN_MCP_TRANSPORT"
HOST_ENV = "BRAIN_MCP_HOST"
PORT_ENV = "BRAIN_MCP_PORT"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MCP_PATH = "/mcp"

Transport = Literal["stdio", "http"]


@dataclass(frozen=True)
class ServerConfig:
    transport: Transport
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT


def parse_argv(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> ServerConfig:
    """Parse CLI flags and env into a server config. Stdio is the default."""
    environ = os.environ if env is None else env
    parser = argparse.ArgumentParser(
        prog="brain-mcp",
        description="MCP server for an Obsidian second-brain vault.",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Serve MCP over streamable HTTP (requires BRAIN_MCP_TOKEN).",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "http", "streamable-http"),
        default=None,
        help="Transport to use. Default: stdio (or BRAIN_MCP_TRANSPORT).",
    )
    parser.add_argument(
        "--host",
        default=None,
        help=f"HTTP bind host (default: {DEFAULT_HOST} or {HOST_ENV}).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"HTTP bind port (default: {DEFAULT_PORT} or {PORT_ENV}).",
    )
    args = parser.parse_args(argv)

    env_transport = (environ.get(TRANSPORT_ENV) or "").strip().lower()
    if args.transport is not None:
        transport: Transport = (
            "http" if args.transport in ("http", "streamable-http") else "stdio"
        )
    elif args.http:
        transport = "http"
    elif env_transport in ("http", "streamable-http"):
        transport = "http"
    else:
        transport = "stdio"

    host = args.host or environ.get(HOST_ENV) or DEFAULT_HOST
    if args.port is not None:
        port = args.port
    else:
        raw_port = environ.get(PORT_ENV)
        port = int(raw_port) if raw_port else DEFAULT_PORT
    if not 1 <= port <= 65535:
        parser.error(f"port must be in 1..65535, got {port}")

    return ServerConfig(transport=transport, host=host, port=port)


def require_token(env: Mapping[str, str] | None = None) -> str:
    """Return BRAIN_MCP_TOKEN or raise SystemExit. Never logs the value."""
    environ = os.environ if env is None else env
    token = (environ.get(TOKEN_ENV) or "").strip()
    if not token:
        raise SystemExit(
            f"{TOKEN_ENV} must be set to a non-empty bearer token when running HTTP mode."
        )
    return token


def _is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


def _bearer_matches(header: bytes, expected_token: bytes) -> bool:
    """Constant-time compare of `Authorization: Bearer <token>`. Never logs."""
    prefix = b"bearer "
    if len(header) < len(prefix) or header[: len(prefix)].lower() != prefix:
        return False
    provided = header[len(prefix) :].strip()
    if len(provided) != len(expected_token):
        hmac.compare_digest(expected_token, expected_token)
        return False
    return hmac.compare_digest(provided, expected_token)


class BearerAuthMiddleware:
    """Reject HTTP requests that lack a matching bearer token.

    Lifespan and other non-HTTP ASGI scopes are forwarded unchanged so the
    SDK's StreamableHTTPSessionManager can start.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._expected = token.encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        auth = b""
        for key, value in scope.get("headers", []):
            if key.lower() == b"authorization":
                auth = value
                break

        if not _bearer_matches(auth, self._expected):
            body = b'{"error":"unauthorized"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)


def _configure_http_settings(host: str, port: int) -> None:
    """Point the existing FastMCP instance at this bind address."""
    from .server import mcp

    mcp.settings.host = host
    mcp.settings.port = port
    # Constructor enabled localhost DNS-rebinding protection. Keep that on
    # loopback; turn it off when binding a public/wildcard interface so a
    # remote Host header is not rejected (bearer auth is the access control).
    if _is_loopback(host):
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=[
                "http://127.0.0.1:*",
                "http://localhost:*",
                "http://[::1]:*",
            ],
        )
    else:
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        )


def create_http_app(token: str, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> ASGIApp:
    """Build the SDK streamable-HTTP app wrapped with bearer auth.

    Uses the same FastMCP instance (and therefore the same tools) as stdio.
    Resets the session manager so the app can be constructed more than once
    (tests start/stop the server).
    """
    token = token.strip()
    if not token:
        raise ValueError("HTTP mode requires a non-empty bearer token")

    from .server import mcp

    _configure_http_settings(host, port)
    # StreamableHTTPSessionManager.run() is single-use; rebuild per app.
    mcp._session_manager = None
    inner = mcp.streamable_http_app()
    return BearerAuthMiddleware(inner, token)


def run_http(host: str, port: int, token: str | None = None) -> None:
    """Serve streamable HTTP until the process is stopped."""
    import uvicorn

    resolved = token if token is not None else require_token()
    app = create_http_app(resolved, host=host, port=port)
    logger.info("brain-mcp streamable HTTP listening on http://%s:%s%s", host, port, MCP_PATH)
    uvicorn.run(app, host=host, port=port, log_level="info")
