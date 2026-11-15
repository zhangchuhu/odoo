# Automatic screening facts arrangement

Each newly persisted screening match now includes its snapshot, a succeeded `product.facts` task, a completion audit event and an archived JSON artifact before `screening_execute` returns. The existing `workbench_archive` operation assigns task/snapshot/result-hash provenance. Keyword/title flows can select that artifact through their existing case-owned input selectors. Previously completed batches are returned unchanged; no backfill is performed.

The worker and screening path share `domain/facts.py`. It preserves the existing worker contract: every supplied snapshot field is copied as a fact with `snapshot:<id>` evidence and an explicit uncertainty that external truth has not been verified. Missing seller SKU, size and other fields are never inferred from title/image/quantity. This operation performs no image analysis or provider/network call.

Screening retains its normal editor/shop authorization and company boundaries without `sudo`. Normal task creation validates the input and case/snapshot ownership; normal state transitions and result validation precede archival. The completion receipt records `execution: local_snapshot` and the screening ID, distinguishing this local execution from leased worker submissions. No worker attempt, lease or external request is fabricated. Public task writes and artifact provenance assignment remain protected. Manual creation and worker completion retain their existing flow.

All new records are inside the existing persistence savepoint. Archive failure rolls back the batch, cases, snapshots, tasks, events and artifacts, permitting a clean retry. Completed-request replay is unchanged and cannot append duplicate tasks or artifacts.

## Verification

TDD RED: `.venv/bin/python deploy/mercado/tests/run_optimization_odoo.py --http` completed with exactly two expected failures (47 tests, zero errors): automatic facts task missing, and archival failure injection did not run. Log: `/tmp/auto-facts-red.log`.

GREEN: the same complete disposable Odoo HTTP suite passed with 47 tests, zero failures/errors (`/tmp/auto-facts-green.log`). The final complete rerun also passed 47 tests, zero failures/errors (`/tmp/auto-facts-green-final.log`). Assertions cover local execution provenance, no worker claimability or attempts, retained explicit size evidence and keyword task selection.

Additional verification:

- `.venv/bin/python deploy/mercado/tests/run_optimization_worker.py`: 48 passed.
- `.venv/bin/python deploy/mercado/tests/run_unit.py`: 62 passed.
- `node --test deploy/mercado/tests/test_workbench_ui.mjs deploy/mercado/tests/test_workbench_actions.mjs`: 9 passed.
- `git diff --check`: passed.

Odoo tests use a fresh owned PostgreSQL cluster and block outbound Python sockets. No business database upgrade/write, platform call, provider request or deployment was performed.
