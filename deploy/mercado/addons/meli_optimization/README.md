# Durable optimization API (schema 1)

Install `meli_optimization` on Odoo 19. Assign `Optimization Editor` to human API users and `Optimization Worker` to service users. Editor implies Worker. All routes require `Authorization: Bearer <Odoo rpc API key>`. Authentication resets session context and uses the API user's allowed companies. Never send credentials in query parameters. Company IDs are derived from parent records; caller context cannot authorize protected writes. The durable records are immutable through generic ORM write/unlink, including sudo; trusted private methods perform fenced state updates. API key holders do not receive generic model dispatch through these routes.

POST requests use `Content-Type: application/json`. Successful responses are `{"success":true,"data":...}`. Business errors return 409 `invalid_operation`, authorization failures 403, missing/bad API keys 401, missing records 404, malformed envelopes 400, oversized requests 413. Framework parsing errors may use framework HTML errors on `/internal/` routes. HTTP writes run in savepoints. All IDs are Odoo integers at transport level. IDs inside domain payloads are strings. No route executes live platform writes. `GET /api/meli/optimization/capabilities` returns `live_execution:false`, `code:"unsupported_capability"` and supported task types.

## Human/editor endpoints

- `POST /api/meli/optimization/cases`: `{"batch_name":"November","name":"Product A","snapshot":{"item_id":"MLM-A","schema_version":"1"}}` -> 201 data `{batch_id,case_id,snapshot_id,snapshot_digest}`. A fresh batch is created for this case.
- `GET /api/meli/optimization/cases/12` -> data `{id,name,company_id,revision,tasks:[{id,task_type,state,result,status_detail}]}`.
- `POST /api/meli/optimization/cases/12/tasks`: `{"snapshot_id":15,"task_type":"product.facts","payload":{"schema_version":"1","product_snapshot_id":"15"},"dependency_ids":[]}` -> 201 data `{task_id,state:"queued"}`. Dependencies and snapshot must belong to the same case. Inputs follow domain/contracts.py.
- `POST /api/meli/optimization/cases/12/proposals`: `{"payload":{"titles":["Example"]}}` -> 201 data `{proposal_id,digest,input_revision}`. This stores a proposal; it does not execute it.
- `POST /api/meli/optimization/proposals/18/confirm`: `{"digest":"<exact returned hash>"}` -> data `{confirmation_id,valid:true}`. Confirmation binds the current API user, exact proposal payload digest, and case input revision. New snapshots increment revision and invalidate older confirmations. Confirmation does not authorize any live execution endpoint.

Worker or Editor: `GET /api/meli/optimization/snapshots/15` returns `{snapshot_id,case_id,company_id,payload,digest}`. Use this to resolve the snapshot ID from a claim before producing product facts.

## Worker endpoints

`POST /internal/meli-optimization/claim` with `{"capabilities":["product.facts","title.generate"]}` returns data `false` if none eligible, otherwise:

```json
{"id":21,"company_id":1,"snapshot_id":15,"task_type":"product.facts","schema_version":"1","input_hash":"<sha256 of canonical payload>","payload":{"schema_version":"1","product_snapshot_id":"15"},"lease_token":"<uuid>","lease_epoch":1,"lease_expires_at":"2026-11-15 01:00:00","heartbeat_seconds":30}
```

Expiry timestamps are UTC. Claims use PostgreSQL `FOR UPDATE SKIP LOCKED`, allow only matching capabilities and authorized companies, and wait for all dependencies to succeed. A lease lasts 120 seconds. A reclaimed expired lease increments the epoch. Task attempts record epoch and worker user. A worker must retain the token, epoch and stable submission keys durably.

`POST /internal/meli-optimization/tasks/21/heartbeat`: `{"lease_token":"<uuid>","lease_epoch":1}` -> updated claim envelope, extending the lease by 120 seconds. Send every 30 seconds. Expired or stale leases cannot renew or submit. The worker user must match the claiming user.

`POST /internal/meli-optimization/tasks/21/checkpoint`:

```json
{"lease_token":"<uuid>","lease_epoch":1,"submission_key":"dispatch-remote-A","body":{"request_id":"remote-A","state":"unknown"}}
```

Checkpoint state is one of `unknown`, `dispatched`, `succeeded`, `failed`. Record unknown/dispatched before a potentially side-effecting external dispatch. Such records block automatic reclaim after lease expiry. While the lease remains valid the same worker can report terminal status using a new submission key. Expired unknown requests require operator reconciliation; this version intentionally has no public force-unblock endpoint. Terminal external request states cannot be changed. This ledger is bookkeeping, not a permission to use unverified live providers.

`POST /internal/meli-optimization/tasks/21/complete`:

```json
{"lease_token":"<uuid>","lease_epoch":1,"submission_key":"complete-21","body":{"schema_version":"1","evidence":[{"id":"e1","source":"snapshot:15","observed_at":"2026-11-15T00:00:00Z"}],"facts":[{"name":"item_id","value":"MLM-A","evidence_ids":["e1"]}],"uncertainties":[]}}
```

Complete rejects unresolved `unknown`/`dispatched` external requests, validates the domain result, stores it and changes task state to succeeded. Checkpoint/complete/fail/wait return `{task_id,submission_key,state,accepted:true}`. Retries with the identical key, token, epoch, operation, body and user return the original receipt even after completion, failure, waiting, cancellation or a later lease; conflicting reuse returns 409. This does not make arbitrary external provider calls idempotent.

`POST /internal/meli-optimization/tasks/21/wait` and `POST /internal/meli-optimization/tasks/21/fail` use the same fenced submission envelope:

```json
{"lease_token":"<uuid>","lease_epoch":1,"submission_key":"wait-provider-21","body":{"code":"capability_missing","message":"Configured provider is unavailable"}}
```

The body has exactly `code` (nonblank string, max 128 characters) and `message` (nonblank string, max 4096 characters); the full submission remains bounded to 256 KiB. Wait changes running to waiting; fail changes running to terminal failed. Both persist `status_detail`, emit an immutable event, and revoke the lease. Use wait for operator-actionable missing capabilities; use fail for terminal invalid results. The same exact replay rules apply. New submissions require a live matching lease and the claiming user. Fail/wait never resolve external requests or unblock dependents.

Editor only: `POST /api/meli/optimization/tasks/21/resume` with `{}` changes waiting to queued, returning `{task_id:21,state:"queued"}`. Any unresolved external request rejects resume with 409, preserving the uncertainty ledger. Next claim issues a new token and increments the epoch. `POST /api/meli/optimization/tasks/21/cancel` with `{}` changes queued/running/waiting to terminal cancelled and returns `{task_id:21,state:"cancelled"}`. Cancellation revokes any lease but leaves every external request intact. Controls check the editor role and authorized company, serialize with worker submissions, and append audit events. Terminal tasks cannot resume or cancel; duplicate controls return 409. A cancelled task with an unknown provider outcome still needs external reconciliation; this version provides no force-unblock operation.

## Artifacts

Worker or Editor: `POST /api/meli/optimization/cases/12/artifacts` with exactly one of `payload` (JSON, max 256 KiB) or `data` (strict base64, decoded max 8 MiB), plus `digest` and optional `mime_type`. JSON digest uses domain `canonical_hash`; binary digest hashes decoded bytes. -> 201 `{artifact_id,digest}`. Artifact company is derived from the authorized case. `GET /api/meli/optimization/artifacts/23` returns `{artifact_id,digest,payload,data,mime_type}`, with base64 data for binary. No URLs are fetched server-side. Domain reference strings and image metadata still require producer verification; these APIs do not decode or semantically verify images/evidence.

## Isolated verification

From repository root: `.venv/bin/python deploy/mercado/tests/run_optimization_odoo.py` runs ORM tests in a freshly initialized private Unix-socket PostgreSQL 16 cluster. `--http` additionally runs real HttpCase tests on owned loopback port 55440. The runner unsets inherited PG settings, uses `/dev/null` config, blocks non-test network connections in the Odoo child, and stops its owned cluster in `finally`. It never uses the existing Mercado database or `.local/odoo.conf`. Port conflicts fail rather than attach to an existing server. `.venv/bin/python deploy/mercado/tests/run_unit.py` runs the full pure suite.

Queue search filters company, capability, lease expiry, succeeded dependencies and unresolved requests before selecting up to 100 eligible candidates per claim; each locked candidate is rechecked. Permanently blocked older tasks do not starve later ready work. Concurrently locked candidates are skipped; callers may poll again. There is no reconciliation or cancel/resume UI or automatic task graph builder in this version. The minimal backend views show tasks; creation and operation use the narrow APIs. Do not enable live execution based on a confirmation or task capability string alone.
