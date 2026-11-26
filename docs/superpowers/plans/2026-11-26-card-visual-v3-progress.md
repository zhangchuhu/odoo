# Card visual v3 execution ledger

The approved policy limits each query to three pages and its top 30 distinct cards with explicitly evidenced sales above 100. It assesses at most 30 detail-qualified products visually, ranks the entire assessed set, and separates products meeting the user-specified threshold from supplemental products. Existing v2 tasks retain their strategy.

Implemented in this worktree: card sales parsing and selection; page and batch checkpoints; browser and broker pagination and evidence capture; scoped recovery with fresh page verification; detail and visual evidence; result tiers and gallery handoff; Odoo task configuration, progress and display. Synthetic end-to-end tests exercise the workflow. The code is not yet deployed to the local running services, and live end-to-end verification remains outstanding.

An isolated headless browser previously received HTTP 403 despite the user's Chrome session having access. An existing task has Mercado search pages open in that Chrome; do not close or navigate those tabs. That isolated-browser observation does not establish whether the authenticated worker can perform the complete new workflow.

Before enabling the new strategy in the running environment, rerun the full test suites on the final code, check active tasks, deploy safely, and verify the complete authenticated browser collection and return flow. Do not interpret inaccessible pages as empty results.
