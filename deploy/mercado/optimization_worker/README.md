# Standalone optimization Worker

This Worker consumes the [schema 1 addon API](../addons/meli_optimization/README.md). It uses Python 3.10+ standard library on Unix; there is no Odoo import, browser driver, paid API SDK or vendor endpoint. Keep `optimization_worker` beside `addons/meli_optimization/domain` when deploying. `domain.py` uses a private importlib package name to load that pure directory, never the Odoo addon initializer. All domain validators remain available through that boundary.

Install `meli_optimization` on a separate intended Odoo instance using normal Odoo module installation, create a service user with `Optimization Worker` and the intended allowed companies, and generate its `rpc` API key. Installation is an operator step; these commands do not install or upgrade a database. From the repository root:

```bash
export PYTHONPATH="$PWD/deploy/mercado${PYTHONPATH:+:$PYTHONPATH}"
export ODOO_OPTIMIZATION_URL='https://odoo.example.test'
# Set ODOO_API_KEY through a secret manager or protected environment file.
export OPTIMIZATION_JOURNAL="$HOME/.local/state/optimization-worker/journal.sqlite"
.venv/bin/python -m optimization_worker once
.venv/bin/python -m optimization_worker loop
```

Use one persistent, private journal per process/service identity. Do not delete it to repair a task. It contains lease tokens, inputs, evidence and provider outputs; files use mode 0600 and the default newly created directory uses 0700. API keys are not persisted; a SHA-256 identity fingerprint binds the journal to the exact API origin and key. Key rotation requires a deliberate journal migration/reconciliation, not changing credentials against an active journal. A nonblocking Unix process lock rejects concurrent use. Independent instances can use separate journals and the server claim queue.

HTTP uses bearer headers, HTTPS except loopback, a 15 second socket timeout, bounded request/response bodies, no redirects, no environment proxy and no transport retry. Errors and CLI output exclude credentials and upstream exception bodies. The once command prints `idle`, `succeeded`, `failed`, `waiting` or `lease_lost`; API/configuration failure exits 1. The loop polls every five seconds; API transport failures retain pending work for exact replay. SIGINT/SIGTERM stops after the current bounded attempt. The timeout bounds socket operations, not the total wall time of a slow stream.

## Capabilities and providers

| Task | Available behavior | Live limitation |
| --- | --- | --- |
| `product.facts` | Fetch digest-verified snapshot; preserve its fields as evidence-backed observations and explicit uncertainty | Does not independently infer garment properties or verify supplied facts |
| `research.keywords` | Resolve facts artifact, invoke injected provider, validate status and grounded keywords | No live language model configured |
| `title.generate` | Resolve facts/keywords, validate exactly ten unique grounded titles | No platform length limit invented; no title publication |
| `research.bestseller` | Resolve facts/keywords, validate explicit complete/partial/empty status, evidence, raw sales and nullable metrics | No browser/vendor integration; no sales inferred from display text |
| `gallery.collect` | Validate selected SKU identity, manifest, completeness, unique image order, metadata and referenced binary artifacts | No gallery scraper, download, or main-SKU inference |
| `image.generate` | Resolve source/target/plan artifacts, persist external intent, verify request binding, validate image metadata and binary artifact digest/MIME | Codex CLI host uses the native image tool after a capability probe; candidate QC may require manual review |
| `image.qc` | Resolve image/plan artifacts, validate image binding, named checks and passed/failed/review consistency | No visual model or human QC; no pixel truth guarantee |

The claim capabilities list registers implemented handler contracts. Missing configured providers explicitly put claimed tasks in `waiting` with `capability_missing`; they never silently use fixtures. Production CLI supplies only built-in snapshot facts. A service embedding `Worker(..., providers={task_type: provider})` may inject implementations of `Provider.run(task_type, payload, context, request_id)`. The context contains digest-verified snapshot/artifact envelopes and no HTTP client or credentials. The provider returns a schema 1 domain result; provider exceptions/timeouts are conservatively unknown outcomes, and providers must bound their own I/O. `CapabilityMissing` means the provider did not dispatch any external work.

`browser_provider.BrowserResearchProvider` provides an explicit callable adapter
boundary for `research.bestseller`. It validates and copies the returned domain
body; it does not provide a browser, access platform data, verify observations,
or register itself with the CLI. A missing adapter raises `CapabilityMissing`.
Its offline fixture tests do not establish live access. Use only after an actual
browser adapter and evidence provenance have been independently verified.

Providers for image/gallery results must arrange storage through a separately configured artifact adapter before returning real decimal Odoo artifact IDs; the runtime does not invent remote storage or accept arbitrary URLs as artifacts. `Client.create_artifact` exposes the documented JSON/binary artifact API for that adapter, without automatic retry. Artifact upload itself has no idempotency key; reconcile an ambiguous upload separately. Domain references are opaque in the pure contract, but this HTTP deployment resolves dependency/artifact references only as positive decimal Odoo IDs. Reference existence, byte digest and MIME are checked; image decoding, dimensions and evidence truth are producer responsibilities. A QC `failed` result can be a successfully completed task: task execution success does not mean image approval.

Offline fixtures require both flags and an explicit JSON map of task types to domain result bodies:

```bash
.venv/bin/python -m optimization_worker once --offline --fixture /absolute/path/results.json
```

This still consumes an actual configured Odoo queue; use only a disposable/test instance. It does not automatically create cases or artifacts. `OfflineProvider(offline=True, results=...)` is also available for explicit programmatic test setup. Fixture files are bounded to 256 KiB. Nothing enables offline providers automatically during deployment.

## Recovery and cancellation

SQLite FULL synchronous commits record the claim (task/input hash/epoch), stable request ID and exact submission envelopes before network sends. Pending checkpoint/complete/fail/wait requests replay before a new claim, with the original lease, key and body. Lost acknowledgements do not invoke a provider twice. Unknown intent is checkpointed before dispatch; the local `calling` marker is committed before invoking a provider. A crash in that phase requires operator reconciliation even if the call may never have left the process. Returned results are persisted before terminal checkpoints and completion.

A separate thread sends heartbeats throughout input resolution, provider execution and submissions. A synchronous heartbeat fences a recovered claim before new work. Any heartbeat failure conservatively stops new submissions; stale/cancelled/expired leases are recorded locally as lost. The original server external ledger is preserved. A provider running in a daemon thread cannot be forcibly cancelled safely; after the default 90 second provider timeout, late results are ignored and the task waits with `external_result_unknown`. No automatic provider retry occurs. A terminal checkpoint records success/failure only for a known result. The server refuses completion/resume while external results are unresolved. Cancellation revokes the lease but does not undo external work.

An editor can use the documented `resume` or `cancel` endpoints; the Worker never invokes editor controls. This version deliberately has no force-unblock/reconciliation endpoint. Review provider-side records and arrange an audited reconciliation implementation before clearing unknown outcomes. Copying/deleting journals or merely resuming a task is not reconciliation. A task lost before its first provider invocation can be reclaimed by the server after expiry, as allowed by its ledger policy.

## Verification

```bash
.venv/bin/python deploy/mercado/tests/run_optimization_worker.py
.venv/bin/python deploy/mercado/tests/run_unit.py
.venv/bin/python deploy/mercado/tests/run_optimization_odoo.py --http
node --test deploy/mercado/mcp/test/registry-http.test.js
```

The Worker suite uses an in-process loopback HTTP server and controlled providers, plus subprocess CLI checks. The isolated Odoo runner owns a fresh private PostgreSQL cluster and runs an actual Worker HttpCase through the real claim/snapshot/heartbeat/complete routes. Neither command reads `.local/odoo.conf` or changes the existing Mercado database. The runtime does not construct a task graph, publish proposals, mutate live marketplace products, execute campaigns or measure business lift. Those capabilities require separate verified external contracts and live acceptance.

Native Codex session execution is available through `loop --session` and the
[host-session protocol](HOST_SESSION.md). It requires an interactive host with
actual browser/vision/image tools; Python queues durable requests and imports
real artifacts, and does not impersonate or invoke native session tools.
