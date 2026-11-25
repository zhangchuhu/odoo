# ERP BFF delivery

Implementation is in `addons/meli_bff`; setup, routes and examples are in
`addons/meli_bff/README.md`. The existing `meli_accounts` credential service now
persists OAuth rotation separately from caller business transactions.

## Verification

- 13 pure Python protocol/transport tests passed.
- 52 Odoo integration and existing connector tests passed, zero failures/errors.
- A fresh `meli_bff_test_*` database installation and subsequent upgrade were tested.
- Independent read-only review completed. Query encoding/order, encoded path
  handling and secondary-company audit attribution findings each received a failing
  regression test and a passing fix; the full suite then passed.
- Full log: `/tmp/meli-bff-tests-k0849052/odoo-tests.log`.
- Reproducible runner: `tests/run_bff.py`; it retains isolated databases and artifacts.
- Production installation completed on 2026-09-29: `meli_accounts` upgraded to
  `19.0.1.4.0`, `meli_bff` installed at `19.0.1.0.0`, and `odoo-mercado` restarted.
- Local and public HTTPS smoke checks passed: missing API Key 401, shop list 200
  (2 visible shops), shop detail 200, unsupported path 404. No credential fields
  appeared in shop responses. The temporary verification API Key was revoked.
- Database, filestore and module source backup:
  `backups/bff-20260929-013954/`; private artifacts are excluded from Git.
- No live Mercado Libre product writes were performed.

## Implementation decisions

- Reused the existing `/tmp/erp-bff-work` isolated addon copy rather than rebuilding
  a full worktree. Only the reviewed files are synchronized; original touched files
  are retained in `/tmp/erp-bff-work/delivery-baseline`. This requires targeted file
  comparison rather than a historical feature-branch diff.
- Import-only regression tests mock token acquisition because their fixtures are
  uncommitted. Dedicated real-cursor tests cover credential persistence/concurrency.
  The company-rule fixture explicitly grants the manager role it requires.
- Internal sudo operations by Odoo's built-in technical superuser remain supported
  for scheduled jobs. Inactive human users and inactive API callers are rejected.
- Standard-library `http.client` preserves exact encoded queries; requests/urllib3
  normalize them. Connection cleanup, gzip bytes, truncation, repeated headers,
  timeouts and redirect refusal are covered. OAuth refresh still uses requests.
- Public HTTPS routing and production API authentication are verified. Live
  Mercado Libre product integration still uses simulated upstream coverage.
- Trusted upstream business responses retain their original bytes; speculative
  rewriting for a hypothetical bearer-token echo was not introduced. No supported
  endpoint returning OAuth secrets was identified during review.
- Existing OAuth callback and legacy `_get()` behavior remain unchanged and were
  not comprehensively re-audited; existing connector regression passes.
- The planned two-second credential lock wait is retained. Slow contention can
  return an error requiring an explicit later retry; platform writes never replay.

No review findings remain deferred. The repository includes the complete local
`meli_accounts` dependency and BFF addon. The external `meli_oerp` connector remains
an installed dependency at the revision recorded in `SOURCE.txt`.
