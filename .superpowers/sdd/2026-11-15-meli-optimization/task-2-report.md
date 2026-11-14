# Task 2 implementation report

Status: complete; durable addon/API, isolated integration verification and self-review finished.

## Durable interfaces

Addon `meli_optimization` depends on `meli_bff` and provides models `meli.optimization.batch`, `case`, `snapshot`, `task`, `attempt`, `event`, `external.request`, `artifact`, `proposal`, `confirmation`. All durable models reject generic ORM writes/unlinks. Internal ledgers reject public creates; trusted mutation methods are private and use super, never context authority. Company is derived from an authorized parent, with global company rules and Editor/Worker groups. Ordinary inactive callers are denied; Odoo's intentionally inactive superuser remains usable for trusted installation/tests.

Task public business operations: `claim(capabilities) -> envelope|False`, `heartbeat(token,epoch) -> envelope`, `checkpoint(token,epoch,key,body) -> receipt`, `complete(token,epoch,key,body) -> receipt`. Claims lock rows with PostgreSQL FOR UPDATE SKIP LOCKED, fence with monotonic epoch and UUID token, assign worker uid and 120-second expiry. Recommended heartbeat 30 seconds. Dependencies must succeed and unknown/dispatched external requests block reclamation. Attempts and submissions have database uniqueness constraints. Task row locks serialize checkpoint/complete submissions; repeated exact submissions return the original receipt, including after completion, while conflicts reject. Evidence-backed domain contracts validate inputs/results.

Transport and every request/response are documented in `deploy/mercado/addons/meli_optimization/README.md`. New `meli_optimization` authentication delegates existing stateless API-key checks while avoiding the legacy route resolver's auth-name guard. Human `/api/meli/optimization` routes provide cases, tasks, snapshots, artifacts, proposals, confirmation and capabilities; worker `/internal/meli-optimization` routes expose only claim/heartbeat/checkpoint/complete. There is no generic ORM dispatch or live platform execute route. Capabilities explicitly return unsupported live execution.

Claim data includes id, task_type, payload, schema_version, input_hash, company_id, snapshot_id, lease_token, lease_epoch, UTC lease_expires_at, heartbeat_seconds. All transport IDs are integers; domain references remain strings. Artifacts verify SHA256 over canonical JSON or decoded binary, derive company from case, and bound JSON/binary ingress. Custom ExactJson preserves empty objects which Odoo's stock Json would collapse to NULL, maintaining digest identity. Confirmation binds exact proposal digest, current caller and case input revision; new snapshots invalidate old confirmations.

## TDD and integration evidence

Read test-driven-development skill and writing-good-tests reference before test creation. First wrote a minimal installable manifest plus real TransactionCase expectations, then ran the disposable cluster before implementing models.

- Initial RED: `.venv/bin/python deploy/mercado/tests/run_optimization_odoo.py`, `/tmp/meli-task2-red.log`: 3 failures, zero errors; all failed on assertion `durable optimization implementation is missing`, after loading actual Odoo and 87 modules.
- First implementation run `/tmp/meli-task2-green.log` was NOT green: 2 errors revealed an invalid empty-facts result fixture and Odoo Json's empty-object-to-NULL behavior. Corrected fixture to domain-valid facts with evidence; preserved exact JSON values in production.
- Expanded role/envelope RED: `/tmp/meli-task2-red2.log`: 2 errors, including missing claim `input_hash`, and the empty JSON regression prior to fix.
- Intermediate run `/tmp/meli-task2-green2.log` was NOT green: ordinary active-user enforcement initially rejected intentionally inactive Odoo superuser. Narrowed that guard to non-sudo users; actual HTTP API-key auth still always requires active nonpublic users.
- Integration GREEN: `.venv/bin/python deploy/mercado/tests/run_optimization_odoo.py --http`, `/tmp/meli-task2-green3.log`: 0 failed, 0 errors of 6 tests. Includes a real HttpCase with valid/missing API keys, new API and internal routes, malformed and oversized requests, group denial; independent real PostgreSQL connections prove SKIP LOCKED returns no second claim and committed live lease cannot be reclaimed.
- Full pure suite: `.venv/bin/python deploy/mercado/tests/run_unit.py`, `/tmp/meli-task2-unit.log`: 36 tests, OK.
- Self-review snapshot-read RED: `/tmp/meli-task2-red-snapshot.log`: 1 assertion failure, zero errors of 7 tests, expected HTTP 200 but received 404 via legacy catch-all because snapshot read route was absent.
- Final GREEN: `.venv/bin/python deploy/mercado/tests/run_optimization_odoo.py --http`, `/tmp/meli-task2-final.log`: 0 failed, 0 errors of 7 tests; includes successful real HTTP snapshot read, inactive ordinary-user denial, worker artifact upload, foreign-company artifact rejection and empty-object preservation.
- `git diff --check` and compileall passed. The addon initially emitted a missing manifest author warning; added manifest author metadata after verification. Dependency warnings are from existing modules, not errors.

The runner creates only an owned mkdtemp cluster, uses explicit Homebrew PostgreSQL 16 binaries, `initdb -U odoo_test --auth=trust --encoding=UTF8 --locale=C`, Unix socket with listen_addresses empty, fixed private-socket port 55439, explicit Odoo `/dev/null` config and fresh database `odoo_opt_test`, zero workers/cron. Inherited PG variables are removed. Odoo child socket.connect blocks outbound connections; optional --http allows only its owned 127.0.0.1:55440 server. Finally stops the owned cluster, then deletes its temp directory. Existing Mercado database and `.local/odoo.conf` were never used. No live API calls or outputs changes.

## Scope and limitations

This implementation provides durable persistence and transport, not provider execution or task graph orchestration. Snapshot and artifact reference strings inside domain payloads remain opaque; producers must verify provenance/content and resolve artifact IDs through authorized APIs. Image bytes are digest-checked, not decoded or semantically inspected. Proposal confirmation is not permission to execute an unverified provider.

Unknown/dispatched requests block automatic retries indefinitely after lease loss; operator reconciliation UI/API is intentionally absent, preventing blind resends. A worker with a still-valid lease may update an external request to terminal state using a new checkpoint key. Bounded queue scanning considers the oldest 100 candidates, so blocked front-of-queue candidates can delay later work. No cancel/retry UI or task-graph builder is present; read-only backend task list/form views are minimal. Transport responses for framework errors on internal routes may be framework HTML, so clients must inspect HTTP status before JSON parsing.

Integration tests establish ORM behavior, non-superuser company/artifact access, direct protected mutation rejection, lease expiry/stale token rejection, replay conflicts, dependencies, uncertainty blocking, confirmation invalidation, HTTP auth/route/body behavior and independent-cursor claims. They do not prove multi-process stress behavior, worker crash recovery with real external providers, production upgrades, semantic evidence validity, or live platform capabilities.

## Self-review

Reviewed role checks at ORM entrypoints, immutable create/write/unlink boundaries, server company derivation, protected ledger insertion, task row lock ordering, epoch/token/user fencing, replay identity including operation and caller, external uncertainty checks, snapshot revision and exact proposal binding, bounded HTTP ingress and explicit route auth names. Confirmed no caller-supplied context flag grants access, and no credential fields are present in claim or artifact responses. Scoped commit contains only Task 2 addon files, its disposable runner, and this report; existing domain files and outputs remain unchanged.

## Review fixes (2026-11-15)

Addressed all three Important findings in `task-2-review.md`, plus the minor canonical hash Unicode finding. This section supersedes the earlier limitations about missing cancel/resume operations and oldest-100 queue starvation. Read the receiving-code-review, test-driven-development (including writing-good-tests), and verification-before-completion skills. No subagents were used for these fixes.

- Added Worker `fail` and `wait` methods/routes with the same lease token/epoch, worker identity, bounded JSON and immutable idempotency receipt contract as checkpoint/complete. Their exact body is `{code,message}`: nonblank strings bounded to 128/4096 characters respectively. They persist `status_detail`, transition running to failed/waiting through `validate_transition`, and revoke the active lease. Exact replay remains valid after lease revocation and even after editor resume; conflicting key reuse and new stale/expired submissions reject.
- Added Editor `resume`/`cancel` methods and `/api/meli/optimization/tasks/<id>/resume|cancel` routes accepting `{}`. Both serialize on the task row and append immutable control audit events. Resume accepts only waiting tasks with no unknown/dispatched requests. Cancel accepts queued/running/waiting tasks, revokes any lease, and preserves the entire request ledger. Terminal states cannot reopen. Tests exercise a real non-sudo editor, worker denial, foreign-company editor denial and terminal transition denial.
- Queue search now applies company/capability, lease expiry, dependency readiness and unknown/dispatched exclusion before its 100-candidate cap, then retains the locked eligibility recheck. The starvation fixture has an expired upstream with unknown external outcome plus 101 blocked dependents before a later ready task; model-level claim now returns the later task. Work per claim remains bounded to at most 100 candidate locks. Concurrently locked candidates can still cause a transient false claim response, requiring normal polling.
- First-time completion rejects unresolved requests before result/state writes. Tests cover both unknown and dispatched, preserve running state and dependency blocking on rejection, then resolve the request under the valid lease, complete, replay and successfully claim the dependent.
- `canonical_hash` now converts invalid UTF-8 surrogate encoding to `DomainError(code='invalid_json')`; regressions cover both object keys and values. Valid Unicode hashing remains unchanged.
- Updated addon README with exact fail/wait/control routes, bodies, receipts, bounds, authorization and uncertainty semantics. Case read responses now expose `status_detail` for operator diagnosis.

### Observed RED / GREEN evidence

All commands ran from repository root using the existing safe disposable runner; the existing Mercado database, `.local/odoo.conf`, live APIs and unrelated outputs were never accessed or changed.

1. Wrote real ORM/HTTP regressions before implementation. `.venv/bin/python deploy/mercado/tests/run_optimization_odoo.py --http` -> `/tmp/meli-task2-review-red.log`, **5 failed, 0 errors of 12 tests**. Failures reproduced the absent wait route (404), accepted unresolved completion (expected ValidationError absent), missing fail/wait operations, blocked oldest-100 starvation, and absent wait/control lifecycle. Existing tests passed.
2. `.venv/bin/python deploy/mercado/tests/run_unit.py` -> `/tmp/meli-task2-review-unit-red.log`, **37 tests, 2 subtest errors**. Surrogate key and value each leaked UnicodeEncodeError instead of DomainError.
3. First implementation run -> `/tmp/meli-task2-review-green.log`, **1 failed, 0 errors of 12 tests**. All ORM regressions passed. The new HTTP test helper's `json={}` selected GET in Odoo's test client, causing a 404 on resume; changed the test helper to serialize JSON explicitly as POST data. This was a test fixture error, not a production route change.
4. Final `.venv/bin/python deploy/mercado/tests/run_optimization_odoo.py --http` -> `/tmp/meli-task2-review-green2.log`, **0 failed, 0 errors of 13 tests**, process exit 0. Includes the complete HTTP wait/replay/resume/fail/terminal-reject/cancel flow plus expanded ORM tests for expired leases, other worker identity, non-sudo editor controls, role/company denial, bounded status body, monotonic epoch after resume, old exact receipt replay and preserved unknown ledger.
5. `.venv/bin/python deploy/mercado/tests/run_unit.py` -> `/tmp/meli-task2-review-unit-green.log`, **37 tests, OK**. `git diff --check` and compileall of the addon and modified pure test also passed.

The runner again used only its owned temporary PostgreSQL cluster and fresh database, explicit `/dev/null` config, blocked non-test network, and owned loopback HTTP service. Existing dependency manifest warnings remain unrelated to these changes.

### Remaining limits

There is still no force-unblock/reconciliation endpoint, and waiting/cancelled/failed tasks never erase unresolved provider outcomes. An operator must reconcile those outcomes outside this version; resume deliberately rejects uncertainty rather than risk duplicate effects. Editor controls are authorized transitions, not replay-keyed worker submissions: repeated resume/cancel returns 409. There is no control UI or automatic graph builder. These tests verify real ORM/HTTP and independent-cursor locking behavior; they do not establish production stress behavior or real external-provider semantics.
