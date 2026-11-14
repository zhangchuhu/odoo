# Task 3 delivery report

Implemented a standalone Unix/Python standard-library Worker with once/loop CLI, authenticated bounded stateless HTTP, independent heartbeat, injected provider contracts, explicit offline fixtures, pure domain import boundary, and a private SQLite journal. The journal locks out concurrent use, binds to API origin/credential fingerprint, keys records by task/input hash/epoch, commits external intent before dispatch, preserves stable exact submission envelopes, and replays pending requests before claiming fresh work. Interrupted/timeout/exception provider outcomes never automatically dispatch again.

Handlers resolve digest-verified snapshots/artifacts and validate all seven task contracts. Product facts preserves supplied snapshot observations, titles require ten distinct grounded candidates, research preserves explicit status and nullable metrics, galleries bind SKU identity and referenced binary artifacts, image generation binds the request ID, and QC binds the image with consistent named checks. No live vendor/browser/payment/platform calls were added or made. `outputs/` remains untouched/untracked.

## Observed RED and GREEN

All commands below used `.venv/bin/python` from the repository root.

1. `-m unittest discover -s deploy/mercado/optimization_worker/tests -v`: RED, **11 failures**, each asserting `standalone optimization Worker is missing` (the missing production implementation). Implemented client/runtime/handlers/journal/providers: GREEN **11 tests**, 5.953s.
2. Expanded suite: RED **27 tests, 1 failure + 1 error** (11 tests were temporarily inherited twice): CLI module absent and journal identity mismatch was not enforced (received HTTP401 instead). Added CLI explicit offline gate and journal origin/key binding, removed duplicate test inheritance: GREEN **16 tests**, 8.609s.
3. Added all handler types and standalone pure import collision test: RED **18 tests, 1 failure**, preloaded unrelated generic `domain` module blocked imports. Replaced generic sys.path import with a private importlib package boundary; subsequent suite green.
4. Added lost-checkpoint replay, CLI successful offline HTTP execution and non-JSON provider output: RED **21 tests, 1 error**, NaN output could poison the journal and failure path. Validate finite JSON and bounded output before persistence; GREEN **21 tests**, 11.294s.
5. `-m unittest discover -s deploy/mercado/optimization_worker/tests -k restart_after_known -v`: RED **1 failure**, known missing provider crash recovery omitted failed terminal checkpoint (`[] != ['failed']`). Persisted explicit recoverable provider-missing phase: GREEN **22 tests**, 11.619s.
6. `-m unittest discover -s deploy/mercado/optimization_worker/tests -k non_object -v`: RED **1 error**, nonobject JSON snapshot lacked `.items()`. Preserve it as one supplied snapshot-payload fact; final Worker suite **23 tests GREEN**.

Real Odoo integration initially produced one error: direct urllib requests lacked HttpCase test-cursor request enablement (HTTP400). Corrected only the test harness using `allow_requests(all_requests=True)`, preserving the production stateless client. The actual Worker then passed against the real Odoo claim/heartbeat/snapshot/artifact/checkpoint/complete endpoints, including facts task -> artifact upload -> dependent keyword task using an explicit offline provider.

## Final verification

- `.venv/bin/python deploy/mercado/tests/run_optimization_worker.py`: **23 passed**. In-process actual loopback HTTP server, actual client/runtime, controlled providers and subprocess CLI. Covers success and lifecycle checkpoints, invalid/non-JSON output, missing capability, slow-provider heartbeats, exception/timeout unknown results, interrupted dispatch, no duplicate invocation after restart, lost checkpoint/complete acknowledgements with exact replay, stale lease, cancellation during work, journal identity, all seven handler types, SKU binding, pure imports and CLI offline gating.
- `.venv/bin/python deploy/mercado/tests/run_unit.py`: **37 passed**, 3.534s, including the original pure domain/BFF/adapter/registry suite.
- `.venv/bin/python deploy/mercado/tests/run_optimization_odoo.py --http`: **14 tests, 0 failed, 0 errors** (Odoo stats reports 18 including framework counts), with the real standalone Worker HttpCase and the existing ORM/HTTP tests. Final log `/tmp/optimization-worker-odoo-final.log`.
- `node --test deploy/mercado/mcp/test/registry-http.test.js`: **5 passed**, 0 failed.
- `git diff --check`: passed.

The owned disposable PostgreSQL runner stopped its private cluster in cleanup and never read `.local/odoo.conf` or changed the existing Mercado database. No live/paid provider or platform was invoked. Test logs reside under `/tmp/optimization-worker-*`; they are execution evidence, not committed deliverables.

## Capability limits and concerns

Deployment instructions and capability matrix are in `deploy/mercado/optimization_worker/README.md`; deployment README links them. Production CLI configures only local snapshot facts. All other providers require explicit injection or explicit `--offline --fixture`; unconfigured task handlers enter waiting/capability_missing. Tests establish simulated infrastructure behavior, not live garment research, image generation, quality assessment, publication, campaign execution or business lift.

Image/gallery providers currently must return already stored decimal Odoo binary artifact IDs, arranged through a separately configured artifact adapter. `Client.create_artifact` implements the documented JSON/base64 transport, without automatic upload retry/idempotency guarantees. Worker checks reference existence, byte digest and declared MIME, and domain metadata/SKU/request binding; image decoding/dimensions, evidence truth, and visual correctness remain producer responsibilities. No task graph builder or automated downstream result-artifact wiring is supplied. These precise limitations are documented rather than claimed as production-complete integration.

A timed-out daemon provider thread cannot be forcibly killed safely; its late result is discarded and the external ledger stays unknown. Providers must implement their own bounded I/O. Socket timeouts bound individual HTTP operations, not a slow stream's total wall time. A stale/cancelled/heartbeat-failed task is conservatively abandoned locally, while unknown external requests remain blocked server-side. There is no reconciliation/force-unblock endpoint in this version; operator review and separately implemented audited reconciliation are required. API key rotation needs deliberate journal reconciliation/migration because origin and credential identity are bound. Journals contain sensitive lease/input/output information and must be retained privately. No automatic external retry is introduced by recovery.
