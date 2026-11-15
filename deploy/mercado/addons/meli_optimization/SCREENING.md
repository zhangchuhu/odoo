Automatic screening uses three OpenClaw MCP tools:

1. `optimization_screen_preview(shop_id, request_key, criteria)` returns immutable parsed conditions and a digest. OpenClaw displays these conditions before execution. It must ask for missing numeric thresholds rather than inventing what “low” or “high” means.
2. `optimization_screen_execute(preview_id, digest)` collects complete observations and saves matching own-shop products into one optimization batch automatically. No extra approval step follows clear thresholds. No platform writes occur.
3. `optimization_screen_history()` locates durable previews and batch outcomes. Resulting cases appear in the existing optimization workbench and batch filter.

Criteria require exactly `site_id`, `timezone`, offset-aware `start` and `end`, integer `sales_max` and `stock_min`, and `sales_policy: "paid_gross_units"`. For MLM, use `America/Mexico_City`. Selection means total product paid units **<=** sales_max and total available stock across every SKU **>=** stock_min. This is product-level selection, with SKU stock evidence preserved. The creation-time window is start-inclusive and end-exclusive. Only currently paid orders count; cancelled, invalid and unpaid orders do not count. Returns and refunds are not deducted from gross units. No lifetime sold_quantity is used.

Shop identity comes from BFF authorization, GET /users/me token identity and the parent account's exact child-seller/site/logistics mapping. Missing order detail, seller/site, page totals, stock quantities or unknown statuses cannot become zero. Missing individual stock or sales values exclude that product as unknown; incomplete orders/search ownership or pagination abort the whole batch.

The dedicated provider uses authenticated `/marketplace/users/{child_seller_id}/items/search`, `/marketplace/items/{item_id}`, `/marketplace/orders/search`, and `/marketplace/orders/{order_id}` through the existing fixed-origin transport. It fetches all status orders within the explicit creation window, retrieves every group member's detail and verifies ownership/site. It requires stable paging totals, rejects duplicate identities and performs a second search plus full order and product/SKU/stock detail pass to detect drift. These are observations at collection time, not an atomic platform snapshot. An order window above 1000 search results must be narrowed. A shop with more than 1000 products requires a provider with verified scan/scroll enumeration; shortening the order window cannot reduce the product count. Both limits fail explicitly. Unsupported accounts or incomplete detail schemas also fail closed.

Remote GET calls precede request-row locks and local batch writes. Odoo request authentication still opens a read transaction for identity/credentials; the application never commits another caller's transaction or holds a batch lock during remote requests. The final row lock, batch, cases, snapshots and outcome all share one savepoint. Retries with the same shop/company/request_key and digest reuse the original batch; changed criteria require a new key. Only parsed previews and outcomes are stored; platform tokens and buyer data are excluded.

Primary contracts checked 2026-11-15:
- [Official CBT orders](https://global-selling.mercadolibre.com/devsite/en_us/support-for-integrators/manage-orders-cbt)
- [Official CBT items and searches](https://global-selling.mercadolibre.com/devsite/en_us/pricing-reference/items-and-searches-global-selling)

Live account acceptance remains separate from offline correctness tests. No platform request or production database mutation is part of the test runner.
