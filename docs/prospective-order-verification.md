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
