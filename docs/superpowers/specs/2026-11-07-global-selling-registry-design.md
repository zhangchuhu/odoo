# Global Selling 接口注册表与 MCP 设计

状态：用户已确认本设计，进入逐接口盘点与实施。

## 1. 目标、范围与顺序

运营人员 → OpenClaw → MCP / Streamable HTTP → Odoo BFF → 内部 meli_platform_adapter → Mercado Libre。
先完成官方接口盘点及 BFF/适配层覆盖，再交付 MCP；不以原九项操作或旧 MCP 的139项目录作为全量范围。
官方 Global Selling 文档为范围依据，来源和初步领域见 docs/architecture/global-selling/coverage-inventory.md。Amazon不属于该范围。
不新增审批、业务任务、执行记录或本地后台长任务。沿用现有公司/店铺权限、Reader/Writer角色。只修改本机 mercado:5433，不建测试数据库、不发布远端。

## 2. 方案选择

已选择声明式注册表＋专用 handler。它统一参数与能力目录，允许特殊协议保留专门实现。
未选择逐域重复维护BFF与MCP定义：容易出现参数漂移和覆盖遗漏。
不采用任意URL透传：无法可靠完成资源归属和能力约束。

## 3. 全量接口基线

第一交付物是逐接口矩阵，覆盖已盘点的16个领域及其官方子文档。每项记录：来源URL、核对日期、method/path、请求字段及必填项、固定请求头、响应类型、账号/站点能力、资源归属规则、实现位置、测试和状态。
一个HTTP method+规范路径+有实质差异的账号模式作为一项契约；别名/示例不重复计数。
状态区分 verified、implemented、tested、blocked、deprecated、non_api；blocked必须有具体缺失证据或平台前置条件。未读取文档不可标记已支持，待办不能算覆盖。
全量验收要求所有在范围内且仍有效的可实现接口完成测试；文档无法访问、依赖未知或归属无法核实的接口列入阻塞清单，存在此类缺口时不宣称全量完成。
非API指导页不生成工具。平台授权开通限制与代码未实现分开统计。真实平台写调用不作为默认测试手段。

## 4. 注册表归属和字段

权威定义保存在 `meli_platform_adapter/registry/` 的版本化Python定义中，运行时构建不可变注册表；不提供网页动态编辑、任意模块导入或执行代码能力。
BFF依赖适配层；适配层不反向依赖BFF。定义用JSON Schema描述输入，运行时BFF执行完整schema校验，MCP使用同一schema预校验。
每项字段：
- operation_id：稳定业务操作ID，命名为领域.动作；契约破坏性变化使用新版本。
- domain、title、description、docs_url、verified_at。
- input_schema：path/query/body/file的完整类型与限制；默认拒绝未知顶层字段，平台明确允许的动态字段局部放行。
- permission：read/write显式声明，不能仅由BFF外层POST推断。
- scope：public/shop；平台业务与凭据相关操作必须shop scope。
- capability_requirements：账号类型、站点、物流、应用授权及已验证的标签要求。
- ownership_policy：无资源公共读取、店铺绑定、单资源归属、复合资源归属、批量逐项归属等已登记策略ID。
- handler_id：仅来自静态白名单。
- upstream：固定method/path模板、允许query/header、身份字段注入规则；特殊handler可自行构建，但不得接受调用方上游URL。
- request_kind、response_kind：json、multipart、binary等；单项大小、超时限制。
- exposure：mcp / bff_only / inbound；OAuth回调与通知不能当作普通工具调用。
- retry_policy：写请求默认never；本阶段不在MCP实现自动重试或后台轮询。

构建校验拒绝重复ID、无handler、无归属策略、错误schema、任意源站、缺少文档证据及权限定义。注册表版本使用导出契约的确定性hash。

## 5. Odoo BFF 接口

现有 `/api/meli/shops...` 保持兼容，内部复用新注册表，既有query编码及原始multipart行为继续回归验证。
新增接口：
- GET `/api/meli/operations`：按调用者读写角色过滤后的目录，支持domain与shop_id过滤；指定shop时必须校验访问权并返回能力信息。目录不含上游凭据、内部handler路径。
- POST `/api/meli/operations/{operation_id}/execute`：JSON信封 `{contract_version, shop_id, arguments}`；shop操作的shop_id必填。公共操作仍要求BFF读权限。
- 文件上传使用相同execute地址，multipart中固定 `request` JSON字段和 `file` 二进制字段；字段数和大小按操作定义限制。
- 已知可直接返回的二进制结果保留Content-Type和安全Content-Disposition，禁止平台Cookie和重定向传播。

执行顺序：Odoo API Key识别用户 → operation查找与版本匹配 → 角色与shop访问 → schema → 能力要求 → 资源归属预检 → 平台调用 → 有界响应。
外层POST既可执行read也可write，以operation.permission为准。所有入口（包括旧路由）共享强制权限与身份规则，不信任模型或请求头传入的用户/公司身份。
错误需区分校验失败、401/403、能力不支持、契约版本不匹配、上游限流及网络结果不确定；返回结构不得泄露凭据。上游明确业务状态及Retry-After在允许范围保留。写超时返回结果不确定提示，禁止自动重发。

## 6. 平台适配与特殊handler

一般JSON接口使用白名单映射handler，仍执行操作专属参数和归属策略；不能仅拼接任意路径。
特殊handler覆盖 OAuth、文件上传/下载、批量操作、组合身份、账号模式差异、通知回调。
资源归属逐类定义：商品核验seller；订单/Packs/物流通过官方关联关系验证；问题/消息/客诉核验相关卖家、订单或会话；广告/促销核验广告主、活动与卖家关联。批量输入在任何写请求前验证全部资源，防止部分跨店写入。
归属证明失败或文档未明确时拒绝执行，不能因使用了某店铺token就默认资源属于它。读取前必要的元数据预检只用于授权，不将未经授权的数据返回给调用者。
能力检查使用店铺数据与已验证平台元数据，不将“传统CBT、User Products、全托管”混为一套规则；未知能力不能缓存成已授权，平台仍进行最终权限判断。
OAuth继续复用已实现的独立事务、锁和单一刷新入口；授权流程返回浏览器入口，不把access/refresh token作为MCP结果。
通知是入站HTTP而非MCP工具；依据官方规定验证来源/内容，并以绑定店铺凭据重新获取资源确认归属。只进行有界同步更新，不引入本地worker。无法在短请求内完成的通知处理列为约束冲突，不能静默吞掉或返回虚假成功。
平台自身异步API允许提供提交和状态查询两个短操作；不在本机等待完整任务结束。

## 7. MCP Streamable HTTP

仅HTTP入口，不保留stdio。沿用现有Node项目和MCP SDK，使用无状态POST `/mcp`，每请求独立server/transport，避免用户身份串用；不提供长连接事件订阅。
默认本机127.0.0.1:8071；严格Host/Origin和请求体限制。公网暴露需要单独配置HTTPS反向代理，不在此次本机部署中默认开放。
服务端私有配置映射MCP bearer至Odoo用户API Key；两个凭据不是同一个值。初始可使用现有admin对应的本机Key，后续按运营人员配置独立Key。任何工具参数不得修改该映射。
MCP从经过认证的Odoo目录获取schema；缓存键包括身份和contract_version，不共享用户可见目录。凭据撤销后Odoo仍在每次执行时重新鉴权，目录缓存不能充当授权。
保留四个元工具：listing_module_router、listing_operations_for_modules、listing_list_operations、listing_call。operation来自Odoo目录，不再维护Node中的第二份平台映射。只导出exposure=mcp的操作。
请求携带contract_version；旧目录遇到版本不匹配先刷新，再由调用方重新决定执行，不能自动重放写操作。
文件输入由MCP有界base64解码再转multipart，校验文件名和MIME；不得接受任意本地路径或下载URL。较大二进制输出提供Odoo受鉴权短期文件引用，绑定用户和店铺，每次读取重新鉴权，到期删除临时文件；MCP不将大文件塞入模型上下文，链接中不带API Key。临时文件不是业务执行记录。

## 8. 验证与交付

按领域分批实现，避免一次升级全部模块；验收顺序不改变“BFF先齐全，MCP再上线”。
1. 官方文档矩阵与注册表校验器：验证每项schema、handler、归属策略、证据和覆盖状态。
2. BFF与handler：mock平台进行参数/路径/请求头/二进制回归；跨公司、跨店、批量混合归属、伪造身份和无权限操作均拒绝；能力差异有测试。
3. 本机数据库测试：备份后使用当前mercado，默认回滚；OAuth独立事务测试精确清理自建记录，测试禁止真实平台写操作；cron保持关闭。
4. BFF全量覆盖验收：矩阵逐项关联实现与通过的测试；不能以注册成功代替接口可用。
5. MCP：SDK真实HTTP握手、目录和调用；两身份并发、过期凭据、目录版本变化、错误脱敏、文件限制；实际只读店铺查询联调。
6. 交付覆盖矩阵、配置模板、测试报告、遗留/阻塞清单、启动与回退说明。

现有本机Odoo服务保持运行，未完成的MCP草稿仅保留备份。部署前保存数据库/代码/配置，回退按同一版本恢复；不覆盖远端数据。

## 9. 留给实施者的选择

JSON Schema校验库、按领域文件拆分、缓存实现及临时文件默认大小/有效期可由实施者依据文档和依赖兼容性选择，写入执行计划并测试。不得自行改变身份模型、扩大源站白名单或省略归属校验。

## 10. 设计批准后下一步

先完成逐接口官方证据矩阵，再编写按领域可验收的执行计划。批准本设计不代表已核实所有官方接口，也不代表已完成BFF或MCP扩展。
