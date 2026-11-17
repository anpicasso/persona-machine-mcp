"""Capability-gated stdio proxy for an authenticated Streamable HTTP server."""

from __future__ import annotations

import logging
import warnings
from contextlib import asynccontextmanager
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .auth import PersonaAuth, TokenMinter
from .config import Settings

log = logging.getLogger(__name__)


class UpstreamError(Exception):
    """An upstream connection or initialization failed."""


def make_minter(settings: Settings, *, resource: str | None) -> TokenMinter:
    if not settings.client_id:
        raise ValueError("This command requires a client id; run configure --client-id oac_...")
    if not settings.private_key_pem:
        raise ValueError("Missing private key; run keygen or set --key")
    return TokenMinter(
        settings.client_id,
        settings.private_key_pem,
        settings.token_url,
        resource,
        settings.scope,
    )


def tool_exposed(name: str, settings: Settings) -> bool:
    allowed = not settings.tools or any(fnmatchcase(name, pattern) for pattern in settings.tools)
    denied = any(fnmatchcase(name, pattern) for pattern in settings.exclude_tools)
    return allowed and not denied


async def collect_tools(upstream: ClientSession) -> list[types.Tool]:
    tools: list[types.Tool] = []
    cursor: str | None = None
    seen: set[str] = set()
    while True:
        page = await upstream.list_tools(cursor=cursor)
        tools.extend(page.tools)
        cursor = page.nextCursor
        if not cursor:
            return tools
        if cursor in seen:
            raise UpstreamError("Upstream returned a repeated tool pagination cursor")
        seen.add(cursor)


def create_server(
    upstream: ClientSession, caps: types.ServerCapabilities, settings: Settings
) -> Server:
    server: Server = Server("persona")
    if caps.tools is not None:

        @server.list_tools()
        async def list_tools() -> list[types.Tool]:
            return [
                tool for tool in await collect_tools(upstream) if tool_exposed(tool.name, settings)
            ]

        @server.call_tool(validate_input=False)
        async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
            if not tool_exposed(name, settings):
                return types.CallToolResult(
                    isError=True,
                    content=[
                        types.TextContent(type="text", text="tool not exposed by this bridge")
                    ],
                )
            return await upstream.call_tool(name, arguments)

        # Early MCP 1.x decorators treat a CallToolResult as an iterable, losing
        # isError/metadata. Keep the registration above, but return its raw result
        # at the protocol boundary consistently across the supported SDK range.
        async def call_request(request: types.CallToolRequest) -> types.ServerResult:
            return types.ServerResult(
                await call_tool(request.params.name, request.params.arguments or {})
            )

        server.request_handlers[types.CallToolRequest] = call_request

    if caps.resources is not None:

        @server.list_resources()
        async def list_resources() -> list[types.Resource]:
            return (await upstream.list_resources()).resources

        # The read_resource convenience decorator rebuilds contents (and their URIs).
        # Return the protocol result directly to preserve text/blob, MIME and metadata.
        async def read_resource(request: types.ReadResourceRequest) -> types.ServerResult:
            return types.ServerResult(await upstream.read_resource(request.params.uri))

        server.request_handlers[types.ReadResourceRequest] = read_resource

    if caps.prompts is not None:

        @server.list_prompts()
        async def list_prompts() -> list[types.Prompt]:
            return (await upstream.list_prompts()).prompts

        @server.get_prompt()
        async def get_prompt(name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
            return await upstream.get_prompt(name, arguments)

    return server


@asynccontextmanager
async def _upstream(settings: Settings, auth: httpx.Auth | None):
    try:
        # MCP 1.30 deprecated this name, but it is the shared API back to 1.12.
        # Suppress only its rename warning while constructing the context manager;
        # no await occurs while the process-wide warning filter is changed.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"Use `streamable_http_client` instead\.",
                category=DeprecationWarning,
            )
            connection = streamablehttp_client(settings.mcp_url, auth=auth)
        async with connection as (read, write, _):
            async with ClientSession(read, write) as upstream:
                initialized = await upstream.initialize()
                log.info("connected to upstream MCP server")
                yield upstream, initialized.capabilities
    except Exception as exc:
        # Do not copy upstream messages into diagnostics (they can contain credentials).
        raise UpstreamError("Upstream MCP connection or initialization failed") from exc


async def run_bridge(settings: Settings) -> None:
    auth: PersonaAuth | None = None
    if settings.client_id:
        minter = make_minter(settings, resource=settings.mcp_url)
        await minter.get()  # Fail before stdio opens; no TaskGroup traceback for bad credentials.
        auth = PersonaAuth(minter)
    else:
        log.warning("No client id configured; proxying WITHOUT auth (test mode)")
    async with _upstream(settings, auth) as (upstream, caps):
        server = create_server(upstream, caps, settings)
        async with stdio_server() as (stdin, stdout):
            await server.run(stdin, stdout, server.create_initialization_options())


@dataclass(frozen=True)
class CheckResult:
    tools: list[types.Tool]
    total: int


async def check(settings: Settings) -> CheckResult:
    minter = make_minter(settings, resource=settings.mcp_url)
    await minter.get()
    async with _upstream(settings, PersonaAuth(minter)) as (upstream, caps):
        tools = await collect_tools(upstream) if caps.tools is not None else []
        return CheckResult(
            tools=[tool for tool in tools if tool_exposed(tool.name, settings)], total=len(tools)
        )
