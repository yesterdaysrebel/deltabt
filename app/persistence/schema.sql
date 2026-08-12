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
--   * TWO timestamps on every event: exchange_ts (when the market produced the
--     thing) and received_ts (when this process learned of it). One timestamp
--     cannot express both the replayable ordering and the processing lag, and
--     conflating them is audit finding F8.

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
    exchange_ts             TIMESTAMPTZ,
    received_ts             TIMESTAMPTZ,
    event_type              TEXT        NOT NULL DEFAULT 'SIGNAL_EVALUATED',
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
    -- EXCHANGE time. Expiry compares this against tick timestamps, which are
    -- also exchange time; comparing it against a wall clock is F8.
    created_exchange_ts TIMESTAMPTZ,
    expires_exchange_ts TIMESTAMPTZ,
    received_ts         TIMESTAMPTZ,
    event_type          TEXT        NOT NULL DEFAULT 'ORDER_CREATED',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_fills (
    id                  BIGSERIAL PRIMARY KEY,
    -- DETERMINISTIC: "{order_uid}:f{seq}". Was a random uuid, which meant the
    -- only thing stopping a replayed fill from being inserted twice was the
    -- UNIQUE(order_uid) index below -- and that index also forbade a second
    -- fill for the same order. Deterministic identity separates the two
    -- concerns: replay protection here, multi-fill capability there.
    fill_uid            TEXT        NOT NULL UNIQUE,
    order_uid           TEXT        NOT NULL REFERENCES paper_orders (order_uid),
    -- audit F1: the position is stated, never inferred. Inferring it from
    -- "first position matching symbol" attached a short's fill to a
    -- previously CLOSED long, so every fill after the first in a symbol
    -- recorded the wrong side.
    position_uid        TEXT        NOT NULL,
    seq                 INTEGER     NOT NULL DEFAULT 1,
    purpose             TEXT        NOT NULL DEFAULT 'entry',   -- entry | exit
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
    exchange_ts         TIMESTAMPTZ,
    received_ts         TIMESTAMPTZ,
    event_type          TEXT        NOT NULL DEFAULT 'ORDER_FILLED',
    -- Microsecond exchange timestamp of the tick that caused the fill. This is
    -- what makes live stop-vs-target ordering provable rather than assumed.
    tick_ts_us          BIGINT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Replay safety is UNIQUE(fill_uid) above, which is deterministic. This pair
-- additionally guarantees a fill sequence cannot be reused for an order, so a
-- duplicate arriving with a rewritten uid still cannot double-book.
CREATE UNIQUE INDEX IF NOT EXISTS ux_fills_order_seq ON paper_fills (order_uid, seq);
CREATE INDEX IF NOT EXISTS ix_fills_position ON paper_fills (position_uid);

-- A fill that cannot be matched to a known order and position is QUARANTINED,
-- never written to paper_fills and never silently dropped. Silent corruption of
-- the audit trail is the failure this whole phase exists to prevent.
CREATE TABLE IF NOT EXISTS quarantined_fills (
    id              BIGSERIAL PRIMARY KEY,
    quarantine_uid  TEXT        NOT NULL UNIQUE,
    instance_uid    TEXT        NOT NULL,
    symbol          TEXT,
    order_uid       TEXT,
    position_uid    TEXT,
    reason          TEXT        NOT NULL,
    payload         JSONB       NOT NULL,
    exchange_ts     TIMESTAMPTZ,
    received_ts     TIMESTAMPTZ,
    resolved        BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

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
    hold_seconds        BIGINT,
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
    exchange_ts     TIMESTAMPTZ,
    received_ts     TIMESTAMPTZ,
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
    exchange_ts     TIMESTAMPTZ,
    received_ts     TIMESTAMPTZ,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_system_events_time ON system_events (occurred_at DESC);

-- Small key/value durable state: equity, daily counters, consecutive losses.
CREATE TABLE IF NOT EXISTS strategy_state (
    key             TEXT PRIMARY KEY,
    value           JSONB       NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
