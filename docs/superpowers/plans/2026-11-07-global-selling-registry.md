# Global Selling Registry Implementation Plan

> **For agentic workers:** Use executing-plans for implementation in this session. Complete each evidence and test gate before exposing an operation.

**Goal:** Build an evidence-backed Global Selling operation registry, Odoo BFF and Streamable HTTP MCP.
**Architecture:** Odoo owns validated, immutable operation definitions; BFF performs authorization, schema and ownership checks and dispatches adapter handlers. MCP consumes only the authorized, versioned BFF catalog. The existing nine BFF operations keep compatibility.
**Tech stack:** Python / Odoo 19 / PostgreSQL 16; Node.js / MCP SDK; JSON Schema.
**Spec:** docs/superpowers/specs/2026-11-07-global-selling-registry-design.md.

## Constraints
Local `mercado` on port 5433 only; no independent test database; no real platform writes; cron off. No task worker, approval or execution-record model; do not expose unknown resource ownership as authorized.

## Tasks and interfaces

1. **Inventory:** `docs/architecture/global-selling/endpoint-matrix.csv` and `source-index.csv`. Each method+path+account mode records source, verification and unresolved ownership. Independently track inaccessible docs; do not count inferred endpoints as tested. Inventory all official navigation domains before declaring completeness.
2. **Registry:** `meli_platform_adapter/registry/` defines immutable `Operation` records and canonical version hash. Tests must reject duplicate IDs, missing source, ambiguous scope, bad schema, unregistered handler/policy and URL schemes. A catalog returns only public fields; credential/handler internals never leave the server.
3. **BFF:** `/api/meli/operations` and `/api/meli/operations/{id}/execute`: authorize Odoo user and shop before dispatch, validate entire JSON Schema, verify contract version and operation-specific resource ownership. Keep nine existing routes stable. Implement multipart/binary as separately verified handlers.
4. **Domains:** for each inventory entry, verify method/path/parameters and ownership against official source, then implement handler and mock tests; list inaccessible/uncertain entries as blocked, never publish them. Special handlers handle authorization, upload, batch, webhook, ownership and account capabilities. No automatic retry on writes.
5. **MCP:** only after BFF coverage, expose Streamable HTTP transport and four meta tools reading Odoo catalog and forwarding calls with per-identity Odoo API keys. Test two users, spoofing, malformed input, stale catalog, file limits, no automatic write replay.
6. **Local deployment:** back up local database/source, run transaction and mock regression suite with outbound network disabled, update local Odoo and test real read-only BFF, then start local MCP and test SDK end-to-end. Publish final coverage matrix, verification and rollback instructions.

## Review focus
Cross-company/store or resource access, malformed schemas/unknown fields, seller identity override, binary/multipart bounds, refresh concurrency and independent transaction, ambiguous platform write outcomes, bearer isolation, stale catalog, external redirect, webhook replay and identity.

## Current execution notes
User explicitly authorized implementation. Work in existing `codex/odoo-platform-adapter` checkout, preserving untracked `deploy/` and `docs/` artifacts. Code or service changes are not considered complete until full inventory and tests meet the spec.

## Progress 2026-11-07

- Inventory: 54 distinct method/path/account-mode candidates from official documents or indexed excerpts (`endpoint-matrix.csv`), all still `identified`; 126 old MCP candidates tracked separately and not counted as verified. Source index records accessible, indexed-only and 403 sources.
- Registry: immutable contract version, input schema validation, source/handler/policy checks and restricted public catalog implemented. JSON Schema dependency recorded in `deploy/mercado/requirements-registry.txt`.
- Local BFF: catalog and versioned operation dispatch for six existing item functions; reader/writer, company/shop and item ownership reused and covered by tests. Catalog response is private/no-store. These are partial implementations, not complete official API contracts.
- Validation: 24 pure unit tests and 55 local Odoo tests passed; health check, anonymous rejection and authenticated catalog verified; temporary API key removed. No actual platform writes.
- MCP: not implemented/deployed for this version because full BFF coverage is not yet met.
- Blocker: directly fetching the requested introduction and multiple official subpages returned HTTP 403; browser access attempt timed out. Search index exposes excerpts but not complete contracts. Need an accessible official document source or existing login method for remaining parameter, capability and ownership verification. Continue accessible-source inventory in the meantime.

## MCP documentation access update

The official MCP was already configured; OAuth read-only authorization succeeded. Searches and page reads returned 75 documents; one requested page returned empty. Evidence index and unreviewed method/path candidates are saved under `docs/architecture/global-selling/`, with raw source only in ignored `.local/mercadolibre-docs/`. The previous direct-browser HTTP 403 no longer blocks those retrieved pages. Continue manually validating endpoints, account-mode capabilities and ownership before exposing them; the automatic extraction is not the completed coverage matrix.

## Progress 2026-11-07, continued

- Registry now has 21 operations: 6 item, 7 shipment, 2 order, 1 pack, 3 question and 2 pack-message operations. Pack reads validate every associated order; question access validates both question and item. Shipment metadata uses `x-format-new: true`. No attachment messaging.
- Platform response size capped at 200 MiB; transport extra headers are explicitly allowlisted.
- Confirmed-account and advanced domain coverage is still incomplete; the full official document matrix and project MCP remain outstanding.

- Added 5 order-claim reads with respondent, order seller and site validation. Other claim resource types remain unsupported; Model 6 is restricted upstream. Registry: 26 operations. 61 local integration tests and 26 unit tests passed.

- Added shop-scoped questions.search with server-bound marketplace seller, bounded pages, per-item and site ownership, and no cross-shop aggregate metadata. Registry: 27 operations. 62 local integration tests passed.

- Added shipment PDF label and order billing info reads after shipment/order shop ownership checks. Platform logistics restrictions remain enforced upstream. Registry: 29 operations; 63 local integration tests passed.

- Added marketplace item performance and reviews with shop site and seller validation. Reviews support bounded limit and offset. Registry: 31 operations; 64 local integration tests passed.

- Added shop-bound site payment methods, payment method details, and active CBT size chart domains. Registry: 34 operations; 65 local integration tests passed.

- Added site-bound size equivalences and claim reason references. Registry: 36 operations; 66 local integration tests passed.

- Added seller/site-bound order search with every nested order verified; page and nested order counts bounded. Registry: 37 operations; 67 local integration tests passed.

- Added 4 Global Selling category/domain reference operations with restricted CBT IDs and query validation; non-CBT category IDs remain unexposed. Registry: 41 operations; 68 local integration tests passed.

- Added custom shipment tracking and terminal status updates after writer, ownership, site and me1/default checks; no retries and no real writes in tests. Registry: 43 operations; 69 local integration tests passed.

- Added registered multipart picture upload with writer/shop/version validation, single JPEG/PNG capped at 10 MB, forwarding only the image. Registry: 44 operations; 70 local integration tests passed.

- Added item promotion candidate reads with item ownership, site, server-bound marketplace seller and app_version=v2. Registry: 45 operations; 71 local integration tests passed.

- Added shipment-order lookup with X-New-Domain header; validates all sellers and orders, rejects mixed-shop lists. Registry: 46 operations; 72 local integration tests passed.

- Added shop-bound seller identity and filtered marketplace user mapping. Registry: 48 operations; 73 local integration tests passed.

- Added shop-scoped order claim search, re-fetching and authorizing every claim before returning snapshots. Registry: 49 operations; 74 local integration tests passed.

- Added marketplace item shipping cost read after item seller/site verification. Registry: 50 operations; 75 local integration tests passed.

- Added fulfillment inventory stock for MLM/MLC, validating marketplace item, seller, logistics and inventory ID including variants. Registry: 51 operations; 76 local integration tests passed.

- Added fulfillment inventory operations with item/variation ownership, 60-day date validation and returned inventory/seller checks. Registry: 52 operations; 77 local integration tests passed.

- Added claim messages, returns, expected resolutions and partial refund offers for verified order claims. Registry: 56 operations; 78 local integration tests passed.

- Added claim text messages gated by writer, order claim ownership and respondent available action; attachments excluded and tests mock the platform. Registry: 57 operations; 79 local integration tests passed.

- Added full refund, allow-return and open-dispute actions for verified order claims; writer and available-action gates; no real writes in tests, no retries. Registry: 60 operations; 80 local integration tests passed.

- Added partial refund with PDD/action/pending return checks and exact comparison against current available percentages; tests mock writes. Registry: 61 operations; 81 local integration tests passed.

- Added return shipping cost lookup with optional USD conversion after order claim authorization. Registry: 62 operations; 82 local integration tests passed.

- Added marketplace item detail with exact-snapshot seller/site verification. Registry: 63 operations; 83 local integration tests passed.

- Official MCP now retrieved the previously unavailable Items & Searches document. Added global item mappings filtered to verified shop marketplace items. Registry: 64 operations; 84 local integration tests passed.

- Added global item batch read with individual authorization and complete batch response seller/ID validation. Registry: 65 operations; 85 local integration tests passed.

- Scope change: focus on Full / Fulfillment. Fully Managed capability is distinct and must not be inferred. Added stock.fulfillment_operation_read; Full-specific status and remaining verification are in docs/architecture/global-selling/fulfillment-scope.md.

- User confirmed Full/Fulfillment only. Added stock operation detail and limit/scroll/type support; added hosted order-to-shipments lookup with association checks. 88 local integration tests passed. Stock notifications remain unresolved because the documented 500 ms acknowledgement/queued processing conflicts with the no-worker constraint.

- Ruling: use the hosted order-to-shipment association as evidence after order authorization and return only IDs/types; shipment order_id is documented as discontinued. Cost if upstream association is wrong: incorrect linkage, so platform response shape and real account behavior still require verification.

- Fulfillment stock movements now support seller-bound pagination, type and shipment filter; split/label explicitly outside Full per official documentation. Tests remain mock-based.

- Added multi-inventory Full movement search; verifies all item/inventory pairs before searching and validates every returned seller/inventory. 89 local integration tests passed. Two connected MLM Full shops have expired access tokens; no refresh attempted due to potential shared-token rotation.

- User confirmed that every BFF user can access all stores in their company; retain company-level rules and resource-to-store ownership checks. M4 inbound and replenishment remain in scope, but official documentation does not establish a reliable local M4 capability mapping; inbound status responses do not consistently identify their seller. No unsupported capability inference or cross-store process access is exposed.

- Scope expanded by user to orders, shipping, items, promotions, advertising, Catalog and pricing. Added `promotions.seller_list`: marketplace seller ID is selected server-side and `version: v2` is fixed. RED→GREEN: 90 local integration tests and 26 unit tests passed. Remaining inventory and limitations: `docs/architecture/global-selling/business-capabilities-status.md`. Advertising ownership and Catalog caller/client identity remain unproven; no guessing or broad passthrough.

- User explicitly requested starting project MCP before BFF inventory is complete. Ruling: expose only current BFF catalog while continuing to mark missing BFF capabilities as incomplete. Added local-only stateless Streamable HTTP service with per-identity BFF credentials, fresh authorized catalog, schema validation, contract conflict without replay and bounded upload; binary download is rejected pending authenticated temporary-file references. Real SDK handshake and authenticated local Odoo catalog returned 69 operations using a temporary key that was removed. Mock HTTP tests cover concurrent identities and validation. No real marketplace writes or production deployment.

- MCP cutover: added `listing_shops`, replaced the standalone stdio project's active directory after a full local backup, and started a loopback-only launchd MCP service with a separate private token and 90-day local Odoo admin key. Real MCP-to-Odoo shop lookup returned two shops. The specified OpenClaw 2026.11.1 checkout was installed and built; an isolated profile successfully probed five tools with Node 24. The installed 2026.3.7 gateway was left intact, so its existing agents do not yet use this profile. Live product queries remain blocked: both local shop access tokens are expired, and the selected shop's registered OAuth redirect points to the production host rather than this local Odoo. No shared refresh token was used.
