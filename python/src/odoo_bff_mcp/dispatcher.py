"""Local discovery and validated dispatch; errors never echo caller inputs."""

import logging
import time

import httpx
from pydantic import Field, JsonValue, ValidationError

from .client import BffClient
from .models import ToolResult, error
from .operations import Arguments, build_registry

logger = logging.getLogger(__name__)
MODULE = {
    "key": "odoo_meli",
    "label": "Odoo Mercado Libre BFF",
    "route_hints": "店铺、Global Selling 商品、CBT 描述、图片上传",
    "operationCount": 9,
}


class ModulesArguments(Arguments):
    modules: list[str] = Field(min_length=1)


class CallArguments(Arguments):
    operation: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


TOOL_MODELS = {
    "listing_module_router": Arguments,
    "listing_operations_for_modules": ModulesArguments,
    "listing_list_operations": Arguments,
    "listing_call": CallArguments,
}


def invalid(exc: ValueError):
    issues = [{"path": [], "code": "invalid_value", "message": "Invalid operation arguments"}]
    if isinstance(exc, ValidationError):
        # Never include Pydantic input/ctx or attacker-controlled dict keys in errors.
        issues = [
            {
                "path": [item["loc"][0]]
                if item["loc"]
                and item["loc"][0]
                in {
                    "shopId",
                    "itemId",
                    "item",
                    "query",
                    "scope",
                    "plain_text",
                    "fileBase64",
                    "filename",
                    "mimeType",
                    "operation",
                    "arguments",
                    "modules",
                }
                else [],
                "code": item["type"],
                "message": "Invalid argument value",
            }
            for item in exc.errors(include_input=False, include_context=False, include_url=False)
        ]
    return error("INVALID_ARGUMENTS", issues=issues)


class Dispatcher:
    def __init__(self, client: BffClient):
        self.client = client
        self.registry = build_registry()

    def catalog(self):
        return [self.registry[key].catalog() for key in sorted(self.registry)]

    async def tool(self, name: str, arguments: dict | None = None) -> ToolResult:
        model = TOOL_MODELS.get(name)
        if model is None:
            return error("UNKNOWN_TOOL")
        try:
            args = model.model_validate({} if arguments is None else arguments)
        except ValueError as exc:
            return invalid(exc)
        if name == "listing_call":
            return await self.call(args.operation, args.arguments)
        if name == "listing_module_router":
            return ToolResult(
                {
                    "version": 1,
                    "description": "先选择模块，再查询 operation 参数。",
                    "modules": [MODULE.copy()],
                    "moduleCount": 1,
                    "totalOperations": 9,
                }
            )
        if name == "listing_list_operations":
            return ToolResult({"operationCount": len(self.registry), "operations": self.catalog()})
        keys = [key.strip() for key in args.modules if key.strip()]
        if not keys:
            return error("EMPTY_MODULES")
        known = [key for key in keys if key == MODULE["key"]]
        unknown = [key for key in keys if key != MODULE["key"]]
        if not known:
            return error(
                "UNKNOWN_MODULE_KEYS", unknownModuleKeys=unknown, validKeys=[MODULE["key"]]
            )
        payload = {
            "requestedModules": known,
            "operationCount": len(self.registry),
            "operations": self.catalog(),
        }
        if unknown:
            payload["unknownModuleKeys"] = unknown
        return ToolResult(payload)

    async def call(self, operation: str, arguments: dict | None = None) -> ToolResult:
        spec = self.registry.get(operation)
        if spec is None:
            return error("UNKNOWN_OPERATION")
        try:
            args = spec.arguments_model.model_validate({} if arguments is None else arguments)
            request = spec.build_request(args, self.client.settings.max_upload_bytes)
        except ValueError as exc:
            return invalid(exc)
        start = time.monotonic()
        try:
            result = await self.client.send(request)
        except (TimeoutError, httpx.TimeoutException):
            result = error("HANDLER_ERROR", kind="timeout")
        except httpx.RequestError:
            result = error("HANDLER_ERROR", kind="network")
        except Exception:
            result = error("HANDLER_ERROR", kind="internal")
        logger.info(
            "operation=%s duration=%.3f status=%s code=%s",
            spec.id,
            time.monotonic() - start,
            result.payload.get("httpStatus"),
            result.payload.get("code"),
        )
        return result
