# Existing business DB deployment and real backend verification

Completed 2026-11-15. Installed reviewed commit `642265dce` into the existing local `mercado` database, PostgreSQL16.15 at `127.0.0.1:5433`. No disposable database was substituted. The module was previously absent; `meli_optimization` is now `installed`, version `19.0.1.0.0`.

## Backup and migration

Fresh pre-install DB/filestore/config/addon backup: `/Users/hugo_1/Workspace/PythonProject/odoo/.local/deployment-backups/library-20261115-142316`. Parent validated its custom-format dump (~9MB), 793 filestore files and manifest before GO. Backup pointer is `.superpowers/sdd/independent-library/backup-path`. Also saved exact deployed module source as `meli_optimization-installed-642265dce.tgz` inside that backup directory.

Executed successfully (exit0):

```sh
/usr/bin/sandbox-exec -p '(version 1) (allow default) (deny network*) (allow network-outbound (remote ip "localhost:5433"))' .venv/bin/python odoo-bin -c .local/odoo.conf -d mercado -i meli_optimization --stop-after-init --no-http --max-cron-threads=0 --without-demo=all
```

Preflight checked the existing configured target through the same sandbox. No tests were enabled or run against business data. Migration loaded89 modules, including new module security/views/assets, and shut down normally. The initial sandbox profile using literal127.0.0.1 was rejected before any command ran; macOS requires `localhost` in this filter. Corrected profile was validated before migration.

## Running backend

URL: `http://127.0.0.1:8069/odoo` (normal existing user login). Final listener verified only `127.0.0.1:8069`, process PID21552 at time of verification; managed exec session98666 remains running after agent completion. Started directly using `.local/odoo.conf` because PG was already running and GUI launchctl service was unavailable:

```sh
.venv/bin/python odoo-bin -c .local/odoo.conf --max-cron-threads=0
```

Installation and smoke used an outbound-denying sandbox (only local database connections allowed). After smoke and cleanup, gracefully terminated the owned sandboxed Odoo PID19180 and started the normal configured backend above, preserving existing business platform connectivity. Cron remains0. No integration actions, paid provider calls, marketplace writes, or tests were performed. PostgreSQL was started separately by parent before GO. This is a managed running process, not a new auto-start service; it will need restart after machine reboot/app process-session cleanup.

Final verification after restart: `/web/health` returned HTTP200 `{"status":"pass"}`; direct read confirmed `meli_optimization`, `installed`, `19.0.1.0.0` on configured mercado DB. Temporary session remained removed. Existing external connectivity is available to the normal application, but was not exercised by this deployment check.

## Actual authenticated UI/RPC chain

Used the existing active system administrator referenced by `base.user_admin` (user ID2). Added only `meli_optimization.group_editor` (Optimization Editor), which implies its worker role. Did not create/modify passwords, API keys, other accounts, or company memberships. A temporary filesystem session was injected privately into an isolated Chrome profile using CDP, never printed or placed in a URL. At completion the exact temporary server session was deleted, its0600 private state file removed, browser cookies cleared, and Chrome closed.

Ran the real native Odoo backend in Chrome1440×1100 against the existing business DB. Chrome outbound networking was limited to local8069; local Unix sockets and debugger55442 were allowed. Browser CDP evaluation drove actual visible forms and buttons. Fixture completion/validation and record assertions used the authenticated HTTP ORM endpoint, not test harness rollback or direct SQL inserts.

Verified sequence:

1. Created standalone collection in the UI: case1, name `MANUAL E2E 20261115 standalone`, source `https://example.test/manual-e2e`. It is explicitly manual/test only.
2. Archived a real decoded128×128 PNG containing `TEST ONLY`, SHA256 `c8f02d51ef37adeb7d87c0bbe856a9f3281a5922c5091b4a87830fb5700578bc`. Completed gallery.collect task2 through lease/complete RPC with explicit synthetic evidence, reference `MANUAL-E2E-REFERENCE`, SKU `TEST-ONLY-SKU`. No source was fetched.
3. Clicked save verified gallery in the UI, opened company library1 and its source/provenance dialog. The actual archived image rendered with source, SKU, dimensions and digest.
4. Created a different product optimization via UI: case2, item `MANUAL-E2E-DEST-20261115`, title `TEST FIXTURE cross-case garment`. Uploaded fixture source garment artifact2 through actual authenticated HTTP RPC.
5. Opened target selection through UI, selected the library image and separate source garment, verified no modal horizontal overflow, and clicked freeze/create.
6. Asserted exactly one generation task3, frozen planartifact4 with destination case2, snapshot2, `input_revision:1`, selected indices `[0]`, `target_count:1`, sourceartifact2, cloned targetartifact3, immutable library result hash and preserve fields. This verifies reusable cross-case selection and precise frozen scope.
7. Cancelled every queued task for these marked cases through public task controls. Final task states: task1 product.facts **cancelled**; task2 gallery.collect **succeeded (manual fixture only)**; task3 image.generate **cancelled**. No fixture task remains queued/running. Records intentionally retained and clearly marked for inspection.

Evidence: `/Users/hugo_1/Workspace/PythonProject/odoo/outputs/library-deployment/evidence.json`. Browser Runtime.exceptionThrown capture: `browser-errors.json`, empty array. Screenshot05 was visually inspected: correct Chinese native backend, verified image/provenance, distinct garment selector and freeze button, readable layout.

## Screenshots

All in `/Users/hugo_1/Workspace/PythonProject/odoo/outputs/library-deployment/`:

- `01-workbench.png`: real authenticated native backend.
- `02-collection-form.png`: standalone collection creation.
- `03-persistent-library.png`: saved reusable company library.
- `04-library-provenance.png`: actual image with SKU/source/digest.
- `05-cross-case-selection.png`: different product target selection and separate source garment.
- `06-frozen-generation-plan.png`: queued generation after frozen UI submission.
- `07-cancelled-fixture-tasks.png`: safe retained final UI state.

## Practical limits

This proves installation, actual backend rendering/interaction, real archived PNG validation, persistent library reuse across different cases, exact frozen task plan and cancellation on the existing DB. It does not prove live source scraping, an actual fashion photograph or garment transfer, paid provider operation, or publication back to Mercado Libre. The synthetic128px reference is deliberately labeled. No new integration credentials were configured, and no external operation was invoked during these checks. Network isolation applied during installation/smoke; the final normal server preserves pre-existing integration connectivity. Only the Optimization Editor membership, new addon tables/assets and marked manual verification records were added.
