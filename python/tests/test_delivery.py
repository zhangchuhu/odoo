import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from odoo_bff_mcp.client import BffClient
from odoo_bff_mcp.config import load_settings
from odoo_bff_mcp.dispatcher import Dispatcher

ROOT = Path(__file__).resolve().parents[1]


async def test_total_deadline():
    settings = load_settings(
        {
            "ODOO_BFF_BASE_URL": "https://example.test",
            "ODOO_BFF_API_KEY": "secret",
            "ODOO_BFF_TIMEOUT_SECONDS": "0.01",
        }
    )
    calls = []

    async def delayed(request):
        calls.append(request)
        await asyncio.sleep(10)
        return httpx.Response(200)

    async with BffClient(settings, transport=httpx.MockTransport(delayed)) as client:
        result = await Dispatcher(client).call("odoo_shops_list", {})
    assert result.payload["kind"] == "timeout"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "url", ["https://example.test/%5csecret", "https://example.test/%00hidden"]
)
def test_encoded_control_config(url):
    with pytest.raises(ValueError):
        load_settings({"ODOO_BFF_BASE_URL": url, "ODOO_BFF_API_KEY": "secret"})


def test_smoke_offline():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/smoke_stdio.py")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "9 operations" in result.stdout


def test_smoke_read_requires_configuration():
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ODOO_BFF_", "HAISHANG_ERP_"))}
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/smoke_read.py")],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "configuration" in result.stderr.lower()


def test_example_configs():
    for name in ("openclaw.json", "mcporter.json"):
        config = json.loads((ROOT / "examples" / name).read_text())
        servers = config["mcp"]["servers"] if name == "openclaw.json" else config["mcpServers"]
        entry = servers["odoo_bff"]
        assert entry["args"][:2] == ["-m", "odoo_bff_mcp"]
        assert entry["args"][2] == "--env-file"
