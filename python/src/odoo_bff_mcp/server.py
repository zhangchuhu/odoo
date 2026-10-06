"""Official MCP SDK v2 adapter; return protocol results without automatic wrapping."""

import json
from contextlib import asynccontextmanager

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp_types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations

from .client import BffClient
from .config import Settings
from .dispatcher import TOOL_MODELS, Dispatcher

DESCRIPTIONS = {
    "listing_module_router": "查看 Odoo BFF 模块及支持范围。本地目录，不发 HTTP。",
    "listing_operations_for_modules": "按模块获取 operation 及参数 schema。模块 key: odoo_meli。",
    "listing_list_operations": "列出 Odoo BFF 的全部九项 operation 和参数 schema。本地目录。",
    "listing_call": "执行 Odoo BFF operation。先查询目录；shopId 来自 odoo_shops_list。"
    "包含真实创建、更新和上传操作，按用户意图调用；权限由 Odoo API Key 决定。",
}


def create_server(settings: Settings) -> Server:
    @asynccontextmanager
    async def lifespan(server):
        async with BffClient(settings) as client:
            yield Dispatcher(client)

    async def list_tools(ctx, params):
        return ListToolsResult(
            tools=[
                Tool(
                    name=name,
                    description=DESCRIPTIONS[name],
                    inputSchema=model.model_json_schema(),
                    annotations=ToolAnnotations(
                        readOnlyHint=name != "listing_call",
                        destructiveHint=name == "listing_call",
                        openWorldHint=name == "listing_call",
                    ),
                )
                for name, model in TOOL_MODELS.items()
            ]
        )

    async def call_tool(ctx, params):
        result = await ctx.lifespan_context.tool(params.name, params.arguments)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(result.payload, ensure_ascii=False, allow_nan=False),
                )
            ],
            isError=result.is_error,
        )

    return Server(
        "odoo-bff-mcp",
        version="0.1.0",
        lifespan=lifespan,
        instructions="使用 listing_list_operations 查询当前 Odoo BFF 能力和参数。"
        "仅 odoo_meli 模块，不支持 Amazon 或任意平台代理。",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def run_stdio(settings: Settings):
    server = create_server(settings)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
