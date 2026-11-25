# Odoo Mercado Libre MCP（开发阶段）

无状态 Streamable HTTP：OpenClaw → `POST http://127.0.0.1:8071/mcp` → Odoo BFF。支持 `listing_shops`、`listing_module_router`、`listing_operations_for_modules`、`listing_list_operations`、`listing_call`。模块按 BFF 目录的 domain 划分。调用 `listing_call` 时传 `operation`、数字 `shop_id`、`arguments`，可选图片 `file`（`filename`、`mime_type`、`base64`）。各工具每次以当前身份重新读取 Odoo 目录；执行依然由 Odoo 鉴权。写入不自动重试，版本冲突返回 `CONTRACT_CHANGED`。

`ads.item_groups` 按店铺商品查询 Product Ads 广告组和 campaign ID；请求传 `item_id` 及可选 `limit`、`offset`。BFF 校验商品属于站点卖家，再从授权账号发现站点广告主；同站点广告主不唯一时拒绝查询。返回分页数据，调用方须检查 `paging`。该操作不返回 campaign 名称，也不覆盖 Display 或 Brand Ads。

安装 Node.js 20+ 后执行 `npm ci`。复制 `config.example.json` 到仓库外的私有配置，设置文件权限为 0600，填入 Odoo 地址、独立随机 MCP token 和对应 Odoo 用户 API Key。不要把实际密钥放进源码。用 `MCP_CONFIG=/absolute/path/config.json npm start` 启动。OpenClaw 以 Bearer MCP token 连接，不能把 Odoo key 当作工具参数。服务只监听本机 IPv4；对外访问需要另行配置 HTTPS 代理和访问策略。

局限：BFF 仍在扩展；没有列在目录中的功能不能调用。用 `listing_shops` 获取调用所需的 Odoo 店铺 ID。二进制下载被明确拒绝，等待实现与用户、店铺绑定且每次下载重新鉴权的短期文件引用；不返回未受控下载 URL。图片上传和真实店铺联调仍需验证，不要用于真实业务写操作，直到验收完成。

执行 `npm test` 运行本机回环 HTTP 和模拟 BFF 测试。不会调用 Mercado Libre。
