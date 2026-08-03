# Automated execution — Phase 0 scope

Status: **proposed, not built.** This document is the plan, and the open
decisions at the end need answering before any of it is written.

---

## What Phase 0 is

**Record every order the system would have sent. Send nothing.**

Each time the monitor decides something — an entry filled, a target hit, a stop
moved — translate that decision into the exact orders an exchange would need,
and write them to a table. Never call an exchange. Never hold a key.

The point is that the translation layer is the part most likely to be wrong,
and it can be proven correct without risking a cent. By the time the v44 freeze
lifts, the execution path would be written, tested against months of real
decisions, and idle.

## What Phase 0 is not

It cannot tell you about fills, slippage, latency, partial fills, exchange
rejections, rate limits, or reconciliation drift. Those need a testnet
(Phase 1). Anyone reading a clean Phase 0 report as "the execution engine
works" has misread it — it proves the *intent* is right, not that the market
will co-operate.

---

## Why this maps cleanly onto what exists

`ALLOWED_TRANSITIONS` in `signal_store.py` is already an order lifecycle, and
`signal_monitor.evaluate` already emits exactly the events that would drive
order management:

| monitor action | orders it implies |
|---|---|
| *(signal created)* | place entry limit, `PENDING` |
| `ENTRY_FILLED` | place stop-market + three reduce-only TP limits |
| `TARGET_HIT` | that TP filled — amend remaining sizes |
| `STOP_MOVED` | amend the stop to the new level |
| `STOP_LOSS_HIT` | stop filled — cancel siblings |
| `EXPIRED` | cancel everything, flatten |
| `CANCELLED` / `NEVER_FILLED` | cancel the unfilled entry |

`evaluate` is already pure — candles in, actions out, no database. The
translation function will be pure in the same way, for the same reason: it is
the part that has to be provably right.

---

## The pieces

### 1. Migration 007 — `execution_intents`

One row per order the system would have placed, amended or cancelled.

| column | type | why |
|---|---|---|
| `id` | `uuid` | primary key |
| `environment` | `text` | same tagging as `signals`; a preview deploy must never pollute production intents |
| `signal_id` | `uuid` | FK to `signals` |
| `client_order_id` | `text` | deterministic, unique — see below |
| `role` | `text` | `entry` / `stop` / `tp1` / `tp2` / `tp3` |
| `action` | `text` | `PLACE` / `AMEND` / `CANCEL` |
| `side` | `text` | `BUY` / `SELL` |
| `order_type` | `text` | `LIMIT` / `STOP_MARKET` |
| `price` | `numeric(30,12)` | never float — same rule as every other price here |
| `quantity` | `numeric(30,12)` | null until sizing is decided (see open questions) |
| `size_fraction` | `numeric(9,6)` | share of the position: 0.5 / 0.3 / 0.2 from `SCALE_OUT_SHARES` |
| `reduce_only` | `boolean` | a TP or stop must never be able to open a new position |
| `triggered_by` | `text` | the monitor action that caused it |
| `candle_close_time` | `timestamptz` | the bar the decision was made on — the idempotency anchor |
| `decided_at` | `timestamptz` | wall clock, for latency analysis only |
| `venue` | `text` | `paper` in Phase 0 |
| `payload` | `jsonb` | the request body that *would* be sent, allow-listed |
| `idempotency_key` | `text` | unique index |

**`payload` is allow-listed, exactly like `signal_snapshot`.** No credentials,
no headers, no signatures — those never exist in this codebase in Phase 0 and
must never be stored in any phase.

### 2. `backend/execution.py` — pure translation

```
plan_orders(signal, targets, action, config) -> list[intent]
```

No database, no network, no clock beyond what is passed in. Same contract as
`validate_price_structure` and `evaluate`: the rules that protect real money
are pure, so they can be tested exhaustively.

**`client_order_id` is derived, never generated.** Something like
`cm-{signal_id[:8]}-{role}` — deterministic from the signal and the role, so a
retry after a timeout re-derives the same ID and the exchange rejects the
duplicate instead of opening a second position. This is the single most
important property in the whole design, and it is the same claim-before-act
discipline already used by `_dispatch_once` and the bar-derived keys in
`pattern_store`.

Note the length limit: Binance caps `newClientOrderId` at 36 characters, so the
format has to be checked against the venue before Phase 1.

### 3. Integration — one call site

Inside the monitor's existing `apply_actions`, after the state change commits:
translate the same actions and record the intents. Behind
`EXECUTION_SHADOW=true`, defaulting to **off**.

Failure to record an intent must never affect the signal record — same rule the
pattern log already follows: *losing a log entry must never stop a signal being
published.*

### 4. `GET /api/execution/intents` — read-only

For eyeballing what would have happened. No mutation endpoint exists in Phase 0
because there is nothing to mutate.

---

## The guards, as tests

These are the tests worth writing first, because they encode what must never
happen:

- **No credential ever reaches the database.** Grep the built payload for
  anything key-shaped, the same way `signal_snapshot._is_secretish` does.
- **`client_order_id` is stable.** Same signal and role, called a hundred
  times, one ID.
- **`client_order_id` is unique across roles and signals.** Two TPs on one
  trade must not collide — the same class of bug found in `pattern_store`'s
  idempotency key this week, where type and direction were missing and two
  patterns on one bar overwrote each other.
- **Every exit order is `reduce_only`.** A TP that can open a position is how
  an automated system ends up accidentally short.
- **Shares come from `SCALE_OUT_SHARES`**, so the orders match what the
  dashboard tells the reader and what `exit_fraction` records. One source.
- **Sizes sum to the position.** No rounding leaves a dust remainder open.
- **A LONG's stop is below entry and its targets above** — reuse
  `validate_price_structure` rather than restating geometry.
- **Nothing is emitted for a `NEUTRAL` signal.**
- **The whole path is a no-op when `EXECUTION_SHADOW` is unset.**

---

## What it costs

Roughly five intents per signal lifecycle at eighteen signals a day — about
90 rows a day, comparable to `signal_events`. Not a storage concern.

No new external dependency, no new cron, no new secret.

---

## Open decisions — these need answering before code

1. **Venue for Phase 1.** Bybit or Binance? It changes the payload shape, the
   client-order-id limit, and whether native OCO is available. Recommendation:
   **Bybit testnet** — the testnet is a closer match to production than
   Binance's, and the unified account model handles reduce-only cleanly.

2. **Sizing model.** Three options, and this is a trading decision, not a
   technical one:
   - fixed notional per trade (simplest, ignores risk)
   - fixed % of equity (scales, still ignores stop distance)
   - **fixed risk per trade** — `qty = risk_amount / stop_distance` (standard,
     and it automatically shrinks the wide-stop trades that dominated the loss
     analysis)

   Recommendation: fixed risk. It is the only one that makes a 1% stop and a
   4% stop cost the same when wrong.

3. **Entry order type.** Limit at the entry price, or market once price
   touches? Limit is cheaper and may not fill; market always fills and pays
   the spread. The current `PENDING → OPEN` model assumes a resting limit.

4. **Max concurrent positions**, and whether correlated symbols count as one.
   The PAXG/XAUT pair that published together this week would be two real
   positions in one bet.

5. **Spot or perpetuals.** Leverage is already suggested per signal, which
   implies perps, but that has never been an explicit decision.

---

## Sequencing, and one caveat

Phase 0 can be built during the v44 freeze: it touches no scoring path and
places no orders.

But it should not be *followed* by Phase 1 until the freeze has answered its
question. v44 currently shows 8 closed trades, 0 wins, −0.006%/trade.
Automation multiplies whatever the strategy already does, in both directions —
building the machinery now is prudent, pointing it at an unproven edge is not.
