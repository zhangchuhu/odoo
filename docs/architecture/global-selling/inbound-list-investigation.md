# Full 入库单列表核验

核验日期：2026-11-07。范围为 Guangzhou YingShi Keji，Odoo 店铺 ID 2、墨西哥站。通过项目 MCP 的 `users.marketplace_mapping` 只读查询，站点账号返回 `business_model=CBT Local Fulfillment`、`logistic_type=fulfillment`、`pricing_model=listing_price`、`user_product=false`。这与 Fully Managed 的 `CBT CN Fulfillment Managed` 模式不同。

## 官方契约

| 能力 | 官方契约 | 对本需求的适用性 |
| --- | --- | --- |
| 入库创建及创建进程状态 | `POST /marketplace/fbm/inbounds`、`GET /marketplace/fbm/inbounds/process/{process_id}` | 官方限定 Fulfillment China；状态查询依赖已知 process ID，不是店铺历史入库单列表。 |
| 补货单及其关联入库 | `GET /marketplace/fbm/orders` | 官方限定 Fully Managed；且列出的是活跃补货单及关联入库，不能当作全部 Full 入库单。当前店铺业务模式不符合文档中的 Fully Managed 标识。 |
| 库存接收流水 | `GET /marketplace/stock/fulfillment/operations/search`，可筛选 `INBOUND_RECEPTION` | 官方用于库存入库接收核对；是按 inventory ID 和日期查询流水，并非全部入库发货单列表。项目 BFF 和 MCP 已有 `stock.fulfillment_operations` 等操作。 |

来源：[Fulfilment Inbound Creation](https://global-selling.mercadolibre.com/devsite/en_us/fulfilment-inbound-creation)、[Get Replenishment Orders](https://global-selling.mercadolibre.com/devsite/en_us/get-replenishment-orders-cbt)、[Fully Managed product publishing](https://global-selling.mercadolibre.com/devsite/en_us/fully-managed-product-publishing)、[Fulfillment stock](https://global-selling.mercadolibre.com/devsite/en_us/fulfillment-stock-gs)、[Fulfillment inventory FAQ](https://global-selling.mercadolibre.com/devsite/en_us/fulfillment-inventory-consistency-across-child-sellerid-and-shared-stock)。

## 店铺只读核验

商品 `MLM6161204608` 的变体包含 inventory ID `IRAV35667`，商品物流类型为 `fulfillment`。通过项目 MCP 调用 `stock.fulfillment_operations`，筛选 `INBOUND_RECEPTION`、2026-09-09 至 2026-11-07，首次返回 HTTP 429；间隔后重试一次成功，返回 `paging.total=0`、`results=[]`。这证明该次对该库存的流水查询可访问，但不代表其他商品、其他时间段或全店没有入库单。

## 结论和实施边界

本次未找到已公开、可证实适用于该店铺的完整入库单列表接口，无法证明接口字段、分页和店铺归属策略，因此未添加臆测的 BFF 或 MCP 操作。应向 Mercado Libre 确认该店铺模式是否提供入库单列表 API、授权要求、资源归属和分页契约；获得证据后再接入。现有入库接收流水可作为有时间及商品范围限制的核对信息，不能冒充完整列表。
