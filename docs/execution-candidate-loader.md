# Bounded persisted-order candidate loader

`backend/execution_candidate_loader.py` supplies a SELECT-only helper, not a
scheduled job, trading caller, collector activation, or ML eligibility gate.
It supersedes the earlier readiness note that no loader exists, but only for
**previously observed order links**, not unlinked historical trades.

The caller supplies a read-only repeatable-read/serializable transaction, 1–10
explicit signal UUIDs, environment, network, account hash, and an aware
observation interval of at most 24 hours. The interval filters the originating
evidence run's receipt time with an inclusive lower/exclusive upper boundary;
it is not the signal publication or order-placement interval.

The helper joins signals, execution_order_links and execution_evidence_runs,
checks the stable identity hash and retained venue order identity, and returns
at most 40 candidates. Overflow raises an error; there is no silent truncation.
Each candidate retains its exact client order ID, venue order ID, instrument,
intent, signal UUID, source run ID, and explicit scope. Missing scoped links
produce exclusions. There is no candle timestamp reconstruction or alternate-ID
search. No outcome columns, position payloads or signing credentials are read.

Returned candidates are NOT the timestamp-based input shape for
`execution_evidence.collect`; do not pass them there or invent candle_ts values.
They are inputs for a subsequent explicit identity-based provenance audit.
No automatic caller is installed. Training eligibility and attribution_verified
remain false. Stored provenance is a consistency check, not proof of provider
history completeness, cross-clock timing, or original application intent.

Synthetic PostgreSQL checks cover exact ID preservation, read-only snapshot
requirements, network/account/environment/time exclusion, malformed provenance,
bounded cohorts and result overflow. The existing 23 integration tests plus
the first three loader tests passed together; the final four loader tests
passed after adding the overflow case. Temporary clusters were shut down.

Next: compose these candidates with the existing publication/lifecycle audit in
the same read-only snapshot and report exclusion reasons. Keep missing original
links quarantined; a separate prospective capture design is needed to bootstrap
them. Scalp-trade identities are not automatically mapped into signals UUIDs.

## Composed audit — 2026-11-05

`execution_cohort_audit.audit_cohort` now performs that composition on the same
caller-owned read-only repeatable-read/serializable connection. The caller
supplies 1–10 explicit signal/opening-run/closing-run triples and candidate scope.
The wrapper checks boundary account/network metadata before loading positions,
requires every existing target-signal link to be admitted by candidate scope,
then invokes the publication/lifecycle audit without changing transactions.
Missing candidates, bad boundaries, extra out-of-scope identities, and typed
evidence failures produce explicit exclusions; SQL/runtime errors still fail the
call rather than being disguised as valid audit results.

Reports retain lifecycle issues and quarantine reasons, including unverified
clock alignment and independent history coverage. `training_eligible` and
`attribution_verified` remain false even when all consistency checks pass.
No new automatic caller, database migration, production access, or trade exists.

Verification: 27 disposable PostgreSQL integration tests passed; 124 selected
synthetic tests passed; diff whitespace check passed. Tests exercise successful
publication-before-fill composition, missing candidates, missing boundaries,
wrong-account boundary rejection, out-of-scope links, and evidence-error handling.

Next gate: review/package the local changes and define an explicitly permitted
prospective development cohort before running this composed audit against any
real account evidence. Missing original links cannot be recovered by this helper.
