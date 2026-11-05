# 15m scalp execution review — Nov 4 2026

Scope: the 15m scalp book (`backend/app.py` `_scalp_run` / `_scalp_hl`,
`backend/scalp_live.py`). Reported behaviour: many correlated longs opened at
once, repeat entries after stop-outs, entries that chase extended moves, and
positions stopped out almost immediately. No AGENTS.md exists; this follows
the repository docs and tests.

Scoring weights, the 100 threshold, leverage (11x) and the fixed exits
(−10% / +15% of $50 margin) are **unchanged**. Stops are never widened.

## Confirmed causes (read in the code before this change)

| # | Finding | Where |
|---|---------|-------|
| 1 | No portfolio limits: `room()` allowed one position per coin (18); no per-candle, per-direction or aggregate-risk cap. `SCALP_MAX_EXPOSURE_USD` is a $100k notional ceiling. | `scalp_live.py`, `_scalp_hl` |
| 2 | Candidates were ordered by R:R, which is 1.5 for every fixed-exit scalp, so the coin list order decided who entered. | `_scalp_run` |
| 3 | No stop-out memory. Only flips had a cooldown, and flips were stored as exit reason `other`. A same-way signal on the next candle re-entered. | `_scalp_hl`, `_scalp_flip` |
| 4 | Adds defaulted to 2. A replacement stop that failed after an add only logged a warning; the added size could run without a stop. | `_scalp_add`, `_scalp_add_fixed` |
| 5 | `_attach_exits` returned `exits_ok: True` whenever no exception was raised. Hyperliquid rejects orders inside a 200 response, so a **rejected stop was reported as placed**. This also applies to the 4H book. | `hl_autoexec._attach_exits` |
| 6 | Only an in-process thread lock; two serverless runs could interleave (per-signal claims stopped duplicates of the same signal only). | `_scalp_hl` |
| 7 | Chase handling exists only as engine score points (`range_chase` penalty, `chase_warning`) that a high score can outweigh; `rec_policy.candidate_is_chased` is used by close picks, not the scalp book. | engine, `rec_policy.py` |

## Immediate stop-outs: not attributed

Hyperliquid fills and the scalp DB records are not reachable from the review
environment, so **the reported immediate stop-outs cannot yet be attributed to
a confirmed cause.** Hypotheses, in order of how directly the code allows them:

1. The stop is the plan's stop shifted onto HL's price, while the stale-entry
   guard accepts a fill up to 0.5 × the stop distance away, so the
   fill-to-stop distance can be about half the plan (≈0.45% of price at 11x).
2. A rejected stop recorded as placed (cause 5) — the position would then
   close on a later flip or timeout, not on a stop.
3. A stop moved up by re-fixing exits after an add.
4. Flips and timeouts recorded as `other`.

Every entry and add now writes a `trade_audit` row (signal slot, reference
entry, mark at fill, size, placed stop, stop acknowledgement, trigger type,
15m ATR, fill-to-stop % and in ATR, score) and every exit records its real
reason, so the next stop-outs can be attributed.

## Changes

* `backend/scalp_guard.py` (new, pure): portfolio batch limits, risk per
  position (stop distance × size + 13 bps costs), stop-out memory, reset
  detection, add rules, never-looser stops, entry quality and live-price
  checks, ranking.
* `_scalp_hl`: a KV lock (`scalp:lock:{env}`, 110 s) so concurrent runs return
  `BUSY`; entries per candle counted in KV (`scalp:slot:{env}:{slot}`) and
  committed immediately, so a retry in the same candle sees them; candidates
  ranked explicitly (quality ok, then score, then least extended, then
  symbol); decisions with reasons in the run log.
* Exits: the actual reason (`stop`, `target`, `timeout`, `flip`, `unknown`)
  and time are stored per coin and direction (`scalp:exits:{env}`, bounded to
  300 entries / 7 days). A position that vanished without fills is
  `unknown` — never assumed to be a stop.
* Re-entry after a **stop** needs both the cooldown and a fresh setup: a later
  closed candle on which that direction did not qualify. A new candle alone
  does not count; a coin whose data failed resets nothing. Other and unknown
  exits start no cooldown.
* Adds: off by default. When enabled, an add needs a newer setup than the last
  entry and a reset in between, and must fit the portfolio risk. The stop for
  the whole resulting size is placed first; old stops are cancelled only after
  it is accepted. If it is rejected, the added size is market-reduced and
  checked (`ADD_UNWOUND`, or `ADD_UNWIND_FAILED` with an alert).
* Entries whose stop is rejected are closed at once (`UNPROTECTED_CLOSED`).
* Anti-chase: closed-candle extension from EMA20 in 15m ATR, room to the
  nearest opposing confirmed pivot in R, and live net reward:risk after costs
  at the HL mid. A high score does not override it. LONG and SHORT mirror;
  missing inputs are named (`NO_ATR`, `NO_LEVELS`, `NO_MARK`,
  `ENTRY_QUALITY_UNKNOWN`) and rejected.
* Existing positions are still managed (stops, flips, timeouts) when new
  entries are blocked.
* `/api/hl/scalp` (HL admin token): `portfolio` (limits, open LONG/SHORT,
  aggregate risk, entries this candle, re-entry state per coin, protection per
  coin) and `trade_audit` (last 60). Older run records still read.

## Settings (environment; repository defaults below)

| Setting | Default | Was | Why |
|---------|---------|-----|-----|
| `SCALP_MAX_OPEN` | 4 | 18 | Correlated coins move together; 4 caps a basket. |
| `SCALP_MAX_NEW_PER_CANDLE` | 2 | none | One candle's burst of longs is mostly one bet. |
| `SCALP_MAX_LONGS` / `SCALP_MAX_SHORTS` | 2 / 2 | none | Directional concentration. |
| `SCALP_MAX_RISK_USD` | 25 | none | ≈$5.71 per position (−10% of $50 margin + costs), so 4 full stops; counts pending entries and adds. |
| `SCALP_STOPOUT_COOLDOWN_HOURS` | 4 | none | 16 candles; plus the reset rule. |
| `SCALP_MAX_ADDS` | 0 | 2 | Adds raised risk with no confirmed benefit. |
| `SCALP_MAX_ATR_EXTENSION` | 2.0 | none | Close more than 2 ATR past EMA20 in the trade's way. |
| `SCALP_MIN_ROOM_R` | 0.5 | none | 1.0 rejected ~60% in a synthetic check; 0.5 is a starting point to validate, not a tuned value. |
| `SCALP_MIN_NET_RR` | 1.1 | none | The fixed exits give ≈1.23 after costs at the plan; less means the price moved. |
| `SCALP_MIN_STOP_FRACTION` | 0.5 | none | Fill-to-stop at least half the planned distance (hypothesis 1). |

| `SCALP_HOT_GUARD` | off | — | Nov 5: hot-market cooldown, off until the replay supports it. |
| `SCALP_HOT_READS` | 6 | — | Same-way qualifying setups across the coins in the last hour (4 closes). |
| `SCALP_HOT_TPS` | 3 | — | Same-way targets hit in the last 4 hours. |
| `SCALP_HOT_COOLDOWN_HOURS` | 2 | — | No new entries or adds that way once either fires. |

The hot-market thresholds were fixed before any replay of them. The heat is
recorded (KV `scalp:heat:{env}`, 24h) and shown in `/api/hl/scalp` under
`portfolio.hot_market` even while the guard is off. Its untouched period is
`end_days_ago` 270, because 200 has already been read.

Deployed values were not inspected (no access to the deployment settings);
set them explicitly there rather than relying on these defaults.

## Validation

* Tests: `tests/test_scalp_portfolio.py` (execution),
  `tests/test_scalp_portfolio_compare.py` (replay), updated
  `tests/test_scalp_live.py`.
* Replay: `backend/scalp_portfolio_compare.py` runs the same `scalp_guard`
  rules over the reviewed engine reads — baseline, each change alone, all
  changes — with fees and slippage. Run it through `cadence-backtest.yml`
  (`scalp-portfolio`) for `end_days_ago` 0, 65 and 135, and keep 200 as the
  untouched validation period: do not change settings after looking at it.
* Replay limits: an add is approximated as a second position; the live
  mid-price check cannot be replayed; flips close at the candle close.
