# Python ERP BFF：直连 Mercado Libre

## 目标与已确认要求

用户要求按照 `erp-bff.md` 用 Python 实现 ERP BFF，并已明确选择：
BFF 直接调用 Mercado Libre，由 BFF 管理 OAuth token。
因此不使用文档中的 `platform_proxy` 上游和内部服务密钥。
用户进一步明确不保留 `/api/proxy/platform` 入口，并指定使用
`mercadolibre-mcp-server` 查询具体 Mercado Libre API。
本设计已通过该 MCP 的搜索与文档读取工具核实下述上游映射。

## 推荐架构与假设

新增 `deploy/mercado/addons/meli_bff` Odoo 模块，依赖现有 `meli_accounts`。
链路为 MCP/客户端 → Odoo BFF → `https://api.mercadolibre.com`。
这是推荐方案，尚待用户确认；用户没有要求独立进程。

现有项目具备 `meli.independent.store` 店铺模型、公司隔离规则、
OAuth PKCE 授权入口和回调、token 持久化与加锁刷新能力。
复用这些模型与存储，使 BFF 和 Odoo 店铺操作共用一份凭证。
不另建用户库或 token 库。

备选方案是独立 FastAPI 服务，但需要定义 Odoo 身份验证接口、店铺数据访问
和跨进程 token 轮换协议，部署与一致性成本更高。

## 鉴权、权限与租户

- 统一使用 `/api/meli` 前缀；业务接口必须携带
  `Authorization: Bearer <Odoo API Key>`。
- 使用 Odoo API Key 校验和有效用户检查，不将原文 Node ERP JWT 当作 Odoo 凭证。
- 为 BFF 设置独立只读和读写用户组；现有 Mercado Libre 管理员具备读写权限。
- 按路由所需权限检查读写权限，随后按当前用户可访问公司与店铺记录规则查店铺。
- 每个涉及私有平台资源的接口都必须明确店铺，不使用默认店铺兜底。
- 不接受外部 `X-Internal-Tenant-*`、用户 ID 或 company ID 作为授权依据。
- 对路径与查询中出现的店铺选择进行一致性校验；冲突时拒绝。
- 获取 token 时才使用受控提权，权限和店铺归属检查必须先完成。

## 接口与映射范围

使用显式的 HTTP 方法与路径映射表，不将业务路径直接拼接成上游 URL。
第一版实现文档举例所需商品能力，以及 OAuth 和店铺状态查询：

| BFF 路由（省略 `/api/meli`） | 行为 / Mercado Libre 上游 |
| --- | --- |
| `GET /shops` | 返回用户可见店铺与授权状态，不返回凭证 |
| `GET /shops/{shopId}` | 返回单店铺状态与到期时间 |
| `GET /shops/{shopId}/items` | `GET /marketplace/users/{sellerId}/items/search` |
| `GET /shops/{shopId}/items/{itemId}` | `GET /items/{itemId}` |
| `POST /shops/{shopId}/items` | `POST /global/items`，传统 Global Selling 刊登 |
| `PUT /shops/{shopId}/items/{itemId}` | `PUT /global/items/{itemId}` |
| `GET /shops/{shopId}/items/{itemId}/description` | `GET /marketplace/items/{itemId}/description` |
| `PUT /shops/{shopId}/items/{itemId}/description` | `PUT /global/items/{itemId}`，写入 `description.plain_text` |
| `POST /shops/{shopId}/pictures` | `POST /pictures/items/upload`，multipart 上传 |

具体上游方法与路径以 MCP 返回的 Mercado Libre 官方文档为准。
商品列表默认按已绑定主账号查询，`scope=marketplace` 时改用店铺已绑定的站点账号；
`scope` 是 BFF 参数，不透传给平台，缺失站点绑定时拒绝该查询。
描述读取使用店铺的 `site_id` 和 `logistic_type`，客户端不能覆盖为其他店铺配置。
描述更新接受 `{ "plain_text": "..." }`，由 BFF 构造包含站点、物流类型和
`description.plain_text` 的平台请求体；这是明确的数据转换接口。
普通商品创建/更新使用平台原生 JSON，不自动改写刊登模型或自动重试另一条路由。
MCP 文档明确指出带 `user_products_seller` 标签的账号不能使用传统创建接口。
第一版传统创建接口检查账号模型；遇到 User Products 账号返回明确的不支持错误，
不假装已支持其独立创建流程。User Products 创建作为单独扩展，不混用 payload。
商品读取和更新校验 seller 与店铺绑定关系，包含 Global Selling 主账号与
已登记的站点账号，避免用一个合法店铺 ID 操作另一个店铺的商品。

OAuth 使用现有 `/meli/stores/{shopId}/authorize` 和 `/meli_login` 浏览器会话流程，
BFF 店铺状态响应提供授权入口。授权码交换、保存和刷新均由同一 Odoo 服务完成。
API 响应不能包含 access_token、refresh_token 或 client_secret。

原文中的 Amazon、运营反馈和批量发货属于其他业务能力，不能映射为通用
Mercado Libre 透传。未实现路由明确返回 404；不宣称完整兼容旧 platform_proxy。
后续新增接口必须补充方法映射、权限与资源归属规则。

## OAuth 生命周期

- 复用现有授权过程的随机 state、PKCE、有效期、会话用户绑定和 seller 校验。
- 将 token 获取与轮换放入明确的凭证服务入口，供 BFF 及现有店铺调用复用。
- 使用数据库行锁串行化同一店铺的刷新；拿锁后重新读取到期时间。
- 按响应 `expires_in` 记录过期时间，轮换后的 access/refresh token 原子持久化。
- 使用独立数据库事务保存轮换，避免后续业务错误回滚已失效的旧 refresh token，
  也避免提交调用者无关业务变更。检查现有调用链的锁顺序以避免自锁。
- 网络失败或返回不完整凭证时保留原记录并返回可识别错误，不记录凭证。
- 写操作不因超时或 401 自动重放；避免重复创建商品。

## HTTP 行为

- 保留查询参数的重复值和编码；禁止查询参数覆盖 Authorization 或指定任意上游。
- 透传接口的 JSON、multipart 和二进制请求体保持字节内容，最大 200 MiB；
  超限返回 413。描述更新按上述映射构造 JSON，是原样透传的明确例外。
  图片接口另遵守平台 10 MB 图片限制；multipart 外层大小不等于图片文件大小。
- 转发仅保留必要 Content-Type、Accept 等头，由 BFF 注入平台 Bearer token。
- 上游地址固定；拒绝路径穿越和异常编码，关闭自动重定向。
- 保留平台业务状态码与响应体，过滤 hop-by-hop 头及 Connection 指定的头。
- 默认超时 120 秒，可配置；连接失败和上游超时返回 502。
- BFF 错误使用 `{ "success": false, "error": "..." }`；鉴权失败为 401，
  无权限或店铺不属于用户为 403，未知接口为 404。
- 日志包含用户、公司、店铺、方法、路由、状态和耗时，不记录请求体或凭证。

## 验证与交付

提供 Odoo 模块、接口映射表、配置说明、API Key 使用说明、curl 示例和测试。
测试覆盖实际 HTTP 鉴权、只读/写权限、跨公司与跨店铺访问、路由映射、
请求体与响应透传、大小限制、token 轮换持久化与并发、超时和上游错误。
使用模拟 Mercado Libre 上游与隔离测试数据库，不向真实店铺创建测试商品。
实现完成后报告运行过的检查及未验证部分；上线需另行安排模块安装/升级。

## 参考

- 本地 `erp-bff.md`
- `deploy/mercado/addons/meli_accounts/{models,controllers,store_api}.py`
- `odoo/addons/base/models/ir_http.py` 中 Bearer 鉴权
- https://global-selling.mercadolibre.com/devsite/authentication-and-authorization-global-selling
- https://global-selling.mercadolibre.com/devsite/items-and-searches-global-selling
- https://global-selling.mercadolibre.com/devsite/global-listing
- https://global-selling.mercadolibre.com/devsite/global-selling-item-create-update-global-items
- https://global-selling.mercadolibre.com/devsite/item-description
- https://global-selling.mercadolibre.com/devsite/pictures

以上 OAuth、搜索、创建/更新 FAQ、描述和图片文档已于 2026-09-28 使用
`mercadolibre-mcp-server.search_documentation` 与 `get_documentation_page`
（`siteId=CBT`, `language=en_us`）查询。描述文档更新时间为 2026-09-03。

## 待用户审阅

确认采用 Odoo 模块架构、Odoo API Key 鉴权、`/api/meli` 路由和以上第一版接口范围。
此文档是设计提案，尚未实现产品代码。
