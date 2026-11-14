# Mercado Libre Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development task-by-task. Use TDD.

**Goal:** Implement the independently executable optimization foundation, deterministic task handlers, Odoo persistence/API, and a resumable Worker, with platform writes gated behind explicit validated adapters.

**Architecture:** Odoo owns tasks and artifacts. A pure Python domain contract provides validation and task rules. An authenticated internal API leases durable tasks to a standalone Worker; external providers are injected rather than invented.

**Tech Stack:** Python, Odoo 19, PostgreSQL, unittest, existing API-key authentication.

**Spec:** docs/superpowers/specs/2026-11-15-meli-optimization-design.md

## Global Constraints

- No Feishu dependencies or credentials; no modification to OAuth/transport behavior.
- Work in current feature checkout; preserve unrelated outputs/ and local data.
- TDD: observe meaningful RED before implementation and record RED/GREEN commands in each report.
- No live platform writes, paid service calls, or modification of the existing mercado database during development.
- Company ownership and current permissions are authoritative. Worker claims use bounded server-side scope.
- Preserve immutable input versions, reject stale lease submissions, and never auto-replay unknown external requests.
- Provider integrations without verified credentials/contracts fail closed with explicit capability_missing; do not present fake execution as success.
- Each implementation task commits only its own files. Reports and review packages live in this plan's SDD workspace.

## Review Focus

- Cross-company links and attachment access must fail closed (Task 2).
- Expired leases and replayed completion must not mutate new attempts (Task 2).
- Unknown external requests prevent automatic paid retry (Tasks 1–3).
- Invalid model/provider output cannot become an accepted result (Tasks 1,3).
- Network uncertainty must preserve identity rather than trigger write replay (Tasks 2,3).

### Task 1: Pure domain contracts and deterministic validation

**Files:** Create deploy/mercado/addons/meli_optimization/domain/{__init__,contracts,policies}.py and deploy/mercado/tests/test_optimization_domain.py; add its suite to run_unit.py without changing existing test semantics.

**Interfaces:** Produces DomainError(code,message), canonical_hash(value), validate_input(task_type,payload), validate_result(task_type,result), validate_transition(current,target), validate_lease(state,token,epoch,expires_at,now), validate_title_candidates(result), validate_research_candidates(result), validate_gallery(result). Functions use JSON-compatible values; times are explicit UTC datetime. Document exact accepted schemas in module docstrings. Task types: product.facts, research.keywords, research.bestseller, gallery.collect, title.generate, image.generate, image.qc. Output validators enforce truthful evidence references, exact 10 unique title candidates, typed image/gallery metadata, and explicit partial/empty research status. No hardcoded fictional platform title limit. States: draft,queued,running,retry_wait,waiting,succeeded,failed,cancelled.

- [ ] Write behavior tests covering deterministic hashes, NaN rejection, unsupported task, illegal transitions, expired/stale lease, 10 unique titles and invalid research/gallery evidence.
- [ ] Run .venv/bin/python deploy/mercado/tests/run_unit.py; observe new tests fail for missing behavior.
- [ ] Implement minimal pure modules; outputs retain unknown values and raw sold displays.
- [ ] Run full unit entrypoint; record RED/GREEN and commit.

### Task 2: Odoo durable models, fenced queue and authenticated API

**Files:** Create remaining addon files in deploy/mercado/addons/meli_optimization: manifest, init, models/, controllers/, security/, views/, tests/. Create deploy/mercado/tests/run_optimization_odoo.py for disposable test database only if safely supported; never use run_local_upgrade.py for this feature.

**Interfaces:** Consume Task 1 domain functions. Produce authenticated /api/meli/optimization API and /internal/meli-optimization API documented in addon README. Use separate auth method inheriting existing API key authentication so legacy BFF route resolution cannot reject new endpoints. Implement batch/case/snapshot, task/attempt/event, artifact, external request, proposal/confirmation models. Queue claims atomic PostgreSQL row locks; 120-second leases and 30-second recommended heartbeats; capability matching, company scope, dependency readiness; epoch fencing; idempotent checkpoint/complete submissions; unknown/dispatched external requests block reclaim. Immutable inputs and server-side company links. Artifacts restricted by company with digest validation, bounded JSON/binary ingress. Confirmation binds exact proposal hash and current authorized user; invalidation on changed inputs; no ungated platform execute endpoint. Provide explicit unsupported capability response for unverified live execution.

- [ ] Write Odoo TransactionCase tests first for immutable snapshots, company mismatch, queued claim, stale/expired lease, completion replay, dependency block, unknown request block, confirmation invalidation, and direct ORM attempts to mutate protected state.
- [ ] Run tests in a new disposable database or document infrastructure blockers accurately; never claim ORM/concurrency proven by pure tests.
- [ ] Implement models and narrow controllers with bounded parsing, no generic ORM dispatch, no credentials exposure. Internal state mutators are private; public business methods enforce groups.
- [ ] Add minimal list/form views and groups/record rules; artifact access cannot rely on company_id supplied by caller.
- [ ] Run available full unit suite and isolated Odoo integration tests; commit and report limitations.

### Task 3: Worker runtime, provider contracts and end-to-end simulated execution

**Files:** Create deploy/mercado/optimization_worker/{__init__,__main__,client,runtime,handlers,providers}.py (split focused modules when necessary), tests/, README.md; deploy/mercado/tests/run_optimization_worker.py.

**Interfaces:** Consume Task 2 API and Task 1 validation via a documented pure import boundary. Worker supports once/loop, authenticated bounded HTTP, independent heartbeat, capability registration, checkpoint submissions, external intent/result lifecycle and explicit wait/fail. Implements product facts, keyword/title, research and gallery validation, image generation and QC orchestration via injected provider contracts. Production unconfigured providers return capability_missing; no invented model endpoint or hidden browser fallback. Add a local fixture provider restricted to explicit offline mode, never auto-enabled in deployment. CLI uses environment secrets, no secrets in argv/logs. Unknown provider results are not retried automatically.

- [ ] Write failing tests with an in-process HTTP fixture server plus controlled provider: claim to completion, stale lease, invalid provider output, heartbeat during slow work, timeout/unknown result, capability_missing and cancellation.
- [ ] Observe RED, then implement runtime and handlers against actual API schema.
- [ ] Run domain, Worker and existing project suite once code passes; record results.
- [ ] Add deployment instructions, capability matrix and precise unimplemented live-provider/platform limitations. Update deployment README with install instructions without changing local database.
- [ ] Commit and report.

## Delivery coverage

This plan implements independently usable execution infrastructure and provider-driven handlers. Browser vendor integration, paid image service configuration, real platform mutation, campaign execution and measured business metrics require verified external contracts and remain disabled until separate live acceptance; their schemas and blockers are explicit. Do not claim all design phases are production-ready.
