# Final combined fix wave

Addressed both Important findings from `final-review.md` and `task-3-review.md` in one TDD fix wave. No subagents, live provider/platform calls, existing database access, or developer configuration changes. `outputs/` remains untouched and untracked.

## Changes

Public ORM creation resolves context/user defaults before validation. Task creation rejects protected state/result/lease/status/request context defaults just as it rejects explicit protected values, and supplies trusted queued/empty/epoch-zero initialization before base ORM creation. Case creation rejects a revision context default and initializes revision to zero. Snapshot digest and proposal digest/input revision derive from resolved inputs. Artifact payload/data exclusivity and digest validation see the resolved defaults, so a hidden second representation cannot be appended later. Company defaults pass the same parent-company mismatch check as explicit values; ordinary omitted company still derives from the parent. Valid context defaults for normal input fields remain usable.

Snapshot/artifact GET boundaries convert reference-denied/missing/conflicting HTTP 403/404/409 into validation errors. Input lookup failure therefore submits fail/invalid_input before dispatch. A known provider output referencing an unavailable decimal artifact resolves the external ledger with a failed checkpoint, then submits fail/invalid_result. Heartbeat and fenced submission rejection retain lease_lost; server/transport errors remain recoverable API errors. No provider redispatch is introduced.

## Observed RED

All Python commands used repository `.venv/bin/python`.

1. `deploy/mercado/tests/run_optimization_worker.py` → `/tmp/meli-final-worker-red.log`: **25 tests, 9 failures**. New output-artifact 403/404/409 and input snapshot/artifact 403/404/409 cases returned `lease_lost` instead of `failed`. Existing 23 tests passed. These exercise the actual Client and Worker against the loopback HTTP server.
2. `deploy/mercado/tests/run_optimization_odoo.py --http` → `/tmp/meli-final-odoo-red.log`: **17 tests, 9 failures, 1 error**. Eight protected Task context defaults and Case revision were accepted; normal default-only task creation raised `KeyError: task_type` before default expansion. Existing tests passed.
3. Expanded the artifact test into independently executed subtests, before ORM production changes. Same Odoo command → `/tmp/meli-final-odoo-red2.log`: **17 tests, 11 failures, 1 error**. Both context-injected binary data alongside explicit JSON and context-injected JSON alongside explicit binary were accepted, independently confirming the artifact bypass. The previous failures/error remained.

## Observed GREEN

- `deploy/mercado/tests/run_optimization_worker.py` → `/tmp/meli-final-worker-green.log`: **27 passed**, 14.241s. Original 23 plus four tests: known output reference rejection, input reference rejection before dispatch, recoverable reference HTTP503, and actual heartbeat/checkpoint 403/404/409 fencing. The result-failure regression verifies checkpoint states unknown→failed, invalid_result submission, server failed state, idle restart, and exactly one provider invocation.
- `deploy/mercado/tests/run_optimization_odoo.py --http` → `/tmp/meli-final-odoo-green.log`: **17 tests, 0 failed, 0 errors**, process exit 0. Odoo stats reports 21 including framework counts. Original 14 plus three regressions use actual non-sudo users with_context(...).create(...), covering protected Task fields, Case revision, both artifact representations, valid ordinary defaults, invalid defaulted task payload, foreign company default, and digest recomputation from a defaulted snapshot payload. Existing real HTTP/Worker integration, independent-cursor concurrency, lease and role/company cases still pass. Disposable runner stopped its owned private PostgreSQL cluster in cleanup.
- `deploy/mercado/tests/run_unit.py` → `/tmp/meli-final-pure-green.log`: **37 passed**, 3.537s.
- `node --test deploy/mercado/mcp/test/registry-http.test.js` → `/tmp/meli-final-mcp-green.log`: **5 passed**, 0 failed.
- `git diff --check`: passed.

Existing provider/live acceptance limitations remain as documented in Task 3 and the Worker README. This report establishes the two reviewed infrastructure corrections with offline and isolated integration evidence.
