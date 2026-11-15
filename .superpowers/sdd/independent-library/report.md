# Independent collection and company image library

Implemented in native Odoo OWL workbench using existing navy design. Production/local application database has not been touched; installation/upgrade remains with parent after review. No platform traffic or paid generation calls were made. `outputs/` was preserved.

## Schema and boundaries

- `meli.optimization.case.collection`: hidden research case flag. Research cases and their internal batches are omitted from product optimization listings. Collection snapshot retains user garment description, source URL, site, optional uploaded reference image artifact IDs.
- `meli.optimization.library`: immutable company-scoped entry; name/item, site, case/task/snapshot FKs, exact result hash, full gallery JSON including selected SKU, completeness, observed time, evidence, source URLs and image digest/archive refs. Unique `(task_id, result_hash)` makes imports idempotent, including concurrent imports serialized by source case lock.
- Artifact `library_origin_id` and `library_artifact_id`: immutable pointers from destination clone to original library entry/archive. Generic artifact create and context defaults cannot assign these pointers.
- Library ACLs mirror worker/editor read access; global company record rule applies. Business writes require editor. Same-company check additionally rejects transfers across two companies even when both companies are authorized.
- Import requires current source snapshot, succeeded gallery task, exact supplied/current result hash, exact input/output reference item/SKU, valid gallery contract, actual archived bytes owned by the source case/company, matching digest, actual decodable image MIME/dimensions, HTTP(S) source URL without credentials. No user URL is fetched.
- Subsequent generation keeps the immutable imported library reusable across cases even if the source research case later advances. It rechecks archived image content and copies bytes to the destination case, preserving origin pointers.

## Session RPC APIs on `meli.optimization.case`

- `workbench_collection_create(values)` → detail. Values: name, garment (required), site_id, source_url, optional image_data/image_mime. Creates research snapshot and queues product.facts through existing fenced task contracts.
- `workbench_collections()` → latest 200 collection rows.
- `workbench_upload_garment(case_id, base64_data, mime_type)` → artifact ID; local upload only, supported PNG/JPEG/WebP, checked bytes, maximum 8 MiB.
- `workbench_library_import(case_id, task_id, result_hash)` → immutable library ID.
- `workbench_library(query='', site='')` → latest 200 matching entries; query searches reference item; site exact match.
- `workbench_library_generate(case_id, library_id, ordered_indices, source_artifact_ids)` → task IDs, generation plan artifact ID, exact target count. Rejects empty/duplicate/out-of-range indices, missing/non-image/wrong-case garment refs and different-company library. Creates one image.generate task per selected target. Frozen plan includes destination snapshot, original library result hash, ordered selection, source refs, cloned target refs, exact count and identity/pose/accessory/scene/light/framing preservation scope.
- Existing workbench_detail returns collection flag and clone provenance fields. Existing Worker `prepare` resolves uploaded collection source image refs into its verified artifact context. Existing external capability/result/fencing behavior is retained.

## UI and manual flow

1. Sidebar **独立爆款采集** → **新建独立采集**. Enter source garment description/link/site and optional uploaded garment image. No optimization product/shop/SKU is required.
2. Existing real Worker tasks organize facts → archive → keyword research → archive → bestseller research; choose gallery task and exact reference item/SKU. Task state/reason remains visible.
3. Completed gallery result → **保存已验证图片到公司图库**. Missing bytes, stale results and mismatched metadata are rejected server-side.
4. Sidebar **公司图片库** offers item/site filters, archive-backed cards, completeness and detail with SKU/time/source URL/digest/full evidence.
5. A different product case → **图片与质检** → **从公司图库选择换装目标**. Select specific gallery images and independently upload/select the source garment. Modal states the exact number of per-image tasks and frozen preservation scope.

## RED/GREEN evidence

Initial actual Odoo RED: three new ORM tests failed with missing workbench_collection_create API (`/tmp/independent-library-red.log`). Initial node RED: library selection method missing (`/tmp/library-ui-red.log`).

Additional observed RED cycles:
- Arbitrary bytes with matching declared digest/MIME were accepted before real content checking (`/tmp/library-content-red.log`, ValidationError not raised).
- Worker source image was absent from verified context (`/tmp/library-worker-red.log`, KeyError 99).
- Non-web provenance URL accepted before URL validation (`/tmp/library-url-red.log`).
- Late library chooser reopened after navigation before context guard (`/tmp/library-navigation-red.log`).
- Hidden research batch leaked into optimization list before batch filtering (`/tmp/library-batch-red.log`).
- Visual inspection caught an incompatible Sass min() expression despite functional assertions passing. Direct libsass compilation reproduced the error; corrected to compatible grid syntax, and browser now asserts native navy computed styling and no horizontal modal overflow. An earlier browser run also exposed a test racing ingestion completion; it now waits for actual success notification before navigation.

Final verification results and screenshots are recorded below after completion.

## Limits

- Repository has provider contracts and explicitly offline fixture replay, with no built-in live bestseller/browser/image vendor provider. UI explicitly says this; tasks do not manufacture successful live results. A configured capable Worker/provider is required for external collection/generation. Missing-provider wait reasons remain visible.
- Chrome/ORM fixtures are unmistakably test-only (`TEST ONLY`, `example.test`, synthetic 1×1 PNG). They demonstrate archival/selection/provenance, not real live bestseller evidence or generated clothing photos. No fixture is seeded into production/local application database by this work.
- “Verified” here means checked task contracts and actual archived image integrity/ownership; it does not establish external commercial truth beyond provider-supplied evidence.
- Collection and library lists currently return at most 200 latest rows; no pagination or category search. Each generation submission creates a new frozen plan/tasks; only gallery imports are idempotent.

## Final verification

- `.venv/bin/python deploy/mercado/tests/run_optimization_odoo.py --http --browser`: **32 post-install tests, 0 failed, 0 errors**, including real Chrome independent flow, native CSS computed color and modal overflow assertions. Final evidence log: `/tmp/independent-library-verified.log`.
- `.venv/bin/python deploy/mercado/tests/run_unit.py`: **37 tests pass**.
- `.venv/bin/python -m unittest discover -s deploy/mercado/optimization_worker/tests`: **28 tests pass**.
- `node --test deploy/mercado/tests/test_workbench_ui.mjs deploy/mercado/tests/test_workbench_actions.mjs`: **7 tests pass**.
- Direct libsass compilation, Python compileall, XML parse and `git diff --check`: pass.
- Disposable runner warnings are existing missing manifest author keys, legacy `_sql_constraints` in other modules, and alert-role lint warnings. No CSS compilation warnings/errors remain in final run.

Screenshots copied from the final passing real Chrome run and visually inspected:

- [Independent collection input](browser/independent_form.png)
- [Persistent company library](browser/persistent_library.png)
- [Different-case selection, separate garment and exact scope](browser/cross_case_selection.png)

These screenshots display a deliberately synthetic test image, not a live product photograph.

Installation requires normal `meli_optimization` module upgrade to create the library table and new fields/rule/ACL; this task intentionally did not perform it against the existing application database.
