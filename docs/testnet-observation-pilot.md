# Bounded testnet observation pilot

Status: implemented locally; not running, scheduled, or deployed.

`scripts/execution_evidence_pilot.py` is a foreground runner, not a cloud
scheduler. It collects 96 consecutive 15-minute windows over an explicit
prospective 24-hour period. It uses the testnet info API only. Each observation
includes funding and current first-perp-DEX positions. No signing key is needed.
Writes use the existing collector's transactional evidence persistence.

Dry-run is the default. Writes additionally require `--write`, the process-local
`EXECUTION_EVIDENCE_ENABLED=true`, and a privately configured database URL.
`--env-file` imports only DATABASE_URL, not trading settings. No Vercel flags are
changed. Account scope is provided explicitly using `--account`.

The start must be in the future and within 15 minutes. Late observations over
60 seconds are reported as missed, never backfilled. A collector invocation is
limited to 60 seconds; any failure or ambiguous commit stops the pilot without
retry. The final scheduled window ends at start + 24 hours; a final invocation
may finish within its bounded grace/timeout after that boundary.

Run only one instance. Do not restart a stopped pilot with the same cohort:
first reconcile its reported window with the ledger. Keep the terminal and Mac
awake; laptop sleep is not reliable scheduling. Logs are JSON on stdout, with
counts only and an explicit terminal completion/failure event. No Telegram or
external alert delivery is implemented. Do not claim this is unattended cloud
monitoring.

No original order candidates are supplied, so fills remain unlinked. This pilot
tests collection/storage reliability, not signal ownership or profitability.
History completeness remains unverified and training eligibility stays false.
Never expand this pilot into the reserved October historical outcome cohort.

Before activation: review the code, select a future start, confirm exactly one
runner and an operator watching its output. Afterward review gaps and ledger
counts only. Scheduling, manager journaling, automatic test trades, and ML
activation are separate tasks and are not enabled by this runner.
