# 店铺商品筛选上线记录

2026-09-25（北京时间）已上线 meli_accounts 19.0.1.2.0。

入口：https://erp.yingshi.dev/odoo/meli-store-products 。顶部“当前店铺”选择店铺，商品菜单按店铺关联筛选，刷新保留选择。独立店铺选择与 Odoo 公司选择分别使用。

现有刊登按平台核验结果关联：Guangzhou YingShi Keji 28 个，Guangzhou RongZhi WangLuoKeJi 0 个，待归属 1 个。新导入刊登通过归属核验任务关联，成功发布记录自动关联目标店铺。

这是商品菜单筛选，不是新增的数据访问权限隔离；公司权限继续生效。

验证：隔离数据库 9 项回归测试全部通过；隔离环境和正式域名浏览器测试通过，覆盖空店铺、28 个商品、刷新、普通商品菜单、待归属商品，浏览器无错误。部署文件与验证副本一致，生产服务 active。

数据库及原模块备份：`backups/store-filter-20260925-070458/`，包含 `mercado.dump`、`meli_accounts/`、`upgrade.log`、`migration.log`。

本次测试和上线脚本暂存 `/tmp/meli-store-filter/`。部署目录未纳入主仓库版本控制，本次未进行 Git 提交或推送。
