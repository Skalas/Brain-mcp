"""Streamable HTTP transport + bearer auth (no real Obsidian vault)."""
from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Iterator

import anyio
import httpx
import pytest
import uvicorn

from brain_mcp.http import (
    TOKEN_ENV,
    BearerAuthMiddleware,
    create_http_app,
    parse_argv,
    require_token,
)
from brain_mcp.server import main, mcp


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def http_token() -> str:
    return "test-http-token-not-for-production"


@pytest.fixture
def http_server(http_token: str) -> Iterator[tuple[str, str]]:
    """Live uvicorn serving the same FastMCP tools as stdio, on a free port."""
    app = create_http_app(http_token, host="127.0.0.1")
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 8
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError("HTTP server thread exited before listen")
        if time.time() > deadline:
            raise TimeoutError("HTTP server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp", http_token
    server.should_exit = True
    thread.join(timeout=5)


def test_parse_argv_defaults_to_stdio():
    cfg = parse_argv([], env={})
    assert cfg.transport == "stdio"
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8765


def test_parse_argv_http_flag_and_env():
    assert parse_argv(["--http"], env={}).transport == "http"
    assert parse_argv([], env={"BRAIN_MCP_TRANSPORT": "http"}).transport == "http"
    assert parse_argv([], env={"BRAIN_MCP_TRANSPORT": "streamable-http"}).transport == "http"
    assert parse_argv(["--transport", "http"], env={}).transport == "http"
    # CLI wins over env
    assert parse_argv(["--transport", "stdio"], env={"BRAIN_MCP_TRANSPORT": "http"}).transport == "stdio"


def test_parse_argv_host_port():
    cfg = parse_argv(["--http", "--host", "0.0.0.0", "--port", "9001"], env={})
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 9001
    cfg = parse_argv(["--http"], env={"BRAIN_MCP_HOST": "10.0.0.2", "BRAIN_MCP_PORT": "9123"})
    assert cfg.host == "10.0.0.2"
    assert cfg.port == 9123


def test_require_token_rejects_missing(monkeypatch):
    with pytest.raises(SystemExit, match="BRAIN_MCP_TOKEN"):
        require_token(env={})
    with pytest.raises(SystemExit, match="BRAIN_MCP_TOKEN"):
        require_token(env={TOKEN_ENV: "   "})
    assert require_token(env={TOKEN_ENV: "secret"}) == "secret"


def test_main_defaults_to_stdio(monkeypatch):
    called: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> None:
        called["args"] = args
        called["kwargs"] = kwargs

    monkeypatch.setattr(mcp, "run", fake_run)
    main([])
    assert called["args"] == ()
    assert called["kwargs"] == {}


def test_main_http_requires_token(monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    with pytest.raises(SystemExit, match="BRAIN_MCP_TOKEN"):
        main(["--http"])


def test_create_http_app_rejects_empty_token():
    with pytest.raises(ValueError, match="non-empty"):
        create_http_app("")


def test_http_mode_starts(http_server: tuple[str, str]):
    url, _token = http_server
    # Server is up: unauthenticated POST is answered (401), not a connection error.
    response = httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, timeout=5)
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_unauthenticated_requests_fail(http_server: tuple[str, str], http_token: str):
    url, token = http_server
    assert token == http_token
    for headers in ({}, {"Authorization": "Bearer wrong-token"}):
        response = httpx.post(
            url,
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            timeout=5,
        )
        assert response.status_code == 401
        assert "www-authenticate" in response.headers
        assert token not in response.text
        assert "wrong-token" not in response.text


def test_token_is_not_logged(http_server: tuple[str, str], http_token: str, caplog: pytest.LogCaptureFixture):
    url, token = http_server
    with caplog.at_level(logging.DEBUG):
        httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, timeout=5)
        httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            timeout=5,
        )
    assert http_token not in caplog.text


def test_authenticated_lists_and_calls_tool(http_server: tuple[str, str]):
    url, token = http_server

    async def _exercise() -> None:
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(headers=headers, timeout=10) as http_client:
            async with streamable_http_client(url, http_client=http_client) as (
                read,
                write,
                _session_id,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    names = {tool.name for tool in listed.tools}
                    assert "list_kinds" in names
                    assert "get_doctrine" in names
                    assert "search_notes" in names
                    result = await session.call_tool("get_doctrine", {})
                    assert result.isError is False
                    text = "".join(
                        block.text for block in result.content if getattr(block, "text", None)
                    )
                    assert "Doctrine" in text

    anyio.run(_exercise)


def test_bearer_middleware_forwards_non_http():
    """Lifespan must reach the inner app so StreamableHTTPSessionManager can start."""
    seen: list[str] = []

    async def inner(scope: dict, receive: object, send: object) -> None:
        seen.append(scope["type"])

    wrapped = BearerAuthMiddleware(inner, "tok")

    async def _run() -> None:
        await wrapped({"type": "lifespan"}, None, None)

    anyio.run(_run)
    assert seen == ["lifespan"]
