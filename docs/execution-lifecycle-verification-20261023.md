# Lifecycle linkage verification — 2026-10-23 SGT

Scope: local code and synthetic fixtures only. No production migration, venue
requests, Neon reads/writes, publication messages, live orders or held-out data.

Implemented `backend/execution_lifecycle_evidence.py`: an in-memory checker for
one signal, account, network, instrument and flat-to-flat position cycle.
It links original order IDs, fill IDs and funding event IDs without calculating
P&L. Supplied snapshots are checked for matching scope and flat boundaries.
Provider/DB provenance still requires a future loader; this function does not
authenticate caller-supplied evidence.

Targeted tests: **31 passed** (20 lifecycle tests and 11 position-chain tests).
Full regression suite: **3,043 passed, 112 skipped**. Database integration tests
were not rerun in this pure-checker change; earlier migration checks are recorded
in `execution-evidence-verification-20261022.md`.
Cases cover long/short partial exits, duplicate evidence, foreign/missing order
links, incorrect opening intent, nonflat boundaries, open positions, missing
funding, funding-size mismatches, collection-window gaps, events outside their
request windows, ambiguous timing, fee currency and multiple position cycles.

Every output remains `training_eligible=false`. A passing linkage check is not
proof of complete venue history, publication-before-fill timing, model accuracy,
or profitability. No automatic production path invokes this module.

Next gate: load immutable records through a read-only, bounded loader; validate
their hashes and provenance; join publication timing; retain exclusions until
independent completeness and prospective-cohort checks pass.
