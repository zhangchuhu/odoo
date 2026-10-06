# Odoo BFF MCP server

独立 Python stdio MCP server，供 OpenClaw 调用当前仓库的 Odoo Mercado Libre BFF：

```text
OpenClaw → MCP stdio → /api/meli → Odoo 店铺权限/OAuth → Mercado Libre
```

本次按用户明确选定的 **Odoo BFF** 实现。[适配设计](DESIGN.md) 以
[现有 BFF](../deploy/mercado/addons/meli_bff/README.md) 为契约。
根目录 DESIGN.md 的 `/api/proxy/platform`、139 个 operation 和 Amazon 接口属于另一套系统，
本服务提供当前 Odoo BFF 实际支持的 9 项操作。

## 以 platform_proxy_listings 名称接入

支持 `mcpServers` 配置的客户端可使用
[examples/platform_proxy_listings.json](examples/platform_proxy_listings.json)，
将其中两个绝对路径替换为实际安装路径和 `.env` 路径，再合并到客户端配置。
服务注册名为 `platform_proxy_listings`；`mcp__platform_proxy_listings` 是客户端可能
生成的工具命名空间，Python 服务实际暴露 `listing_call` 等四个工具。
采用其他配置格式的客户端应使用相同的 command/args，按客户端格式注册。

例如，调用 `listing_call` 并传入：

```json
{"operation":"listing_items_list","arguments":{"shopId":3,"query":{"limit":20}}}
```

服务向 Odoo 发送 `GET /api/meli/shops/3/items?limit=20`，附带
`Authorization: Bearer <ODOO_BFF_API_KEY>`。实际 `shopId` 应先通过
`odoo_shops_list` 获取；返回值保留为 `{"httpStatus":200,"body":...}`。

## 安装和配置

需要 Python 3.11+ 和 uv。在部署 OpenClaw Gateway 的机器/容器上安装本工程；stdio 子进程
由宿主启动，所以配置中的 Python 路径必须在宿主环境中存在。

```bash
cd /home/ubuntu/odoo/python
uv sync --locked
cp .env.example .env
chmod 600 .env
# 编辑 .env，填入 Odoo 地址与 API Key
uv run odoo-bff-mcp --env-file .env
```

最后一条命令运行 stdio 协议，等待 MCP 客户端输入；它不启动网页，也不打印欢迎语。
同样支持 `.venv/bin/python -m odoo_bff_mcp --env-file /absolute/path/.env`。

| 变量 | 说明 |
| --- | --- |
| `ODOO_BFF_BASE_URL` | Odoo 根地址，如 `https://erp.example.com`；不要带 `/api/meli`。允许部署 base path |
| `ODOO_BFF_API_KEY` | Odoo 内部用户生成的 API Key，不是 ML access token 或登录密码 |
| `ODOO_BFF_TIMEOUT_SECONDS` | 单次 MCP→BFF 总请求超时，默认 180 秒；连接超时最多 10 秒 |
| `MAX_UPLOAD_BYTES` | 图片解码后的上限，默认 10,000,000；可下调，不可超过 BFF 上限 |

URL 和 API Key 优先使用 `ODOO_BFF_*`；空值时依次兼容 `HAISHANG_ERP_BASE_URL` /
`HAISHANG_ERP_TOKEN`、`HAISHANG_ERP_API_BASE_URL` / `HAISHANG_ERP_API_TOKEN`。
同名进程环境变量优先于显式 `--env-file`。不自动搜索 `.env`，不展开其中的 shell 表达式。
缺少配置或文件不可读时退出码为 1，诊断只写 stderr。

Odoo 必须已安装 `meli_bff`，API Key 所属用户需要 **Mercado Libre BFF Reader** 或
**Writer** 权限。店铺/公司和商品归属校验仍由 Odoo 执行。Odoo 地址应落到正确数据库，
多数据库部署需由现有域名/dbfilter 选择数据库。本服务不传入任意数据库或用户身份头。

## OpenClaw 接入

支持原生 MCP 注册的 OpenClaw，将 [examples/openclaw.json](examples/openclaw.json)
中的 `mcp.servers.odoo_bff` 合并到现有配置，替换 Python 和 `.env` 的绝对路径。
示例使用 `--env-file`，配置 JSON 中不包含真实凭证。只合并该条目，保留已有服务器。
原生字段和命令依据 [OpenClaw MCP 文档](https://docs.openclaw.ai/tools/mcp) 及
[stdio/超时说明](https://docs.openclaw.ai/cli/mcp/transports)。

然后验证工具发现：

```bash
openclaw mcp doctor odoo_bff --probe
```

应发现四个 MCP 工具：

- `listing_module_router`：查询模块（当前仅 `odoo_meli`）。
- `listing_operations_for_modules`：输入 `{"modules":["odoo_meli"]}` 获取操作 schema。
- `listing_list_operations`：输入 `{}` 查看全部九项操作。
- `listing_call`：输入 `{"operation":"odoo_shops_list","arguments":{}}` 调用 Odoo。

前三项查询不访问 Odoo。`listing_call` 包含读写能力，MCP 注解不会将其标为只读。
客户端超时建议至少 **210 秒**；如提高 BFF 请求超时，客户端也需相应提高。

可给 OpenClaw 的任务示例：

> 使用 odoo_bff MCP 查询操作目录，再调用 odoo_shops_list 列出我可以访问的店铺。
> 选择我指定的 shopId 后，用 listing_items_list 查看商品。

如果你的 OpenClaw 环境通过 mcporter 调用 MCP，使用
[examples/mcporter.json](examples/mcporter.json) 的 `mcpServers.odoo_bff` 条目，
合并进该环境的 `config/mcporter.json`，同样替换绝对路径。此配置与原生 `mcp.servers`
是两个独立注册表。格式依据 [mcporter 配置文档](https://mcporter.sh/config.html)。

```bash
mcporter --config /absolute/path/config/mcporter.json list odoo_bff --schema
mcporter --config /absolute/path/config/mcporter.json call odoo_bff.listing_call \
  --args '{"operation":"odoo_shops_list","arguments":{}}' --timeout 210000
```

调用参数和超时用法依据 [mcporter CLI 文档](https://mcporter.sh/cli-reference.html)。

## 支持的操作

所有 `shopId` 均为 **Odoo 店铺记录 ID**，接受正整数或纯十进制数字字符串，最大
9,999,999,999。商品 ID 形如 `MLM123` / `CBT123`；描述只接受 `CBT` 数字 ID。
顶层额外参数被拒绝，调用时不能传自定义 URL、headers、Token。

| operation | arguments | BFF 请求 |
| --- | --- | --- |
| `odoo_shops_list` | `{}` | `GET /shops` |
| `odoo_shop_get` | `shopId` | `GET /shops/{shopId}` |
| `listing_items_list` | `shopId`，可选 `scope`、`query` | `GET /shops/{shopId}/items` |
| `listing_get_item` | `shopId`、`itemId`，可选 `query` | `GET /shops/{shopId}/items/{itemId}` |
| `listing_create_item` | `shopId`、`item` | `POST /shops/{shopId}/items` |
| `listing_update_item` | `shopId`、`itemId`、`item` | `PUT /shops/{shopId}/items/{itemId}` |
| `listing_get_description` | `shopId`、`itemId` | `GET /shops/{shopId}/items/{itemId}/description` |
| `listing_update_description` | `shopId`、`itemId`、`plain_text` | `PUT /shops/{shopId}/items/{itemId}/description` |
| `listing_upload_picture` | `shopId`、`fileBase64`，可选 `filename`、`mimeType` | `POST /shops/{shopId}/pictures` |

表中路径均以 `/api/meli` 为前缀。写操作实际改变平台数据；MCP 不额外增加 `confirmed`
参数，Odoo 根据 API Key 权限执行。

商品列表 `scope` 为 `global`（默认）或 `marketplace`。可用 `query` 字段：
`q`、`status`、`offset`、`limit`、`search_type`、`scroll_id`、`category_id`、`sku`、
`seller_sku`、`missing_product_identifiers`、`reputation_health_gauge`、`include_filters`、
`orders`、`listing_type_id`、`labels`。商品详情仅接受 `include_attributes`、`attributes`。
query 值为字符串、布尔、有限数值或上述标量数组；数组转换成重复 key，空数组不发送。
省略可选 query 时使用空对象，显式 null 和嵌套对象被拒绝。

```json
{
  "operation": "listing_items_list",
  "arguments": {
    "shopId": 3,
    "scope": "global",
    "query": {"status": ["active", "paused"], "offset": 0, "limit": 20}
  }
}
```

创建/更新的 `item` 是原生 ML JSON，直接作为 body，不再包一层 `{item: ...}`。
创建必须提供非空 `sites_to_sell`，每项 `site_id` / `logistic_type` 必须匹配店铺；
先读取店铺详情获取实际值。当前 BFF 只支持传统 Global Selling 创建，不支持 User Products。
身份字段 `seller_id`、`user_id`、`access_token`、`shopId`、`shop_id`、`caller.id` 被拒绝。

描述更新正文只有 `plain_text`，由 BFF 注入绑定的站点和物流。
图片使用纯 base64，不能传 data URL 或本地文件路径；解码后作为唯一 `file` multipart 字段，
默认 `filename=upload.jpg`、`mimeType=image/jpeg`，可覆盖文件名和图片 MIME 类型。

## 返回和错误

工具的 `content[0].text` 是可解析的 JSON，例如：

```json
{"httpStatus":200,"body":{"shops":[]}}
```

HTTP 状态和业务正文保留；非 JSON 响应保留文本，空响应为 `""`。
状态 >=400 时 MCP `isError=true`。200 中的 `success=false` 不改写，调用方仍需检查正文。
302 不跟随，保留状态；本地错误形如：

```json
{"mcpError":true,"code":"INVALID_ARGUMENTS","issues":[{"path":["shopId"],"code":"value_error","message":"Invalid argument value"}]}
```

错误码包括 `UNKNOWN_OPERATION`、`INVALID_ARGUMENTS`、`EMPTY_MODULES`、
`UNKNOWN_MODULE_KEYS`、`HANDLER_ERROR`。后者 `kind` 为 `timeout`、`network` 或 `internal`。
所有请求均不重试；写操作超时可能已在上游生效，应先查询状态再决定下一步。

TLS 校验开启，禁止跟随重定向，HTTPX 不使用环境代理。每个 stdio 进程对应一组 API Key，
连接池按实例隔离并在关闭时释放。日志不包含 Token、query、正文或 base64；
BFF 业务响应原样交给调用者。

## 验证

```bash
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run python scripts/smoke_stdio.py
uv build
```

测试不需要真实凭证：MockTransport 捕获九项请求并与 BFF 的纯协议代码核对，覆盖目录、
校验、JSON/multipart、错误状态、网络/总超时/取消和凭证隔离；真实子进程验证 MCP 新旧
协议握手、工具发现、返回内容和本地模拟 HTTP 请求。
SDK 使用官方 `mcp==2.2.0`，通过 v2 `Server` 低层接口显式返回 `CallToolResult`，
避免自动输出包装；依赖版本完整锁定于 `uv.lock`。

需要真实只读联调时，显式执行：

```bash
uv run python scripts/smoke_read.py --env-file .env
uv run python scripts/smoke_read.py --env-file .env --shop-id 3
```

只读脚本只查询店铺列表或店铺状态，非 2xx 或本地错误退出 1。
目前已完成离线测试、stdio、wheel 独立安装以及真实 Odoo BFF 的店铺列表/详情只读联调；
**未执行真实写操作，未修改你的 OpenClaw 配置，也未在实际 OpenClaw 宿主中验证接入**。
不提供多用户 Streamable HTTP 服务。

2026-10-06 完整复核：

- `uv sync --locked` 通过；完整 pytest **116 passed in 8.71s**，包括离线 smoke 和四项
  stdio 测试。当前环境 `socketpair.send` 正常，无需 selector 轮询适配。
- Ruff check/format、独立 `scripts/smoke_stdio.py` 均通过。
- `uv build` 成功生成 sdist 和 wheel；wheel 在 `/tmp` 的全新虚拟环境安装后，
  从非源码目录、无 Node 的 PATH 下成功启动，发现四工具/九操作并验证本地错误。
  项目锁定环境使用 MCP 2.2.0；独立 wheel 安装解析到 MCP 2.3.0，冒烟测试通过。
- 使用现有 `.env` 执行 `smoke_read.py`，真实 BFF 返回 HTTP 200、两家店铺。
  通过真实 MCP stdio 子进程再次查询店铺列表及两家店铺详情，三个请求均返回 HTTP 200，
  `isError=false`。此项验证覆盖 MCP→Odoo BFF 的只读链路，不代表平台商品读写已验证。

本次环境的 `uv` 位于 `/home/ubuntu/.hermes/bin/uv`，验证时使用该绝对路径，
并设置 `UV_CACHE_DIR=/tmp/odoo-mcp-uv-cache`。
