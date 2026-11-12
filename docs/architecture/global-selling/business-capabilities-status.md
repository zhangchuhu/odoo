# Global Selling 业务能力核对进度

更新时间：2026-11-07。用户要求继续覆盖订单、物流、商品、促销、广告、Catalog 和定价；这扩大了此前仅 Full 履约的范围。已实现指注册表及模拟测试已有覆盖，不代表官方全部参数或真实账号联调完成。

| 领域 | 已有能力 | 仍需核对或实现 |
| --- | --- | --- |
| 订单 | 订单搜索、详情、发票、账单信息、订单关联运单、购物车及消息 | 官方全部筛选、字段及账号模式；真实账号验证 |
| 物流 | 运单详情、运单订单、历史、费用、商品、SLA、物流商、预计时间；Full 库存及变动 | Full 入库及通知；非 Full 标签、拆包及自有物流等需按账号和物流能力分别核对 |
| 商品 | 商品读写、描述、搜索、批量读取、市场商品和映射、评价、质量、图片上传 | 发布与更新完整字段、变体和 User Product 契约 |
| 促销 | 商品活动查询、店铺卖家促销列表 | 活动详情、候选商品、价格模型、参与和退出等；逐项核对权限、归属与定价模式 |
| 广告 | 尚未实现 | 广告主与店铺的可靠关联、广告组、活动、指标和写操作；弃用旧版 Ads 路径 |
| Catalog | 尚无专用操作 | Catalog 发布及竞争查询；CBT price_to_win 示例中的 caller.id、client.id 身份取值尚需核对 |
| 定价 | 现有商品更新包含有限价格字段 | 卖家定价模型、价格及变体价格接口；区分 net_proceeds 与 listing_price，不默认猜测模型 |

广告核对依据：[Product Ads 官方文档](https://global-selling.mercadolibre.com/devsite/new-product-ads)。广告主列表按 OAuth 用户返回多个站点和广告主，响应示例未给出店铺卖家 ID：仅凭站点不能证明它属于当前店铺。旧的 item Ads 接口已经列为弃用，使用新的 Ad Group 契约前还需建立广告主、活动和店铺的可信关联。文档对个别替代路径的描述与示例不一致，不能直接推测。

价格竞争依据：[Catalog competition](https://global-selling.mercadolibre.com/devsite/en_us/catalog-competition-gs)。商品权限验证不能替代对文档中 caller.id 和 client.id 所代表身份的核验。

促销依据：[Manage Promotions](https://global-selling.mercadolibre.com/devsite/en_us/manage-promotions-gs)。卖家列表使用服务端选定店铺的 marketplace seller ID 和 version v2；读写接口仍受 BFF 角色及公司级店铺规则约束。
