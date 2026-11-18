# Daily publication receipts

The active close-report path passes original persisted signal IDs to the
public, primary private and extra-private senders. With
PUBLICATION_EVIDENCE_ENABLED=true each nonempty report records a distinct
attempt and Telegram response. ACCEPTED is provider acceptance, not human
delivery, an order fill or training eligibility.

Destination claims hash the chat ID and exact message, scoped to deployment
environment, and expire after seven days. These claims require remote KV;
there is no local-file fallback. An uncertain send retains its claim.
An accepted send remains successful if receipt persistence fails. A later
duplicate claim returns false, not a fabricated successful receipt.

This intentionally prioritizes avoiding duplicate sends over automatic
recovery. Operators must inspect unresolved attempts; do not delete claims
or resend blindly. A changed message has a different claim. This is not an
unconditional exactly-once delivery guarantee.

Empty reports retain destination dedup but cannot link to signal evidence.
The existing outer daily claim and scheduling remain unchanged. No migration,
new trading behavior, historic backfill or research promotion is included.

Pre-release: production KV variable names and production scope were confirmed
without exposing values. This is configuration evidence only, not a live
KV connectivity test. Review runtime evidence after the next normal daily send.
