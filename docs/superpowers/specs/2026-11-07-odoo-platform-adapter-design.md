# Odoo 内部统一平台适配层

状态：用户已确认实施设计；内部适配层已实现并通过本机部署验收。

## 目标与范围

目标链路：运营人员 → OpenClaw → MCP（Streamable HTTP）→ HTTP → Odoo BFF → Odoo 内部平台适配模块 → Mercado Libre API。
本次实施单元为内部适配模块及现有 Odoo 调用方迁移。OpenClaw 接入、MCP transport 改造作为后续实施单元，不将其标记为本次已完成。
不新增任务、审批、执行记录、长任务或 stdio 兼容入口。保留现有公司权限、BFF Reader/Writer 角色、店铺和商品归属校验，不新增员工到店铺分配规则。

## 方案选择

用户选择内部独立 addon：复用 Odoo 的店铺数据与部署流程，避免额外服务间认证和凭据同步。
独立 platform-proxy 可独立发布、扩容和被多系统共享，但本阶段增加部署与故障处理成本，暂不采用。

## 当前依据

- `.local/addons/meli_bff/service.py`：权限、归属校验及平台调用编排。
- `meli_bff/protocol.py`：包含 BFF 输入解析及平台 API 映射，需分离。
- `meli_bff/client.py`：固定平台源站、响应缓冲、超时、禁止自动重定向。
- `meli_accounts/credentials.py`：OAuth 刷新、独立事务和行锁。
- `meli_accounts/controllers.py`、`store_api.py`、`importing.py`、`publishing.py`：存在直接平台请求。
- 当前依赖：meli_bff → meli_accounts → meli_oerp。

## 模块与依赖

新增 `meli_platform_adapter` addon，依赖 Odoo base；meli_accounts 依赖它，meli_bff 通过明确依赖使用它。
适配层不依赖 meli_accounts 或 meli_bff，防止循环依赖。API 映射采用普通值对象接收店铺站点、物流和已解析操作；不导入 BFF controller、request 或 BffError。
店铺模型和凭据字段仍由 meli_accounts 持有；使用该模型提供的凭据存储接口接入适配层。OAuth 网络交换和刷新编排集中到适配层，存储钩子维持既有独立事务、行锁和提交语义。

## 职责与契约

BFF：认证 Odoo 用户、读写角色、公司和店铺权限；解析输入并校验资源归属；将适配层错误转换为原 HTTP 契约。
适配层：OAuth 交换与刷新、平台路径与载荷映射、固定源站 HTTP 访问、超时、响应处理与统一错误类型。
保留 `/api/meli` 路由和现有外部请求/响应兼容性；不暴露任意 URL 代理。
平台凭据不得进入 MCP 响应、模型上下文或日志。客户端提交的 seller_id 不作为可信身份。
写请求不自动重试；平台请求超时并不代表平台未执行。保留现有重定向禁用、凭据刷新失败处理及响应大小限制。

## 调用迁移

先迁移 BFF 的平台传输和 API 映射，再迁移独立店铺的授权、读取、导入、发布调用，统一经过适配入口。
迁移前枚举 meli_oerp 的旧连接器调用；不批量修改未验证的遗留功能。尚未迁移的旧入口必须在交付清单中列出，不能宣称全仓库请求已统一。
平台图片下载属于资源下载，保留独立的域名与大小限制，不混入携带 OAuth token 的 API 请求通道。

## 数据和部署

只使用已切换的本机 mercado（127.0.0.1:5433），不创建独立测试数据库，不连接或升级远端数据库。
实施前备份本机数据库和模块文件；保持 cron 关闭。
当前自定义模块在 Git 忽略目录 `.local/addons`。实施时应将本次相关源码整理到可版本管理的部署目录，再更新本机 addons_path，确保同名 addon 只有一个有效来源。
此源码位置调整不涉及远端发布。

## 验证与回退

运行可脱离数据库的协议映射/HTTP 单元测试，平台请求全部 mock。
在本机数据库验证模块安装升级、权限拒绝、店铺归属、既有接口及网页登录；测试使用回滚事务，避免修改真实业务记录。OAuth 独立事务的测试需单独保证清理，不调用真实平台写接口。
对比旧新映射、状态码、query 编码和 multipart 行为，验证无循环依赖以及刷新仅有单一入口。
失败时停止本机 Odoo，恢复模块与配置；如模块升级更改数据库则配套恢复升级前备份，再启动旧版本。

## 待确认

用户已确认本实施单元边界和内部模块设计。更细店铺授权未纳入本次范围，维持既有规则。
