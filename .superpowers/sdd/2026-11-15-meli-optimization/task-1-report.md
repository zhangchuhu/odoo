# Task 1 report

Status: complete; pure contracts, policies, and namespace-isolated behavior tests implemented.

## Public signatures and return behavior

- `DomainError(code, message)`: ValueError subclass exposing `.code` and `.message`.
- `canonical_hash(value) -> str`: SHA-256 hex over sorted-key, compact UTF-8 JSON preserving Unicode and array order. Rejects NaN/infinity, non-string keys, cycles, Python-only types. Numeric representations remain significant (1 and 1.0 differ).
- `validate_input(task_type, payload) -> payload`
- `validate_result(task_type, result) -> result`
- `validate_title_candidates(result) -> result`
- `validate_research_candidates(result) -> result`
- `validate_gallery(result) -> result`
- `validate_transition(current, target) -> None`
- `validate_lease(state, token, epoch, expires_at, now) -> None`

All validators raise DomainError on rejection, preserve the original JSON object (no normalization/mutation), and keep extension fields. Codes: `invalid_json`, `unsupported_task`, `invalid_input`, `invalid_result`, `invalid_transition`, `invalid_lease`. These are pure body contracts, not worker transport envelopes. Exact accepted schemas are in contracts.py's module docstring; execution policy in policies.py's docstring.

## Contracts for the dependent ORM task

Every input/result body requires `schema_version: "1"`. Input fields:

| task_type | Required fields |
|---|---|
| product.facts | product_snapshot_id |
| research.keywords | facts_artifact_id, site_id |
| research.bestseller | facts_artifact_id, keywords_artifact_id, site_id |
| gallery.collect | reference_item_id, selected_variant (nonempty object) |
| title.generate | facts_artifact_id, keywords_artifact_id, language |
| image.generate | source_artifact_ids (nonempty unique string list), target_artifact_id, generation_plan_id |
| image.qc | image_artifact_id, generation_plan_id |

All other fields above are nonempty strings. All result bodies have an `evidence` list of `{id, source, observed_at}`; arbitrary opaque IDs, nonempty origin reference, ISO UTC timestamp. Every claim/image/check has a nonempty `evidence_ids` list resolving to this catalog. Unknown metrics are explicitly null, not guessed. Source URLs and artifact IDs remain opaque references; trusted storage/producer must verify them.

Result-specific fields:

- product.facts: `facts: [{name, value, evidence_ids}]`, `uncertainties: [string]`.
- research.keywords: `status`, `keywords: [{text, evidence_ids}]`.
- research.bestseller: `status`, `candidates`. Candidate shape: platform/item_id/url/raw_url/query/selection_reason nonempty strings; position integer >=1 or null; is_ad bool or null; sold_display/image_url/visual_category string or null; sold_lower_bound/sold_upper_bound/review_count integer >=0 or null; rating number >=0 or null; observed_at UTC ISO timestamp; evidence_ids. Duplicate platform/item_id rejected. Ordered sales bounds; raw sold display untouched.
- Both research types: status `complete` needs >=1 result; `partial` needs >=1 result and nonempty reason; `empty` needs zero results and nonempty reason.
- title.generate: `candidates: [{text, evidence_ids}]`, exactly 10, unique under casefold/whitespace folding. No length cap.
- gallery.collect: reference_item_id, selected_variant (nonempty object), observed_at (UTC ISO), gallery_manifest_hash (64 hex), completeness_status (complete/partial), nonempty images; partial requires reason. Image: image_id/order/role/artifact_id/source/evidence_ids/mime_type/width/height/sha256. order >=0, dimensions >0, MIME image/*, digest 64 hex. Image IDs and orders unique.
- image.generate: request_id, image (same shape as gallery image).
- image.qc: image_artifact_id, status (passed/failed/review), nonempty checks [{name, status: passed/failed/unknown, evidence_ids}]. Names unique. Passed requires all passed; failed requires at least one failed; review requires at least one unknown.

Lease state argument is a snapshot mapping `{state: "running", lease_token: str, lease_epoch: int>=1}`. Token and epoch are submitted values; expires_at must come from trusted persistence. Both times are aware UTC datetime; expiry must strictly exceed now. Stale token/epoch, bool epochs, and inactive state reject.

Transitions: draft->queued/cancelled; queued->running/waiting/cancelled; running->succeeded/retry_wait/waiting/failed/cancelled; retry_wait->queued/cancelled; waiting->queued/cancelled/failed. Terminal states and self transitions reject.

## RED/GREEN evidence

Read test-driven-development/SKILL.md and writing-good-tests.md before writing tests. Tests exercise actual pure functions, no service mocks. Namespace package `optimization_unit` follows the existing runner's package-alias approach and bypasses addon initialization.

- RED: `.venv/bin/python deploy/mercado/tests/run_unit.py`: 36 run, 10 new assertion failures (`pure optimization domain implementation is missing`), existing 26 pass. Captured `/tmp/meli-task1-red.log` before creating production modules.
- GREEN: same complete entrypoint: 36 run, OK. Captured `/tmp/meli-task1-green.log`.
- Additional isolated import proved no `odoo` import/initialization; `git diff --check` passed.

Behavior tests cover canonical ordering, array order, Unicode, non-JSON rejection, every input task and missing field, unsupported task input/result, illegal transitions, lease expiry/token/epoch/state/time failures, title count/deduplication/references, unbounded title length, research explicit empty/partial states/raw sales/unknown metrics, invented evidence, typed research metrics, gallery metadata/order/references, all remaining result variants and QC contradictions.

## Changed files

- deploy/mercado/addons/meli_optimization/domain/__init__.py
- deploy/mercado/addons/meli_optimization/domain/contracts.py
- deploy/mercado/addons/meli_optimization/domain/policies.py
- deploy/mercado/tests/test_optimization_domain.py
- deploy/mercado/tests/run_unit.py (one added suite; existing semantics retained)
- this report

## Self-review and concerns

No Odoo/model imports in domain, wall-clock reads, live service calls, database changes, Feishu, or changes under outputs/. Domain imports succeed without Odoo. Invalid input is rejected before comparisons or field access; bool is excluded from numeric metrics/epochs/dimensions. Validators preserve all unknown fields, missing metric values, and raw displays.

Pure validation verifies evidence reference integrity, metadata types, and logical consistency; it cannot establish that archived evidence actually supports a statement, decode an image, verify artifact existence, or verify a manifest digest against actual image bytes. Those checks belong to trusted producers/commit services. Gallery digest format is validated; content digest recomputation is intentionally left to the manifest producer because digest payload canonicalization must be shared there. Title semantic differentiation, factual accuracy, brands/promotions/language and current platform write limits need evidence-backed policy/provider checks; this contract enforces structural count and normalized distinctness only.
