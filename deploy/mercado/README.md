# Odoo Mercado Libre 部署

访问地址：https://erp.yingshi.dev/web/login
数据库：mercado（独立新建，无演示数据）
管理员：admin；随机初始密码位于 private/admin.txt（仅部署用户可读）。

服务：odoo-mercado.service，已启用开机启动及失败重启。
配置：odoo.conf；日志：logs/odoo.log；附件：data/。
连接器源版本见 SOURCE.txt，代码固定保存在 addons/meli_oerp。
Caddy 域名配置：/etc/caddy/acli.d/sites/odoo-mercado.caddy。
原 IP 路由：/etc/caddy/acli.d/routes/odoo-mercado.caddy。
当前 Caddy 禁用管理接口，修改配置后需 validate 并 restart。

已配置管理员连接器权限、商品变体、手动订单确认。
连接器定时任务及自动同步保持关闭，尚未连接真实店铺。

## 待完成的店铺配置

1. 确认本土店或 Global Selling 跨境店、站点，以及最终域名。
2. App Id 和 Secret Key 已写入数据库；在设置 → 用户与公司 → 公司 → MercadoLibre 完成 Seller Id 和站点相关配置。
3. 当前 Redirect URI 为 https://erp.yingshi.dev/meli_login，需与开发者应用登记值一致；需在 Mercado Libre 开发者后台登记此回调地址。
4. 完成授权，按店铺核对币种、价格表、仓库及税设置。
5. 验证少量订单和商品导入后，再启用所需同步任务。

## 已完成验证

- 新数据库安装成功。
- 本机登录页面 HTTP 200，管理员登录成功。
- 本机经 Caddy 的 HTTPS 登录页面返回 200，并通过证书验证。
- erp.yingshi.dev 域名证书已签发，本机通过正式域名 TLS 验证并返回 200。
- ACME 的公网 443 验证曾超时，HTTP 80 验证成功；本机 UFW 未启用，INPUT 默认 ACCEPT。需要检查云安全组的 TCP 443 入站规则。

请在首次登录后修改初始密码。备份时同时保存 PostgreSQL 数据库和 data/filestore/mercado。
