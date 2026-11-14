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
