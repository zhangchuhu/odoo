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
