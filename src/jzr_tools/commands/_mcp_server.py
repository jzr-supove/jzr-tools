"""The MCP server of `jzr agent mcp`. Needs the optional ``mcp`` package."""

from __future__ import annotations

from functools import partial

import anyio
import anyio.to_thread
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
    ToolAnnotations,
)

from jzr_tools import __version__
from jzr_tools.commands import _mcp_tools

_INSTRUCTIONS = (
    "jzr works on files and processes of this computer. Give absolute paths. "
    "Image, video, audio and PDF tools write new files beside their sources and "
    "name them in the result; data_convert prints text unless given an output. "
    "No tool replaces a file: when the target exists, give another outdir or "
    "output. file_rename only previews; file_rename_apply renames. "
    "Ask the user before calling a tool marked destructive. When ffmpeg is missing, "
    "ask the user to run: jzr ffmpeg install"
)


def build_server() -> Server:
    """A server that offers every jzr tool."""
    listed = [_tool(spec) for spec in _mcp_tools.tools()]
    # A command replaces the process's output streams while it runs.
    one_at_a_time = anyio.Lock()

    async def list_tools(
        _ctx: ServerRequestContext, _params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        return ListToolsResult(tools=listed)

    async def call_tool(_ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
        async with one_at_a_time:
            result = await anyio.to_thread.run_sync(
                partial(_mcp_tools.call, params.name, params.arguments)
            )
        data = result.data
        if data is not None and not isinstance(data, dict):
            data = {"result": data}
        return CallToolResult(
            content=[TextContent(type="text", text=result.text)],
            structured_content=data,
            is_error=result.is_error,
        )

    return Server(
        "jzr",
        version=__version__,
        instructions=_INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def _tool(spec: _mcp_tools.ToolSpec) -> Tool:
    read_only = spec.exposed.kind == "read"
    return Tool(
        name=spec.name,
        description=spec.description,
        input_schema=spec.schema,
        annotations=ToolAnnotations(
            read_only_hint=read_only,
            # Only means something for tools that change things.
            destructive_hint=None if read_only else spec.exposed.kind == "destructive",
            open_world_hint=False,
        ),
    )


async def _serve() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def serve() -> None:
    """Serve over stdin and stdout until the client closes them."""
    try:
        anyio.run(_serve)
    except KeyboardInterrupt:
        pass
