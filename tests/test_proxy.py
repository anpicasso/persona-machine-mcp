import socket
import sys
import threading
import time
from contextlib import asynccontextmanager

import anyio
import pytest
import uvicorn
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp import FastMCP


@pytest.fixture(scope="module")
def upstream():
    server = FastMCP("offline-fixture")

    @server.tool()
    def add(a: int, b: int) -> int:
        return a + b

    @server.tool()
    def list_items() -> str:
        return "items"

    @server.tool()
    def explode() -> str:
        raise ValueError("intentional upstream failure")

    @server.resource("test://greeting")
    def greeting() -> str:
        return "Hello from upstream"

    @server.prompt()
    def welcome(name: str) -> str:
        return f"Welcome, {name}!"

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        service = uvicorn.Server(
            uvicorn.Config(
                server.streamable_http_app(),
                log_level="warning",
                ws="none",
            )
        )
        thread = threading.Thread(target=service.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if service.started:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            if not thread.is_alive():
                pytest.fail("upstream thread exited during startup")
            time.sleep(0.01)
        else:
            service.should_exit = True
            thread.join(timeout=5)
            pytest.fail("upstream did not become ready")
        try:
            yield f"http://127.0.0.1:{port}/mcp"
        finally:
            service.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), "upstream did not shut down"


@asynccontextmanager
async def bridge(upstream, tmp_path, *extra):
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-X",
            "dev",
            "-m",
            "persona_machine_mcp",
            "serve",
            "--mcp-url",
            upstream,
            "--home",
            str(tmp_path),
            *extra,
        ],
        env={"PERSONA_MCP_HOME": str(tmp_path)},
    )
    with anyio.fail_after(30):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                yield session, initialized


@pytest.mark.anyio
async def test_real_stdio_passthrough(upstream, tmp_path):
    async with bridge(upstream, tmp_path) as (session, init):
        assert init.serverInfo.name == "persona"
        assert init.capabilities.tools is not None
        assert init.capabilities.resources is not None
        assert init.capabilities.prompts is not None
        tools = (await session.list_tools()).tools
        assert {tool.name for tool in tools} == {"add", "list_items", "explode"}
        added = await session.call_tool("add", {"a": 2, "b": 3})
        assert not added.isError
        assert added.content[0].text == "5"
        failed = await session.call_tool("explode", {})
        assert failed.isError
        assert "intentional upstream failure" in failed.content[0].text
        invalid = await session.call_tool("add", {"a": "invalid"})
        assert invalid.isError
        assert "validation" in invalid.content[0].text.lower()
        resources = (await session.list_resources()).resources
        assert [str(resource.uri) for resource in resources] == ["test://greeting"]
        read = await session.read_resource("test://greeting")
        assert isinstance(read.contents[0], types.TextResourceContents)
        assert read.contents[0].text == "Hello from upstream"
        prompts = (await session.list_prompts()).prompts
        assert [prompt.name for prompt in prompts] == ["welcome"]
        prompt = await session.get_prompt("welcome", {"name": "Ada"})
        assert prompt.messages[0].content.text == "Welcome, Ada!"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "options,visible,denied",
    [
        (["--tools", "list_*"], {"list_items"}, "add"),
        (["--tools", "*", "--exclude-tools", "ex*"], {"add", "list_items"}, "explode"),
        (["--tools", "add", "--tools", "list_*", "--exclude-tools", "add"], {"list_items"}, "add"),
    ],
)
async def test_filtered_tools_hidden_and_calls_denied(upstream, tmp_path, options, visible, denied):
    async with bridge(upstream, tmp_path, *options) as (session, _):
        assert {tool.name for tool in (await session.list_tools()).tools} == visible
        result = await session.call_tool(denied, {})
        assert result.isError
        assert result.content[0].text == "tool not exposed by this bridge"


@pytest.mark.anyio
async def test_paginated_tools_and_capability_gating(tmp_path):
    from persona_machine_mcp.config import Settings
    from persona_machine_mcp.proxy import collect_tools, create_server

    class Upstream:
        def __init__(self):
            self.cursors = []

        async def list_tools(self, cursor=None):
            self.cursors.append(cursor)
            return types.ListToolsResult(
                tools=[types.Tool(name="first" if cursor is None else "second", inputSchema={})],
                nextCursor="page-two" if cursor is None else None,
            )

    session = Upstream()
    assert [tool.name for tool in await collect_tools(session)] == ["first", "second"]
    assert session.cursors == [None, "page-two"]
    settings = Settings(home=tmp_path, key_path=None, client_id_path=tmp_path / "client_id")
    server = create_server(session, types.ServerCapabilities(), settings)
    caps = server.create_initialization_options().capabilities
    assert caps.tools is None
    assert caps.resources is None
    assert caps.prompts is None


@pytest.mark.anyio
async def test_raw_call_result_metadata_and_errors_are_preserved(tmp_path):
    from persona_machine_mcp.config import Settings
    from persona_machine_mcp.proxy import create_server

    expected = types.CallToolResult(
        content=[types.TextContent(type="text", text="upstream failure")],
        isError=True,
        _meta={"trace": "opaque"},
    )

    class Upstream:
        async def call_tool(self, name, arguments):
            return expected

    settings = Settings(home=tmp_path, key_path=None, client_id_path=tmp_path / "client_id")
    server = create_server(
        Upstream(), types.ServerCapabilities(tools=types.ToolsCapability()), settings
    )
    result = await server.request_handlers[types.CallToolRequest](
        types.CallToolRequest(method="tools/call", params=types.CallToolRequestParams(name="fail"))
    )
    assert result.root == expected


def test_check_cli_with_real_upstream_and_mock_token(upstream, tmp_path, monkeypatch, capsys):
    import httpx

    from persona_machine_mcp import auth, proxy
    from persona_machine_mcp.cli import main

    assert main(["keygen", "--home", str(tmp_path)]) == 0
    capsys.readouterr()
    original = auth.TokenMinter
    monkeypatch.setattr(
        proxy,
        "TokenMinter",
        lambda *args, **kwargs: original(
            *args,
            **kwargs,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"access_token": "offline-only"})
            ),
        ),
    )
    assert (
        main(
            [
                "check",
                "--home",
                str(tmp_path),
                "--client-id",
                "oac_fixture",
                "--mcp-url",
                upstream,
                "--tools",
                "list_*",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == "OK: 1 tools (1/3)\nlist_items\n"


def test_actual_upstream_connect_failure_exits_five(tmp_path):
    import subprocess

    # Reserve an unlistening port so no unrelated process can accept it.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"
        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "dev",
                "-m",
                "persona_machine_mcp",
                "serve",
                "--home",
                str(tmp_path),
                "--mcp-url",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
    assert result.returncode == 5
    assert result.stdout == ""
    assert "WITHOUT auth" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.anyio
async def test_repeated_pagination_cursor_is_bounded():
    from persona_machine_mcp.proxy import UpstreamError, collect_tools

    class RepeatingUpstream:
        async def list_tools(self, cursor=None):
            return types.ListToolsResult(tools=[], nextCursor="same")

    with pytest.raises(UpstreamError, match="repeated"):
        await collect_tools(RepeatingUpstream())
