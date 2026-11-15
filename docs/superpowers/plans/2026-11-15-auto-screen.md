# Automatic shop screening implementation plan

> **For agentic workers:** Execute inline with TDD; do not delegate.

**Goal:** Preview explicit shop sales/stock criteria and atomically persist matching products into an optimization batch, with unknown data excluded.

**Architecture:** A pure screening engine validates criteria and complete paginated observations. An authenticated Odoo facade binds company/shop/seller, stores immutable previews, executes an internal verified provider, and commits batches in a short atomic savepoint. MCP exposes preview, execution and history; existing workbench displays resulting cases.

**Tech Stack:** Python, Odoo 19, existing Node MCP SDK.

**Spec:** docs/superpowers/specs/2026-11-15-meli-optimization-design.md

## Global constraints
- Never infer low/high thresholds, stock or sales zero, account capabilities, or complete evidence.
- Window is explicit, timezone-aware, start-inclusive/end-exclusive; explicit paid gross-unit policy excludes cancelled orders and includes returns in gross sales.
- No platform writes or live DB writes; disposable test database only.
- Existing public BFF search lacks complete window/pagination evidence. A dedicated GET-only CBT provider follows official date-window, order-detail and total-based pagination contracts; unavailable evidence and >1000-result windows fail closed.

## Review focus
- Duplicate/repeated or inconsistent pagination must fail closed.
- Unknown per-SKU stock and sales cannot match; genuine complete empty orders may yield zero.
- Shop/company/seller identity cannot come from untrusted snapshots.
- Reused identity with changed criteria must fail; identical execution must return original batch.
- Any snapshot failure must roll back the entire batch.

### Task 1: Pure screening contract
- [x] Write tests for missing thresholds, invalid windows, ownership, complete pagination, duplicate records, unknown SKU observations and explicit gross order statuses.
- [x] Run domain tests RED; implement `parse_criteria`, `collect_pages`, `screen_products`; rerun GREEN.

### Task 2: Durable authenticated preview/execution
- [x] Write Odoo tests for preview binding, authorization, idempotency, rollback, current-provider refusal and workbench visibility.
- [x] Run disposable runner RED; implement fenced model, preview/execute routes and ACL/rule; rerun GREEN.

### Task 3: OpenClaw interface
- [x] Add MCP route tests RED; register strict preview/execute/history tools and rerun GREEN.
- [x] Record exact commands, results and integration limitations in .superpowers/sdd/auto-screen/report.md; commit scoped files.

### Follow-up: All listed sizes in stock

- [x] Add optional boolean `require_all_skus_in_stock`, retaining existing input/digest behavior when omitted.
- [x] RED tests: mixed stocked/out-of-stock sizes; missing size metadata/unknown stock; no-variation items require explicit one-size; colors sharing a size must each have stock; product-level aggregate sales <2 is `sales_max=1`.
- [x] GREEN: verify each listed SKU has known positive stock and a supported SIZE attribute, preserve per-SKU size evidence. Reject missing or contradictory size metadata as unknown. Do not infer an unlisted intended size range.
- [x] Preserve item-level attributes in the live provider and compare on both passes; update MCP optional boolean, preview semantics and documentation.
- [x] Run pure/MCP/disposable Odoo tests, commit scoped files; parent handles review and live validation.

### Follow-up: durable bounded execution

- Reproduce the synchronous 120-second timeout from server logs; parent checked preview1 has no batch and old request was reloaded.
- Add pure TDD replay reader: one remote GET per execute call, private sanitized ordered checkpoints, both observation passes remain separate; refuse oversized or divergent checkpoints.
- Reuse immutable request outcome JSON for private running state (no schema change). Nonblocking advisory lock prevents concurrent collection steps. Each HTTP step is bounded by one 30-second GET; retries retain original criteria/window/digest. No detached threads or hidden process lifetime.
- HTTP execute returns running/progress until complete; same tool polls/resumes. History exposes safe status without checkpoint data. Completion uses existing atomic batch transaction and stable identity.
- Test replay/restart, drift, concurrency, sanitized persistence, HTTP progress and completion. Update MCP/skill contract and docs; parent coordinates deployment and actual preview1 execution.
