"""Repository interface with a Postgres implementation and an in-memory twin.

The in-memory implementation is not a stub. It enforces exactly the same
uniqueness constraints as the SQL schema, so idempotency and recovery tests
exercise real semantics without needing a database. Any divergence between the
two is a bug in the in-memory one, and
``tests/live/test_persistence_pg.py::test_backends_agree`` runs the same
scenario through both.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import asdict

from app.clock import wall_now
from app.persistence.jsonb import register_codecs
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


def _ts(value):
    """Optional unix seconds -> aware datetime, or None."""
    return utc(value) if value else None


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
    @abstractmethod
    async def load_fills_for_position(self, position_uid: str) -> list: ...
    @abstractmethod
    async def quarantine_fill(self, rec) -> bool:
        """Record a fill that could not be matched. Never silently dropped."""
    @abstractmethod
    async def quarantined_fills(self, limit: int = 100) -> list[dict]: ...

    # -- forward test ------------------------------------------------------
    @abstractmethod
    async def create_experiment(self, ident, planned_days: int = 30) -> bool:
        """Register a new experiment. False if the id already exists."""
    @abstractmethod
    async def active_experiment(self) -> dict | None: ...
    @abstractmethod
    async def get_experiment(self, experiment_id: str) -> dict | None: ...
    @abstractmethod
    async def stop_experiment(self, experiment_id: str, reason: str) -> None: ...

    # -- funding -----------------------------------------------------------
    @abstractmethod
    async def record_funding(self, rec) -> bool:
        """Insert one settlement. False if already charged (idempotent)."""
    @abstractmethod
    async def funding_for_position(self, position_uid: str) -> list: ...
    @abstractmethod
    async def total_funding(self, since: int | None = None) -> float: ...

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
        s.setdefault("fill_seq_keys", set())
        s.setdefault("quarantine", {})
        s.setdefault("funding", {})
        s.setdefault("experiments", {})
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
        # UNIQUE(fill_uid): the deterministic id is what makes a replay a
        # no-op. UNIQUE(order_uid, seq) additionally stops a duplicate whose
        # uid was rewritten from double-booking the same sequence.
        if rec.fill_uid in self._s["fills"]:
            return False
        key = (rec.order_uid, rec.seq)
        if key in self._s["fill_seq_keys"]:
            return False
        self._s["fills"][rec.fill_uid] = rec
        self._s["fill_seq_keys"].add(key)
        self._s["fills_by_order"].setdefault(rec.order_uid, []).append(rec)
        return True

    async def fill_exists_for_order(self, order_uid: str) -> bool:
        return bool(self._s["fills_by_order"].get(order_uid))

    async def load_fills_for_position(self, position_uid: str) -> list:
        return [f for f in self._s["fills"].values()
                if f.position_uid == position_uid]

    async def quarantine_fill(self, rec) -> bool:
        if rec.quarantine_uid in self._s["quarantine"]:
            return False
        self._s["quarantine"][rec.quarantine_uid] = rec
        return True

    async def quarantined_fills(self, limit: int = 100) -> list[dict]:
        return [asdict(r) for r in list(self._s["quarantine"].values())[-limit:]]

    async def create_experiment(self, ident, planned_days: int = 30) -> bool:
        if ident.experiment_id in self._s["experiments"]:
            return False
        if any(e["status"] == "RUNNING" for e in self._s["experiments"].values()):
            return False                       # ux_forward_test_running
        row = ident.to_dict()
        row.update(status="RUNNING", planned_days=planned_days,
                   started_at=None, stopped_at=None, stop_reason=None)
        self._s["experiments"][ident.experiment_id] = row
        return True

    async def active_experiment(self) -> dict | None:
        for e in self._s["experiments"].values():
            if e["status"] == "RUNNING":
                return e
        return None

    async def get_experiment(self, experiment_id: str) -> dict | None:
        return self._s["experiments"].get(experiment_id)

    async def stop_experiment(self, experiment_id: str, reason: str) -> None:
        e = self._s["experiments"].get(experiment_id)
        if e:
            e["status"] = "STOPPED"
            e["stop_reason"] = reason

    async def record_funding(self, rec) -> bool:
        key = (rec.position_uid, rec.exchange_ts)
        if rec.event_id in self._s["funding"] or any(
                (r.position_uid, r.exchange_ts) == key
                for r in self._s["funding"].values()):
            return False
        self._s["funding"][rec.event_id] = rec
        return True

    async def funding_for_position(self, position_uid: str) -> list:
        return [r for r in self._s["funding"].values()
                if r.position_uid == position_uid]

    async def total_funding(self, since: int | None = None) -> float:
        return sum(r.funding_amount for r in self._s["funding"].values()
                   if since is None or r.exchange_ts >= since)

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
        # `init` runs on EVERY pooled connection. Registering the codecs on a
        # single connection would make behaviour depend on which one a query
        # happened to acquire (audit F2).
        self._pool = await asyncpg.create_pool(
            self.dsn, min_size=self._min, max_size=self._max,
            init=register_codecs,
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
                    "VALUES ('_writable_probe', '{}', now()) "
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
                   VALUES ($1,$2,$3,$4,$5,$6,$7)
                   ON CONFLICT (instance_uid) DO NOTHING""",
                rec.instance_uid, rec.hostname, rec.pid, rec.strategy_version,
                rec.strategy_config, rec.risk_config,
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
                   VALUES ($1, now(), $2,$3,$4,$5,$6,$7,$8)
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
                f.get("detail") or {},
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
                    exchange_ts, received_ts, event_type,
                    primary_timeframe, confirmation_timeframe, direction, outcome,
                    strategy_version, strategy_config_hash,
                    experiment_id, config_hash, git_sha, conditions_passed,
                    conditions_failed, indicators, entry_price, stop_price,
                    target_price, stop_distance_pct, reward_risk,
                    rejection_reason, detail)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,
                           $14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26)
                   ON CONFLICT (idempotency_key) DO NOTHING
                   RETURNING id""",
                r.idempotency_key, r.instance_uid, r.symbol, utc(r.bar_open),
                _ts(r.exchange_ts), _ts(r.received_ts), r.event_type,
                r.primary_timeframe, r.confirmation_timeframe, r.direction,
                r.outcome, r.strategy_version, r.strategy_config_hash,
                r.experiment_id, r.config_hash, r.git_sha,
                r.conditions_passed, r.conditions_failed,
                r.indicators, r.entry_price, r.stop_price,
                r.target_price, r.stop_distance_pct, r.reward_risk,
                r.rejection_reason, r.detail)
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
                       risk_amount, created_exchange_ts, expires_exchange_ts,
                       received_ts, event_type)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,
                           $16,$17)
                   ON CONFLICT (idempotency_key) DO NOTHING
                   RETURNING id""",
                r.order_uid, r.idempotency_key, r.signal_key, r.instance_uid,
                r.symbol, r.side, r.order_type, r.purpose, r.quantity,
                r.limit_price, r.status, r.equity_before, r.risk_amount,
                _ts(r.created_exchange_ts), _ts(r.expires_exchange_ts),
                _ts(r.received_ts), r.event_type)
        return out is not None

    async def update_order_status(self, order_uid: str, status: str) -> None:
        async with self._pool.acquire() as con:
            await con.execute(
                "UPDATE paper_orders SET status=$2, updated_at=now() "
                "WHERE order_uid=$1", order_uid, status)

    async def record_fill(self, r: FillRecord) -> bool:
        """Insert one fill. False means it was already durable.

        Two independent guards, and both must report the SAME way as the
        in-memory twin or the shared scenarios are testing a fiction:

          ON CONFLICT (fill_uid)      -- the deterministic id; a plain replay
          ux_fills_order_seq          -- a duplicate whose uid was rewritten

        The second raises rather than conflicting, so it is caught here and
        turned into the same False.
        """
        try:
            return await self._insert_fill(r)
        except Exception as exc:                           # noqa: BLE001
            if "ux_fills_order_seq" in str(exc):
                log.info("fill sequence already booked; not double-booking",
                         extra={"order_uid": r.order_uid, "seq": r.seq})
                return False
            raise

    async def _insert_fill(self, r: FillRecord) -> bool:
        async with self._pool.acquire() as con:
            out = await con.fetchval(
                """INSERT INTO paper_fills (fill_uid, order_uid, instance_uid,
                       position_uid, seq, purpose,
                       symbol, side, quantity, price, notional, fee, slippage,
                       liquidity, filled_at, tick_ts_us, exchange_ts,
                       received_ts, event_type)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,
                           $16,$17,$18,$19)
                   ON CONFLICT (fill_uid) DO NOTHING
                   RETURNING id""",
                r.fill_uid, r.order_uid, r.instance_uid,
                r.position_uid, r.seq, r.purpose,
                r.symbol, r.side,
                r.quantity, r.price, r.notional, r.fee, r.slippage,
                r.liquidity, utc(r.filled_at), r.tick_ts_us,
                _ts(r.exchange_ts), _ts(r.received_ts), r.event_type)
        return out is not None

    async def fill_exists_for_order(self, order_uid: str) -> bool:
        async with self._pool.acquire() as con:
            return await con.fetchval(
                "SELECT 1 FROM paper_fills WHERE order_uid=$1", order_uid) is not None

    async def load_fills_for_position(self, position_uid: str) -> list:
        async with self._pool.acquire() as con:
            rows = await con.fetch(
                "SELECT * FROM paper_fills WHERE position_uid=$1 ORDER BY seq",
                position_uid)
        return [dict(r) for r in rows]

    async def quarantine_fill(self, r) -> bool:
        async with self._pool.acquire() as con:
            out = await con.fetchval(
                """INSERT INTO quarantined_fills (quarantine_uid, instance_uid,
                       symbol, order_uid, position_uid, reason, payload,
                       exchange_ts, received_ts)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                   ON CONFLICT (quarantine_uid) DO NOTHING
                   RETURNING id""",
                r.quarantine_uid, r.instance_uid, r.symbol, r.order_uid,
                r.position_uid, r.reason, r.payload,
                _ts(r.exchange_ts), _ts(r.received_ts))
        return out is not None

    async def quarantined_fills(self, limit: int = 100) -> list[dict]:
        async with self._pool.acquire() as con:
            rows = await con.fetch(
                "SELECT * FROM quarantined_fills ORDER BY created_at DESC "
                "LIMIT $1", limit)
        return [dict(r) for r in rows]

    async def create_experiment(self, ident, planned_days: int = 30) -> bool:
        try:
            async with self._pool.acquire() as con:
                out = await con.fetchval(
                    """INSERT INTO forward_test (experiment_id, status,
                           config_hash, strategy_hash, risk_hash,
                           execution_hash, git_sha, git_dirty, app_version,
                           strategy_version, symbols, snapshot, planned_days)
                       VALUES ($1,'RUNNING',$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                       ON CONFLICT (experiment_id) DO NOTHING
                       RETURNING experiment_id""",
                    ident.experiment_id, ident.config_hash, ident.strategy_hash,
                    ident.risk_hash, ident.execution_hash, ident.git_sha,
                    ident.git_dirty, ident.app_version, ident.strategy_version,
                    list(ident.symbols), ident.snapshot, planned_days)
            return out is not None
        except Exception as exc:                          # noqa: BLE001
            if "ux_forward_test_running" in str(exc):
                log.error("another experiment is already RUNNING")
                return False
            raise

    async def active_experiment(self) -> dict | None:
        async with self._pool.acquire() as con:
            row = await con.fetchrow(
                "SELECT * FROM forward_test WHERE status='RUNNING'")
        return dict(row) if row else None

    async def get_experiment(self, experiment_id: str) -> dict | None:
        async with self._pool.acquire() as con:
            row = await con.fetchrow(
                "SELECT * FROM forward_test WHERE experiment_id=$1",
                experiment_id)
        return dict(row) if row else None

    async def stop_experiment(self, experiment_id: str, reason: str) -> None:
        async with self._pool.acquire() as con:
            await con.execute(
                "UPDATE forward_test SET status='STOPPED', stopped_at=now(), "
                "stop_reason=$2 WHERE experiment_id=$1", experiment_id, reason)

    async def record_funding(self, r) -> bool:
        """False means already charged. Matches the in-memory twin.

        ON CONFLICT covers event_id; the (position_uid, exchange_ts) index
        raises instead, so it is caught and reported the same way.
        """
        try:
            return await self._insert_funding(r)
        except Exception as exc:                           # noqa: BLE001
            if "ux_funding_position_instant" in str(exc):
                log.info("settlement already charged for this position",
                         extra={"position_uid": r.position_uid,
                                "exchange_ts": r.exchange_ts})
                return False
            raise

    async def _insert_funding(self, r) -> bool:
        async with self._pool.acquire() as con:
            out = await con.fetchval(
                """INSERT INTO funding_events (event_id, instance_uid,
                       position_uid, symbol, side, quantity, exchange_ts,
                       received_ts, funding_rate, mark_price, notional,
                       funding_amount, interval_seconds, rate_source)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                   ON CONFLICT (event_id) DO NOTHING
                   RETURNING id""",
                r.event_id, r.instance_uid, r.position_uid, r.symbol, r.side,
                r.quantity, utc(r.exchange_ts), _ts(r.received_ts),
                r.funding_rate, r.mark_price, r.notional, r.funding_amount,
                r.interval_seconds, r.rate_source)
        return out is not None

    async def funding_for_position(self, position_uid: str) -> list:
        async with self._pool.acquire() as con:
            rows = await con.fetch(
                "SELECT * FROM funding_events WHERE position_uid=$1 "
                "ORDER BY exchange_ts", position_uid)
        return [dict(r) for r in rows]

    async def total_funding(self, since: int | None = None) -> float:
        async with self._pool.acquire() as con:
            if since is None:
                v = await con.fetchval("SELECT COALESCE(sum(funding_amount),0) "
                                       "FROM funding_events")
            else:
                v = await con.fetchval(
                    "SELECT COALESCE(sum(funding_amount),0) FROM funding_events "
                    "WHERE exchange_ts >= $1", utc(since))
        return float(v)

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
                       closed_at=$7, exit_fee=$8, funding=$9, hold_seconds=$10
                   WHERE position_uid=$1""",
                r.position_uid, r.status, r.exit_price, r.realized_pnl,
                r.r_multiple, r.exit_reason, _ts(r.closed_at), r.exit_fee,
                r.funding, r.hold_seconds)

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
            hold_seconds=row["hold_seconds"],
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
                       reason, payload, exchange_ts, received_ts)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                   ON CONFLICT (event_id) DO NOTHING""",
                r.event_id, r.instance_uid, r.symbol, r.event_type,
                r.limit_name, r.limit_value, r.observed_value, r.reason,
                r.payload, _ts(r.exchange_ts), _ts(r.received_ts))

    async def record_system_event(self, r: SystemEventRecord) -> None:
        async with self._pool.acquire() as con:
            await con.execute(
                """INSERT INTO system_events (event_id, instance_uid, symbol,
                       component, event_type, severity, payload,
                       strategy_version, exchange_ts, received_ts)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                   ON CONFLICT (event_id) DO NOTHING""",
                r.event_id, r.instance_uid, r.symbol, r.component, r.event_type,
                r.severity, r.payload, r.strategy_version,
                _ts(r.exchange_ts), _ts(r.received_ts))

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
                   VALUES ($1, $2, now())
                   ON CONFLICT (key) DO UPDATE
                   SET value = EXCLUDED.value, updated_at = now()""",
                key, value)
