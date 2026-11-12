# Global Selling 全量接口扩展：范围盘点

日期：2026-11-07。状态：文档目录已核对，逐接口 method/path/schema/归属校验矩阵尚未完成；不是已实现接口清单。

## 证据

- 用户指定入口：https://global-selling.mercadolibre.com/devsite/en_us/introduction-globalselling （抓取返回403）
- 可读取的官方共享导航及订单文档：https://global-selling.mercadolibre.com/devsite/manage-orders-cbt
- 本机 BFF 现有 README：deploy/mercado/addons/meli_bff/README.md，九组已实现业务路由。
- 旧 MCP docs/python-rewrite/operations.json：139项目录快照，其中Native65、CBT61、Amazon13。该快照仅用于比对，不作为官方完整性或当前API正确性的证据。

## 官方导航中的覆盖领域

|领域|目标纳入内容|当前状态|
|---|---|---|
|认证与用户|OAuth、用户、应用权限|已有独立店铺OAuth，需逐项核对其余|
|基础资源|搜索、位置、配送基础数据、分页|待盘点|
|通知与审核|通知接收、moderations、品牌保护|待区分入站webhook与出站工具|
|商品|类目预测、发布、价格、属性、变体、图片、描述、Clips|已有部分CRUD，其余待盘点|
|尺码表与Catalog|尺码表管理/校验、目录资格/搜索/竞争|待盘点|
|User Products|价格按变体、UPTIN迁移|当前BFF明确不支持创建，需能力建模|
|库存与店铺|入库、Full库存、官方店铺|待盘点|
|售前|问答|待盘点|
|订单与物流|订单、Packs、发货、补偿、派送、出口凭证|待盘点|
|退款退货客诉|退款、退货、客诉、消息、解决方案|待盘点|
|售后消息|消息、待处理、受限消息|待盘点|
|促销|优惠、联合活动、价格折扣、数量折扣等|待盘点|
|账单|订单/Packs账单报告、账单资料|待盘点|
|指标|质量、趋势、访问、评论、声誉、购物体验|待盘点|
|广告|Product Ads、Display Ads|待盘点|
|全托管|发布、Grouped Listings、补货单|待盘点；不得套用当前传统CBT刊登规则|

## 扩展原则

先按官方文档建立 method/path、参数、固定请求头、账号能力、资源归属、响应类型和测试的逐接口矩阵；再实现BFF和适配层；最后生成MCP目录并切换HTTP transport。
平台异步API的提交/查询可以作为短请求；本系统不新增长任务worker。入站webhook不伪装成MCP主动调用工具。
Odoo保留身份与公司/店铺权限，写权限沿用Writer；不新建审批、业务任务和执行记录。
资源ID（order/pack/shipment/claim/campaign等）必须有对应的归属检查，不能只替换商品ID校验。
平台能力受账号类型和应用授权影响，覆盖接口不等于所有店铺可调用；不支持的能力应明确返回原因。
不采用调用方传入任意上游URL的通用透传。

## 已选择的实现方式

用户已选择声明式白名单注册表＋特殊接口独立处理：共享operation契约驱动BFF分发、适配映射、校验与MCP目录；OAuth/文件/资源归属等特殊逻辑使用专用handler。
备选逐域手写controller与MCP注册：改动直观，但参数契约会在两侧重复，难以核验全量覆盖。

## 已暂停的旧工作

.local/mcp-work 中存在未完成的HTTP迁移草稿，尚未同步原MCP项目或部署。
“仅九项操作”的阶段目标已被本次全量扩展替代；现有本机Odoo部署保持不变。

## 本次盘点进展（2026-11-07）

- `endpoint-matrix.csv` 列出已从官方页面或搜索索引识别的方法、路径及账号模式。`identified` 只意味着找到线索，未代表参数、归属规则或完整账号模式已验证。
- `legacy-mcp-candidates.csv` 包含旧项目中126个美客多候选操作，仅供追踪，不能作为官方API的完整范围或实现依据。
- `source-index.csv` 区分可读取、搜索摘要及403来源；网页可能更新，应在实现时复核。
- 注册表目前仅开放 `items.read`、`items.description_read`、`items.update`、`items.search`、`items.create`、`items.description_update`。它们复用已运行的BFF商品权限和归属逻辑，完整平台参数和其他账号模式仍需补齐。
- 官方全托管发布文档说明其 `POST /global/items` 载荷与普通跨境刊登不同；当前已有的传统店铺创建校验不能直接套用到全托管。

## 官方 MCP 接入进展（2026-11-07）

- 已使用 Mercado Libre 官方 MCP 的 `search_documentation` 和 `get_documentation_page`；原先直接访问403的入口、订单、物流等页面已通过MCP读取。
- `mcp-documents.csv` 索引75篇实际取回的文档，另有1篇返回空内容；原文只保存在本机 `.local/mercadolibre-docs/`，该目录不进入Git。
- `mcp-extracted-candidates.csv` 记录386条从58篇文档中自动提取的候选；包含示例路径及待确认方法，不能当作已完成逐接口审核或已实现数量。
- 官方物流文档提示查询运单需要 `x-format-new: true`；订单、pack与物流的归属关系需继续逐接口核验。
- MCP仅解决文档来源，Odoo全量接口、资源归属测试及项目MCP改造仍待完成。

## 后续实施进展（2026-11-07）

- `endpoint-matrix.csv` 与代码存在未同步条目，需以注册表和测试交叉核对；本机注册表当前有 68 个操作，仍不表示 Global Selling 全量完成。
- 官方 MCP 已成功取回 Items & Searches 文档并更新 `mcp-documents.csv`；自动候选仍需逐项人工核对。
- 写操作测试均为模拟上游；账号模式和真实平台行为仍需在有权限的环境验证。
