# 发布修复与复测（2026-09-25）

部署版本：meli_accounts 19.0.1.3.1。

来源商品读取改用商品的 meli_source_store_id 授权，并校验远端 seller_id、商品编号和美元币种。发布请求携带来源商品的 sale_terms，不伪造保修条款。重新检查会刷新来源图片和销售条款，保留用户确认的英文名称。具体规格只准备对应规格；旧未提交草稿可纠正商品关联，锁定记录禁止改关联。数据库按目标店铺、来源商品和规格唯一约束，防止重复发布。

隔离数据库运行 33 项测试，0 失败、0 错误。包含来源授权、销售条款、模拟发布请求、历史草稿及唯一约束回归。真实源商品快照回放通过，生产实时检查通过。

备份：`backups/publish-fix-20260925-085331`（数据库及旧模块）。部署后服务 active；原 107 条 ready 记录退回待检查，以重新校验来源授权和条款。

仅实际重试发布记录 129：YingShi 商品 CBT5287191246、SKU MiBaiSeMaJiaTaoZhuang，原价 USD 118.94，目标 RongZhi。请求已带 WARRANTY_TYPE=6150835（来源值 No warranty）。平台返回 HTTP 500、integration.circuit_open.users-api、Service temporarily unavailable。记录保留 uncertain，未自动重发；不得视为发布成功，后续须核对目标刊登后再决定重试。

随后只读扫描目标店铺返回的全部 37 件商品，未找到测试 SKU。该结果不排除平台延迟，因此继续保留待核对状态。

测试日志：`/tmp/meli-publish-fix/final-tests.log`。隔离测试及单条复测脚本在 `/tmp/meli-publish-fix/`，不包含凭据。
