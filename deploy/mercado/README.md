# 本机 Odoo 平台适配层

目标：OpenClaw → MCP（Streamable HTTP）→ Odoo BFF → Odoo 内部 `meli_platform_adapter` → Mercado Libre。
本次实现内部适配层和 Odoo 调用方迁移，OpenClaw / MCP transport 不属于本次交付。

## 模块

- `addons/meli_platform_adapter`：依赖 base；`mapping.py` 为平台映射，`transport.py` 为原始字节 HTTP 通道，`api.py` 为 requests 风格 OAuth/店铺通道，`oauth.py` 管理刷新编排。两种通道均固定源站、无自动重试和重定向；保留原 BFF 的 query 编码和 multipart 字节。
- `addons/meli_accounts`：店铺模型、授权入口和现有业务流程；`credentials.py` 实现适配层调用的持久化钩子，负责独立事务、权限复查和刷新行锁。
- `addons/meli_bff`：保留 `/api/meli` HTTP 契约，API Key 认证、Reader/Writer、公司和资源归属校验；不新增任务、审批或执行记录。
- `.local/addons/meli_oerp`：原有第三方连接器依赖，未在本次批量重构。[剩余入口](docs/legacy-platform-calls.md)。

OAuth 只通过 `meli_accounts.credentials.credential_token` 委托适配层刷新，凭据仍在原店铺表，不新增凭据数据库。原有导入/发布队列保留，但本机 cron 继续关闭；没有新增长任务能力。
图片 CDN 下载不携带 OAuth，仍在原业务模块执行，未与平台 API 请求混合。

## 本机运行

有效 addons_path 顺序：仓库 `addons`、`deploy/mercado/addons`、`.local/addons`。
旧同名 BFF/accounts 已移出 `.local/addons`，归档至 `.local/adapter-backup`，避免载入重复版本。
数据库：仅 `127.0.0.1:5433/mercado`。配置在 `.local/odoo.conf`，不得提交。
启动：`./.local/manage.command start`；状态：`./.local/manage.command status`。

## 验证

纯单元测试（不连接数据库）：`.venv/bin/python deploy/mercado/tests/run_unit.py`。
本机升级与回归：`.venv/bin/python deploy/mercado/tests/run_local_upgrade.py`。
升级测试会更新本机模块，执行前停止本机 Odoo并备份；脚本限定本机 mercado:5433 且禁止外网 TCP。它不是日常启动命令，也不应在生产数据库运行。
数据库测试使用回滚事务；OAuth 并发测试的独立持久化测试记录通过 teardown 清理。测试不会执行真实平台写操作。

## 回退

升级前数据库备份 `.local/adapter-backup/mercado.dump`，原模块 `addons.tar.gz`，原配置 `odoo.conf`。目录含敏感数据，权限受限、Git忽略。
停止本机 Odoo，恢复同一备份时点的数据库、模块和配置，再启动；不要在新版数据库上只替换旧代码。恢复数据库会覆盖备份后新增的数据，实际回退前需核对并保留新增业务数据。
远端数据库和远端服务未参与此次部署。

## 本次验收结果

2026-11-07：17项纯单元测试、52项Odoo数据库回归通过（0失败、0错误）。
实际运行模块均来自本目录；health/login页正常，匿名BFF401，临时授权Key店铺查询200。
OAuth测试店铺、用户关联联系人及验证Key已清理。日志位于 `.local/adapter-backup`。
独立审阅未发现迁移代码的重要回归；遗留入口清单只包含位置和用途，不复制凭据示例。

## Global Selling 统一操作注册表（实施中）

已新增 Odoo 权威注册表基础和 `/api/meli/operations`、`/api/meli/operations/{operation_id}/execute` 两个入口。目录按 Odoo 用户读写角色过滤，按需校验店铺；执行检查契约版本、参数、店铺及资源归属。目前注册68个操作，覆盖商品、订单、订单包、物流、问答、无附件售后消息与订单纠纷读取，尚不代表完整 Global Selling 接口。

新增依赖见 `requirements-registry.txt`；本机虚拟环境已安装。接口范围与证据进度见 `docs/architecture/global-selling/`，历史 MCP 候选不视为已实现。Streamable HTTP MCP 已开始实施，开发阶段的配置和限制见 `mcp/README.md`；BFF 业务覆盖和 MCP 完整验收仍未完成。

读取当前用户的注册表目录：

```bash
curl -H "Authorization: Bearer $ODOO_API_KEY" \
  'http://127.0.0.1:8069/api/meli/operations?shop_id=1'
```

执行入口接受 `contract_version`、`shop_id`、`arguments`；版本取自目录响应。目录只暴露当前角色可用的操作定义，服务端仍会在每次执行时检查用户、店铺及资源归属。版本变化时应重新读取目录，写请求不能自动重放。当前六项能力只是经过部分文档核对的增量，不代表全量接口契约完成。


本次扩展中，运单调用附带官方要求的 `x-format-new: true`，读取前核验发件人；订单核验卖家，pack 必须逐项核验1至100个订单。问题的读取、回复和删除需要同时核验问题及关联商品。售后消息仅支持已验证pack和文字，尚不支持附件。Model 6账号的平台限制仍由平台返回。平台单次响应限制为200 MiB。

纠纷读取目前仅支持关联订单的纠纷：必须核验卖家、订单及站点归属；支付、物流和购买类型暂不开放。Model 6 卖家受 Mercado Libre 平台限制。

问答检索使用当前店铺绑定的市场卖家，每页最多 10 条；站点或商品归属无法确认时拒绝整页，并移除上游总数等汇总信息。

物流标签需要运单满足平台状态和物流模式限制；订单开票资料包含买家税务信息，需具备店铺读取权限。

商品表现与评价读取仅支持当前店铺站点的市场商品，并核验商品卖家。

站点付款方式和尺码图可用领域以授权店铺为上下文；付款方式站点由服务端绑定。

尺码换算以店铺站点、领域和性别筛选；纠纷原因查询要求合法原因 ID，Model 6 的限制由平台执行。

订单搜索由服务端绑定卖家和站点，每页最多 10 个结果和 20 个订单；逐个验证订单归属，移除上游聚合数据。

CBT 类目属性、发布规格和领域规格以及领域搜索支持有界参数；非 CBT 类目暂未开放。

自有物流跟踪写入仅允许 Writer 且运单须为 me1/default；delivered 和 not_delivered 为平台最终状态，写入不自动重试。

图片上传使用 multipart，包含一个 request JSON 字段和一个 file 字段；支持 JPEG/PNG，最多 10 MB，仅 Writer 可操作。

商品活动查询会核验店铺站点与商品卖家，平台 user_id 和 app_version 由服务端绑定。

运单关联订单查询逐项核验卖家和订单归属，最多 100 项；存在跨店数据时拒绝整份结果。

卖家身份读取校验全球卖家 ID；市场账号映射仅保留当前店铺的卖家、站点和物流模式。

纠纷搜索限定订单类型和当前店铺卖家、站点，每页最多10项，并重新验证每个纠纷及关联订单。

市场商品运费读取会核验店铺站点与商品卖家；商品须满足平台活跃状态要求。

Full 库存仅支持墨西哥和智利；库存 ID 必须来自当前店铺市场商品或其变体，并核验卖家、站点和 Full 物流模式。

Full 库存变动查询要求日期范围不超过 60 天，并校验每条结果的卖家和库存 ID。

订单纠纷可读取消息、退货、预期方案和部分退款报价；需核验纠纷及订单归属。

纠纷消息发送仅支持文本和订单纠纷；必须是 Writer，且平台返回对应的可用操作；不支持附件。

订单纠纷支持全额退款、允许退货和请求调解；必须有 Writer 权限及对应平台可用动作，写入不自动重试。全额退款会产生真实资金后果。

部分退款需明确选择平台当前报价中的百分比，且纠纷必须满足 PDD、卖家可执行动作及待处理退货方案。

退货费用查询支持可选 USD 换算，需先通过订单纠纷的归属校验。

市场商品详情只返回当前店铺站点和市场卖家匹配的商品快照。

全球商品到市场商品的映射只返回当前店铺站点且卖家匹配的商品。

全球商品批量查询最多10个，逐个校验归属并验证批量返回的每项 ID 和卖家。

Full 履约的范围、进度及未解决事项见 `docs/architecture/global-selling/fulfillment-scope.md`。
