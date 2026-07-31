# CryptoBadshah

Trading Consultant

## Deploy

This repository includes a Vercel configuration in `vercel.json`.

---

# Persistent signal tracking (Neon Postgres)

> ### ⚠️ Creating a Neon database does NOT create the application tables.
> The reviewed migration must be executed explicitly, once, by a human.
> See [Running the migration](#running-the-migration).

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
| `STRATEGY_VERSION` | optional | Identifies the rule-set. Defaults to `v43_wedgefix`. Bump whenever the signal maths changes. |
| `TRACKER_PRICE_BUDGET_S` | optional | How long `/api/signals/tracker` may spend fetching live prices before serving the table without them. Default 6s. |
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
  The current default is `v43_wedgefix`; anything scored before the
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

Filters on `/api/signals/history`: `symbol`, `timeframe`, `direction`,
`status` (repeatable), `strategy_version`, `exchange`, `include_archived=1`,
`environment`, `limit` (default 25, max 100), `offset`. Archived rows are hidden
by default and results are scoped to this deployment's environment.

`/api/signals/tracker` takes `days` (closed window, default 3, max 30) and
`environment`.

## Outcome tracking

Signals used to be recorded and then left at `OPEN` forever: the lifecycle
functions existed and were tested, but nothing called them. Entries with no exits
are not a track record. `POST /api/signals/monitor` is the driver.

**What it does.** For every working signal it walks the CLOSED candles published
since the signal's own candle and decides what the market did:

| Rule | Behaviour |
|---|---|
| **A signal is a working order first** | The monitor used to ignore `entry_price` entirely, so a setup was treated as filled the moment it published — and a target reached without price ever trading the entry booked a win nobody could have taken. An order now becomes `OPEN` only when a candle trades AT the entry, from either side. |
| **Never filled is not a trade** | An order price never returned to is `CANCELLED` after `fill_window_hours` (24 by default), excluded from the win rate, the averages and the P/L. |
| **MFE / MAE** | Every filled trade records how far it ran in favour and against, measured from the fill. A loss that first ran +1.8R is a stop-placement problem, not a signal problem. Both only ever widen — a shorter candle window must not shrink a high-water mark already observed. |
| Closed candles only | A forming candle can un-touch a level before it closes. Recording a hit from one would write an outcome the market never confirmed. |
| The signal's own candle is excluded | Otherwise a trade could be stopped out by the very bar that triggered it. |
| One candle touching BOTH a target and the stop records the **STOP** | A candle says where price went, not in what order. Recording the target would claim a win the data cannot prove — `backtest.py` has always made the same assumption. |
| A gap straight through a level still counts | Price traded through it; pretending otherwise would invent a fill that never happened. |
| Stale signals `EXPIRE` after 72h | The trade is **closed at the last price seen**, so the row carries a real exit and a real P/L — a sideways trade is still a result, and "expired" with a NULL return teaches nobody anything. Still **not** a loss: it never reached a target and was never stopped, so it stays out of the win rate while its P/L counts towards the averages. |
| Idempotent | Every decision is keyed on the candle that caused it, never wall-clock time, so re-running over the same candles changes nothing. |
| One bad signal never abandons the batch | A monitor that stops at the first error silently leaves the rest open. |

`.github/workflows/signal-monitor.yml` runs **hourly**, and can also be triggered
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

Rows are grouped into the **publication batch** they came from — *"Jul 30 · 8:00
PM SGT"* — because a slot is the unit these are decided and reviewed in, and each
batch carries its own scoreboard. Batch headers expand and collapse; live batches
start open and closed ones start collapsed, and your choice is remembered in
`localStorage` — the table re-renders on a 5-minute poll, so without that a batch
you opened would snap shut under you. Grouping reads `generated_at`, not the candle
time: two symbols in one batch can sit on different candles but were still one
decision. Anything published between midnight and 08:00 SGT belongs to the
previous day's 20:00 batch, matching the recommendation cache — those hours are
served the 8pm set. A row whose timestamp cannot be read lands in an *Ungrouped*
batch rather than disappearing.

The API returns `live_batches` and `closed_batches` alongside the flat `live` and
`closed` lists, so a caller that just wants every live signal need not walk them.

The scoreboard counts only **decided** trades. Expired and cancelled signals are
reported separately and excluded from the win-rate denominator, because a setup
that never resolved is not evidence either way. With nothing decided the rate is
absent rather than `0%`.

The view is read-only. It reports what the monitor recorded and never advances a
signal itself — and because the monitor only acts on closed candles, a row can
legitimately show price already through a target or stop while the status has not
caught up. Those rows say so explicitly (*"awaiting candle close"*) rather than
rendering as a healthy open trade.

## Database outage behaviour

With **`DB_REQUIRED=true`**:

* A signal is published only after its transaction commits.
* If persistence fails, `/api/recommendations` returns **503** with a sanitized
  `error_code` and an **empty** `recommendations` array. Telegram dispatch is
  skipped. The result is **not cached**, so the next request retries instead of
  serving an unrecorded set for the rest of the slot.
* Read-only market analysis (`/api/analysis/*`) is unaffected and continues to
  work — it is clearly non-actionable output.

With **`DB_REQUIRED=false`** (default), a persistence failure is logged and
recommendations continue to be served, exactly as before this feature existed.

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
