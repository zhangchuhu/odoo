"""Explicit contracts for the nine operations in meli_bff/protocol.py."""

import base64
import binascii
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, JsonValue, model_validator

from .models import RequestSpec, Upload


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def shop_id(value):
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        value = int(value)
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        value = int(value)
    if type(value) is not int or not 1 <= value <= 9_999_999_999:
        raise ValueError("shopId must be an integer between 1 and 9999999999")
    return value


ShopId = Annotated[int, BeforeValidator(shop_id, json_schema_input_type=int | float | str)]
ItemId = Annotated[str, Field(pattern=r"^[A-Z]{3}[0-9]+$", max_length=2000)]
CbtId = Annotated[str, Field(pattern=r"^CBT[0-9]+$", max_length=2000)]
Scalar = str | bool | int | float
QueryValue = Scalar | list[Scalar]
ITEM_QUERY_KEYS = frozenset({"include_attributes", "attributes"})
LIST_QUERY_KEYS = frozenset(
    {
        "q",
        "status",
        "offset",
        "limit",
        "search_type",
        "scroll_id",
        "category_id",
        "sku",
        "seller_sku",
        "missing_product_identifiers",
        "reputation_health_gauge",
        "include_filters",
        "orders",
        "listing_type_id",
        "labels",
    }
)
IDENTITY_KEYS = frozenset(
    {"seller_id", "user_id", "access_token", "shopId", "shop_id", "caller.id"}
)


class ShopArguments(Arguments):
    shopId: ShopId


class ItemArguments(ShopArguments):
    itemId: ItemId


class DescriptionArguments(ShopArguments):
    itemId: CbtId


def query_pairs(query: dict[str, QueryValue], allowed: frozenset[str]):
    if query.keys() - allowed:
        raise ValueError("Unsupported query parameter")
    pairs = []
    for key, value in query.items():
        for entry in value if isinstance(value, list) else [value]:
            if isinstance(entry, float) and not math.isfinite(entry):
                raise ValueError("Query numbers must be finite")
            if isinstance(entry, bool):
                text = str(entry).lower()
            elif isinstance(entry, float) and entry.is_integer():
                text = str(int(entry))
            else:
                text = str(entry)
            pairs.append((key, text))
    return tuple(pairs)


class ListArguments(ShopArguments):
    scope: Literal["global", "marketplace"] = "global"
    query: dict[str, QueryValue] = Field(
        default_factory=dict, description="Allowed filters: " + ", ".join(sorted(LIST_QUERY_KEYS))
    )

    @model_validator(mode="after")
    def check_query(self):
        query_pairs(self.query, LIST_QUERY_KEYS)
        return self


class GetItemArguments(ItemArguments):
    query: dict[str, QueryValue] = Field(
        default_factory=dict, description="Only include_attributes and attributes are accepted."
    )

    @model_validator(mode="after")
    def check_query(self):
        query_pairs(self.query, ITEM_QUERY_KEYS)
        return self


class ItemBodyArguments(ShopArguments):
    item: dict[str, JsonValue] = Field(
        description="Native ML JSON object, forwarded without a wrapper."
    )

    @model_validator(mode="after")
    def check_body(self):
        if self.item.keys() & IDENTITY_KEYS:
            raise ValueError("Identity fields are controlled by Odoo")
        json.dumps(self.item, allow_nan=False)
        sites = self.item.get("sites_to_sell")
        if sites is not None and (
            not isinstance(sites, list)
            or not sites
            or any(
                not isinstance(site, dict)
                or not isinstance(site.get("site_id"), str)
                or not site["site_id"]
                or not isinstance(site.get("logistic_type"), str)
                or not site["logistic_type"]
                for site in sites
            )
        ):
            raise ValueError("sites_to_sell requires site_id and logistic_type objects")
        return self


class CreateArguments(ItemBodyArguments):
    @model_validator(mode="after")
    def require_sites(self):
        if not self.item.get("sites_to_sell"):
            raise ValueError("sites_to_sell is required for Global Selling creation")
        return self


class UpdateArguments(ItemBodyArguments):
    itemId: ItemId


class UpdateDescriptionArguments(DescriptionArguments):
    plain_text: str


class UploadArguments(ShopArguments):
    fileBase64: str = Field(
        description="Raw base64 image bytes; no data URL. Maximum 10 MB decoded."
    )
    filename: str = Field(default="upload.jpg", min_length=1, max_length=255)
    mimeType: str = Field(default="image/jpeg", pattern=r"^image/[a-zA-Z0-9.+-]+$")

    @model_validator(mode="after")
    def check_filename(self):
        if self.filename in (".", "..") or re.search(r'[/\\\x00-\x1f\x7f"]', self.filename):
            raise ValueError("Invalid upload filename")
        return self


def decode_upload(value: str, limit: int) -> bytes:
    if len(value) > 4 * ((limit + 2) // 3):
        raise ValueError("Upload exceeds configured limit")
    try:
        content = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Invalid base64 image") from None
    if not content or len(content) > limit:
        raise ValueError("Image is empty or exceeds configured limit")
    return content


def shops_path(args):
    return f"/shops/{args.shopId}"


def item_path(args):
    return shops_path(args) + "/items/" + args.itemId


def upload_request(args, limit):
    return RequestSpec(
        "POST",
        shops_path(args) + "/pictures",
        upload=Upload(decode_upload(args.fileBase64, limit), args.filename, args.mimeType),
    )


@dataclass(frozen=True)
class OperationSpec:
    id: str
    description: str
    arguments_model: type[Arguments]
    build_request: Callable[[Arguments, int], RequestSpec]
    writes: bool = False

    def catalog(self):
        return {
            "id": self.id,
            "module": "odoo_meli",
            "title": self.id,
            "summary": self.description.splitlines()[0][:240],
            "description": self.description,
            "inputSchema": self.arguments_model.model_json_schema(),
            "readOnly": not self.writes,
        }


def build_registry() -> dict[str, OperationSpec]:
    specs = [
        OperationSpec(
            "odoo_shops_list",
            "列出当前 Odoo API Key 可访问的店铺。先调用此接口获取 shopId。",
            Arguments,
            lambda a, limit: RequestSpec("GET", "/shops"),
        ),
        OperationSpec(
            "odoo_shop_get",
            "读取店铺状态、站点、物流和授权地址；不返回 OAuth token。",
            ShopArguments,
            lambda a, limit: RequestSpec("GET", shops_path(a)),
        ),
        OperationSpec(
            "listing_items_list",
            "列出店铺商品；scope=global（默认）或 marketplace。",
            ListArguments,
            lambda a, limit: RequestSpec(
                "GET",
                shops_path(a) + "/items",
                query_pairs(a.query, LIST_QUERY_KEYS)
                + ((("scope", a.scope),) if "scope" in a.model_fields_set else ()),
            ),
        ),
        OperationSpec(
            "listing_get_item",
            "读取商品详情；BFF 校验该商品属于当前店铺。",
            GetItemArguments,
            lambda a, limit: RequestSpec(
                "GET", item_path(a), query_pairs(a.query, ITEM_QUERY_KEYS)
            ),
        ),
        OperationSpec(
            "listing_create_item",
            "创建传统 Global Selling 商品（真实写操作）。item 直接作为正文；"
            "sites_to_sell 必填并匹配店铺站点/物流。BFF 不支持 User Products 创建。",
            CreateArguments,
            lambda a, limit: RequestSpec("POST", shops_path(a) + "/items", body=a.item),
            True,
        ),
        OperationSpec(
            "listing_update_item",
            "更新商品（真实写操作），item 直接作为 JSON 正文。",
            UpdateArguments,
            lambda a, limit: RequestSpec("PUT", item_path(a), body=a.item),
            True,
        ),
        OperationSpec(
            "listing_get_description",
            "读取 CBT 商品描述，仅接受 CBT 数字 ID。",
            DescriptionArguments,
            lambda a, limit: RequestSpec("GET", item_path(a) + "/description"),
        ),
        OperationSpec(
            "listing_update_description",
            "更新 CBT 商品描述（真实写操作），正文仅 plain_text；站点/物流由 Odoo 注入。",
            UpdateDescriptionArguments,
            lambda a, limit: RequestSpec(
                "PUT", item_path(a) + "/description", body={"plain_text": a.plain_text}
            ),
            True,
        ),
        OperationSpec(
            "listing_upload_picture",
            "上传图片（真实写操作），raw base64 转为单个 multipart file，最多 10 MB。",
            UploadArguments,
            upload_request,
            True,
        ),
    ]
    return {spec.id: spec for spec in specs}
