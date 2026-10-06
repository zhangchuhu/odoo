"""One HTTP connection pool and one Odoo credential per MCP server instance."""

import asyncio
import json
import re

import httpx

from .config import BFF_PREFIX, Settings
from .models import UNSET, RequestSpec, ToolResult

ROUTE = re.compile(
    r"/shops(?:/[1-9][0-9]{0,9}(?:/items(?:/[A-Z]{3}[0-9]+(?:/description)?)?|/pictures)?)?"
)


class BffClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            timeout=httpx.Timeout(
                settings.timeout_seconds, connect=min(10, settings.timeout_seconds)
            ),
        )

    @property
    def is_closed(self):
        return self._client.is_closed

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    async def aclose(self):
        await self._client.aclose()

    async def send(self, spec: RequestSpec) -> ToolResult:
        if not ROUTE.fullmatch(spec.path) or spec.method not in ("GET", "POST", "PUT"):
            raise ValueError("Unsupported BFF route")
        url = httpx.URL(self.settings.base_url + BFF_PREFIX + spec.path)
        base = httpx.URL(self.settings.base_url)
        if (url.scheme, url.host, url.port) != (
            base.scheme,
            base.host,
            base.port,
        ) or not url.path.startswith(base.path.rstrip("/") + BFF_PREFIX + "/"):
            raise ValueError("Invalid BFF destination")
        headers = {
            "Authorization": "Bearer " + self.settings.api_key.get_secret_value(),
            "Accept": "application/json",
        }
        kwargs = {}
        if spec.upload is not None:
            file = spec.upload
            kwargs["files"] = {"file": (file.filename, file.content, file.content_type)}
        elif spec.body is not UNSET:
            kwargs["content"] = json.dumps(spec.body, ensure_ascii=False, allow_nan=False).encode()
            headers["Content-Type"] = "application/json"
        async with asyncio.timeout(self.settings.timeout_seconds):
            response = await self._client.request(
                spec.method, url, params=spec.query, headers=headers, **kwargs
            )
        try:
            body = json.loads(response.content, parse_constant=_invalid_json_constant)
        except (ValueError, UnicodeError):
            body = response.text
        return ToolResult(
            {"httpStatus": response.status_code, "body": body}, response.status_code >= 400
        )


def _invalid_json_constant(value):
    raise ValueError("Non-finite JSON")
