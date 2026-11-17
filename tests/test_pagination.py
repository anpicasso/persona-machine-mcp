"""Resource and prompt discovery must follow nextCursor like tools (review SHOULD-FIX)."""

from __future__ import annotations

import pytest
from mcp import types

from persona_machine_mcp.proxy import (
    UpstreamError,
    collect_prompts,
    collect_resources,
    collect_tools,
)


class PagedUpstream:
    def __init__(self, pages: dict[str | None, tuple[list, str | None]]):
        self.pages = pages
        self.cursors: list[str | None] = []

    async def _page(self, cursor, result_type, field):
        self.cursors.append(cursor)
        items, next_cursor = self.pages[cursor]
        return result_type(**{field: items}, nextCursor=next_cursor)

    async def list_tools(self, cursor=None):
        return await self._page(cursor, types.ListToolsResult, "tools")

    async def list_resources(self, cursor=None):
        return await self._page(cursor, types.ListResourcesResult, "resources")

    async def list_prompts(self, cursor=None):
        return await self._page(cursor, types.ListPromptsResult, "prompts")


def _tool(name):
    return types.Tool(name=name, inputSchema={"type": "object"})


def _resource(name):
    return types.Resource(name=name, uri=f"res://{name}")


def _prompt(name):
    return types.Prompt(name=name)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("collect", "make"),
    [(collect_tools, _tool), (collect_resources, _resource), (collect_prompts, _prompt)],
)
async def test_follows_all_pages(collect, make):
    upstream = PagedUpstream(
        {None: ([make("a"), make("b")], "p2"), "p2": ([make("c")], "p3"), "p3": ([make("d")], None)}
    )
    items = await collect(upstream)
    assert [item.name for item in items] == ["a", "b", "c", "d"]
    assert upstream.cursors == [None, "p2", "p3"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("collect", "make"),
    [(collect_tools, _tool), (collect_resources, _resource), (collect_prompts, _prompt)],
)
async def test_rejects_cursor_loops(collect, make):
    upstream = PagedUpstream({None: ([make("a")], "p2"), "p2": ([make("b")], "p2")})
    with pytest.raises(UpstreamError, match="repeated"):
        await collect(upstream)
