"""Offline smoke: real MCP subprocess, discovery and local errors only."""

import asyncio
import json
import sys
import tempfile

from mcp import Client
from mcp.client.stdio import StdioServerParameters


async def main():
    with tempfile.TemporaryDirectory() as cwd:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "odoo_bff_mcp"],
            env={"ODOO_BFF_BASE_URL": "http://127.0.0.1:1", "ODOO_BFF_API_KEY": "offline-test"},
            cwd=cwd,
        )
        async with Client(params, read_timeout_seconds=10) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "listing_module_router",
                "listing_operations_for_modules",
                "listing_list_operations",
                "listing_call",
            }
            result = await client.call_tool("listing_list_operations", {})
            assert not result.is_error
            assert json.loads(result.content[0].text)["operationCount"] == 9
            result = await client.call_tool("listing_call", {"operation": "unknown"})
            assert result.is_error
            assert json.loads(result.content[0].text)["code"] == "UNKNOWN_OPERATION"
    print("OK: stdio, 4 tools, 9 operations, local error envelope; no ERP request")


if __name__ == "__main__":
    asyncio.run(main())
