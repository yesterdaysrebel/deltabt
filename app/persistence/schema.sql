-- V1 paper-trading bot schema.
--
-- Design rules:
--   * Every timestamp is UTC (timestamptz). IST exists only in the UI.
--   * Append-only where it matters. Rows describing what happened are never
--     UPDATEd; state changes are new rows. positions is the one mutable table
--     and it carries its own event trail in system_events.
--   * Idempotency is a UNIQUE CONSTRAINT, not an in-memory set. An in-memory
--     set is empty exactly when duplicates matter most: after a restart.
--   * strategy_config_hash is stored on every signal so a rule change is
--     visible in the data rather than relying on someone bumping a version.

CREATE TABLE IF NOT EXISTS bot_instance (
    id                  BIGSERIAL PRIMARY KEY,
    instance_uid        TEXT        NOT NULL UNIQUE,
    hostname            TEXT        NOT NULL,
    pid                 INTEGER     NOT NULL,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    stopped_at          TIMESTAMPTZ,
    strategy_version    TEXT        NOT NULL,
    strategy_config     JSONB       NOT NULL,
    risk_config         JSONB       NOT NULL,
    symbols             TEXT[]      NOT NULL
);

CREATE TABLE IF NOT EXISTS heartbeat (
    instance_uid            TEXT PRIMARY KEY,
    beat_at                 TIMESTAMPTZ NOT NULL,
    ws_connected            BOOLEAN     NOT NULL,
    last_ws_message_at      TIMESTAMPTZ,
    last_closed_1m          TIMESTAMPTZ,
    last_closed_5m          TIMESTAMPTZ,
    open_positions          INTEGER     NOT NULL DEFAULT 0,
    equity                  NUMERIC(20,8),
    detail                  JSONB
);

CREATE TABLE IF NOT EXISTS market_candles (
    symbol          TEXT        NOT NULL,
    timeframe       TEXT        NOT NULL,
    bar_open        TIMESTAMPTZ NOT NULL,
    open            NUMERIC(20,8) NOT NULL,
    high            NUMERIC(20,8) NOT NULL,
    low             NUMERIC(20,8) NOT NULL,
    close           NUMERIC(20,8) NOT NULL,
    volume          NUMERIC(24,8) NOT NULL,
    source          TEXT        NOT NULL,
    complete        BOOLEAN     NOT NULL DEFAULT TRUE,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, timeframe, bar_open)
);

-- Every evaluation, whether or not it produced a signal. The rejected ones are
-- the point: "why did it NOT enter" must be answerable from the database.
CREATE TABLE IF NOT EXISTS strategy_signals (
    id                      BIGSERIAL PRIMARY KEY,
    idempotency_key         TEXT        NOT NULL UNIQUE,
    instance_uid            TEXT        NOT NULL,
    symbol                  TEXT        NOT NULL,
    bar_open                TIMESTAMPTZ NOT NULL,
    primary_timeframe       TEXT        NOT NULL,
    confirmation_timeframe  TEXT        NOT NULL,
    direction               SMALLINT,
    outcome                 TEXT        NOT NULL,   -- DETECTED | NO_SETUP | SUPPRESSED | REJECTED | APPROVED
    strategy_version        TEXT        NOT NULL,
    strategy_config_hash    TEXT        NOT NULL,
    conditions_passed       JSONB       NOT NULL,
    conditions_failed       JSONB       NOT NULL,
    indicators              JSONB       NOT NULL,
    entry_price             NUMERIC(20,8),
    stop_price              NUMERIC(20,8),
    target_price            NUMERIC(20,8),
    stop_distance_pct       NUMERIC(12,6),
    reward_risk             NUMERIC(12,6),
    rejection_reason        TEXT,
    detail                  JSONB,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_signals_symbol_bar ON strategy_signals (symbol, bar_open DESC);
CREATE INDEX IF NOT EXISTS ix_signals_outcome ON strategy_signals (outcome, created_at DESC);

CREATE TABLE IF NOT EXISTS paper_orders (
    id                  BIGSERIAL PRIMARY KEY,
    order_uid           TEXT        NOT NULL UNIQUE,
    idempotency_key     TEXT        NOT NULL UNIQUE,
    signal_key          TEXT        NOT NULL REFERENCES strategy_signals (idempotency_key),
    instance_uid        TEXT        NOT NULL,
    symbol              TEXT        NOT NULL,
    side                SMALLINT    NOT NULL,
    order_type          TEXT        NOT NULL,       -- market | limit
    purpose             TEXT        NOT NULL,       -- entry | stop | target
    quantity            INTEGER     NOT NULL,
    limit_price         NUMERIC(20,8),
    status              TEXT        NOT NULL,       -- NEW | WORKING | FILLED | CANCELLED | EXPIRED
    equity_before       NUMERIC(20,8) NOT NULL,
    risk_amount         NUMERIC(20,8) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_fills (
    id                  BIGSERIAL PRIMARY KEY,
    fill_uid            TEXT        NOT NULL UNIQUE,
    order_uid           TEXT        NOT NULL REFERENCES paper_orders (order_uid),
    instance_uid        TEXT        NOT NULL,
    symbol              TEXT        NOT NULL,
    side                SMALLINT    NOT NULL,
    quantity            INTEGER     NOT NULL,
    price               NUMERIC(20,8) NOT NULL,
    notional            NUMERIC(20,8) NOT NULL,
    fee                 NUMERIC(20,8) NOT NULL,
    slippage            NUMERIC(20,8) NOT NULL,
    liquidity           TEXT        NOT NULL,       -- maker | taker
    filled_at           TIMESTAMPTZ NOT NULL,
    -- Microsecond exchange timestamp of the tick that caused the fill. This is
    -- what makes live stop-vs-target ordering provable rather than assumed.
    tick_ts_us          BIGINT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- One fill per order in V1 (no partial fills). The constraint, not the code,
-- is what makes replay after a crash safe.
CREATE UNIQUE INDEX IF NOT EXISTS ux_fills_order ON paper_fills (order_uid);

CREATE TABLE IF NOT EXISTS positions (
    id                  BIGSERIAL PRIMARY KEY,
    position_uid        TEXT        NOT NULL UNIQUE,
    signal_key          TEXT        NOT NULL UNIQUE,
    instance_uid        TEXT        NOT NULL,
    symbol              TEXT        NOT NULL,
    side                SMALLINT    NOT NULL,
    status              TEXT        NOT NULL,       -- OPENING | OPEN | SUSPENDED | CLOSING | CLOSED
    quantity            INTEGER     NOT NULL,
    entry_price         NUMERIC(20,8) NOT NULL,
    stop_price          NUMERIC(20,8) NOT NULL,
    target_price        NUMERIC(20,8) NOT NULL,
    initial_risk        NUMERIC(20,8) NOT NULL,
    risk_per_unit       NUMERIC(20,8) NOT NULL,
    notional            NUMERIC(20,8) NOT NULL,
    equity_before       NUMERIC(20,8) NOT NULL,
    entry_fee           NUMERIC(20,8) NOT NULL DEFAULT 0,
    exit_fee            NUMERIC(20,8) NOT NULL DEFAULT 0,
    funding             NUMERIC(20,8) NOT NULL DEFAULT 0,
    exit_price          NUMERIC(20,8),
    realized_pnl        NUMERIC(20,8),
    r_multiple          NUMERIC(12,6),
    exit_reason         TEXT,
    opened_at           TIMESTAMPTZ NOT NULL,
    closed_at           TIMESTAMPTZ,
    strategy_version    TEXT        NOT NULL
);
-- At most ONE open position per symbol, enforced by the database. This is the
-- backstop behind every application-level check.
CREATE UNIQUE INDEX IF NOT EXISTS ux_positions_open_symbol
    ON positions (symbol) WHERE status IN ('OPENING', 'OPEN', 'SUSPENDED', 'CLOSING');

CREATE TABLE IF NOT EXISTS risk_events (
    id              BIGSERIAL PRIMARY KEY,
    event_id        TEXT        NOT NULL UNIQUE,
    instance_uid    TEXT        NOT NULL,
    symbol          TEXT,
    event_type      TEXT        NOT NULL,       -- LIMIT_BREACH | REJECTION | RESET | COOLDOWN
    limit_name      TEXT,
    limit_value     NUMERIC(20,8),
    observed_value  NUMERIC(20,8),
    reason          TEXT        NOT NULL,
    payload         JSONB,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS system_events (
    id              BIGSERIAL PRIMARY KEY,
    event_id        TEXT        NOT NULL UNIQUE,
    instance_uid    TEXT        NOT NULL,
    symbol          TEXT,
    component       TEXT        NOT NULL,
    event_type      TEXT        NOT NULL,
    severity        TEXT        NOT NULL DEFAULT 'INFO',
    payload         JSONB,
    strategy_version TEXT,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_system_events_time ON system_events (occurred_at DESC);

-- Small key/value durable state: equity, daily counters, consecutive losses.
CREATE TABLE IF NOT EXISTS strategy_state (
    key             TEXT PRIMARY KEY,
    value           JSONB       NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
