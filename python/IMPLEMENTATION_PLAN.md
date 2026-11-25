# Odoo BFF MCP Implementation Plan

Spec: DESIGN.md。按用户选定的 Odoo BFF 目标在当前工作区独立 python/ 工程实施。

- [x] 1. 创建独立 pyproject/uv.lock；用配置、九项映射、输入边界和 MockTransport 测试建立失败基线。
- [x] 2. 实现不可变配置、严格模型、注册目录、异步 HTTP client 与 dispatcher；验证与 BFF protocol.py 的兼容、错误、取消和凭证隔离。
- [x] 3. 实现官方 SDK stdio 生命周期与 CLI；真实子进程验证四工具、调用返回及协议错误。
- [x] 4. 编写 OpenClaw 配置示例和显式只读 smoke，验证 wheel 独立安装；运行完整 pytest、Ruff、离线 smoke。

决策记录：原实施计划针对另一套 BFF，按用户澄清替换为上述四步；不修改已有 Odoo addon、工作区其他修改或宿主配置。SDK 版本以实际可安装并验证的版本锁定，记录任何偏离。

验证记录：2026-09-29，pytest 116 passed；Ruff check/format 通过；真实 stdio 新旧协议和模拟 HTTP 通过；wheel 在 /tmp 下新建虚拟环境安装后从非源码目录成功发现四工具九项操作。官方 MCP SDK 2.2.0 使用低层 Server 明确返回协议结果。独立只读审查无 Critical/Important。未执行生产联调或部署。

续测记录：2026-10-06，完整 pytest 116 passed in 8.71s（含四项 stdio 与离线 smoke）；当前 socketpair 正常，无需环境适配。锁定依赖同步、Ruff check/format、独立 stdio smoke、sdist/wheel 构建均通过。新建 /tmp 虚拟环境安装 wheel（解析到 MCP 2.3.0），在非源码目录且 PATH 无 Node 时成功发现四工具九操作并验证本地错误、console script 入口。使用现有 .env 完成真实 Odoo 店铺列表只读 smoke；通过真实 MCP stdio 查询列表及两家店铺详情，均 HTTP 200、isError=false。未执行平台商品读写、实际 OpenClaw 宿主接入或部署。具体环境命令见 README 验证章节。
