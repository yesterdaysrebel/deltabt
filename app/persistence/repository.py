"""Repository interface with a Postgres implementation and an in-memory twin.

The in-memory implementation is not a stub. It enforces exactly the same
uniqueness constraints as the SQL schema, so idempotency and recovery tests
exercise real semantics without needing a database. Any divergence between the
two is a bug in the in-memory one, and
``tests/live/test_persistence_pg.py::test_backends_agree`` runs the same
scenario through both.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import asdict

from app.persistence.models import (
    DuplicateRecord,
    FillRecord,
    InstanceRecord,
    OrderRecord,
    PositionRecord,
    RiskEventRecord,
    SignalRecord,
    SystemEventRecord,
    utc,
)

log = logging.getLogger(__name__)


class Repository(ABC):
    """Everything the bot needs to persist, and nothing it does not."""

    # -- lifecycle ---------------------------------------------------------
    @abstractmethod
    async def connect(self) -> None: ...
    @abstractmethod
    async def close(self) -> None: ...
    @abstractmethod
    async def is_writable(self) -> bool: ...

    # -- instance ----------------------------------------------------------
    @abstractmethod
    async def register_instance(self, rec: InstanceRecord) -> None: ...
    @abstractmethod
    async def stop_instance(self, instance_uid: str) -> None: ...
    @abstractmethod
    async def heartbeat(self, instance_uid: str, **fields) -> None: ...

    # -- market data -------------------------------------------------------
    @abstractmethod
    async def save_candles(self, symbol: str, timeframe: str, bars: list,
                           *, source: str = "ws", complete: bool = True) -> int: ...

    # -- strategy ----------------------------------------------------------
    @abstractmethod
    async def record_signal(self, rec: SignalRecord) -> bool:
        """Insert a signal. Returns False if the idempotency key already
        exists, which means this exact evaluation was already durable."""

    @abstractmethod
    async def signal_exists(self, idempotency_key: str) -> bool: ...

    # -- execution ---------------------------------------------------------
    @abstractmethod
    async def create_order(self, rec: OrderRecord) -> bool: ...
    @abstractmethod
    async def update_order_status(self, order_uid: str, status: str) -> None: ...
    @abstractmethod
    async def record_fill(self, rec: FillRecord) -> bool: ...
    @abstractmethod
    async def fill_exists_for_order(self, order_uid: str) -> bool: ...

    # -- positions ---------------------------------------------------------
    @abstractmethod
    async def open_position(self, rec: PositionRecord) -> bool: ...
    @abstractmethod
    async def update_position(self, rec: PositionRecord) -> None: ...
    @abstractmethod
    async def load_open_positions(self) -> list[PositionRecord]: ...
    @abstractmethod
    async def load_recent_positions(self, limit: int = 50) -> list[PositionRecord]: ...

    # -- audit -------------------------------------------------------------
    @abstractmethod
    async def record_risk_event(self, rec: RiskEventRecord) -> None: ...
    @abstractmethod
    async def record_system_event(self, rec: SystemEventRecord) -> None: ...
    @abstractmethod
    async def recent_signals(self, limit: int = 50) -> list[dict]: ...
    @abstractmethod
    async def recent_system_events(self, limit: int = 50) -> list[dict]: ...

    # -- durable key/value -------------------------------------------------
    @abstractmethod
    async def get_state(self, key: str) -> dict | None: ...
    @abstractmethod
    async def set_state(self, key: str, value: dict) -> None: ...


# =====================================================================
# In-memory
# =====================================================================


class InMemoryRepository(Repository):
    """Same constraints as the schema, no database.

    Used by unit and recovery tests. ``surviving_state`` lets a test simulate
    ``kill -9`` by constructing a fresh repository over the same stores, which
    is what a restart against an unchanged database looks like.
    """

    def __init__(self, shared: dict | None = None) -> None:
        s = shared if shared is not None else {}
        self._s = s
        s.setdefault("instances", {})
        s.setdefault("heartbeats", {})
        s.setdefault("candles", {})
        s.setdefault("signals", {})
        s.setdefault("orders", {})
        s.setdefault("order_keys", set())
        s.setdefault("fills", {})
        s.setdefault("fills_by_order", {})
        s.setdefault("positions", {})
        s.setdefault("positions_by_signal", {})
        s.setdefault("risk_events", [])
        s.setdefault("system_events", [])
        s.setdefault("kv", {})
        self._connected = False
        self.writable = True

    @property
    def store(self) -> dict:
        """The durable substrate. Survives a simulated process death."""
        return self._s

    async def connect(self) -> None:
        self._connected = True

    async def close(self) -> None:
        self._connected = False

    async def is_writable(self) -> bool:
        return self._connected and self.writable

    async def register_instance(self, rec: InstanceRecord) -> None:
        self._s["instances"][rec.instance_uid] = asdict(rec)

    async def stop_instance(self, instance_uid: str) -> None:
        inst = self._s["instances"].get(instance_uid)
        if inst:
            inst["stopped_at"] = True

    async def heartbeat(self, instance_uid: str, **fields) -> None:
        self._s["heartbeats"][instance_uid] = fields

    async def save_candles(self, symbol, timeframe, bars, *, source="ws",
                           complete=True) -> int:
        store = self._s["candles"].setdefault((symbol, timeframe), {})
        n = 0
        for b in bars:
            start = b.start if hasattr(b, "start") else int(b["time"])
            if start in store:
                continue
            store[start] = b
            n += 1
        return n

    async def record_signal(self, rec: SignalRecord) -> bool:
        if rec.idempotency_key in self._s["signals"]:
            return False
        self._s["signals"][rec.idempotency_key] = rec
        return True

    async def signal_exists(self, idempotency_key: str) -> bool:
        return idempotency_key in self._s["signals"]

    async def create_order(self, rec: OrderRecord) -> bool:
        if rec.idempotency_key in self._s["order_keys"]:
            return False
        if rec.order_uid in self._s["orders"]:
            return False
        self._s["order_keys"].add(rec.idempotency_key)
        self._s["orders"][rec.order_uid] = rec
        return True

    async def update_order_status(self, order_uid: str, status: str) -> None:
        o = self._s["orders"].get(order_uid)
        if o:
            o.status = status

    async def record_fill(self, rec: FillRecord) -> bool:
        if rec.order_uid in self._s["fills_by_order"]:
            return False                      # ux_fills_order
        self._s["fills"][rec.fill_uid] = rec
        self._s["fills_by_order"][rec.order_uid] = rec
        return True

    async def fill_exists_for_order(self, order_uid: str) -> bool:
        return order_uid in self._s["fills_by_order"]

    async def open_position(self, rec: PositionRecord) -> bool:
        if rec.signal_key in self._s["positions_by_signal"]:
            return False                      # positions.signal_key UNIQUE
        for p in self._s["positions"].values():
            if p.symbol == rec.symbol and p.is_open:
                return False                  # ux_positions_open_symbol
        self._s["positions"][rec.position_uid] = rec
        self._s["positions_by_signal"][rec.signal_key] = rec.position_uid
        return True

    async def update_position(self, rec: PositionRecord) -> None:
        self._s["positions"][rec.position_uid] = rec

    async def load_open_positions(self) -> list[PositionRecord]:
        return [p for p in self._s["positions"].values() if p.is_open]

    async def load_recent_positions(self, limit: int = 50) -> list[PositionRecord]:
        rows = sorted(self._s["positions"].values(), key=lambda p: p.opened_at,
                      reverse=True)
        return rows[:limit]

    async def record_risk_event(self, rec: RiskEventRecord) -> None:
        self._s["risk_events"].append(rec)

    async def record_system_event(self, rec: SystemEventRecord) -> None:
        self._s["system_events"].append(rec)

    async def recent_signals(self, limit: int = 50) -> list[dict]:
        rows = list(self._s["signals"].values())[-limit:]
        return [asdict(r) for r in reversed(rows)]

    async def recent_system_events(self, limit: int = 50) -> list[dict]:
        return [asdict(r) for r in self._s["system_events"][-limit:][::-1]]

    async def get_state(self, key: str) -> dict | None:
        return self._s["kv"].get(key)

    async def set_state(self, key: str, value: dict) -> None:
        self._s["kv"][key] = value


# =====================================================================
# PostgreSQL
# =====================================================================


class PostgresRepository(Repository):
    def __init__(self, dsn: str, *, min_size: int = 2, max_size: int = 8) -> None:
        self.dsn = dsn
        self._pool = None
        self._min, self._max = min_size, max_size

    async def connect(self) -> None:
        import asyncpg
        self._pool = await asyncpg.create_pool(
            self.dsn, min_size=self._min, max_size=self._max
        )
        await self.migrate()

    async def migrate(self) -> None:
        from pathlib import Path
        sql = (Path(__file__).parent / "schema.sql").read_text()
        async with self._pool.acquire() as con:
            await con.execute(sql)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def is_writable(self) -> bool:
        """Actually attempt a write. A readable database can be read-only
        (failover, disk-full), and a SELECT would not notice."""
        if self._pool is None:
            return False
        try:
            async with self._pool.acquire() as con:
                await con.execute(
                    "INSERT INTO strategy_state (key, value, updated_at) "
                    "VALUES ('_writable_probe', '{}'::jsonb, now()) "
                    "ON CONFLICT (key) DO UPDATE SET updated_at = now()"
                )
            return True
        except Exception as exc:                      # noqa: BLE001
            log.error("database not writable: %s", exc)
            return False

    # -- instance ----------------------------------------------------------

    async def register_instance(self, rec: InstanceRecord) -> None:
        async with self._pool.acquire() as con:
            await con.execute(
                """INSERT INTO bot_instance
                   (instance_uid, hostname, pid, strategy_version,
                    strategy_config, risk_config, symbols)
                   VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7)
                   ON CONFLICT (instance_uid) DO NOTHING""",
                rec.instance_uid, rec.hostname, rec.pid, rec.strategy_version,
                json.dumps(rec.strategy_config), json.dumps(rec.risk_config),
                rec.symbols,
            )

    async def stop_instance(self, instance_uid: str) -> None:
        async with self._pool.acquire() as con:
            await con.execute(
                "UPDATE bot_instance SET stopped_at = now() WHERE instance_uid = $1",
                instance_uid)

    async def heartbeat(self, instance_uid: str, **f) -> None:
        async with self._pool.acquire() as con:
            await con.execute(
                """INSERT INTO heartbeat (instance_uid, beat_at, ws_connected,
                       last_ws_message_at, last_closed_1m, last_closed_5m,
                       open_positions, equity, detail)
                   VALUES ($1, now(), $2,$3,$4,$5,$6,$7,$8::jsonb)
                   ON CONFLICT (instance_uid) DO UPDATE SET
                       beat_at = now(), ws_connected = EXCLUDED.ws_connected,
                       last_ws_message_at = EXCLUDED.last_ws_message_at,
                       last_closed_1m = EXCLUDED.last_closed_1m,
                       last_closed_5m = EXCLUDED.last_closed_5m,
                       open_positions = EXCLUDED.open_positions,
                       equity = EXCLUDED.equity, detail = EXCLUDED.detail""",
                instance_uid, bool(f.get("ws_connected", False)),
                utc(f["last_ws_message_at"]) if f.get("last_ws_message_at") else None,
                utc(f["last_closed_1m"]) if f.get("last_closed_1m") else None,
                utc(f["last_closed_5m"]) if f.get("last_closed_5m") else None,
                int(f.get("open_positions", 0)), f.get("equity"),
                json.dumps(f.get("detail") or {}),
            )

    # -- market data -------------------------------------------------------

    async def save_candles(self, symbol, timeframe, bars, *, source="ws",
                           complete=True) -> int:
        if not bars:
            return 0
        rows = []
        for b in bars:
            start = b.start if hasattr(b, "start") else int(b["time"])
            g = (lambda k: getattr(b, k)) if hasattr(b, "open") else (lambda k: b[k])
            rows.append((symbol, timeframe, utc(start), g("open"), g("high"),
                         g("low"), g("close"), g("volume"), source, complete))
        async with self._pool.acquire() as con:
            await con.executemany(
                """INSERT INTO market_candles (symbol, timeframe, bar_open, open,
                       high, low, close, volume, source, complete)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                   ON CONFLICT (symbol, timeframe, bar_open) DO NOTHING""",
                rows)
        return len(rows)

    # -- strategy ----------------------------------------------------------

    async def record_signal(self, r: SignalRecord) -> bool:
        async with self._pool.acquire() as con:
            out = await con.fetchval(
                """INSERT INTO strategy_signals
                   (idempotency_key, instance_uid, symbol, bar_open,
                    primary_timeframe, confirmation_timeframe, direction, outcome,
                    strategy_version, strategy_config_hash, conditions_passed,
                    conditions_failed, indicators, entry_price, stop_price,
                    target_price, stop_distance_pct, reward_risk,
                    rejection_reason, detail)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12::jsonb,
                           $13::jsonb,$14,$15,$16,$17,$18,$19,$20::jsonb)
                   ON CONFLICT (idempotency_key) DO NOTHING
                   RETURNING id""",
                r.idempotency_key, r.instance_uid, r.symbol, utc(r.bar_open),
                r.primary_timeframe, r.confirmation_timeframe, r.direction,
                r.outcome, r.strategy_version, r.strategy_config_hash,
                json.dumps(r.conditions_passed), json.dumps(r.conditions_failed),
                json.dumps(r.indicators), r.entry_price, r.stop_price,
                r.target_price, r.stop_distance_pct, r.reward_risk,
                r.rejection_reason, json.dumps(r.detail))
        return out is not None

    async def signal_exists(self, key: str) -> bool:
        async with self._pool.acquire() as con:
            return await con.fetchval(
                "SELECT 1 FROM strategy_signals WHERE idempotency_key = $1",
                key) is not None

    # -- execution ---------------------------------------------------------

    async def create_order(self, r: OrderRecord) -> bool:
        async with self._pool.acquire() as con:
            out = await con.fetchval(
                """INSERT INTO paper_orders (order_uid, idempotency_key,
                       signal_key, instance_uid, symbol, side, order_type,
                       purpose, quantity, limit_price, status, equity_before,
                       risk_amount)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                   ON CONFLICT (idempotency_key) DO NOTHING
                   RETURNING id""",
                r.order_uid, r.idempotency_key, r.signal_key, r.instance_uid,
                r.symbol, r.side, r.order_type, r.purpose, r.quantity,
                r.limit_price, r.status, r.equity_before, r.risk_amount)
        return out is not None

    async def update_order_status(self, order_uid: str, status: str) -> None:
        async with self._pool.acquire() as con:
            await con.execute(
                "UPDATE paper_orders SET status=$2, updated_at=now() "
                "WHERE order_uid=$1", order_uid, status)

    async def record_fill(self, r: FillRecord) -> bool:
        async with self._pool.acquire() as con:
            out = await con.fetchval(
                """INSERT INTO paper_fills (fill_uid, order_uid, instance_uid,
                       symbol, side, quantity, price, notional, fee, slippage,
                       liquidity, filled_at, tick_ts_us)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                   ON CONFLICT (order_uid) DO NOTHING
                   RETURNING id""",
                r.fill_uid, r.order_uid, r.instance_uid, r.symbol, r.side,
                r.quantity, r.price, r.notional, r.fee, r.slippage,
                r.liquidity, utc(r.filled_at), r.tick_ts_us)
        return out is not None

    async def fill_exists_for_order(self, order_uid: str) -> bool:
        async with self._pool.acquire() as con:
            return await con.fetchval(
                "SELECT 1 FROM paper_fills WHERE order_uid=$1", order_uid) is not None

    # -- positions ---------------------------------------------------------

    async def open_position(self, r: PositionRecord) -> bool:
        try:
            async with self._pool.acquire() as con:
                out = await con.fetchval(
                    """INSERT INTO positions (position_uid, signal_key,
                           instance_uid, symbol, side, status, quantity,
                           entry_price, stop_price, target_price, initial_risk,
                           risk_per_unit, notional, equity_before, entry_fee,
                           opened_at, strategy_version)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,
                               $15,$16,$17)
                       ON CONFLICT (signal_key) DO NOTHING
                       RETURNING id""",
                    r.position_uid, r.signal_key, r.instance_uid, r.symbol,
                    r.side, r.status, r.quantity, r.entry_price, r.stop_price,
                    r.target_price, r.initial_risk, r.risk_per_unit, r.notional,
                    r.equity_before, r.entry_fee, utc(r.opened_at),
                    r.strategy_version)
            return out is not None
        except Exception as exc:                       # noqa: BLE001
            # ux_positions_open_symbol -- another open position on this symbol.
            if "ux_positions_open_symbol" in str(exc):
                log.error("refusing duplicate open position", extra={
                    "symbol": r.symbol})
                return False
            raise

    async def update_position(self, r: PositionRecord) -> None:
        async with self._pool.acquire() as con:
            await con.execute(
                """UPDATE positions SET status=$2, exit_price=$3,
                       realized_pnl=$4, r_multiple=$5, exit_reason=$6,
                       closed_at=$7, exit_fee=$8, funding=$9
                   WHERE position_uid=$1""",
                r.position_uid, r.status, r.exit_price, r.realized_pnl,
                r.r_multiple, r.exit_reason,
                utc(r.closed_at) if r.closed_at else None, r.exit_fee, r.funding)

    @staticmethod
    def _to_position(row) -> PositionRecord:
        return PositionRecord(
            position_uid=row["position_uid"], signal_key=row["signal_key"],
            instance_uid=row["instance_uid"], symbol=row["symbol"],
            side=row["side"], status=row["status"], quantity=row["quantity"],
            entry_price=float(row["entry_price"]),
            stop_price=float(row["stop_price"]),
            target_price=float(row["target_price"]),
            initial_risk=float(row["initial_risk"]),
            risk_per_unit=float(row["risk_per_unit"]),
            notional=float(row["notional"]),
            equity_before=float(row["equity_before"]),
            entry_fee=float(row["entry_fee"]), exit_fee=float(row["exit_fee"]),
            funding=float(row["funding"]),
            exit_price=float(row["exit_price"]) if row["exit_price"] is not None else None,
            realized_pnl=float(row["realized_pnl"]) if row["realized_pnl"] is not None else None,
            r_multiple=float(row["r_multiple"]) if row["r_multiple"] is not None else None,
            exit_reason=row["exit_reason"],
            opened_at=int(row["opened_at"].timestamp()),
            closed_at=int(row["closed_at"].timestamp()) if row["closed_at"] else None,
            strategy_version=row["strategy_version"])

    async def load_open_positions(self) -> list[PositionRecord]:
        async with self._pool.acquire() as con:
            rows = await con.fetch(
                "SELECT * FROM positions WHERE status IN "
                "('OPENING','OPEN','SUSPENDED','CLOSING') ORDER BY opened_at")
        return [self._to_position(r) for r in rows]

    async def load_recent_positions(self, limit: int = 50) -> list[PositionRecord]:
        async with self._pool.acquire() as con:
            rows = await con.fetch(
                "SELECT * FROM positions ORDER BY opened_at DESC LIMIT $1", limit)
        return [self._to_position(r) for r in rows]

    # -- audit -------------------------------------------------------------

    async def record_risk_event(self, r: RiskEventRecord) -> None:
        async with self._pool.acquire() as con:
            await con.execute(
                """INSERT INTO risk_events (event_id, instance_uid, symbol,
                       event_type, limit_name, limit_value, observed_value,
                       reason, payload)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)
                   ON CONFLICT (event_id) DO NOTHING""",
                r.event_id, r.instance_uid, r.symbol, r.event_type,
                r.limit_name, r.limit_value, r.observed_value, r.reason,
                json.dumps(r.payload))

    async def record_system_event(self, r: SystemEventRecord) -> None:
        async with self._pool.acquire() as con:
            await con.execute(
                """INSERT INTO system_events (event_id, instance_uid, symbol,
                       component, event_type, severity, payload, strategy_version)
                   VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8)
                   ON CONFLICT (event_id) DO NOTHING""",
                r.event_id, r.instance_uid, r.symbol, r.component, r.event_type,
                r.severity, json.dumps(r.payload), r.strategy_version)

    async def recent_signals(self, limit: int = 50) -> list[dict]:
        async with self._pool.acquire() as con:
            rows = await con.fetch(
                "SELECT * FROM strategy_signals ORDER BY created_at DESC LIMIT $1",
                limit)
        return [dict(r) for r in rows]

    async def recent_system_events(self, limit: int = 50) -> list[dict]:
        async with self._pool.acquire() as con:
            rows = await con.fetch(
                "SELECT * FROM system_events ORDER BY occurred_at DESC LIMIT $1",
                limit)
        return [dict(r) for r in rows]

    # -- kv ----------------------------------------------------------------

    async def get_state(self, key: str) -> dict | None:
        async with self._pool.acquire() as con:
            v = await con.fetchval(
                "SELECT value FROM strategy_state WHERE key=$1", key)
        return json.loads(v) if isinstance(v, str) else v

    async def set_state(self, key: str, value: dict) -> None:
        async with self._pool.acquire() as con:
            await con.execute(
                """INSERT INTO strategy_state (key, value, updated_at)
                   VALUES ($1, $2::jsonb, now())
                   ON CONFLICT (key) DO UPDATE
                   SET value = EXCLUDED.value, updated_at = now()""",
                key, json.dumps(value))
