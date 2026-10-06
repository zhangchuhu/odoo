"""Explicit real BFF read only; no platform writes or automatic invocation."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from odoo_bff_mcp.__main__ import read_environment
from odoo_bff_mcp.client import BffClient
from odoo_bff_mcp.config import load_settings
from odoo_bff_mcp.dispatcher import Dispatcher


async def run(settings, shop_id):
    async with BffClient(settings) as client:
        result = await Dispatcher(client).call(
            "odoo_shops_list" if shop_id is None else "odoo_shop_get",
            {} if shop_id is None else {"shopId": shop_id},
        )
    print(json.dumps(result.payload, ensure_ascii=False, indent=2))
    return 0 if not result.is_error and 200 <= result.payload["httpStatus"] < 300 else 1


def main():
    parser = argparse.ArgumentParser(description="Read Odoo shops or one shop status")
    parser.add_argument("--shop-id", type=int)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    try:
        settings = load_settings(read_environment(args.env_file))
    except (ValueError, OSError):
        print(
            "Configuration error: set Odoo URL/API key or an explicit --env-file.", file=sys.stderr
        )
        return 1
    return asyncio.run(run(settings, args.shop_id))


if __name__ == "__main__":
    raise SystemExit(main())
