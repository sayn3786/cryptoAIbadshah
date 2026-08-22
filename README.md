# CryptoBadshah

Trading Consultant

## Deploy

This repository includes a Vercel configuration in `vercel.json`.

---

# Persistent signal tracking (Neon Postgres)

> ### ⚠️ Creating a Neon database does NOT create the application tables.
> The reviewed migration must be executed explicitly, once, by a human.
> See [Running the migration](#running-the-migration).

## Documentation

* **[DATA_MODEL.md](DATA_MODEL.md)** — the guide and data dictionary: lifecycle,
  every column, the identity rule, the API surface and the queries worth knowing.
* [INDICATORS.md](INDICATORS.md) — indicator scoring and the signal maths.

## Architecture

Single Python service. There is no separate frontend deployment.

```
dashboard/            static HTML/CSS/JS  ── served BY the Flask app
api/index.py          Vercel entrypoint   ── imports backend/app.py
backend/
  app.py              Flask routes, recommendation engine
  signals.py          signal maths          (UNCHANGED by this feature)
  indicators.py       indicators            (UNCHANGED by this feature)
  patterns.py         chart patterns        (UNCHANGED by this feature)
  db.py               engine + config     ── SERVER-SIDE ONLY
  signal_store.py     repository layer    ── all signal SQL lives here
  signal_snapshot.py  decision snapshot builder (allow-listed)
  signal_publish.py   recommendation -> stored signal
  deploy_context.py   which deployment am I (production / preview / local)
database/
  migrations/001_initial_signal_schema.sql
  migrations/002_signal_environment.sql
  migrations/rollback/001_rollback_signal_schema.sql   (review only)
  migrations/rollback/002_rollback_signal_environment.sql  (review only)
  verify_schema.sql
  migrate.py          explicit CLI runner (never automatic)
```

* **No ORM models.** The project had no ORM, so this uses SQLAlchemy 2.x Core
  with `psycopg` 3 and hand-written SQL in one repository module. Route
  handlers never build queries.
* **NullPool.** Serverless functions freeze between requests, so a pooled TCP
  connection is usually dead when reused. Every checkout opens and closes its
  own connection; point `DATABASE_URL` at Neon's `-pooler` host to get pooling
  server-side.
* **Lazy.** Importing `db.py` never requires `DATABASE_URL`, so a build or a
  test run without a database still works.

## Environment variables

Set these in **Vercel → Project → Settings → Environment Variables**
(server-side only — never a `NEXT_PUBLIC_` / `VITE_` prefix).

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | yes, to persist | Neon connection string. Injected automatically by the Vercel↔Neon integration. |
| `DB_REQUIRED` | recommended | `true` in production: refuse to publish a signal that was not recorded. Defaults to `false`. |
| `STRATEGY_VERSION` | optional | Identifies the rule-set. Defaults to `v49_4h_avg`. Bump whenever the signal maths changes. |
| `TRACKER_PRICE_BUDGET_S` | optional | How long `/api/signals/tracker` may spend fetching live prices before serving the table without them. Default 6s — generous, because pricing a row is one ticker call, not a full analysis. |
| `SIGNAL_ENVIRONMENT` | optional | Overrides the environment label written on every signal. Defaults to Vercel's own `VERCEL_ENV`, then `local`. See *Shared database, separate environments*. |
| `CRON_SECRET` | yes, for mutations | Existing project secret. Protects archive / postmortem / usage endpoints. |
| `TEST_DATABASE_URL` | tests only | Throwaway database for the DB test suite. **Never production.** |
| `DB_CONNECT_TIMEOUT`, `DB_STATEMENT_TIMEOUT_MS` | optional | Defaults 10s / 15000ms. |

`DATABASE_URL` is **never** returned from an API, logged, or included in an
error message. `db.sanitize_db_error()` strips connection strings, passwords
and bare hostnames from any driver exception before it reaches a log or a
response, and `/api/db/health` returns an error *code* only.

### Why `DB_REQUIRED` defaults to false

So an existing deployment that has not provisioned a database keeps behaving
exactly as it did before. Set it to `true` once the migration has run.

## Verifying the Vercel ↔ Neon connection

Neon was created through the Vercel Marketplace. **Do not create another Neon
project.** To confirm the wiring:

1. **Vercel → Project → Storage** (or **Integrations**) — the Neon database is
   listed and linked to *this* project. If you have several Vercel projects,
   check the project name, not just the account.
2. **Vercel → Project → Settings → Environment Variables** — `DATABASE_URL`
   exists. Do not reveal or copy the value.
3. Check which environments it is enabled for: **Production**, **Preview**,
   **Development**. A variable enabled only for Production means Preview
   deployments have no database and, with `DB_REQUIRED=true`, will 503.
4. **Preview vs Production data.** By default both point at the *same* Neon
   database, so preview deployments write real signals. To separate them,
   create a Neon **branch** and override `DATABASE_URL` for the Preview
   environment.
5. **Redeploy after any change.** Environment variables are baked in at deploy
   time — an existing deployment will not pick up a new or changed variable.

Creating the database does not create the tables. Continue below.

## Running the migration

### Option A — Neon Dashboard (no CLI needed)

1. Open <https://console.neon.tech> and select the existing project — the one
   created by the Vercel integration. **Do not create a new database.**
2. Choose the correct **branch** (usually `main`/`production`).
3. Click **SQL Editor** / **Query** in the left navigation.
4. Open `database/migrations/001_initial_signal_schema.sql` in this repository,
   **read it**, and copy its entire contents into the editor.
5. Click **Run**. Execute it **exactly once**. It is wrapped in a single
   transaction, so it either fully applies or does nothing.
6. Confirm the last statements returned `INSERT 0 1` and `COMMIT`.
7. Open **Schema** in the left navigation and confirm five new tables plus
   `schema_migrations`.
8. Copy the contents of `database/verify_schema.sql` into the editor and run
   it. Check the five result sets described at the top of that file.
9. Never paste `DATABASE_URL` into a chat, an issue, or a support ticket.

Repeat steps 4-6 for each later migration in order —
`002_signal_environment.sql`, then `003_entry_fill_and_excursion.sql`. The notes
below are about 002; 003 follows the same deploy-then-migrate rule and its own
header explains why.

Then for `database/migrations/002_signal_environment.sql`,
which tags each signal with the deployment that wrote it.

> **Deploy the application code BEFORE running 002.** It replaces the
> idempotency index, and the pre-002 code names the old index's exact columns in
> its `ON CONFLICT` clause. PostgreSQL infers an arbiter index by matching that
> list exactly, so once the narrow index is gone every insert from the old code
> fails with *"there is no unique or exclusion constraint matching the ON
> CONFLICT specification"* — and with `DB_REQUIRED=true` that means
> `/api/recommendations` returns 503. The new code targets whichever index
> exists, so deploy-then-migrate is safe; migrate-then-deploy is not. Recovery
> if it was run early is in the migration's header comment.

After it applies, `/api/db/health` reports `"environment_tagging": true`.

Re-running either migration is safe (every object uses `IF NOT EXISTS` and the
version row uses `ON CONFLICT DO NOTHING`), but there is no reason to.

### Option B — CLI

```bash
export DATABASE_URL="…"          # never commit this
python database/migrate.py status   # what is applied, what is pending
python database/migrate.py up       # apply pending migrations
python database/migrate.py verify   # confirm tables + no float money columns
```

or with psql:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f database/migrations/001_initial_signal_schema.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f database/migrations/002_signal_environment.sql
psql "$DATABASE_URL" -f database/verify_schema.sql
```

The migration is **never** run automatically — not on import, not on app
start, not per request, not during a Vercel build.

## Schema

| Table | Purpose |
|---|---|
| `signals` | One row per **published** signal. Rejected candidates are never stored. |
| `signal_targets` | TP ladder, one row per target, with hit time and price. |
| `signal_indicator_snapshots` | Exactly one decision-time snapshot per signal. |
| `signal_events` | Append-only lifecycle audit trail. |
| `signal_postmortems` | Post-trade analysis, one per signal. |
| `schema_migrations` | Applied migration versions. |

All prices are `numeric(30,12)` and all percentages `numeric(18,8)` — **never**
floating point. Binary floats cannot represent decimal prices exactly, and a
silently rounded entry or stop is a real-money error. Values cross the API as
strings in plain decimal notation to preserve that exactness.

All timestamps are `timestamptz`, stored and compared in **UTC**. A naive
datetime is rejected rather than assumed to be UTC.

## How idempotency works

There is one unique constraint:

```
(environment, symbol, exchange, timeframe, strategy_name, strategy_version,
 candle_close_time)
```

(`environment` joins the key in migration 002 — before that it is the same key
without it.)

* **Re-evaluating the same closed candle** returns the existing signal and
  writes nothing. The publish path can safely run on every slot and every
  on-demand recompute.
* **`direction` is deliberately NOT part of the key.** If it were, a
  re-evaluation that flipped LONG→SHORT would insert a second row for the same
  candle, leaving two contradictory live signals. Excluding it means the first
  published decision for that candle stands.
* **The next closed candle is a new signal.** Because `candle_close_time` is in
  the key, the same token produces a fresh signal on the next closed 1H/2H
  candle — several times a day, all on the same calendar date.
* **Strategy versions are independent.** Bump `STRATEGY_VERSION` and the new
  rules can be evaluated on the same candles without colliding with the old.
  The current default is `v49_4h_avg`; anything scored before the
  market-structure confluence work is not comparable with anything after it.

* **Environments do not collide.** A preview deploy sharing `DATABASE_URL`
  publishes its own row for the same candle instead of claiming production's.
  See below for why that matters.

Lifecycle **events** have their own unique `idempotency_key`, derived from
`(signal_id, event_type, source timestamp)` — never wall-clock time, or a
replay one second later would look like a new event.

## Shared database, separate environments

In this project `DATABASE_URL` is scoped to **All Environments** in Vercel, so a
preview deployment writes into the *same* Neon branch as production. Two problems
follow, and migration `002_signal_environment.sql` fixes both:

1. **Preview rows looked exactly like real ones.** Every signal now records the
   deployment that wrote it in `signals.environment` — `production`, `preview`,
   `local`, or whatever `SIGNAL_ENVIRONMENT` says.
2. **Preview could silently suppress production.** The old idempotency key had no
   notion of environment, so whichever deployment evaluated a candle first
   claimed it and the other one's write came back as a duplicate and was dropped.
   `environment` is now part of the key.

How it behaves:

| | Behaviour |
|---|---|
| Label source | `SIGNAL_ENVIRONMENT`, else `VERCEL_ENV`, else `local`. Never request input — a client cannot influence it. |
| Reads | Scoped to the *current* environment by default, so production never serves a preview deploy's signals. `?environment=all` shows everything; `?environment=preview` shows one. |
| Storage cost | One short text column plus two indexes. |
| Deployment detail | The branch and commit sha are recorded on the `CREATED` event (`metadata.deployment`), not in a column, and are **not** exposed by `/api/db/health`. |
| Deploying before migrating | Safe. The code probes for the column and writes untagged while it is absent. |
| Migrating before deploying | **Not safe** — 002 replaces the idempotency index and the old code's `ON CONFLICT` can no longer infer an arbiter, so every write fails. See the warning under *Running the migration*. |

`/api/db/health` reports `environment` (which deployment answered) and
`environment_tagging` (whether 002 has been applied). `/api/db/usage` adds
`signals_by_environment`, so you can see how much stored history came from
preview deploys.

**Keeping preview out entirely** is still the stronger option, if you want it:
give the Preview scope its own `DATABASE_URL` (a separate Neon branch), or enable
Neon's per-preview branching in the Vercel integration. The tagging above is what
makes the shared setup *safe*; a separate branch makes it *unnecessary*.

## Signal lifecycle

```
PENDING ─> OPEN ──┬─> PARTIAL_TP ──┬─> TP_HIT      (terminal)
       │                ├─> SL_HIT      (terminal)
       ├────────────────┼─> CLOSED      (terminal)
       │                ├─> EXPIRED     (terminal)
       │                └─> CANCELLED   (terminal)
```

| Status | Meaning |
|---|---|
| `PENDING` | Published, **working order** — price has not reached the entry. No position, no P/L, in no statistic. |
| `OPEN` | Entry filled. This is where the trade actually starts. |
| `PARTIAL_TP` | At least one target hit, more remain. |
| `TP_HIT` | Final target reached. Terminal. |
| `SL_HIT` | Stop hit. Terminal. |
| `CLOSED` | Closed manually or by rule. Terminal. |
| `EXPIRED` | Timed out without resolving. Terminal. |
| `CANCELLED` | Withdrawn before resolving — including `NEVER_FILLED`, an order price never came back to. Terminal, and **not a trade**. |

**Terminal means terminal.** A terminal signal can never return to `OPEN` or
`PARTIAL_TP`, and a signal can never record both `TP_HIT` and `SL_HIT` —
whichever lands first wins and the other is rejected. Lifecycle updates take a
row lock (`SELECT … FOR UPDATE`) plus a compare-and-set on status, so
concurrent workers cannot both terminate the same signal.

The original decision is **immutable**: direction, entry, stop, targets,
strategy version and the decision snapshot are never rewritten. Changes are
appended as events.

## Postmortem data

Written after a signal completes, to answer *why*:

| Field | Answers |
|---|---|
| `outcome` | How it ended. |
| `maximum_favorable_excursion_pct` | How far it went in your favour before losing. |
| `maximum_adverse_excursion_pct` | How far against you it went. |
| `duration_minutes` | Time spent open. |
| `failed_conditions` | e.g. `["volume_confirmation_failed", "trend_flipped"]`. |
| `analysis_summary` | Free text. |
| `strategy_version` | Which rules are being judged. |

Combined with `signal_indicator_snapshots`, this supports asking what losing
signals had in common — trend flip after entry, failed volume confirmation, a
false flag breakout, abnormal volatility.

> **Postmortems never modify live strategy parameters.** Any strategy change
> requires separate backtesting, a new `STRATEGY_VERSION`, and human approval.

## API

Reads are public. **Every mutation requires `CRON_SECRET`** via
`Authorization: Bearer <secret>` or `x-cron-secret`. With no secret configured
the mutation endpoints stay **closed**, not open.

| Method | Route | Auth |
|---|---|---|
| GET | `/api/signals/active` | public |
| GET | `/api/signals/history` | public |
| GET | `/api/signals/outcomes` | public |
| GET | `/api/signals/postmortems` | public |
| GET | `/api/signals/tracker` | public |
| GET | `/api/signals/<id>` | public |
| POST | `/api/signals/monitor` | internal |
| POST | `/api/signals/<id>/archive` | internal |
| POST | `/api/signals/<id>/postmortem` | internal |
| GET | `/api/db/health` | public (code only) |
| GET | `/api/db/usage` | internal |
| GET | `/api/backtest/portfolio` | public |
| GET | `/api/backtest/<symbol>` | public (**deprecated**) |

Filters on `/api/signals/history`: `symbol`, `timeframe`, `direction`,
`status` (repeatable), `strategy_version`, `exchange`, `include_archived=1`,
`environment`, `limit` (default 25, max 100), `offset`. Archived rows are hidden
by default and results are scoped to this deployment's environment.

`/api/signals/tracker` takes `days` (closed window, default 3, max 30) and
`environment`.

### Validating the strategy

`/api/backtest/portfolio` is the walk-forward replay of the **published**
strategy: every historical 4H slot, closed candles only, the real gates and
ranking from `rec_policy`, the correlation-aware top-three, resting limit
entries with the 24-hour cancellation, and the 50/30/20 scale-out with the stop
to breakeven — all through the same functions production and the monitor call.
Params: `symbols`, `cap` (6), `slots` (20), `limit` (700), `fee_bps` (6),
`slippage_bps` (2), `trades`.

It runs in `price_only` mode: funding, OI, futures CVD, sentiment, macro,
on-chain and options have no historical series, so it measures the
price/structure edge and **cannot validate any external input**. Every response
states its parity mode, the gates it replayed, what it omitted, and where it
still differs from production. Asking for `historical_full` without supplying
timestamped snapshots returns 400 rather than substituting today's values for
last March's.

The endpoint is **capped** (12 symbols) for timeout safety, so it always returns
`result_kind: subset_price_only`: production ranks across all 31 `SCAN_SYMBOLS`,
and a top three chosen from a smaller field is a different top three. A subset
run validates the gates, the fills and the exits — **not** the selection. For
the complete universe use the offline CLI, which is not capped:

```
python -m portfolio_backtest_cli --symbols production --slots 100 \
    --limit 1000 --output report.json
```

It fails closed when any symbol is short of history rather than quietly dropping
it and still claiming full-universe parity.

`/api/backtest/<symbol>` is **deprecated** and returns
`result_kind: legacy_price_only`. It is a single-symbol indicator study with a
per-group ablation — no 1H/2H agreement, no BTC adjustment, no R/R gate, no
ranking, market entry at the next open, TP1 as a full exit. It is not a test of
the published strategy and its responses say so.

Full detail, including the metric definitions and the remaining parity gaps, is
in [SIGNAL_RELIABILITY.md](SIGNAL_RELIABILITY.md).

## Publication cadence

A set is **recorded on the 4H candle close and nowhere else** — six slots a day
(00, 04, 08, 12, 16, 20; SGT and UTC boundaries coincide), three trades each, so
at most **eighteen** published trades a day.

Before this, every 2H close was a publication point. The idempotency key is
per-candle, so a setup that stayed valid across six bars became six rows — which
is how sixty-odd "working" signals accumulated while only a handful of distinct
trades were ever taken.

* A recompute **between** bars still serves recommendations. It records nothing
  and says so: `persistence.skipped_reason = "NOT_A_PUBLICATION_BAR"`, with
  `actionable` still true and `error_code` null. A skip is not a failure, and
  `DB_REQUIRED` does not turn it into a 503.
* **Announcements go out at most once per slot.** `/api/cron/daily` sends
  Telegram *after* computing, so a run killed by the timeout after the send
  would, on retry, announce the same set twice — which is why that workflow
  had no retries while the publish cron did. It now claims a per-slot key
  **before** dispatching (atomic `SET NX` via `kv.py`) and **releases it if the
  send fails**, so a retry either finishes the job or correctly does nothing.
  The asymmetry is deliberate: a release that itself fails loses an alert but
  never duplicates one. The manual *Send to Telegram* button is not gated — a
  person pressing it means it — but a successful manual send claims the slot so
  the cron will not repeat it.
* **Publishing runs close to Vercel's 60s `maxDuration`.** `/api/cron/daily` has
  already been killed at 61s with `FUNCTION_INVOCATION_TIMEOUT`, and
  `/api/cron/publish` runs the same compute. `signal-publish.yml` therefore
  retries up to three times with backoff — the per-symbol analysis cache is
  warmed by the attempt that died, so a follow-up usually completes inside the
  limit. A 401 or 404 is not retried; those do not improve by waiting.
  Retrying is safe because publication is idempotent on the candle.
  This is mitigation, not the cure.
* **The publish path fetches 4H only where it is read.** On the Hobby plan 60s
  is a hard ceiling — `maxDuration` cannot be raised — so the work came down
  instead. The 4H analysis contributes exactly two fields (`direction`,
  `tradeable`) and both are consumed *after* the 1H/2H gates, purely to feed
  `htf_4h_dir` into the quality tiebreak. A symbol that fails those gates never
  reads its 4H data, yet a full `build_analysis` was run for it anyway. Fetching
  it only for the survivors cut roughly **a fifth** of the heavy calls and
  changes **nothing** about the output — every candidate still gets the same 4H
  reading. `_passes_tf_gates` is shared by the prefetch and the candidate loop
  so the two cannot drift; if they did, a symbol could arrive with no 4H data
  and be scored as though 4H were neutral. A slot that publishes nothing now
  shows nothing, because the read path no longer computes a fallback.
* **The gate is the SLOT, not the candle.** It used to require the latest
  closed candle to *be* a 4H boundary, which assumed the cron fired near it.
  GitHub Actions cron is best-effort and ran **one to three hours late**; a run
  arriving after the next 2H close saw a non-boundary candle, published nothing
  and reported success. **Two of the first four real slots were lost that way**,
  silently, because a skip is not an error by design.
  Publication now asks the database *"has this slot published yet?"*, so a late
  run publishes its slot using whatever candle is current by then - fresher
  levels for the same slot, which beats no signal. Still at most 3 per slot.
* **`signal-publish.yml` runs hourly**, and the endpoint answers the slot
  question **before** computing. The 23 runs a day that find the slot already
  done cost one cheap query each instead of ~50s of upstream fetching, which is
  what makes the frequency affordable - and frequency is what absorbs the delay.
* The in-process pre-warm scheduler still runs at :02 past each boundary.
  The gate reads the last CLOSED candle, so a job that fires early sees the
  previous bar and publishes nothing — `telegram-alerts.yml` used to fire at
  23:50 UTC precisely to absorb GitHub's delay, and that became a bug.
* If exchange data still lags at the boundary, the set is built on the previous
  bar. It is served but **not cached** (`slot_current: false`), so the next
  request recomputes against the fresh candle instead of serving an unrecorded
  set for four hours.

### The cards are served from the database

`/api/recommendations` **reads the recorded set** for the current slot. It does
not compute and it does not publish — publication is driven by
`/api/cron/publish` at all six boundaries, and this route is a pure read of what
that wrote.

It used to be the other way round: three caches sat in front of the cards
(browser `localStorage` on a 30-minute key, a server-side JSON blob, and the
compute itself) and none of them was the database — the `signals` table was
write-only in that path. So the cards and the Signal Tracker could legitimately
disagree about what had been published, and under the 4H cadence that would have
become the normal case for three hours out of every four.

* **Nothing published for a slot shows as exactly that** (`published: false`
  with a `reason`), never as a set computed on the fly. An unrecorded set shown
  as a recommendation is what the publication gate exists to prevent.
* **The browser cache is keyed to the 4H slot**, so it expires when the thing it
  caches is replaced. On a 30-minute key a browser kept showing the previous set
  for up to half an hour after a new one published.
* **The published strength stays put.** The live-score refresh used to overwrite
  it with a fresh recomputation; it now shows alongside as a labelled `now N`
  badge, so a setup that has decayed since publication reads as decay rather
  than being quietly rewritten.
* Prices are served as exact numeric strings straight off the column — never
  floated on the way out.

What the card renders is stored at publish time as `published_card` on the
snapshot's `market_context`, under the same allow-list discipline as the rest of
the snapshot: named keys only, bounded and redacted, no prices (those are
columns) and no credentials.

### Ranking

Ranking changed with the cadence: candidates are ordered by the **average of 1H
and 2H strength**, with the composite `quality_score` demoted to the tiebreak.
Every quality gate still gates — R/R ≥ 1.5, direction agreement, data quality,
the expired-setup filter and correlation diversification all still remove
candidates. See [INDICATORS.md](INDICATORS.md) for the detail.

## Pattern lifecycle

Every detector answered "how old is this, and does it still count?" differently,
and none of them said it out loud. CHoCH faded over 10 candles and the liquidity
grab over 5 - both as bare divisions buried inside `signals.py`, invisible to the
dashboard. Flags and wedges carried a `status` but no weight. **RSI divergence
had no age term at all**: it scored the same on candle 1 as on candle 29, then
vanished outright when its pivots fell out of the lookback.

`backend/lifecycle.py` owns the windows and the vocabulary:

| `status` | Meaning |
|---|---|
| `forming` | not yet a fact - waiting on a close |
| `confirmed` | inside its window |
| `expired` | past it, fading over 3 grace bars |
| *(dropped)* | beyond that, not reported |

Two decay curves, and they are **not** interchangeable. CHoCH and the liquidity
grab fade from the moment they happen (`1 - age/window`); a called turn like a
divergence holds full weight inside its window then fades. Collapsing them into
one would silently double the weight of a 5-candle-old CHoCH - a strategy change
nobody asked for - so tests assert both curves stay **bit-identical** to the
arithmetic `signals.py` has always used.

The 3-candle grace is `patterns.FAILURE_SHOW_BARS`, the same window failed flags
already used. One number, not two.

### The log

`pattern_events` (migration **005**) records what the detectors saw, one row per
`(pattern, status, bar)`. It exists because pattern state is otherwise entirely
ephemeral - recomputed from candles every request, so *"this divergence was
confirmed on the 4pm bar and expired eleven candles later"* survived only while
those candles stayed in the lookback.

**It is a log, never an input.** The detectors read candles and are the only
source of truth; if a row ever disagreed with a recomputation, the recomputation
is right. Nothing in the scoring path reads it, and a test asserts `signals.py`,
`indicators.py` and `patterns.py` never import the store - the same rule that
keeps postmortem data from modifying live strategy parameters.

* Written on the **publication bar only**, and only for the **published**
  symbols. Logging all 32 every 4H bar would be ~100 rows a bar for symbols
  nobody acted on.
* Idempotent on the **bar**, never the clock - the analysis is recomputed on
  every dashboard load, and without this a busy afternoon would write the same
  CHoCH hundreds of times.
* `detail` is allow-listed, exactly as the decision snapshot is.
* Recording is a **no-op until migration 005 has run**, and losing a log entry
  never stops a signal being published.

`GET /api/patterns/history` reads it; the dashboard's **Pattern History** panel
renders it and stays hidden until there is something to show.

## Outcome tracking

Signals used to be recorded and then left at `OPEN` forever: the lifecycle
functions existed and were tested, but nothing called them. Entries with no exits
are not a track record. `POST /api/signals/monitor` is the driver.

**What it does.** For every working signal it walks the CLOSED candles published
since the signal's own candle and decides what the market did:

| Rule | Behaviour |
|---|---|
| **Scale-outs are weighted** | Banking TP1 takes part of the position off at TP1's price; only the remainder closes at whatever ends the trade. The realised return used to come from the FINAL exit alone, so a trade that took TP1 and reversed recorded a full loss and none of the profit it had already taken. Shares come from `signal_targets.exit_fraction`, or an even split across the ladder. The column was added by migration 004 and read from day one, but nothing WROTE it, so it was NULL everywhere and every scale-out was silently an even third — while the dashboard told the reader to sell 50% at TP1. The published plan now goes into the row: `SCALE_OUT_SHARES` is the one place it is written down, and a test fails if the dashboard copy and the constant drift apart. |
| **The stop moves to breakeven after a partial** | The tracker has always advised it; the record now does it, as a `STOP_MOVED` event. `stop_loss` is never rewritten — it is the record of the risk originally taken — and `current_stop_loss` is where the stop actually sits. A stop is never moved FURTHER from entry: widening mid-trade is how a small loss becomes a large one, and the record must not make it look like a plan. |
| **A signal is a working order first** | The monitor used to ignore `entry_price` entirely, so a setup was treated as filled the moment it published — and a target reached without price ever trading the entry booked a win nobody could have taken. An order now becomes `OPEN` only when a candle trades AT the entry, from either side. |
| **Never filled is not a trade** | An order price never returned to is `CANCELLED` after `fill_window_hours` (24 by default), excluded from the win rate, the averages and the P/L. |
| **MFE / MAE** | Every filled trade records how far it ran in favour and against, measured from the fill. A loss that first ran +1.8R is a stop-placement problem, not a signal problem. Both only ever widen — a shorter candle window must not shrink a high-water mark already observed. |
| Closed candles only | A forming candle can un-touch a level before it closes. Recording a hit from one would write an outcome the market never confirmed. |
| The signal's own candle is excluded | Otherwise a trade could be stopped out by the very bar that triggered it. |
| One candle touching BOTH a target and the stop records the **STOP** | A candle says where price went, not in what order. Recording the target would claim a win the data cannot prove — `backtest.py` has always made the same assumption. |
| A gap straight through a level still counts | Price traded through it; pretending otherwise would invent a fill that never happened. |
| Stale signals `EXPIRE` after 72h | The trade is **closed at the last price seen**, so the row carries a real exit and a real P/L — a sideways trade is still a result, and "expired" with a NULL return teaches nobody anything. Still **not** a loss: it never reached a target and was never stopped, so it stays out of the win rate while its P/L counts towards the averages. |
| Idempotent | Every decision is keyed on the candle that caused it, never wall-clock time, so re-running over the same candles changes nothing. |
| Runs on a clock | One run is bounded by `MONITOR_BUDGET_S` (45s, inside the 60s serverless ceiling) and fetches every symbol's candles in parallel. Past the budget it stops cleanly and reports `truncated`: what was decided has committed, and the next tick resumes, because every decision is keyed on its candle. Being killed mid-run records **nothing**. |
| One bad signal never abandons the batch | A monitor that stops at the first error silently leaves the rest open. |

`.github/workflows/signal-monitor.yml` runs **every 30 minutes**, and can also be triggered
by hand from the Actions tab. Running it more often is harmless — every decision
is keyed on the candle that caused it, so an extra run records nothing new.

**The first tick resolves the whole backlog.** Genuine target and stop hits are
recorded, and anything older than `max_age_hours` (72 by default) is EXPIRED —
closed at the last price seen, with its P/L.
Expired is terminal but is **not** a loss — it is excluded from the win rate — so
a backlog of stale signals resolves honestly rather than being scored against the
strategy. To record hits without ageing anything out, trigger it by hand first
with `max_age_hours` set to **720**.

## Signal Tracker (dashboard)

The dashboard's **📋 Signal Tracker** section reads `/api/signals/tracker`: every
live signal with its ladder state, distance to the next target, cushion above the
stop, plain-language remarks and the next course of action — then trades that
closed in the last 3 days, marked win or loss.

**One line per setup, not per candle.** The strategy re-evaluates every closed
candle, so a setup whose levels have not moved is published again on the next one
— a new row in the database, correctly, because `candle_close_time` is part of
the idempotency key and each candle is its own decision. It is not a new
position. The tracker collapses rows that are indistinguishable as positions
(same symbol, timeframe, direction and status, with entry and stop agreeing to
within `MERGE_TOLERANCE_PCT`, 0.25%), keeps the earliest — the setup has been
working since it first appeared, and the levels shown are the ones published at
that age — and shows `×N` for how many candles republished it.

**Why a tolerance and not exact equality.** Levels are re-derived from each
candle, so an unchanged setup still returns a few basis points off: SOL at
74.0885 and 74.1503, ETH at 1911.11 and 1911.27, XMR at 350.9238 and 351.5500 —
all observed on one screen, each listed twice. Exact matching merged none of
them, and a tracker that shows one position twice misreports your exposure. Each
candidate is compared against its **cluster's representative**, never against the
row before it: chained comparison would let a long run of small drifts collapse
genuinely different setups into one.

A materially different entry or stop is still a different setup; a different
status means one filled and the other did not, and those are never merged.
Closed trades are never merged at all: history stays whole.

Rows are grouped into the **publication batch** they came from — *"Jul 30 · 8:00
PM SGT"*, one of the six 4H slots — because a slot is the unit these are decided and reviewed in, and each
batch carries its own scoreboard. Batch headers expand and collapse; live batches
start open and closed ones start collapsed, and your choice is remembered in
`localStorage` — the table re-renders on a 5-minute poll, so without that a batch
you opened would snap shut under you. Grouping reads `generated_at`, not the candle
time: two symbols in one batch can sit on different candles but were still one
decision. Every hour of the day now falls inside a batch — the six 4H boundaries
tile the whole day, so there is no overnight gap to fold into the previous
evening. A row whose timestamp cannot be read lands in an *Ungrouped* batch
rather than disappearing.

The API returns `live_batches` and `closed_batches` alongside the flat `live` and
`closed` lists, so a caller that just wants every live signal need not walk them.

The scoreboard counts only **decided** trades. Expired and cancelled signals are
reported separately and excluded from the win-rate denominator, because a setup
that never resolved is not evidence either way. With nothing decided the rate is
absent rather than `0%`.

**Live price is a cheap lookup, not an analysis.** Pricing a row peeks at the
analysis cache — free when the dashboard is warm, and it never *builds* one —
then falls back to a single ticker call per symbol, the same path `/api/prices`
uses. Calling `get_analysis` here used to mean a cold instance ran a full
`build_analysis` per symbol (candles, funding, open interest, CVD, on-chain) just
to read one number; with forty-odd working signals nothing finished inside the
budget and every row rendered with no PRICE, no distance to entry and no cushion
above the stop. A missing price is still not an error — the row renders without
live progress rather than reporting a move of zero.

**A stale bundle says so.** An installed PWA can keep an old `dashboard.js`
alive across a deploy. The tracker then renders through an older code path —
no batch grouping, no collapse controls — which on screen is indistinguishable
from the feature having been removed, and cost a long investigation to identify
as a stale frontend rather than a broken feature. Staleness has **two independent halves**, and conflating them made the first
version of this check cry wolf on every poll:

| | What it is | Recoverable? |
|---|---|---|
| `CODE_BUILD` | Baked into `dashboard.js`. What is actually executing. | No — old code contains none of this, so it can never warn about itself. |
| `SHELL_BUILD` | The `?v=` on the script tag, i.e. what `index.html` says. | Yes. |

The server **ignores** that query string and serves whatever `dashboard.js`
currently is, so a stale shell still pulls fresh code. Reading it therefore
measures the HTML, not the JS — and the first version read it and announced
*"this page is running an old build"*, warning about code that was current.

So they get different treatment. A stale **shell** is fixed silently: caches
dropped and the page reloaded once against an uncached URL — a banner the user
must act on is a worse fix than one they never see, and the guard in
`sessionStorage` is what stops a shell that will not refresh from reloading
forever. A stale **code** build raises the banner, with platform-correct advice
(app switcher on iPhone, Cmd+Q on Mac).

`/api/signals/tracker` reports `frontend_build`, parsed out of `index.html` so it
cannot drift from the real asset. The check fails **silent**: if either side
cannot be read, no banner — a false "you are out of date" is worse than none.

Relatedly, when the API returns no batches the tracker still falls back to one
flat table, but now labels it as a degraded render instead of quietly looking
like the collapse controls were removed.

The view is read-only. It reports what the monitor recorded and never advances a
signal itself — and because the monitor only acts on closed candles, a row can
legitimately show price already through a target or stop while the status has not
caught up. Those rows say so explicitly (*"awaiting candle close"*) rather than
rendering as a healthy open trade.

## Database outage behaviour

Publishing and reading fail differently, because they are now different paths.

**Publishing** (`/api/cron/publish`, the scheduler, `/api/cron/daily`) with
**`DB_REQUIRED=true`**:

* A signal is published only after its transaction commits.
* If persistence fails the set is marked not-actionable with a sanitized
  `error_code`, nothing is cached, and Telegram dispatch is skipped — so the
  next run retries instead of a slot going out unrecorded.

With **`DB_REQUIRED=false`** (default), a persistence failure is logged and does
not block the rest of the run.

**Reading** (`/api/recommendations`) cannot publish anything, so it has no 503 to
give. A database it cannot read simply means it has nothing to show: it returns
**200** with `published: false` and `reason: "DB_READ_FAILED"`, and the dashboard
says so. It never falls back to computing a set — that would put an unrecorded
recommendation on screen, which is the one outcome the whole gate exists to
prevent.

Read-only market analysis (`/api/analysis/*`) is unaffected either way — it is
clearly non-actionable output.

A signal that fails **price-structure validation** is never published under
either setting — that is broken data, not a risky trade.

Error codes: `DB_NOT_CONFIGURED`, `DB_WRITE_FAILED`, `DB_UNAVAILABLE`,
`DB_NOT_MIGRATED`, `DB_SCHEMA_UNREADABLE`, `NO_CLOSED_CANDLE`, `INVALID_SIGNAL`,
`PERSISTENCE_ERROR`.

### Reading `/api/db/health`

`reachable` and `migrated` are reported separately, because they need different
fixes:

| `error_code` | Means | Fix |
|---|---|---|
| `DB_NOT_CONFIGURED` | no `DATABASE_URL` | set it, redeploy |
| `DB_UNAVAILABLE` | cannot connect | check the URL, the Neon project, and that the variable is enabled for this environment |
| `DB_NOT_MIGRATED` | **connects fine**, tables absent | run the migration |
| `DB_SCHEMA_UNREADABLE` | connects, cannot inspect schema | check the role's privileges on `public` |

When `error_code` is `DB_UNAVAILABLE` the response also carries a `failure`
class and a matching `hint`:

| `failure` | Means |
|---|---|
| `startup_parameter_rejected` | the pooled endpoint refused a libpq startup parameter |
| `authentication` | credentials rejected — re-copy `DATABASE_URL` |
| `database_missing` | wrong Neon branch, or the role/database is gone |
| `dns` | host cannot be resolved |
| `tls` | TLS negotiation failed |
| `timeout` | a scale-to-zero compute may be slow to wake; retry |
| `refused` | wrong host or port |
| `too_many_connections` | use the `-pooler` host |
| `driver_missing` | `requirements.txt` deps missing — redeploy |

The class is a fixed vocabulary, never the driver's message, so it cannot leak a
host, database name or credential on this unauthenticated endpoint.

> **Never pass libpq `options` when using Neon's pooled (`-pooler`) host.**
> PgBouncer rejects it with *"unsupported startup parameter: options"*, which
> fails every connection. `statement_timeout` is applied with `SET LOCAL` inside
> each transaction instead — pooler-safe, and correctly scoped.

`"reachable": true` with `"migrated": false` is the expected state immediately
after wiring `DATABASE_URL` but **before** running the migration. It is not a
connection fault.

### Which database is this environment using?

`/api/db/health` also returns `target_fingerprint` (a short one-way hash of the
host) and `database` (the database name). Compare the fingerprint across
environments:

```
production : "target_fingerprint": "157cdc35b389", "database": "neondb"
preview    : "target_fingerprint": "9ee3912dbb0d", "database": "neondb"
```

**Different fingerprints mean different databases** — so migrating one does not
migrate the other. This matters because Vercel's Neon integration creates an
isolated branch per preview deployment, and `DATABASE_URL` can differ per
environment. Production reporting `DB_NOT_MIGRATED` while a branch you migrated
clearly has the tables means production is pointed somewhere else.

The fingerprint survives a password rotation (it identifies the target, not the
credential) and cannot be reversed to a hostname, so it is safe on this
unauthenticated endpoint.

**To migrate the database production actually uses:** reveal `DATABASE_URL` in
Vercel → Settings → Environment Variables (Production scope), find the matching
branch in the Neon console via **Connect**, and run the migration on *that*
branch. Then re-check health — `migrated` must become `true` with the SAME
fingerprint.

## Free-tier storage

Phase 1 is deliberately frugal:

* Only **published** signals are stored — never rejected candidates.
* **One** snapshot per signal; indicator data is not duplicated across tables.
* No API responses, no per-tick data, no raw provider payloads, no base64 or
  images. The snapshot builder uses an **allow-list**, so a future analysis key
  containing a credential or a large payload cannot leak in.
* Snapshots are bounded (long strings and lists truncated, deep nesting
  dropped) and asserted under 16 KB by a test.
* History is paginated; indexes are lean and partial where it helps.
* Completed signals are **soft-archived** (`archived_at`). Nothing is deleted
  in phase 1.

Check usage with `GET /api/db/usage` (internal):

```json
{ "database_size_bytes": …, "estimated_row_counts": {…},
  "signals_total": …, "active_signals": …, "archived_signals": …,
  "oldest_signal_at": …, "newest_signal_at": … }
```

> **Review retention when the database reaches 60–70% of the Neon free-tier
> allowance.** Automatic deletion is not implemented and is out of scope here.

## Tests

```bash
python -m pytest tests -q                       # DB tests skip without a database
createdb cryptomonk_test
TEST_DATABASE_URL="postgresql://localhost/cryptomonk_test" python -m pytest tests -q
```

DB-backed tests run in a **fresh schema per test**, dropped afterwards, and
refuse to run against a URL containing `prod`. **Never point
`TEST_DATABASE_URL` at production Neon.**

## Rolling back

**Preferred: revert the application code and leave the tables in place.**
Unused tables cost a few kilobytes; deleted trade history cannot be recovered.

1. Revert/redeploy the previous application commit, or set `DB_REQUIRED=false`
   so publishing no longer depends on the database.
2. The tables can stay. They are additive and nothing else references them.

`database/migrations/rollback/001_rollback_signal_schema.sql` exists **for
review only**. It is destructive, entirely commented out, and nothing in this
repository executes it — not the migration runner, not any test, not any
deployment step. Read the warnings at the top before ever uncommenting it, and
export your data first.

## Deploying

1. Confirm the Vercel ↔ Neon wiring (above) and that `DATABASE_URL` exists for
   the intended environments.
2. **Run the migration** (it does not happen automatically).
3. Verify with `database/verify_schema.sql` or `python database/migrate.py verify`.
4. Deploy the application code.
5. Check `GET /api/db/health` → `{"ok": true, "migrations_applied": ["001"]}`.
6. Only then set `DB_REQUIRED=true` and **redeploy** so it takes effect.
