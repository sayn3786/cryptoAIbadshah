-- ============================================================================
-- 019_scalp_trades.sql
-- The 15m scalp book's history (HL testnet; backend/scalp_live.py). One row per
-- scalp the book placed: the sweep -> FVG setup, then what happened to it —
-- filled, cancelled (not filled in 2h / price through the stop), or closed
-- (target, stop, break-even, or the 12h time-out) with the P&L read from the
-- exchange's own fills. Kept apart from `signals`, so the 4h statistics never
-- see a scalp. Safe on either side of the deploy: without the table the book
-- still trades and only skips recording.
-- ============================================================================
BEGIN;

CREATE TABLE IF NOT EXISTS scalp_trades (
    id               bigserial    PRIMARY KEY,
    environment      text         NOT NULL,
    symbol           text         NOT NULL,
    coin             text         NOT NULL,
    direction        text         NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    setup_close_time timestamptz  NOT NULL,          -- the 15m close that completed the setup
    limit_price      numeric      NOT NULL,
    stop_price       numeric      NOT NULL,
    target_price     numeric      NOT NULL,
    rr               numeric,
    size             numeric,
    notional_usd     numeric,
    status           text         NOT NULL CHECK (status IN ('PENDING', 'OPEN', 'CANCELLED', 'CLOSED')),
    placed_at        timestamptz  NOT NULL DEFAULT now(),
    filled_at        timestamptz,
    fill_price       numeric,
    closed_at        timestamptz,
    exit_price       numeric,
    exit_reason      text         CHECK (exit_reason IS NULL OR exit_reason IN
                                         ('target', 'stop', 'breakeven', 'timeout', 'other')),
    pnl_usd          numeric,                        -- after fees
    fees_usd         numeric,
    cancel_reason    text,
    UNIQUE (environment, symbol, setup_close_time)
);

CREATE INDEX IF NOT EXISTS scalp_trades_env_placed ON scalp_trades (environment, placed_at DESC);

COMMIT;
