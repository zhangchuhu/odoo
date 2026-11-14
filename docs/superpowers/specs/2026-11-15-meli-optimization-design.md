## 美客多链接优化系统技术方案

版本：设计稿 v1.0

日期：2026-11-15

设计范围：独立实现商品研究、标题优化、爆款采集、图库核验、图片生成及质检、运营确认、平台执行与效果观察；完全移除飞书任务表及现有飞书技能的运行依赖。

关联业务文档：[链接优化流程](https://tcnae5j0r4z3.feishu.cn/docx/E7DFdA3MTovC8KxcGX9cSEounLg)。

本文是待实施的技术设计。新增模型、接口与能力不代表当前仓库已经实现；具体平台契约、研究数据源及图片服务能力需要在接入阶段核实。

## 1. 目标与关键原则

1. Odoo 保存业务任务、输入快照、产物、运营确认和平台执行结果，作为业务事实来源。
2. 独立 Worker 执行耗时浏览器、文本模型、视觉模型及图片生成任务。
3. OpenClaw 展示状态、收集选择并调用业务工具，不依靠聊天历史保存任务进度。
4. 复用现有 BFF 身份、公司与店铺权限，以及平台适配层；不在 Worker 中复制平台凭据管理。
5. 每个结果绑定明确输入版本，每个外部请求有持久身份，每次确认绑定确切变更，每次恢复从已验证的检查点继续。
6. 标题、图片、活动和广告独立推进。一个分支失败，不阻塞没有依赖关系的其他已确认操作。
7. 缺失库存、销量、评价等数据保留未知，不按零处理。效果变化作为观测，不自动归因为某一次修改。

第一版默认支持 Mercado Libre México，兼容单商品与批量优化。其他平台通过提供器和规则扩展，首期不承诺完整支持。

## 2. 当前基础与架构选择

仓库已有 `meli_accounts`、`meli_bff`、`meli_platform_adapter` 和开发阶段 MCP。现有 BFF 提供身份、角色、公司和资源归属校验，平台适配层负责 OAuth、协议映射和平台请求。当前基础尚无本方案所需的统一优化任务、检查点和运营确认机制；MCP 文档也注明图片上传及真实店铺联调仍需验收。

候选方案：

| 方案 | 优点 | 代价 | 结论 |
|---|---|---|---|
| 所有流程运行在 Odoo 请求或定时任务内 | 部署简单 | 浏览器及生成任务耗时长、能力依赖重，容易占用业务进程 | 不建议 |
| Odoo 持久化业务，独立 Worker 执行 | 适配现有基础，执行与业务隔离，恢复机制可控 | 需实现租约和任务协议 | 推荐 |
| 专用工作流引擎加多个微服务 | 编排和规模化能力强 | 首期部署与一致性复杂度高 | 后续按规模评估 |

推荐新增一个 Odoo 业务模块和一个模块化 Worker 服务，第一版以数据库任务队列调度。

### 2.1 组件职责

| 组件 | 职责 |
|---|---|
| OpenClaw / Odoo 界面 | 解析意图、展示筛选条件、查看候选、收集选择与确认 |
| MCP | 提供参数明确的优化业务工具，不承载持久状态 |
| meli_bff | 认证、角色、公司与店铺访问校验 |
| 新增 meli_optimization | 批次、商品流程、任务、产物、确认、平台操作与观察 |
| optimization_worker | 商品事实整理、研究、采集、标题和图片处理 |
| 浏览器执行环境 | 有界搜索、商品详情与图库采集、登录状态管理 |
| 模型与图片提供器 | 文本、视觉分析和图片编辑，能力按实际服务验证 |
| 附件 / 对象存储 | 图片、证据、结构化输出与质检报告 |
| meli_platform_adapter | 平台 OAuth、协议和请求转换 |

### 2.2 系统关系

```text
运营 → OpenClaw / Odoo 界面 → MCP → BFF → 优化业务模块
                                             ├─ PostgreSQL：任务、快照、检查点、确认
                                             ├─ 附件或对象存储
                                             └─ 平台适配层 → Mercado Libre

独立 Worker → 内部任务 API → 优化业务模块
     ├─ 浏览器执行环境
     ├─ 文本 / 视觉模型
     └─ 图片生成服务
```

Odoo HTTP 请求只处理短事务，不等待浏览器研究或整组图片生成完成。Worker 不直接连接业务数据库，也不接触 Mercado Libre OAuth 凭据。

## 3. 业务流程与任务依赖

```text
筛选条件解释 → 条件确认 → 批次创建 → 商品与 SKU 快照
                                       ↓
                                  商品事实整理
                  ┌────────────────────┼─────────────────┐
                  ↓                    ↓                 ↓
             关键词研究             爆款研究          活动 / 广告现状
                  ↓                    ↓                 ↓
             标题生成校验          参考商品选择        具体参数方案
                  ↓                    ↓                 ↓
             标题选择确认          目标 SKU 图库核验   分别确认
                  ↓                    ↓                 ↓
             标题更新核对          逐图生成和质检      分别执行核对
                                       ↓
                                  图片选择及顺序确认
                                       ↓
                                  图片更新和核对

各操作生效后 → 指标采集与效果观察
```

标题无需等待爆款研究；关键词依据满足要求即可生成。图片生成必须依赖准确的参考商品和目标 SKU 图库。批量确认绑定具体操作及版本，不是授权以后仍可能变化的筛选范围。

### 3.1 任务粒度

| 类型 | 粒度 |
|---|---|
| 商品快照 | 一个店铺商品 |
| 商品事实整理 | 一版商品输入 |
| 关键词研究 | 一个商品、一个站点 |
| 爆款研究 | 一个商品、一个目标平台 |
| 图库采集 | 一个参考商品及选定 SKU |
| 标题生成 | 一版输入的一组候选 |
| 图片生成 | 一张目标图 |
| 活动方案 | 一个商品的一组可选活动 |
| 广告方案 | 商品关联的具体广告资源 |
| 平台操作 | 一个明确确认的动作 |

每张图片单独建任务，五张图完成四张时中断，只恢复剩余图或剩余阶段。

## 4. 数据模型

采用通用执行层加业务明细层，避免每个任务类型重复实现重试、心跳和状态。

```text
筛选批次
└─ 商品优化流程
   ├─ 商品快照及 SKU 明细
   ├─ 通用任务
   │  ├─ 执行尝试
   │  ├─ 检查点事件
   │  ├─ 外部请求
   │  └─ 产物与证据
   ├─ 标题候选 / 爆款候选 / 目标图库 / 图片候选
   ├─ 活动方案 / 广告方案
   └─ 变更操作
      ├─ 运营确认
      └─ 平台执行与回读记录
```

### 4.1 批次：meli.optimization.batch

| 字段 | 说明 |
|---|---|
| uuid | 对外批次标识 |
| company_id、shop_ids | 公司和店铺范围 |
| requested_filter | 用户原始筛选要求 |
| resolved_filter | 解释后的结构化条件 |
| statistics_window | 统计起止时间、时区和口径 |
| filter_confirmation | 条件确认人、时间和摘要 |
| requested_by | 发起人 |
| workflow_version | 流程版本 |
| state | 草稿、运行中、部分完成、完成、取消 |
| counts | 商品、成功、失败、等待及待确认数量 |

筛选分页未完成时，不把当前数量当作完整候选集。保存分页检查点和完成标记；统计使用明确时间窗口，避免分页期间口径漂移。

### 4.2 商品流程：meli.optimization.case

一行表示某批次内一个店铺商品的优化过程。

核心字段：批次、公司、店铺、站点、商品身份、当前有效快照、流程版本、各分支进度、阻塞原因、创建人、负责人及时间。

唯一约束建议：`(batch_id, shop_id, item_id, item_scope)`。

全球商品 ID、市场商品 ID 和已核实映射分别保存，不能只保留含义不明的商品 ID。读取和更新时明确作用范围，跨站点或全球商品影响须展示。

### 4.3 商品快照：meli.optimization.snapshot

快照不可覆盖，刷新时新增版本。保存原标题、类目、商品状态、图片及顺序、平台图片 ID、SKU 明细、销量和统计窗口、采集时间、来源、原始响应附件、规范化摘要和哈希、数据完整性状态。

SKU 明细字段：

```text
variation_id / seller_sku / size
available_quantity
observed_at
value_status：known / unknown / unavailable
source
```

销量和库存未知时保留空值及原因，不得推断为零或断码。

### 4.4 通用任务：meli.optimization.task

| 字段 | 说明 |
|---|---|
| uuid、case_id | 任务及所属流程 |
| task_type | 如 research.bestseller、image.generate |
| input_revision、input_hash | 输入版本和规范化摘要 |
| input_manifest | 结构化输入及附件引用 |
| depends_on_ids | 依赖任务 |
| policy_version | 研究、验收或质检规则版本 |
| handler_version | 执行模块版本 |
| state、checkpoint | 执行状态及当前阶段 |
| priority、not_before | 优先级及最早执行时间 |
| budget_policy | 时间、次数及费用等限制 |
| current_attempt_id | 当前执行尝试 |
| result_status | 完整、部分、有效空结果、不合格 |
| error_code、resume_action | 错误和恢复动作 |

输入改变后创建新任务版本，旧任务和产物保留历史。任务去重不能仅依赖商品 ID，必须包含业务范围和输入版本。

### 4.5 执行尝试：meli.optimization.attempt

```text
 task_id、attempt_no、worker_id
 lease_token、lease_epoch、lease_expires_at
 started_at、heartbeat_at、finished_at
 capability_snapshot
 handler_version、provider、model（可获取时）
 error_code、error_summary、diagnostic_artifact_id
```

`lease_epoch` 是递增执行代数，用于拒绝失效 Worker 的迟到状态提交。不要把业务任务、执行尝试及模型质量候选混为同一个重试计数。

### 4.6 产物与证据：meli.optimization.artifact

```text
 uuid、company_id、case_id、task_id
 artifact_type、schema_version
 storage_ref、mime_type、byte_size、sha256
 source_url、observed_at
 producer、producer_version
 parent_artifact_ids
 validation_status、validation_details
```

覆盖原图、浏览器证据、关键词研究、模型输出、生成图片、质检报告和平台响应摘要。

文件先上传暂存区，完成哈希和格式校验后再提交为有效产物。未提交文件定期清理。数据库保存引用与摘要，不依赖 Worker 本地绝对路径。第一版可用 Odoo 附件，后续迁移对象存储时保持产物 ID 不变。

### 4.7 业务明细

| 对象 | 核心字段 |
|---|---|
| 标题候选 | 序号、文本、依据、校验结果、版本 |
| 爆款候选 | 平台、规范化链接、商品 ID、原始销量展示、评价、评分、证据、入选原因 |
| 目标图库 | 参考商品、选定 SKU、采集时间、清单哈希、完整性 |
| 目标图片 | 图库、图片身份、顺序、类型、附件 |
| 图片候选 | 目标图、候选版本、请求 ID、图片产物、质检报告 |
| 活动方案 | 活动身份、资格、价格、条件、有效期和证据 |
| 广告方案 | 广告主、campaign、资源、当前值、建议值及影响范围 |

需要搜索、关联和权限控制的核心字段使用正式模型字段；模块化、可演进的输入输出使用版本化 JSON。

## 5. 统一任务协议

每个执行模块实现：`validate_input → execute/resume → validate_output → commit_result`。

模块只读取版本化输入和产物引用，不读取其他模块私有运行目录。

### 5.1 输入示例

```json
{
  "schema_version": "1",
  "task_id": "task-123",
  "type": "image.generate",
  "input_revision": 2,
  "context": {
    "case_id": "case-456",
    "shop_id": 3,
    "site_id": "MLM"
  },
  "inputs": {
    "source_artifact_ids": ["artifact-source"],
    "target_artifact_id": "artifact-target",
    "generation_plan_id": "plan-789"
  },
  "policy": {
    "version": "image-policy-v1",
    "max_candidates": 3
  }
}
```

公司、用户及权限范围由服务端根据已鉴权身份和任务关系推导，不信任 Worker 自报身份。

### 5.2 输出示例

```json
{
  "schema_version": "1",
  "task_id": "task-123",
  "attempt_id": "attempt-1",
  "input_revision": 2,
  "input_hash": "...",
  "result_status": "complete",
  "artifact_ids": ["artifact-output"],
  "validation": {
    "status": "passed",
    "report_artifact_id": "artifact-qc"
  }
}
```

服务端接收时验证租约、执行代数、输入版本、JSON Schema、附件存在性与哈希、业务规则和提交去重身份。模型返回成功不等于业务成功。

有效零结果、有效部分结果和采集故障必须区分；任务可成功完成但结果数量为零。

## 6. 状态与检查点

### 6.1 任务状态

```text
draft → queued → running → succeeded
                    ├─ retry_wait → queued
                    ├─ waiting
                    ├─ failed
                    └─ cancelled
```

| 状态 | 含义 |
|---|---|
| queued | 依赖满足、等待领取 |
| running | 持有有效租约 |
| retry_wait | 可恢复错误、等待退避 |
| waiting | 等待登录、能力、选择、预算或外部结果核实 |
| succeeded | 输出通过验收，可为完整、部分或有效空结果 |
| failed | 永久错误或重试耗尽 |
| cancelled | 取消后续工作 |

等待原因示例：`login_required`、`browser_unavailable`、`capability_missing`、`reference_selection_required`、`budget_exhausted`、`external_result_unknown`、`input_conflict`。

任务执行状态、产物验收状态、运营选择状态、确认状态、平台状态分别保存。整体状态由各分支汇总，同时显示完成和阻塞数量。

### 6.2 检查点

| 环节 | 检查点 | 必须持久化的内容 |
|---|---|---|
| 快照 | snapshot_committed | 商品、SKU、图片、来源与窗口 |
| 商品分析 | facts_validated | 属性、依据和不确定项 |
| 搜索 | queries_frozen | 查询词、规则版本 |
| 搜索 | query_page_committed | 查询、页或游标及证据 |
| 验证 | candidate_verified | 候选证据及判断 |
| 图库 | gallery_committed | SKU、完整清单、顺序、哈希 |
| 生成 | plan_frozen | 输入图、提示词、参数、预算 |
| 生成 | request_prepared | 请求身份和摘要 |
| 生成 | request_dispatched | 服务请求 ID 和调用状态 |
| 生成 | output_committed | 已归档图片 |
| 质检 | qc_committed | 逐项判断及说明 |
| 平台 | operation_prepared | 确认、前值、新值 |
| 平台 | write_dispatched | 发出请求的记录 |
| 平台 | readback_verified | 回读和差异 |

检查点事件追加记录，同时维护任务当前检查点；二者在同一数据库事务内更新。

### 6.3 恢复规则

| 中断场景 | 恢复行为 |
|---|---|
| 标题已提交，响应丢失 | 按提交身份查询，复用已入库结果 |
| 第三个搜索词未完成 | 从第三个词的有效检查点继续 |
| 图片已归档，质检未完成 | 复用图片，从质检继续 |
| 图片请求超时 | 查询原请求，不立即重发 |
| 平台写入响应丢失 | 回读当前值，核实后决定下一步 |
| Worker 失联后迟到提交 | 失效租约不能推进当前任务 |
| 输入发生变化 | 旧产物留历史，受影响下游和确认失效 |

取消停止新任务和新请求调度。已经发出的请求仍需登记最终结果，不承诺可以撤回外部调用。

## 7. 调度、租约及并发

Worker 使用独立服务身份，通过内部 API 领取任务。服务端以短事务和行锁原子领取，创建尝试并分配租约、令牌及执行代数。

建议初始租约 120 秒，心跳 30 秒，具体可配置。长模型调用期间由独立心跳循环续租。空闲领取采用退避，避免频繁轮询。

租约过期后先检查已发出但未结束的外部请求，不能直接再次生成。新 Worker 先恢复或核实未决请求，再进入新工作。

并发限制：

| 范围 | 原因 |
|---|---|
| 同一任务 | 防止重复执行 |
| 同一浏览器会话 | 防止页面互相干扰 |
| 同一模型或图片服务账户 | 限额、成本与并发控制 |
| 同一店铺商品写操作 | 防止字段或图片列表互相覆盖 |

多个 Worker 可并行处理不同商品及不同目标图。同一商品图片最终列表更新需要串行化。标题、图片即使分开确认，也应使用最小字段更新，避免整对象覆盖。

## 8. 独立业务能力实现

### 8.1 商品事实整理

输入为商品快照、原图和已确认属性。每个属性保存名称、值、依据、证据引用及可信状态：`confirmed / inferred / unknown`。

面料、品牌、尺码等不能仅凭画面猜测后变成标题事实。模型推断和人工确认分开保存，由规则决定哪些属性允许进入候选标题。

### 8.2 关键词研究与标题

1. 确认商品主类目及套装组件。
2. 采集目标站点同类商品关键词证据。
3. 建立有依据的词库。
4. 生成 10 条有实质差异的候选。
5. 进行事实、语言、重复与格式校验。
6. 保存候选、依据和逐条结果。

校验分为内容层和平台层。内容层检查虚构属性、竞品品牌、促销宣称及重复；平台层在写入前验证当前商品适用的长度、模式和可修改性。

独立实现后不沿用旧技能“最多 60 个词”作为平台限制。平台规则在接入阶段核实并版本化。

站内研究不可用时，按策略等待，或生成明确标注“未完成站内研究”的草稿；不能将其标成完整研究结果。

### 8.3 爆款研究

流程：`搜索提供器 → 商品详情提供器 → 证据归档 → 规则验证 → 去重排序`。

浏览器和数据源封装为可替换提供器，业务验收不依赖单一浏览器实现。使用经接入验收的数据源；登录、地区或页面阻塞时保留进度并明确等待原因。

每个候选保存原始与规范化链接、平台商品 ID、查询词、位置、广告标识、原始销量展示、解析下界或范围、评价评分及缺失状态、图片和视觉品类、采集时间、证据、入选与排除原因。

`+500 vendidos` 保留展示值及下界 500，不伪装成精确销量。最低销量、评价、评分、跨查询出现次数等放入配置，不埋在提示词里。

达到期望数量、候选池耗尽或预算耗尽时结束，返回实际验证数量。不同平台规则分别配置，不能假设全部采用相同门槛。

### 8.4 目标图库核验

研究结果中的源商品图片不能直接当作目标构图图。选择参考商品后独立获取准确 SKU 图库。

输出结构：

```text
reference_item_id
selected_variant
observed_at
gallery_manifest_hash
images：image_id / order / role / artifact_id / source
completeness_status
```

识别主图、正面、背面、细节与信息图。排除推荐商品和无法确认 SKU 的图片。清单确认后冻结，后续网页变化不得悄悄改变已运行任务的输入。

### 8.5 图片生成与质检

每张目标图建立独立生成计划，包含服装参考图、目标构图图、保留身份与姿态等要求、替换范围、输出尺寸、服务版本、参数及候选预算。

建议每张目标图最多 3 个质量候选，作为可配置规则。网络请求核实或恢复不作为新的质量候选；质量不合格后的新生成才增加候选计数。

| 检查层 | 检查内容 |
|---|---|
| 程序检查 | 可解码、格式、尺寸、空白图、异常重复 |
| 视觉检查 | 服装一致、构图与人物保持、旧衣残留、肢体异常、文字破坏 |

逐项保存依据与结论。无法判断时待复核。预算耗尽可返回最佳候选，但未通过全部检查时不得标记质检通过。

### 8.6 活动与广告

活动方案保存活动身份、类型、资格、价格、优惠承担、有效期、库存条件和证据时间。广告方案保存广告主、资源身份、当前参数、目标参数及影响范围。

campaign 预算可能影响同组其他商品，确认前必须展示影响范围，不能描述为仅调整当前商品。

接口、账户、资源归属或指标不完整时阻塞该分支；平台当前不支持的动作不能由模型虚构执行路径。

## 9. 外部请求日志与去重

新增 `meli.optimization.external.request`：

```text
request_uuid
task_id / attempt_id / operation_id
provider
request_hash
idempotency_key（服务支持时）
provider_request_id
state
response_artifact_id
prepared_at / dispatched_at / resolved_at
```

状态：`prepared → dispatched → succeeded / failed / unknown`。

三层去重：

1. 任务层：业务范围、输入版本和任务类型。
2. 提交层：同一 Worker 提交身份只入库一次。
3. 外部操作层：服务去重键、原请求查询或平台回读。

发起请求前先提交执行意图，随后才进行网络调用，不在长数据库事务中等待服务。

跨系统不承诺天然恰好执行一次。如果服务无查询及去重能力，发出请求后进程崩溃可能留下未知窗口；此时进入结果待核实，不能自动重发或宣称绝不重复计费。

## 10. 运营确认与平台写入

### 10.1 变更操作：meli.optimization.operation

| 字段 | 说明 |
|---|---|
| target | 公司、店铺、商品和资源范围 |
| action_type | 标题、图片、活动、广告 |
| before_value | 确认时前值 |
| desired_value | 确切目标值 |
| proposal_revision、proposal_hash | 方案版本及摘要 |
| dependency_artifacts | 绑定的候选与图片版本 |
| impact_scope | 受影响资源 |
| preconditions | 执行前条件 |
| expires_at | 有效期 |
| state | 待确认、已确认、执行中、成功、失败、待核实、失效 |

确认保存操作 ID、方案摘要、确认人、时间、范围、版本及渠道。服务端使用当前身份，不允许模型自报任意确认人。

OpenClaw 展示确切变更后调用专用确认工具；普通聊天内容不直接替代确认记录。批量确认绑定操作 ID 和摘要清单。

### 10.2 执行顺序

1. 复查身份、公司、店铺及资源归属。
2. 检查确认记录、方案版本和有效期。
3. 回读平台当前状态，比较相关前值。
4. 核验当前规则和可操作条件。
5. 保存持久执行意图。
6. 经现有平台适配层写入。
7. 保存响应并回读核对。
8. 成功、失败或结果待核实分别记录。

若平台不支持条件更新，读写之间仍有竞争窗口；缩短间隔并写后核对，不宣称完全消除冲突。

### 10.3 图片发布

```text
逐张上传 → 保存平台图片 ID
→ 组装完整图片清单及顺序
→ 更新商品
→ 回读核对
```

上传成功而商品更新失败时，复用已保存上传结果。图片生成、验收、运营选用、上传、商品更新分别保存。

恢复旧标题或图片属于新的变更操作，重新检查和确认，不默认自动回滚。

## 11. API 与 MCP

以下均为拟新增接口，实际命名应与仓库 BFF 风格对齐。

### 11.1 运营接口

```text
POST /api/meli/optimization/batches/preview
POST /api/meli/optimization/batches
GET  /api/meli/optimization/batches/{id}
GET  /api/meli/optimization/cases/{id}
POST /api/meli/optimization/cases/{id}/reference-selection
POST /api/meli/optimization/cases/{id}/proposals
POST /api/meli/optimization/operations/confirm
POST /api/meli/optimization/operations/execute
POST /api/meli/optimization/tasks/{id}/resume
POST /api/meli/optimization/tasks/{id}/cancel
```

### 11.2 Worker 内部接口

```text
POST /internal/meli-optimization/tasks/claim
POST /internal/meli-optimization/attempts/{id}/heartbeat
POST /internal/meli-optimization/attempts/{id}/checkpoint
POST /internal/meli-optimization/attempts/{id}/complete
POST /internal/meli-optimization/attempts/{id}/fail
POST /internal/meli-optimization/artifacts/stage
POST /internal/meli-optimization/external-requests
POST /internal/meli-optimization/external-requests/{id}/resolve
```

租约令牌放受控请求认证信息，不作为普通业务日志。提交接口支持唯一提交身份，重复响应不重复变更状态。

`resume` 从检查点继续；重新生成是独立动作，创建新版本并消耗新预算。

MCP 只暴露面向运营的业务动作，不暴露租约、心跳及内部提交。错误响应至少包含 `code`、`message`、`retryable`、`resume_action` 和追踪 ID。

## 12. 权限与运行能力

沿用 BFF 身份及公司边界，同公司店铺范围保持既有业务规则。所有模型和附件应用一致的公司记录规则。

| 角色 | 能力 |
|---|---|
| 查看者 | 查看有权限的任务和产物 |
| 运营编辑者 | 创建优化、选择候选、修改草案 |
| 确认者 | 确认具体方案 |
| 执行者 | 执行已确认动作，同时具备平台 Writer 权限 |
| Worker | 仅领取和提交授权范围任务 |

角色可由同一人兼任，不强制双人审批。创建任务时保存发起身份；平台执行时重新校验当前权限，权限撤销后不得继续写平台。

Worker 只获得任务所需附件访问能力；下载及预览每次鉴权。浏览器会话隔离；下载地址、重定向、文件格式和大小受控。凭据、Cookie 和签名地址不进入普通日志与模型输入。

启动时注册并验证实际能力：`browser.search`、`browser.product_detail`、`vision.inspect`、`text.generate`、`image.edit`。保存服务、输入限制及请求查询能力。能力缺失时等待，不能仅凭宿主或模型名称推定存在。

## 13. 界面、观测与效果

批次页显示商品总数、各分支进度、待确认、失败、等待外部条件及结果待核实数量。

商品详情依次展示：商品快照、标题候选、爆款证据、参考选择、目标图库、图片及质检、活动、广告、变更确认、执行结果与效果。

错误应给出可操作说明，例如“等待登录，已验证 8 个候选”，技术诊断进入详情。

运行观测记录任务等待和执行耗时、检查点停留、重试次数、未决请求、预算使用和平台回读差异。实际服务未返回的模型、token 或费用不能伪造，应标记不可获取。

效果观察保存各动作生效时间、统计窗口、时区、币种、销量、库存、访问和广告指标，以及缺失项与同期其他操作。展示前后变化，不自动宣称因果。

## 14. 实施阶段与验收

| 阶段 | 交付 | 核心验收 |
|---|---|---|
| 1. 执行底座 | 任务、尝试、租约、检查点、附件和请求日志 | 重复领取、失联、迟到提交及恢复正确 |
| 2. 标题闭环 | 快照、事实、研究、候选、确认和回读 | 版本变化使相关确认失效 |
| 3. 爆款与图库 | 搜索、证据、验证、选择和图库冻结 | 不串商品、不混 SKU、部分结果可用 |
| 4. 图片闭环 | 逐图生成、质检、上传和恢复 | 不重做已完成环节 |
| 5. 批量及分支 | 批量确认、活动和广告 | 分支独立、影响范围明确 |
| 6. 观察与扩容 | 指标、多 Worker、预算和告警 | 权限隔离、并发与成本可追溯 |

必须覆盖的故障验收：

1. 两个 Worker 领取同一任务，只有一个获得有效租约。
2. 租约失效后的迟到提交不能推进任务。
3. 请求发出后断电，恢复不无条件重发。
4. 图片已归档但质检未结束，恢复复用图片。
5. 图片上传成功而更新失败，恢复复用平台图片 ID。
6. 平台响应丢失时先回读，不盲目重放。
7. 输入或方案变化后旧确认不可执行。
8. 图片失败不阻塞无依赖的已确认标题操作。
9. 权限撤销后禁止平台执行。
10. 零结果、部分结果和采集故障可区分。
11. 跨公司任务、产物和附件不能访问。
12. 候选预算耗尽后未通过质检的结果不会自动发布。

验证先使用模拟服务和隔离数据，再逐项验收真实浏览器、模型、图片及平台通道。现有底层测试通过不等于新优化业务或真实写入已验收。

## 15. 仓库落点

```text
deploy/mercado/
├── addons/
│   └── meli_optimization/
│       ├── models/
│       ├── services/
│       ├── controllers/
│       ├── security/
│       ├── views/
│       └── tests/
├── optimization_worker/
│   ├── runtime/
│   ├── handlers/
│   ├── providers/
│   ├── schemas/
│   └── tests/
└── mcp/
```

业务编排放新增模块；OAuth、平台映射及 transport 继续放现有适配层。MCP 只扩展优化业务工具，不承担执行状态。

## 16. 实施前待核实项

| 项目 | 核实内容 | 不满足时的处理 |
|---|---|---|
| 研究数据源 | 可用接口、允许的数据获取方式、浏览器登录与地区条件 | 阻塞研究分支，保留进度 |
| 图片服务 | 实际编辑能力、输入限制、请求身份、查询与去重、并发和计费 | 能力不匹配不调度；未知请求待核实 |
| 平台契约 | 商品模式、标题约束、图片更新、活动和广告具体权限 | 不生成可执行操作 |
| 身份范围 | 公司、店铺、确认及 Writer 权限映射 | 拒绝越权动作 |
| 部署环境 | 受控浏览器、文件存储、Worker 心跳和网络访问 | 等待能力恢复 |
| 首期策略 | 候选数量、证据有效期、预算和自动重试上限 | 作为版本化配置明确后启用 |

推荐先交付单商品标题闭环和故障恢复，再增加爆款、图片及并发。独立实现复用的是现有鉴权与平台接入基础，任务数据、规则、检查点和执行协议全部由新模块管理，不再依赖飞书表格或飞书技能。
