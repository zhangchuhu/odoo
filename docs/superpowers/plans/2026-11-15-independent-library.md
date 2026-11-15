# Independent collection and image library implementation plan

> **For agentic workers:** Execute inline with TDD; no subagents.

**Goal:** Collect independently of optimization products, retain verified gallery images in a company library, and select them for later garment swaps.

**Architecture:** Hidden research cases retain existing fenced Worker contracts. An immutable library entry points to the checked gallery result and archived binary artifacts. Selecting targets clones verified bytes into the destination case, with immutable provenance and a frozen generation plan.

**Tech Stack:** Odoo ORM, OWL, existing navy workbench, disposable PostgreSQL/Chrome tests.

**Spec:** User requirement supplied in current task.

## Global constraints
- No platform or paid actions; no server fetch of user URLs.
- Never fabricate collection evidence. Missing providers retain explicit capability reason.
- Company isolation, stale result rejection, immutable provenance, idempotent ingest.
- Source garment inputs separate from library target selection; one task per selected target.
- Preserve outputs/; parent handles production installation after review.

## Review focus
- Forged/missing archived bytes must reject ingest.
- Changed source snapshot/result must reject ingest.
- Authorized multi-company user cannot move images between companies.
- Repeated import/selection must not lose original provenance.
- Partial gallery is labeled partial; generation scope equals selection.

### Task 1: Standalone collection and persistent library
Files: models/library.py, models/__init__.py, security/*, tests/test_library.py.
Interfaces: case.workbench_collection_create(values), workbench_collections(); workbench_library_import(case_id, task_id, result_hash); workbench_library(query, site).
- [x] Write real ORM tests for standalone collection, verified import, idempotency, stale/unverified/company rejection.
- [x] Run disposable ORM runner; observe missing APIs RED.
- [x] Implement immutable entry with result snapshot/hash, source URL/item/SKU, image digest and archive references.
- [x] Run ORM runner GREEN.

### Task 2: Target selection and frozen generation scope
Interfaces: workbench_library_generate(case_id, library_id, indices, source_artifact_ids).
- [x] Test separate source garment, destination clone provenance, exact selected target tasks and frozen plan; reject cross-company selection.
- [x] Implement validated clone and immutable plan/task creation.
- [x] Run ORM and Worker regression suites.

### Task 3: Native OWL independent entries and browser proof
Files: static/src/workbench.{js,xml,scss}, tests/test_workbench_browser.py.
- [x] Add real browser test standalone collection → verified fixture ingest → library → different case target selection.
- [x] Add collection entry/form, library filter/cards/detail, selection modal with garment selector and accurate scope.
- [x] Run Chrome and existing UI/action regressions; capture screenshots.
- [x] Record schema/APIs, RED/GREEN evidence, screenshots and limits in .superpowers/sdd/independent-library/report.md; commit.
