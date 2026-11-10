# Prospective order identity verification

`backend/prospective_order_verification.py` is a pure, inactive checker for one
opening or initial exit order. It accepts two immutable journal envelopes
(`payload`, `payload_sha256`), a separate orderStatus observation envelope, an
expected environment, and an expected hashed account identity.

It checks both hashes; exact INTENT/RESPONSE pairing; canonical UUIDs; unchanged
original candle representation; deterministic client order ID; explicit testnet
and account scope; a single acknowledged venue order ID; separate query identity;
venue order ID, client ID, instrument and reduce-only status; and recorded local
and venue timestamp ordering. Missing/unknown/malformed evidence fails closed.
No timestamps, client IDs or missing events are reconstructed.

The existing `manager_order_provenance.observe_order` can supply the independent
read envelope through an explicitly injected reader. This change does not invoke
that reader, create a connection, fetch venue data, load a cohort, persist a link,
change flags, apply migrations, place orders or enable a scheduler.

The network/account label is supplied by the read adapter: an orderStatus response
does not itself independently attest the queried account. Before a real check,
the caller must select an approved development cohort and bind the reader to the
correct testnet endpoint and account. Hashes check stored consistency, not
cryptographic provider authenticity. Read the persisted journals from a consistent
snapshot, not an arbitrary reconstructed dictionary.

Passing returns a candidate identity only. It always leaves attribution_verified
and training_eligible false, with gates for independent clock bounds, history
coverage, publication timing, order side and fill lifecycle. Existing journals
do not retain intended side, so this checker must not claim side verification.
No result is inserted into execution_order_links automatically.

Verification on 2026-11-10: 59 targeted tests passed, including all four intents,
wrong venue identity, Boolean/non-integer IDs and timestamps, wrong scope,
malformed/tampered evidence, journal gaps and equivalent-but-reformatted candle
timestamps. No production evidence or held-out outcomes were inspected.

Next gate: review this checker, then add the bounded read-only journal loader and
explicitly scoped read adapter. Account binding and intended side need independent
evidence before a complete end-to-end attribution claim is possible.

## Bounded journal loader — 2026-11-10

`prospective_journal_loader.load_journals` now reads an explicitly selected
opening or exit journal in a caller-owned read-only repeatable-read/serializable
transaction. Inputs require at most ten correlation IDs and ten signal UUIDs,
an exact environment/account scope and a window of at most 24 hours. It reads
metadata first, requires exactly INTENT and RESPONSE in scope, rejects payloads
over 64 KiB, and verifies payload hashes and agreement with stored row columns.
Incomplete, out-of-scope and corrupt pairs produce explicit exclusions.

`load_and_verify` accepts separately supplied observation envelopes keyed by
correlation ID and runs the pure verifier. It does not obtain those observations
or authenticate their source. Missing observations are exclusions, never inferred
from acknowledgements. There is no connection creation, configured credential
lookup, production caller, mutation or new migration. SQL/runtime failures remain
errors rather than fabricated successful audit results.

Local verification: 34 disposable PostgreSQL tests and 59 targeted synthetic
tests passed. Loader tests cover opening/exit composition, missing observations,
wrong account/environment, window exclusion, missing correlations, duplicate
IDs, excessive windows and transaction requirements. All tests use synthetic
data; production migrations and capture remain inactive.
