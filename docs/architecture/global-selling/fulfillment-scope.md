# Mercado Libre Full 履约接口范围

更新时间：2026-11-07。用户已确认只做 Mercado Libre 仓储配送 Full / Fulfillment；Fully Managed 全托管销售模式是另一维度，不能直接视作 Full 的同义词。

状态定义：`已实现（待真实账号验证）` 表示有注册表和模拟测试，并非全部筛选项、账号模式均已验证；`待实现` 表示尚无该契约；`待核验` 表示需补齐文档、账号能力或店铺归属证据。平台账号授权限制与本地实现状态分开看。

| 领域 | 契约 / 操作 | 状态和限制 | 官方依据 |
| --- | --- | --- | --- |
| 市场商品 | `items.marketplace_read` | 已实现（待真实账号验证）；核验站点、卖家 | [Fulfillment stock](https://global-selling.mercadolibre.com/devsite/en_us/fulfillment-stock-gs) |
| Full 库存 | `stock.fulfillment` | 已实现（待真实账号验证）；MLM/MLC，校验商品、变体及库存 ID | 同上 |
| 库存变动 | `stock.fulfillment_operations` / `stock.fulfillment_operations_batch` | 已实现（待真实账号验证）；日期范围最多 60 天，支持最多 10 个库存、limit、scroll、type 和运单筛选；sort 待核验 | 同上 |
| 库存变动详情 | `stock.fulfillment_operation_read` | 已实现（待真实账号验证）；核验操作、库存及卖家 | 同上 |
| 入库与库存通知 | Fulfillment stock notifications | 待核验：订阅主题、回调来源确认、重复通知和有界处理；入站事件不作为 MCP 工具 | 同上 |
| 订单、购物车、发货 | `orders.search`、`orders.read`、`packs.read`、`shipments.read`、`shipments.orders`、`orders.shipments` 及运单明细 | 已实现（待真实账号验证）；并非 Full 全量筛选、字段和模式 | [Orders](https://global-selling.mercadolibre.com/devsite/en_us/manage-orders-cbt)、[Shipments](https://global-selling.mercadolibre.com/devsite/en_us/manage-shipments) |
| Full 发货标签 | `shipments.label` | 已提供通用操作；官方注明 Fulfillment 运单不能通过此公共 API 生成标签，不计为 Full 可用能力 | [Shipments](https://global-selling.mercadolibre.com/devsite/en_us/manage-shipments) |
| Full 自有物流跟踪 | `shipments.tracking_update` / `shipments.tracking_status` | 官方限定 me1/default 自有物流；不属于 Full 能力 | 同上 |
| 拆包 | `POST /marketplace/shipments/{shipment_id}/split` | 官方明确仅适用于 drop-off / cross-docking，Full 不支持；不属于本次实现范围 | 同上 |
| 退货与纠纷 | `claims.*` 中的已实现操作 | 已实现订单型纠纷的部分读取和写入（待真实账号验证）；其他资源类型、附件、账号限制未覆盖 | [Claims](https://global-selling.mercadolibre.com/devsite/en_us/manage-claims)、[Returns](https://global-selling.mercadolibre.com/devsite/en_us/manage-returns) |
| M4 中国 Full 补货建议 | `GET /marketplace/fbm/user-products/{user_product_id}/replenishment` | **在 Full 范围内，待实现**；官方限定 M4 Fulfillment China，需证明全球 User Product 与当前店铺的关联，并核实如何可靠识别 M4；`country=CN` | [Replenishment plans](https://global-selling.mercadolibre.com/devsite/en_us/replenishment-plans-inbound) |
| M4 中国 Full 入库创建 | `POST /marketplace/fbm/inbounds` 及创建状态查询 | **在 Full 范围内，待实现**；官方限定 Fulfillment China，需核验 M4 能力、商品归属与异步提交/查询契约；不新增本地 worker | [Fulfillment inbound creation](https://global-selling.mercadolibre.com/devsite/en_us/fulfilment-inbound-creation) |
| Fully Managed 补货单 | `GET /marketplace/fbm/orders` | 文档限定 Fully Managed 卖家；不因 Full 物流自动纳入 | [Replenishment orders](https://global-selling.mercadolibre.com/devsite/en_us/get-replenishment-orders-cbt) |
| Fully Managed 入库和商品 | `/marketplace/fbm/inbounds` 等 | Fully Managed 专属流程不因 Full 物流自动纳入 | [Fully Managed publishing](https://global-selling.mercadolibre.com/devsite/en_us/fully-managed-product-publishing) |

## 店铺隔离的限制

官方 FAQ 指出，同一全球商户下不同市场子账号可以共享物理 Full 库存。当前代码通过店铺市场卖家及商品/变体关联核验访问；这不代表平台为每个子账号隔离物理库存，也不提供库存分摊。若需要分摊，必须有业务层面的分配规则。

用户已确认沿用公司级权限：同一公司的 BFF 用户可访问该公司的所有店铺，不增加逐人店铺授权。Odoo 店铺记录规则以公司为边界；商品、订单和库存仍须按所选店铺核验归属。

来源：[Fulfillment inventory consistency](https://global-selling.mercadolibre.com/devsite/en_us/fulfillment-inventory-consistency-across-child-sellerid-and-shared-stock)。

## 接下来的验收

1. 对 Full 相关接口逐项补齐参数、权限、站点和归属策略；实现遗漏项并用模拟平台覆盖跨店和异常情况。
2. 在不修改真实业务数据的前提下，用有权限的 Full 店铺做只读联调，确认返回字段和账号能力。
3. 对文档或账号证据不足的项目标记明确阻塞；不要把 Fully Managed 专属接口算作所有 Full 店铺必然支持。
4. Full BFF 范围确认完成后，再按已批准设计实现项目自身的 Streamable HTTP MCP。

通知设计约束：官方要求 500 毫秒内返回 HTTP 200，建议先入队再处理；当前已确认范围不包含新增本地长任务或 worker。未具备可靠持久接收与后续处理时，不能宣称通知功能已完成或直接吞掉事件。

M4 入库核验：官方页面正文称该流程限 Fulfillment China，但错误示例又提及 M4/M6；不能由此推断本系统中的业务模式字段可以可靠识别 M4。状态查询示例在 `PENDING` 时只返回状态，缺少卖家标识；虽然文档显示查询其他卖家的流程返回 404，现有设计仍要求在 BFF 独立证明资源与所选店铺的关联。可进一步评估将提交结果封装为绑定店铺的签名凭据，但在账号能力和商品归属核实前不能开放写入。

本机检查到 2 个已连接的 MLM Full 店铺，但当前访问令牌均已过期。因克隆库可能与原环境共享刷新令牌，尚未自动刷新；真实账号只读联调尚未执行。业务模式记录分别为 `CBT Local Fulfillment` 和未知；没有可据此确认 M4 的记录。
