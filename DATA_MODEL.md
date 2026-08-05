# CryptoMonk — Signal Tracking Guide & Data Dictionary

How a recommendation becomes a stored trade, what every column means, and how to
query it.

Everything here is generated from the live schema (migrations `001`–`006`) and
the code that writes it. Where a column exists but nothing writes it yet, this
says so.

---

## 1. The shape of the thing

```
   generate_signal()            _compute_recommendations()        signal_publish
   indicators + patterns   ──>  rank, filter, top 3          ──>  persist_recommendation
                                                                        │
                                                                        ▼
                                                                   signals (row)
                                                                   + targets
                                                                   + snapshot
                                                                   + CREATED event
                                                                        │
   GitHub Actions, every 30 min                                         ▼
   POST /api/signals/monitor   ──>  signal_monitor.evaluate  ──>  lifecycle events
                                    (candles in, actions out)         + status
                                                                        │
   Dashboard                                                            ▼
   GET /api/signals/tracker    ──>  signal_tracker.build_row  ──>  the table you read
```

Three rules hold the whole design together:

1. **The decision is immutable.** Direction, entry, stop, targets and the
   decision snapshot are written once and never rewritten. Changes are appended
   as events.
2. **Every decision is keyed on the candle that caused it**, never on wall-clock
   time. That is what makes the monitor safe to re-run, safe to interrupt, and
   safe to run twice at once.
3. **Absence is not zero.** A missing live price, an unmeasured excursion and an
   unresolved trade are all recorded as NULL, never as `0`.

Since **v44**, `persist_recommendation` only runs on a **4H candle close** — six
publication slots a day (00, 04, 08, 12, 16, 20 SGT/UTC alike), three trades
each, so at most **eighteen** rows a day. A recompute between those bars still
serves the set but writes nothing, reporting
`persistence.skipped_reason = "NOT_A_PUBLICATION_BAR"` with `all_actionable`
still true and `error_code` null — a skip is not a failure. See
[INDICATORS.md § 4H Publication Cadence](INDICATORS.md) for the ranking change
that shipped with it.

Reading and publishing are separate paths. `/api/cron/publish` writes;
`/api/recommendations` only reads `signals` back. Nothing is served that is not
first recorded, so the recommendation cards and the Signal Tracker cannot
disagree about what was published.

---

## 2. Lifecycle

```
PENDING ──(price trades AT the entry)──> OPEN ──> PARTIAL_TP ──┬─> TP_HIT     (terminal)
   │                                       │          │        ├─> SL_HIT     (terminal)
   │                                       └──────────┴────────┼─> CLOSED     (terminal)
   │                                                           ├─> EXPIRED    (terminal)
   └──(entry never reached in 24h)──> CANCELLED (terminal) ────┘
```

| Status | Meaning | Counts as a trade? |
|---|---|---|
| `PENDING` | Published, **working order**. Price has not reached the entry. No position, no P/L. | No |
| `OPEN` | Entry filled. The trade has started. | Yes |
| `PARTIAL_TP` | At least one target banked, more remain. | Yes |
| `TP_HIT` | Final target reached. Terminal. | Yes — **WIN** |
| `SL_HIT` | Stop hit. Terminal. | Yes — **LOSS** |
| `CLOSED` | Closed manually or by rule. Terminal. | Yes — judged on the number |
| `EXPIRED` | Went sideways past `max_age_hours` (72h). Closed at the last price seen. Terminal. | Yes, but **not** win or loss |
| `CANCELLED` | Withdrawn. `close_reason = 'NEVER_FILLED'` means the entry was never reached. | **No** — never a trade |

**Terminal means terminal.** A terminal signal can never return to a working
state, and a signal can never record both `TP_HIT` and `SL_HIT`. Enforced by
`ALLOWED_TRANSITIONS` in `backend/signal_store.py`, plus a row lock
(`SELECT … FOR UPDATE`) and compare-and-set on every update.

**Why `EXPIRED` is not a loss:** it never reached a target and was never
stopped. Counting it either way would corrupt the win rate. It is excluded from
the win-rate denominator while its P/L still counts towards the averages.

**Why `NEVER_FILLED` is not a trade:** the entry never traded, so there was no
position to win or lose. It is excluded from the win rate, the averages *and*
the P/L.

---

## 3. Data dictionary

### 3.1 `signals` — one row per published signal

| Column | Type | Null | Meaning |
|---|---|---|---|
| `id` | `uuid` | no | Primary key, `gen_random_uuid()`. |
| `symbol` | `text` | no | Upper-case ticker (`BTC`, `TAO`). CHECK enforces upper-case. |
| `exchange` | `text` | no | Where the candles came from (`binance`, `okx`). Part of the identity key. |
| `timeframe` | `text` | no | `2H` for published recommendations. |
| `direction` | `text` | no | `LONG` or `SHORT`. CHECK-constrained. |
| `strategy_name` | `text` | no | `mtf_confluence_top3`. |
| `strategy_version` | `text` | no | e.g. `v45_4h_avg`. Bumped whenever the maths changes, so old and new signals stay independently analysable. |
| `candle_open_time` | `timestamptz` | no | Open of the closed candle the decision was made on. |
| `candle_close_time` | `timestamptz` | no | Close of that candle. **Part of the idempotency key.** |
| `generated_at` | `timestamptz` | no | When the recommendation was published. Drives the batch/slot grouping. |
| `entry_price` | `numeric(30,12)` | no | The level the order works at. |
| `stop_loss` | `numeric(30,12)` | no | The ORIGINAL stop. **Never rewritten** — the only record of the risk first taken. |
| `current_stop_loss` | `numeric(30,12)` | yes | Where the stop actually sits now. NULL = never moved. |
| `stop_moved_at` | `timestamptz` | yes | When it was last moved. |
| `confidence_score` | `numeric(10,4)` | yes | Strength at publication (0–100). |
| `status` | `text` | no | See §2. |
| `entry_filled_at` | `timestamptz` | yes | When price reached the entry. NULL while `PENDING` — or on any row written before migration `003`. |
| `entry_fill_price` | `numeric(30,12)` | yes | The entry level, recorded at fill. |
| `mfe_pct` | `numeric(18,8)` | yes | **Maximum favourable excursion** — furthest the trade ran in your favour, in percent, measured from the fill. Only ever widens. |
| `mae_pct` | `numeric(18,8)` | yes | **Maximum adverse excursion** — furthest against you. Negative. Only ever widens. |
| `closed_at` | `timestamptz` | yes | When it became terminal. |
| `close_price` | `numeric(30,12)` | yes | Exit price of the final piece. |
| `realized_return_pct` | `numeric(18,8)` | yes | Realised return, **weighted across scale-outs** — each hit target takes its share at the price it was hit, the remainder closes at `close_price`. |
| `close_reason` | `text` | yes | `TARGET_HIT`, `STOP_LOSS_HIT`, `EXPIRED`, `NEVER_FILLED`, `MANUAL_CLOSE`. |
| `archived_at` | `timestamptz` | yes | Soft archive. Nothing is ever deleted. |
| `environment` | `text` | no | `production` / `preview` / `local`. Default `production`. Part of the identity key. |
| `created_at`, `updated_at` | `timestamptz` | no | Row bookkeeping. `updated_at` drives the monitor's stalest-first rotation. |

### 3.2 `signal_targets` — the take-profit ladder

| Column | Type | Null | Meaning |
|---|---|---|---|
| `id` | `uuid` | no | Primary key. |
| `signal_id` | `uuid` | no | FK → `signals(id)`, `ON DELETE CASCADE`. |
| `target_number` | `integer` | no | 1, 2, 3 — ordered away from entry. Unique per signal. |
| `target_price` | `numeric(30,12)` | no | The level. |
| `hit_at` | `timestamptz` | yes | When it was reached. NULL = still ahead. |
| `hit_price` | `numeric(30,12)` | yes | Price recorded at the hit. Can differ from `target_price` on a gap. |
| `exit_fraction` | `numeric(9,6)` | yes | Share of the position this rung takes (0–1). Written from `signal_store.SCALE_OUT_SHARES` — 0.5 / 0.3 / 0.2 on the standard three-rung ladder, which is what the dashboard tells the reader to sell. NULL = no published plan for that ladder length, split evenly. Rows written before 2026-08-02 were NULL; migration `006` fills them in and rescores the closed trades, so the whole history is now on one convention. |
| `created_at` | `timestamptz` | no | |

### 3.3 `signal_indicator_snapshots` — what the strategy saw

Exactly **one** per signal (UNIQUE on `signal_id`). This is what makes
post-trade analysis possible: it records the decision inputs, not today's market.

| Column | Type | Null | Meaning |
|---|---|---|---|
| `indicator_values` | `jsonb` | no | Allow-listed indicators: RSI, MACD, EMAs, ATR, `structure_adjustment`, `structure_factors`, `stop_liquidity`, `tp_anchor`, … |
| `market_context` | `jsonb` | no | Funding, open interest, BTC correlation, aligned timeframes, quality score, and `published_card` — see below. |
| `source_timestamps` | `jsonb` | no | Provider timestamps, so staleness is provable after the fact. |
| `input_candle_count` | `integer` | no | How many candles fed the decision. |
| `data_quality_flags` | `jsonb` | no | Degradations recorded at decision time. |

> **Never stored here:** API keys, authorization headers, connection strings,
> credentials, raw provider payloads, or per-tick data. Built from a fixed
> **allow-list** in `backend/signal_snapshot.py` — a deny-list would leak the
> first time a provider added a field.

**`market_context.published_card`** — what the dashboard rendered for this
signal, stored so `/api/recommendations` can serve the RECORDED set instead of a
cached recomputation. Same allow-list discipline: `signal_snapshot.CARD_KEYS`
names every field, and `build_card` copies nothing else.

| Group | Keys |
|---|---|
| Conviction | `strength`, `display_strength`, `h1_strength`, `h2_strength`, `avg_tf_strength`, `aligned_tfs` |
| Risk framing | `rr_ratio`, `sl_pct`, `tp_pcts`, `leverage`, `vol_tier`, `vol_tier_label` |
| BTC context | `btc_consensus`, `btc_corr`, `btc_adj`, `btc_aligned`, `btc_conflict` |
| HTF confluence | `mtf_dirs`, `mtf_adj`, `mtf_aligned`, `mtf_confirm`, `mtf_counter` |
| Why | `reasons` |
| Presentation | `view_tf`, `detected_at` |

Three things are deliberately **absent**:

| Not in the card | Because |
|---|---|
| `entry` / `sl` / `tp_targets` / `symbol` / `direction` / `timeframe` | They are real columns on `signals` and `signal_targets`. A second copy could drift from the record of the decision, so the reader fills them in from the row. |
| `quality_score` | Already stored on `market_context`. One copy, not two. |
| `targets_behind_live` | Which rungs are spent is a fact about the LIVE price — true at publication and stale by the time the slot is read back. Storing it would freeze a moving number. |

A signal published before cards were stored has no `published_card`. It renders
from its columns, with `display_strength` falling back to `confidence_score`;
missing fields stay missing rather than being invented.

### 3.35 `pattern_events` — what the detectors saw, and when

**A log, never an input.** The detectors read candles and are the only source of
truth about pattern state; if a row here ever disagreed with a recomputation,
the recomputation is right. Nothing in the scoring path reads it — the same rule
that keeps postmortem data from modifying live strategy parameters.

It exists because pattern state was otherwise entirely ephemeral: recomputed
from candles on every request, so *"this divergence was confirmed on the 4pm bar
and expired eleven candles later"* survived only while those candles stayed
inside the lookback window.

| Column | Type | Null | Meaning |
|---|---|---|---|
| `environment` | `text` | no | Which deployment observed it. |
| `symbol` / `timeframe` | `text` | no | CHECK enforces upper-case symbol. |
| `pattern_kind` | `text` | no | `rsi_divergence`, `choch`, `liquidity_grab`, `engulfing`, `flag`, `triangle`, `acc_eql_fvg` — the keys in `backend/lifecycle.py`. |
| `pattern_type` | `text` | yes | The detector's own label (`bullish`, `hidden_bearish`, …). |
| `direction` | `text` | yes | `LONG` / `SHORT` where the pattern implies one. |
| `status` | `text` | no | `forming` / `confirmed` / `expired` / `invalidated`. CHECK-constrained. |
| `candle_close_time` | `timestamptz` | no | The bar observed. **Part of the identity key.** |
| `age_candles` | `integer` | yes | Closed candles since the event that created the pattern. |
| `fresh_bars` | `integer` | yes | Its window, so a reader can see why it expired. |
| `freshness` | `numeric(6,4)` | yes | The weight the scorer used. CHECK 0–1. |
| `strength` | `numeric(18,8)` | yes | Detector's own strength where it has one. |
| `detail` | `jsonb` | no | Allow-listed: description, level, signal, type, reasons. Never a raw payload. |
| `idempotency_key` | `text` | no | UNIQUE. From `(environment, symbol, timeframe, kind, status, candle_close_time)` — the **bar**, never the clock, so recomputing an analysis records nothing new. |

Written on the **publication bar only**, and only for the **published** symbols.
Logging all 32 on every 4H bar would be ~100 rows a bar for symbols nobody acted
on; these three are the ones a postmortem actually asks about.

`GET /api/patterns/history` reads it (`symbol`, `timeframe`, `kind`, `status`,
`limit`). Empty — not an error — until migration `005` has been run.

### 3.4 `signal_events` — append-only audit trail

Never updated, never deleted in normal operation.

| Column | Type | Null | Meaning |
|---|---|---|---|
| `event_type` | `text` | no | See below. |
| `event_time` | `timestamptz` | no | When it happened in market time. |
| `price` | `numeric(30,12)` | yes | Price associated with the event. |
| `metadata` | `jsonb` | no | Event detail — e.g. `deployment` on CREATED, `from`/`reason` on STOP_MOVED. |
| `idempotency_key` | `text` | no | UNIQUE. Derived from `(signal_id, event_type, source timestamp)`. |

| `event_type` | Written when |
|---|---|
| `CREATED` | The signal is published. Metadata carries the deployment (environment, branch, sha). |
| `ENTRY_FILLED` | Price traded at the entry. |
| `TARGET_HIT` | A rung was reached. |
| `STOP_MOVED` | Stop moved — currently to breakeven after a partial. |
| `STOP_LOSS_HIT` | Stop reached. Terminal. |
| `CLOSED` / `EXPIRED` / `CANCELLED` | Terminal by rule or by hand. |
| `ANALYSIS_ADDED` / `ARCHIVED` | Postmortem attached; row soft-archived. |

### 3.5 `signal_postmortems` — post-trade analysis

One per signal. **Nothing writes this automatically yet** — it is populated only
via `POST /api/signals/<id>/postmortem`. The live MFE/MAE figures are on
`signals`, not here.

| Column | Type | Null | Meaning |
|---|---|---|---|
| `outcome` | `text` | no | `WIN` / `LOSS` / `BREAKEVEN` / `EXPIRED`. |
| `maximum_favorable_excursion_pct` | `numeric(18,8)` | yes | Manual figure. Distinct from `signals.mfe_pct`. |
| `maximum_adverse_excursion_pct` | `numeric(18,8)` | yes | Ditto. |
| `duration_minutes` | `integer` | yes | |
| `failed_conditions` | `jsonb` | no | Which conditions did not hold, as a list. |
| `analysis_summary` | `text` | yes | Free text. |
| `strategy_version` | `text` | no | Which rule-set is being judged. |

> Postmortem data **never** automatically modifies live strategy parameters.

#### The aggregate postmortem report

`signal_postmortems` is per-signal and hand-written. The **aggregate** read is
`GET /api/signals/postmortem-report` (pure logic in `backend/postmortem_report.py`),
and it needs nothing written first — it reads every closed signal of one
`strategy_version` together with its stored decision snapshot.

It answers the standing question — *when a trade hit its stop, what did we
already know?* — by measuring each decision-time flag (structure fought the
trade, stop sat in a sweep zone, reversal-against, chase, 1H/2H disagreement,
thin R/R, violent tape, degraded data, opposed BTC) as its **rate in losers
against its rate in winners**. A flag common to both is not a discriminator,
however common; only the lift between the cohorts is ranked. It also splits the
losers by whether they first ran ≥1R in your favour — which separates a
too-tight stop from a wrong signal.

Three honesty rules are built in: it refuses to call anything a discriminator
until both cohorts clear `MIN_COHORT` (5) trades; a snapshot field a row never
recorded counts as *unknown*, never as *all-clear*; and it states in its own
`caveats`, every response, that it is correlation not causation and changes no
live parameter. A v46 that acts on a discriminator is a separate, backtested,
human-approved strategy_version.

**Running it after the v45 freeze.** v45 resets the sample — v44 and v45 stops,
targets and strength differ, so their trades are not poolable. From the first
v45 deploy, wait for both cohorts to fill (≈15 closed for a first qualitative
read, ≈30 for a quantitative one), then:

```
GET /api/signals/postmortem-report?strategy_version=v45_4h_avg
```

Check `powered` before reading the discriminators; if it is false the sample is
still too thin to mean anything.

### 3.6 `schema_migrations`

`version` (PK), `description`, `applied_at`. Written only by
`database/migrate.py`. Current: `001`, `002`, `003`, `004`, `005`, `006`.

---

## 4. Identity and idempotency

One published signal per instrument, per strategy, per closed candle, per
environment:

```
UNIQUE (environment, symbol, exchange, timeframe,
        strategy_name, strategy_version, candle_close_time)
```

| Why each part | |
|---|---|
| `environment` | A preview deploy sharing the database cannot claim a candle and make production's write look like a duplicate. |
| `candle_close_time` | The next closed candle is a NEW signal — so a symbol legitimately produces several rows a day. Since v44 only the **4H** closes publish, so that is at most six rows a day per symbol, not twelve. |
| `strategy_version` | Old and new rules can be evaluated on the same candles without colliding. |
| **`direction` is deliberately absent** | If it were in the key, a re-evaluation that flipped LONG→SHORT would insert a second row for the same candle, leaving two contradictory live signals. Excluding it means the first published decision for that candle stands. |

**Events** have their own `idempotency_key`, derived from the source candle —
never wall-clock time, or a replay a second later would look like a new event.

### Indexes

| Index | Serves |
|---|---|
| `signals_idempotency_env_uidx` | The uniqueness rule above. |
| `signals_pending_idx` | The monitor's "everything unresolved" query. Partial: `PENDING`/`OPEN`/`PARTIAL_TP`. |
| `signals_active_idx` | Unarchived by status + recency. |
| `signals_closed_idx` | Outcome history. Partial: `closed_at IS NOT NULL`. |
| `signals_environment_generated_idx` | Per-environment listing. |
| `signals_symbol_history_idx` | "Every signal for TAO". |
| `signals_strategy_analysis_idx` | Comparing rule-sets: version × timeframe × direction × status. |
| `signals_instrument_idx` | Exchange + symbol + timeframe. |
| `signals_archived_idx` | Finding soft-archived rows. |
| `signal_events_signal_time_idx` | The trail for one signal, in order. |
| `signal_postmortems_outcome_idx` | Postmortems grouped by outcome × rule-set. |

---

## 5. Conventions that matter

**Money is `numeric`, never float.** Binary floating point cannot represent
decimal prices exactly, and silent rounding on an entry or a stop is a real-money
error. `numeric(30,12)` for prices, `numeric(18,8)` for percentages. Python side:
`Decimal(str(value))`, never `Decimal(float)`. Serialised with `format(v, "f")`
so a sub-satoshi price does not leave the API as `1.2345E-8`.

**Every timestamp is `timestamptz`, stored UTC.** A naive datetime is *rejected*,
not assumed — guessing the zone of a candle silently shifts every stored signal.

**MFE/MAE only ever widen.** The monitor recomputes from whatever candles the
provider still returns; a shorter window must not shrink a high-water mark that
was already observed.

**`DATABASE_URL` is server-side only.** Never returned from an API, never logged,
never prefixed `NEXT_PUBLIC_`/`VITE_`. `db.sanitize_db_error()` strips connection
strings, passwords and bare hostnames from any driver exception before it reaches
a log or a response. `/api/db/health` returns an error *code* only.

---

## 6. API surface

Reads are public. **Every mutation requires `CRON_SECRET`** via
`Authorization: Bearer <secret>` or `x-cron-secret`. With no secret configured,
mutation endpoints stay **closed**, not open.

| Method | Route | Auth | Returns |
|---|---|---|---|
| GET | `/api/patterns/history` | public | The pattern lifecycle log. `symbol`, `timeframe`, `kind`, `status`, `limit`. Empty until migration 005. |
| GET | `/api/recommendations` | public | The set RECORDED for the current 4H slot, read back from `signals`. Never computes, never publishes. `published: false` + `reason` when the slot is empty. |
| GET/POST | `/api/cron/publish` | internal | The publication driver — computes and persists, sends nothing. Runs at :05 past all six 4H boundaries. |
| GET | `/api/signals/tracker` | public | The dashboard view. `days` (default 3, max 30), `environment`. |
| GET | `/api/signals/active` | public | Working signals. |
| GET | `/api/signals/history` | public | Paginated. `symbol`, `timeframe`, `direction`, `status`×N, `strategy_version`, `exchange`, `include_archived=1`, `environment`, `limit`, `offset`. |
| GET | `/api/signals/outcomes` | public | Terminal signals only. |
| GET | `/api/signals/postmortems` | public | Per-signal post-trade analyses, newest first. |
| GET | `/api/signals/postmortem-report` | public | Aggregate across CLOSED signals of a `strategy_version`: ranks decision-time flags by how much more often they preceded a loss than a win. Read-only; never tunes. `strategy_version`, `environment`, `limit`. |
| GET | `/api/signals/<id>` | public | One signal with targets, snapshot, events, postmortem. |
| POST | `/api/signals/monitor` | internal | Advance the lifecycle. `max_age_hours`, `fill_window_hours`, `limit`. |
| POST | `/api/signals/<id>/archive` | internal | |
| POST | `/api/signals/<id>/postmortem` | internal | |
| GET | `/api/db/health` | public | Code only — no DSN, host or user. |
| GET | `/api/db/usage` | internal | Storage and row counts, split by environment. |

### Tracker payload (`/api/signals/tracker`)

```jsonc
{
  "live":   [ /* rows */ ],  "closed": [ /* rows */ ],
  "live_batches": [ /* grouped */ ], "closed_batches": [ /* grouped */ ],
  "summary": { … }, "window_days": 3, "environment": "production"
}
```

Row fields that are **derived, not stored**:

| Field | Meaning |
|---|---|
| `state` | `pending` / `live` / `closed`. |
| `move_pct` | Terminal: the realised (weighted) return. Live: move from entry to the live price, in the trade's favour. `null` while `PENDING`. |
| `r_multiple` | Move ÷ original risk. |
| `stop_distance_pct` | **Cushion**: how far price sits the safe side of the stop. Negative = already through it. |
| `entry_distance_pct` | `PENDING` only — how far price still has to travel to fill. |
| `risk_free` | Stop is at entry or better. |
| `targets[].distance_pct` | Per rung: how far to go. Negative = price already through it. |
| `republished` / `signal_ids` | One setup published on several candles is shown once; this is how many rows are behind it. Merged when symbol, timeframe, direction and status match and both entry and stop agree to within `MERGE_TOLERANCE_PCT` (0.25%) — levels are re-derived each candle, so an unchanged setup returns a few basis points off. |
| `slot` | The publication batch (`Jul 30 · 8:00 PM SGT`) — one of the six 4H slots: 12AM, 4AM, 8AM, 12PM, 4PM, 8PM SGT. |
| `frontend_build` | Top-level, not per row: the `?v=` stamp of the dashboard bundle this deploy ships. The page compares it with its own and warns when they differ. |
| `remark` / `action` | Plain-language state, and the next course of action. |
| `outcome` | `WIN` / `LOSS` / `BREAKEVEN` / `EXPIRED` / `CANCELLED`. |

Summary: `wins`, `losses`, `decided`, `win_rate_pct`, `expired`, `cancelled`,
`never_filled`, `breakeven`, `avg_move_pct`, `best_pct`, `worst_pct`, `closed`.
**`win_rate_pct` counts only decided trades** and is `null` — not `0` — when
nothing has been decided.

---

## 7. Queries worth knowing

Read-only; safe against production. More in `database/verify_signals.sql`.

**Are the stops too tight, or the entries wrong?** The question nine straight
losses should raise:

```sql
SELECT symbol, direction, realized_return_pct, mfe_pct, mae_pct, close_reason
FROM   signals
WHERE  status = 'SL_HIT' AND mfe_pct IS NOT NULL
ORDER  BY closed_at DESC;
```
High `mfe_pct` before a stop-out → stop placement. `mfe_pct` near zero → the
entry was wrong.

**Scoreboard by rule-set:**

```sql
SELECT strategy_version,
       count(*) FILTER (WHERE status = 'TP_HIT')                    AS wins,
       count(*) FILTER (WHERE status = 'SL_HIT')                    AS losses,
       count(*) FILTER (WHERE close_reason = 'NEVER_FILLED')        AS never_filled,
       round(avg(realized_return_pct), 2)                           AS avg_pct
FROM   signals
GROUP  BY strategy_version
ORDER  BY strategy_version;
```

**How long does a fill take?**

```sql
SELECT symbol,
       round(EXTRACT(epoch FROM entry_filled_at - generated_at) / 60) AS minutes_to_fill
FROM   signals
WHERE  entry_filled_at IS NOT NULL
ORDER  BY generated_at DESC LIMIT 20;
```

**The full trail for one trade:**

```sql
SELECT event_type, event_time, price, metadata
FROM   signal_events WHERE signal_id = 'PASTE-ID'
ORDER  BY event_time, created_at;
```

**Who wrote what** (production vs a preview deploy):

```sql
SELECT environment, count(*), max(generated_at) FROM signals GROUP BY environment;
```

---

## 8. Where the code lives

| File | Responsibility |
|---|---|
| `backend/db.py` | Engine, config, error sanitising, health. **Server-side only.** |
| `backend/signal_store.py` | All signal SQL. Route handlers never build queries. |
| `backend/signal_snapshot.py` | Allow-listed decision snapshot. |
| `backend/signal_publish.py` | Recommendation → stored signal. |
| `backend/signal_monitor.py` | `evaluate` (pure: candles in, actions out) + `run_monitor`. |
| `backend/signal_tracker.py` | View model. Pure: rows in, table out. |
| `backend/deploy_context.py` | Which deployment am I. |
| `database/migrations/` | `001`–`006`, plus review-only rollbacks. |
| `database/migrate.py` | Explicit CLI runner. Never automatic. |

**Deploy order for a migration: deploy the code first, then migrate.** Every
migration is written so the running code probes for what it needs and degrades
rather than failing. `002` is the exception that proves the rule — it replaces an
index the older code named directly, so migrating ahead of the deploy breaks
writes. Each migration's header states its own constraint.
