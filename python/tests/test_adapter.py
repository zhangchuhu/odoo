import asyncio
import base64
import importlib.util
import json
import sys
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

import httpx
import pytest

from odoo_bff_mcp.client import BffClient
from odoo_bff_mcp.config import load_settings
from odoo_bff_mcp.dispatcher import Dispatcher

ENV = {"ODOO_BFF_BASE_URL": "https://odoo.test/erp///", "ODOO_BFF_API_KEY": " test-key "}


def settings(**env):
    return load_settings(ENV | env)


def test_config():
    conf = settings(HAISHANG_ERP_BASE_URL="https://wrong.test")
    assert conf.base_url == "https://odoo.test/erp"
    assert conf.api_key.get_secret_value() == "test-key"
    assert "test-key" not in repr(conf)
    assert conf.max_upload_bytes == 10_000_000
    assert conf.timeout_seconds == 180
    legacy = load_settings(
        {"HAISHANG_ERP_BASE_URL": "https://old.test", "HAISHANG_ERP_TOKEN": "old"}
    )
    assert legacy.base_url == "https://old.test"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "file:///tmp",
        "https://u:p@host",
        "https://host?x=1",
        "https://host#frag",
        "https://host/api/meli",
        "https://host/a/../b",
        "https://host/%2e%2e",
        "https://host\\evil",
        "https://host:bad",
    ],
)
def test_invalid_url(url):
    with pytest.raises(ValueError):
        settings(ODOO_BFF_BASE_URL=url, HAISHANG_ERP_BASE_URL="")


@pytest.mark.parametrize(
    "env",
    [
        {"ODOO_BFF_API_KEY": ""},
        {"ODOO_BFF_API_KEY": "a\nb"},
        {"MAX_UPLOAD_BYTES": "0"},
        {"MAX_UPLOAD_BYTES": "10000001"},
        {"ODOO_BFF_TIMEOUT_SECONDS": "nan"},
    ],
)
def test_invalid_config(env):
    with pytest.raises(ValueError):
        settings(**env)


CASES = [
    ("odoo_shops_list", {}, "GET", "/shops", None),
    ("odoo_shop_get", {"shopId": "3"}, "GET", "/shops/3", None),
    ("listing_items_list", {"shopId": 3}, "GET", "/shops/3/items", None),
    ("listing_get_item", {"shopId": 3, "itemId": "MLM123"}, "GET", "/shops/3/items/MLM123", None),
    (
        "listing_create_item",
        {
            "shopId": 3,
            "item": {
                "sites_to_sell": [{"site_id": "MLM", "logistic_type": "remote"}],
                "title": "中文",
                "x": None,
            },
        },
        "POST",
        "/shops/3/items",
        {
            "sites_to_sell": [{"site_id": "MLM", "logistic_type": "remote"}],
            "title": "中文",
            "x": None,
        },
    ),
    (
        "listing_update_item",
        {"shopId": 3, "itemId": "CBT123", "item": {"price": 12}},
        "PUT",
        "/shops/3/items/CBT123",
        {"price": 12},
    ),
    (
        "listing_get_description",
        {"shopId": 3, "itemId": "CBT123"},
        "GET",
        "/shops/3/items/CBT123/description",
        None,
    ),
    (
        "listing_update_description",
        {"shopId": 3, "itemId": "CBT123", "plain_text": "内容"},
        "PUT",
        "/shops/3/items/CBT123/description",
        {"plain_text": "内容"},
    ),
    (
        "listing_upload_picture",
        {"shopId": 3, "fileBase64": "aGVsbG8="},
        "POST",
        "/shops/3/pictures",
        "multipart",
    ),
]


@pytest.mark.parametrize("op,args,method,path,body", CASES)
async def test_routes_match_bff(op, args, method, path, body):
    # Independent authority: load the actual BFF's pure protocol without importing Odoo.
    source = Path(__file__).resolve().parents[2] / "deploy/mercado/addons/meli_bff/protocol.py"
    spec = importlib.util.spec_from_file_location("bff_protocol", source)
    protocol = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = protocol
    spec.loader.exec_module(protocol)
    captured = []

    def respond(request):
        captured.append(request)
        assert request.method == method
        assert request.url.path == "/erp/api/meli" + path
        assert request.headers["authorization"] == "Bearer test-key"
        assert request.extensions["timeout"]["connect"] == 10
        operation = protocol.resolve_route(method, path)
        protocol.validate_query(operation, list(request.url.params.multi_items()))
        if body == "multipart":
            message = BytesParser(policy=default).parsebytes(
                b"Content-Type: "
                + request.headers["content-type"].encode()
                + b"\r\n\r\n"
                + request.content
            )
            parts = list(message.iter_parts())
            assert len(parts) == 1
            assert parts[0].get_param("name", header="content-disposition") == "file"
            assert parts[0].get_filename() == "upload.jpg"
            assert parts[0].get_payload(decode=True) == b"hello"
        elif body is None:
            assert request.content == b""
        else:
            assert json.loads(request.content) == body
            assert request.headers["content-type"] == "application/json"
        return httpx.Response(200, json={"ok": True})

    async with BffClient(settings(), transport=httpx.MockTransport(respond)) as client:
        result = await Dispatcher(client).call(op, args)
        assert result.payload == {"httpStatus": 200, "body": {"ok": True}}
        assert not result.is_error
    assert client.is_closed
    assert len(captured) == 1


async def test_query():
    captured = []
    async with BffClient(
        settings(),
        transport=httpx.MockTransport(
            lambda req: captured.append(req) or httpx.Response(200, json={})
        ),
    ) as client:
        result = await Dispatcher(client).call(
            "listing_items_list",
            {
                "shopId": 3,
                "scope": "marketplace",
                "query": {
                    "status": ["active", "paused"],
                    "include_filters": False,
                    "offset": 0,
                    "q": "中文 %",
                    "labels": [],
                },
            },
        )
    assert not result.is_error
    assert list(captured[0].url.params.multi_items()) == [
        ("status", "active"),
        ("status", "paused"),
        ("include_filters", "false"),
        ("offset", "0"),
        ("q", "中文 %"),
        ("scope", "marketplace"),
    ]


BAD_CALLS = (
    [
        ("odoo_shop_get", {"shopId": value})
        for value in [True, None, "", " ", 0, -1, 3.5, "0x10", 10000000000]
    ]
    + [
        ("listing_get_item", {"shopId": 3, "itemId": value})
        for value in ["../shops", "%2e%2e", "MLM1/other", "http://x", "mlm123", 123]
    ]
    + [
        ("odoo_shops_list", {"token": "secret"}),
        ("listing_items_list", {"shopId": 3, "query": {"seller_id": "123"}}),
        ("listing_items_list", {"shopId": 3, "query": None}),
        ("listing_items_list", {"shopId": 3, "query": {"limit": {"nested": 1}}}),
        ("listing_items_list", {"shopId": 3, "scope": None}),
        ("listing_get_description", {"shopId": 3, "itemId": "MLM123"}),
        ("listing_create_item", {"shopId": 3, "item": {}}),
        ("listing_update_item", {"shopId": 3, "itemId": "CBT1", "item": {"seller_id": "evil"}}),
        ("listing_upload_picture", {"shopId": 3, "fileBase64": "data:image/png;base64,YQ=="}),
        ("listing_upload_picture", {"shopId": 3, "fileBase64": "%%%"}),
        ("listing_upload_picture", {"shopId": 3, "fileBase64": ""}),
        ("listing_upload_picture", {"shopId": 3, "fileBase64": "YQ==", "filename": "../secret"}),
    ]
)


@pytest.mark.parametrize("op,args", BAD_CALLS)
async def test_invalid_is_local(op, args):
    def fail(req):
        pytest.fail("Invalid inputs must not reach HTTP")

    async with BffClient(settings(), transport=httpx.MockTransport(fail)) as client:
        result = await Dispatcher(client).call(op, args)
    assert result.is_error
    assert result.payload["code"] == "INVALID_ARGUMENTS"
    assert "secret" not in json.dumps(result.payload)


async def test_upload_limit():
    async with BffClient(
        settings(MAX_UPLOAD_BYTES="2"),
        transport=httpx.MockTransport(lambda req: pytest.fail("Oversize upload reached HTTP")),
    ) as client:
        result = await Dispatcher(client).call(
            "listing_upload_picture", {"shopId": 1, "fileBase64": base64.b64encode(b"abc").decode()}
        )
    assert result.payload["code"] == "INVALID_ARGUMENTS"


@pytest.mark.parametrize("status", [200, 204, 302, 400, 401, 403, 429, 500])
@pytest.mark.parametrize("body", [b'{"success":false}', b"[]", b"null", b"42", b"hello", b""])
async def test_responses(status, body):
    seen = []
    async with BffClient(
        settings(),
        transport=httpx.MockTransport(
            lambda req: (
                seen.append(req)
                or httpx.Response(
                    status, content=body, headers={"Location": "https://elsewhere.test"}
                )
            )
        ),
    ) as client:
        result = await Dispatcher(client).call("odoo_shops_list", {})
    try:
        expected = json.loads(body)
    except ValueError:
        expected = body.decode()
    assert result.payload == {"httpStatus": status, "body": expected}
    assert result.is_error == (status >= 400)
    assert len(seen) == 1


@pytest.mark.parametrize(
    "exc,kind",
    [(httpx.ConnectError("test-key"), "network"), (httpx.ReadTimeout("test-key"), "timeout")],
)
async def test_network(exc, kind):
    seen = []

    def fail(req):
        seen.append(req)
        raise exc

    async with BffClient(settings(), transport=httpx.MockTransport(fail)) as client:
        result = await Dispatcher(client).call(
            "listing_update_description", {"shopId": 1, "itemId": "CBT1", "plain_text": "private"}
        )
    assert result.payload["kind"] == kind
    assert "test-key" not in json.dumps(result.payload)
    assert len(seen) == 1


async def test_cancel():
    async def cancel(req):
        raise asyncio.CancelledError()

    async with BffClient(settings(), transport=httpx.MockTransport(cancel)) as client:
        with pytest.raises(asyncio.CancelledError):
            await Dispatcher(client).call("odoo_shops_list", {})
    assert client.is_closed


async def test_instances_and_catalog():
    seen = []

    async def respond(req):
        await asyncio.sleep(0)
        seen.append(req.headers["authorization"])
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(respond)
    async with (
        BffClient(settings(), transport=transport) as a,
        BffClient(settings(ODOO_BFF_API_KEY="other"), transport=transport) as b,
    ):
        dispatcher = Dispatcher(a)
        router = await dispatcher.tool("listing_module_router", {})
        assert router.payload["totalOperations"] == 9
        catalog = await dispatcher.tool("listing_list_operations", {})
        ids = [entry["id"] for entry in catalog.payload["operations"]]
        assert ids == sorted(case[0] for case in CASES)
        partial = await dispatcher.tool(
            "listing_operations_for_modules", {"modules": ["odoo_meli", "bad", "odoo_meli"]}
        )
        assert partial.payload["unknownModuleKeys"] == ["bad"]
        assert partial.payload["operationCount"] == 9
        for keys, code in [([" "], "EMPTY_MODULES"), (["bad"], "UNKNOWN_MODULE_KEYS")]:
            result = await dispatcher.tool("listing_operations_for_modules", {"modules": keys})
            assert result.payload["code"] == code
        for name, args in [
            ("listing_module_router", {"extra": 1}),
            ("listing_call", {"operation": "odoo_shops_list", "arguments": None}),
            ("listing_call", {"operation": 1}),
            ("listing_operations_for_modules", {"modules": []}),
        ]:
            result = await dispatcher.tool(name, args)
            assert result.is_error
        unknown = await dispatcher.call("amazon_ads_request", {})
        assert unknown.payload["code"] == "UNKNOWN_OPERATION"
        assert seen == []
        await asyncio.gather(
            dispatcher.call("odoo_shops_list", {}), Dispatcher(b).call("odoo_shops_list", {})
        )
    assert sorted(seen) == ["Bearer other", "Bearer test-key"]
