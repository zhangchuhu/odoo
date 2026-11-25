import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

ENV = {"ODOO_BFF_BASE_URL": "http://127.0.0.1:1", "ODOO_BFF_API_KEY": "stdio-test-key"}
TOOLS = {
    "listing_module_router",
    "listing_operations_for_modules",
    "listing_list_operations",
    "listing_call",
}


async def test_sdk_client(tmp_path):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "odoo_bff_mcp"], env=ENV, cwd=tmp_path
    )
    async with Client(params, read_timeout_seconds=10) as client:
        listed = await client.list_tools()
        assert {tool.name for tool in listed.tools} == TOOLS
        for tool in listed.tools:
            assert tool.input_schema["additionalProperties"] is False
            assert tool.annotations.read_only_hint == (tool.name != "listing_call")
        catalog = await client.call_tool("listing_list_operations", {})
        assert not catalog.is_error
        assert json.loads(catalog.content[0].text)["operationCount"] == 9
        bad = await client.call_tool("listing_call", {"operation": "missing"})
        assert bad.is_error
        assert json.loads(bad.content[0].text)["code"] == "UNKNOWN_OPERATION"
        for args in [
            {"operation": "odoo_shops_list", "arguments": None},
            {"operation": "odoo_shops_list", "token": "secret"},
        ]:
            bad = await client.call_tool("listing_call", args)
            assert bad.is_error


@asynccontextmanager
async def raw_server(env=None, args=()):
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "odoo_bff_mcp",
        *args,
        env=os.environ | ENV | (env or {}),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        yield proc
    finally:
        if proc.stdin and not proc.stdin.is_closing():
            proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), 10)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            pytest.fail("Server did not close after stdin EOF")


async def exchange(proc, message):
    proc.stdin.write(json.dumps(message).encode() + b"\n")
    await proc.stdin.drain()
    while True:
        line = await asyncio.wait_for(proc.stdout.readline(), 10)
        assert line, (await proc.stderr.read()).decode()
        reply = json.loads(line)  # Fails if logs contaminate stdout.
        if reply.get("id") == message["id"]:
            return reply


async def initialize(proc):
    reply = await exchange(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
    )
    assert "result" in reply
    proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    await proc.stdin.drain()


async def test_legacy_stdio():
    async with raw_server() as proc:
        await initialize(proc)
        reply = await exchange(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert {tool["name"] for tool in reply["result"]["tools"]} == TOOLS
        reply = await exchange(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "listing_module_router", "arguments": {}},
            },
        )
        assert json.loads(reply["result"]["content"][0]["text"])["totalOperations"] == 9
    assert proc.returncode == 0
    assert b"stdio-test-key" not in await proc.stderr.read()


async def test_stdio_success_through_http(tmp_path):
    captured = []

    async def handle(reader, writer):
        request = await reader.readuntil(b"\r\n\r\n")
        captured.append(request)
        data = b'{"shops":[{"id":3,"name":"test"}]}'
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(data)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + data
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    http_server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = http_server.sockets[0].getsockname()[1]
    env_file = tmp_path / "test.env"
    env_file.write_text(f"ODOO_BFF_BASE_URL=http://127.0.0.1:{port}\nODOO_BFF_API_KEY=file-token\n")
    async with (
        http_server,
        raw_server(
            {"ODOO_BFF_BASE_URL": f"http://127.0.0.1:{port}"}, args=("--env-file", str(env_file))
        ) as proc,
    ):
        await initialize(proc)
        reply = await exchange(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "listing_call", "arguments": {"operation": "odoo_shops_list"}},
            },
        )
        payload = json.loads(reply["result"]["content"][0]["text"])
        assert payload == {"httpStatus": 200, "body": {"shops": [{"id": 3, "name": "test"}]}}
        assert not reply["result"].get("isError", False)
    assert b"GET /api/meli/shops HTTP/1.1" in captured[0]
    assert b"Bearer stdio-test-key" in captured[0]  # Process env overrides the explicit file.


async def test_missing_config():
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ODOO_BFF_", "HAISHANG_ERP_"))}
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "odoo_bff_mcp",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), 10)
    assert proc.returncode == 1
    assert stdout == b""
    assert b"configuration" in stderr.lower()
