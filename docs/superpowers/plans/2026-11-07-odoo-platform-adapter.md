# Odoo Platform Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 BFF 和独立店铺平台请求收敛到 Odoo 内部独立 addon。
**Architecture:** meli_platform_adapter 依赖 base；meli_accounts 和 meli_bff 使用适配层。持久化店铺字段保留原模型，平台协议与网络逻辑不依赖 BFF。
**Tech Stack:** Odoo 19 / Python / PostgreSQL 16。
**Spec:** docs/superpowers/specs/2026-11-07-odoo-platform-adapter-design.md

## Global Constraints
只操作本机 mercado:5433；不创建测试数据库；无真实平台写测试；不新增任务审批记录或长任务；旧外部 HTTP 契约不变。

## Review Focus
- 伪造 seller / 跨公司 shop 请求仍被 BFF 拒绝：复用 protocol/access 测试。
- query 重复值、百分号编码、二进制 multipart 保持：复用 protocol/client 测试。
- OAuth 并发刷新事务不提交业务：保留存储事务和 token 测试。
- 平台写超时不自动重试，错误不泄露 token：新增 transport mock 测试。
- 同名 addon 来源唯一、无循环依赖：检查 manifests 和运行时 module.__file__。

### Task 1: 备份和适配层核心
Files: deploy/mercado/addons/{meli_bff,meli_accounts,meli_platform_adapter}; deploy/mercado/tests/test_adapter.py
Interfaces: PlatformError(status, code), transport.send(...), api.request(method,path,token=None,...), mapping.prepare_call(...).
- [x] 停止本机 Odoo，备份数据库、配置、旧源码。
- [x] 添加适配层测试并运行，确认新模块缺失导致失败。
- [x] 将既有 BFF/accounts 复制到版本管理目录；新增 base-only adapter，将平台映射/HTTP 传输迁移进去，BFF 保留路由及权限。
- [x] 运行映射、传输回归测试，确认保持原有契约。

### Task 2: OAuth 与独立店铺调用迁移
Files: adapter/oauth.py; accounts/{credentials,controllers,store_api,importing,publishing}.py
Interfaces: oauth.credential_token(store, storage); storage.refresh_context(store)；api.request 保持 requests.Response 接口。
- [x] 测试固定源站、禁用重定向、单次请求、credential刷新锁/事务语义。
- [x] 迁移刷新编排，存储钩子保留模型访问、行锁与独立提交；授权交换、商品读写、图片上传统一调用 api.request。
- [x] 资源图片下载仍使用独立受限逻辑；枚举未迁移 legacy 调用。
- [x] 运行 unit 回归和源码调用边界检查。

### Task 3: 本机部署与验证
Files: .local/odoo.conf; deploy/mercado/README.md
- [x] 更新 addons_path，旧同名模块归档至备份目录，保持 legacy meli_oerp 原路径。
- [x] 安装 adapter 并更新 accounts/bff，cron保持关闭。
- [x] 在本机 mercado 事务回滚测试权限和模型调用；外部网络调用 mock。
- [x] 启动本机 Odoo，检查登录页、health、匿名BFF拒绝和有效API Key读取。
- [x] 独立审阅，修复重要问题；记录测试结果、回滚路径和剩余legacy范围。

## Execution ledger
用户已经明确授权实施；本次直接执行，不再请求相同范围的批准。使用当前工作区的新分支；变更仅涉及新部署目录与本机配置，不覆盖用户已有 docs 文件。

### 实施记录
- 已备份 `.local/adapter-backup/mercado.dump`、模块归档、配置和启动预检。
- 已在 codex/odoo-platform-adapter 分支实施，源码位于 deploy/mercado/addons；本机同名旧模块已归档。
- Ruling: OAuth存储钩子继续返回受锁保护的店铺记录，适配层不导入具体店铺模型；保证原权限和事务行为。
- Ruling: API映射和原始传输原样迁移，BFF以兼容导入保持既有契约；requests通道用于OAuth和独立店铺业务。
- 首次部署发现旧启动预检硬编码addons目录，已改为读取实际配置；无远端改动。
- 17项纯单元、52项数据库回归再次通过；OAuth测试联系人、店铺和临时API Key全部清理。
- 独立审阅：未发现迁移代码重要回归；已移除遗留入口清单中的源码摘录，防止复制历史凭据。
- 修复原OAuth测试只删用户未删联系人的清理缺口，已删除本次精确识别的7条测试联系人。

- Task 1/2/3 complete：模块已安装、本机健康检查和登录页正常；匿名API401、授权查询200；运行时源码来源已核验。测试日志：.local/adapter-backup/{unit.log,upgrade.log}。
