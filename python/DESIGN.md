# Odoo BFF MCP 设计（2026-09-29）

用户已明确选择当前 Odoo BFF。本文件取代根 DESIGN.md 中的平台代理、139 operation、Amazon、CBT 特殊转换和 100 MiB 上传要求；原文件保留供参考。

## 契约

OpenClaw → stdio MCP → Odoo `/api/meli` → Mercado Libre。Python 工程独立安装，不导入 Odoo，不启动 Node，不接触 ML OAuth。Odoo API Key 以 Bearer 发送，权限、公司隔离、商品归属和站点/物流校验仍由 BFF 实施。

保留 listing_module_router、listing_operations_for_modules、listing_list_operations、listing_call 四工具。模块为 odoo_meli，共九个 operation：odoo_shops_list、odoo_shop_get、listing_items_list、listing_get_item、listing_create_item、listing_update_item、listing_get_description、listing_update_description、listing_upload_picture。三个目录工具只读本地；不暴露未实现的旧 operation。

请求以本仓库 meli_bff/protocol.py 为依据：shopId 为 1..9999999999 整数或十进制数字字符串；商品 ID 为三个大写字母加数字，描述仅 CBT ID。query 只允许 BFF 白名单，数组保留同名值顺序。创建/更新的 item 对象直接成为 JSON body；创建必须有非空 sites_to_sell；描述 body 仅 plain_text。严格 base64 图片上传为单个 file，最多 10,000,000 bytes（可配置更低），默认 upload.jpg。可选字段可省略，显式 null 按声明处理，不接受额外顶层字段。

## 结构与运行

config.py 管理不可变配置；operations.py 定义九个模型和纯请求构建器；client.py 管理实例级 HTTPX 连接池；dispatcher.py 实现四工具；server.py 适配官方 MCP SDK；__main__.py 提供 stdio CLI 和显式 --env-file。

ODOO_BFF_BASE_URL 为 Odoo 根地址（可含部署 base path），ODOO_BFF_API_KEY 为 API Key。兼容原 HAISHANG_ERP_* 环境变量。拒绝 userinfo、query、fragment、路径穿越和已含 /api/meli 的 base。仅固定 BFF 路由；禁止自定义 headers 和任意 HTTP 代理。TLS 校验、不跟随重定向、不重试、不使用环境代理。默认总超时 180 秒，覆盖 BFF 的身份检查及上游调用；可用 ODOO_BFF_TIMEOUT_SECONDS 调整。

HTTP 响应返回 httpStatus/body；>=400 标记 isError，保留业务 JSON、文本和空响应。参数/未知 operation/网络错误用 mcpError envelope，不泄漏输入、凭证或请求。取消继续传播，关闭连接池；日志仅 stderr。文本 content 为 JSON，无二次 result 包装。

## 验收

离线测试覆盖九项请求映射，并直接载入 BFF 的纯 protocol.py 验证兼容；校验非法参数零 HTTP、状态和网络异常、实例凭证隔离、关闭、真实 stdio 子进程和 wheel 脱离源码运行。真实联调只提供显式只读脚本，不自动调用生产。README 提供 OpenClaw 原生配置和 mcporter 备选配置；用户自行填入凭证。
