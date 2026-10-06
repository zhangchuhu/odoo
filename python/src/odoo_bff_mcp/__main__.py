"""The stdout stream belongs exclusively to MCP."""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

from .config import load_settings
from .server import run_stdio


def read_environment(env_file: Path | None = None) -> dict[str, str]:
    values = {}
    if env_file is not None:
        # Opening explicitly makes a missing/unreadable file an actionable configuration error.
        with env_file.open(encoding="utf-8") as stream:
            values = {
                key: value
                for key, value in dotenv_values(stream=stream, interpolate=False).items()
                if value is not None
            }
    return values | dict(os.environ)


def main():
    parser = argparse.ArgumentParser(description="Odoo Mercado Libre BFF MCP (stdio)")
    parser.add_argument("--env-file", type=Path, help="Explicit dotenv file; process env wins")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("odoo_bff_mcp").setLevel(logging.INFO)
    try:
        settings = load_settings(read_environment(args.env_file))
    except (ValueError, OSError):
        print(
            "Configuration error: check Odoo URL, API key, limits and --env-file.", file=sys.stderr
        )
        raise SystemExit(1) from None
    try:
        asyncio.run(run_stdio(settings))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
