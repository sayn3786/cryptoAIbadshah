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
database/
  migrations/001_initial_signal_schema.sql
  migrations/rollback/001_rollback_signal_schema.sql   (review only)
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
| `STRATEGY_VERSION` | optional | Identifies the rule-set. Defaults to `v41_poolage`. Bump whenever the signal maths changes. |
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

Re-running the migration is safe (every object uses `IF NOT EXISTS` and the
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
(symbol, exchange, timeframe, strategy_name, strategy_version, candle_close_time)
```

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
  The current default is `v41_poolage`; anything scored before the
  market-structure confluence work is not comparable with anything after it.

Lifecycle **events** have their own unique `idempotency_key`, derived from
`(signal_id, event_type, source timestamp)` — never wall-clock time, or a
replay one second later would look like a new event.

## Signal lifecycle

```
OPEN ──┬─> PARTIAL_TP ──┬─> TP_HIT      (terminal)
       │                ├─> SL_HIT      (terminal)
       ├────────────────┼─> CLOSED      (terminal)
       │                ├─> EXPIRED     (terminal)
       │                └─> CANCELLED   (terminal)
```

| Status | Meaning |
|---|---|
| `OPEN` | Published, nothing hit yet. |
| `PARTIAL_TP` | At least one target hit, more remain. |
| `TP_HIT` | Final target reached. Terminal. |
| `SL_HIT` | Stop hit. Terminal. |
| `CLOSED` | Closed manually or by rule. Terminal. |
| `EXPIRED` | Timed out without resolving. Terminal. |
| `CANCELLED` | Withdrawn before resolving. Terminal. |

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
| GET | `/api/signals/<id>` | public |
| POST | `/api/signals/<id>/archive` | internal |
| POST | `/api/signals/<id>/postmortem` | internal |
| GET | `/api/db/health` | public (code only) |
| GET | `/api/db/usage` | internal |

Filters on `/api/signals/history`: `symbol`, `timeframe`, `direction`,
`status` (repeatable), `strategy_version`, `exchange`, `include_archived=1`,
`limit` (default 25, max 100), `offset`. Archived rows are hidden by default.

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
