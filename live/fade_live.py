"""Gainer-fade REAL-MONEY account (owner, 2026-11-02: "approved on tiny run, I have 176$ in account on
delta"; rule A, 3% risk, stop below $140, own sub-account). The rules were fixed before any order in
docs/fade_live_prereg.md; this module is them, nothing more.

It rides the paper runner (live/fade.py): once the paper picks for a UTC day are entered, each pick is
SOLD on Delta with an immediate-or-cancel limit 1% under the bid and an exchange-held stop at the paper
stop distance; 24 h later it is bought back reduce-only. Every venue call goes through
live.client.LiveClient (one send per order, ambiguous writes looked up by client order id, never
resent) and every order through live.orders.OrderRequest.

OFF unless all of: DELTABOT_DRY_RUN is exactly "0", DELTA_ENV is "prod", both key variables are set,
and the wallet looks like the sub-account (between the floor and 1.25 x DELTABOT_EQUITY). Otherwise
the paper runner runs exactly as before and this module only creates its (empty) tables.

What stops it:
  STOPPED  the wallet fell below FLOOR_USD at an entry check -- the run is over (pre-registered).
  HALTED   something it cannot explain: a position it has no record of, a position without a resting
           stop, an order whose outcome it cannot establish, an exit it could not complete. No new
           entries until the owner looks (`python -m live fade-live-resume` clears it).
Neither touches open positions: their stops rest at Delta, and their 24 h exits still run.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import time
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

log = logging.getLogger(__name__)

ACCOUNT = "LIVE"
#: docs/fade_live_prereg.md -- the owner's choices of 2026-11-02.
FLOOR_USD = 140.0
MAX_RISK_PCT = 0.03
#: A wallet holding more than this x DELTABOT_EQUITY is not the $176 sub-account.
WRONG_ACCOUNT_RATIO = 1.25
#: One position's notional may use at most this share of the balance (two must fit at 1x).
MAX_POSITION_SHARE = 0.45
LEVERAGE = 1
#: live/broker.py: liquidation must sit this many times further away than the stop.
LIQUIDATION_BUFFER = 3.0
#: The worst price an entry or a 24 h exit may fill at, as a fraction beyond the touch.
SLIP = 0.01
#: The stop's fill cap, in R beyond the stop (live/orders.py STOP_LIMIT_CAP_R).
STOP_CAP_R = 1.5
MAX_OPEN = 2
#: An entry later than this after 00:00 UTC is not the trade paper made: skip it.
ENTRY_WINDOW = 30 * 60
#: Seconds past 00:00 UTC before yesterday's positions are bought back.
EXIT_AFTER = 5
#: How long a fresh fill may wait for its stop to show as resting at Delta.
PROTECT_WAIT = 30.0
#: A position flat at Delta with no closing order found is called closed after this long.
UNEXPLAINED_FLAT = 300
#: How often the wallet balance is snapshotted for the report.
BALANCE_EVERY = 300.0
DAY = 86400

SCHEMA = """
CREATE TABLE IF NOT EXISTS fade_live_positions (
    id             BIGSERIAL PRIMARY KEY,
    day            TIMESTAMPTZ NOT NULL,
    symbol         TEXT NOT NULL,
    rank           INTEGER NOT NULL,
    status         TEXT NOT NULL,             -- SENDING | OPEN | CLOSED | SKIPPED | FAILED
    skip_reason    TEXT,
    product_id     BIGINT,
    contract_value DOUBLE PRECISION,
    contracts      INTEGER,
    leverage       INTEGER,
    balance_before DOUBLE PRECISION,
    paper_entry    DOUBLE PRECISION,          -- the paper A trade's entry on the same pick
    ref_price      DOUBLE PRECISION,          -- best bid when the order was sent
    limit_price    DOUBLE PRECISION,
    stop_price     DOUBLE PRECISION,
    entry_cid      TEXT,
    entry_time     TIMESTAMPTZ,
    entry_price    DOUBLE PRECISION,          -- average fill
    entry_fee      DOUBLE PRECISION,
    flat_since     TIMESTAMPTZ,               -- first pass that found it flat with no closing order
    exit_time      TIMESTAMPTZ,
    exit_price     DOUBLE PRECISION,
    exit_fee       DOUBLE PRECISION,
    exit_reason    TEXT,                      -- TIME | STOP_LOSS | LIQUIDATION | UNPROTECTED | MANUAL_CLOSE | UNKNOWN_EXIT
    pnl            DOUBLE PRECISION,          -- price move x size - fees; funding is in the wallet, not here
    r              DOUBLE PRECISION,
    detail         JSONB,
    UNIQUE (day, symbol)
);
CREATE TABLE IF NOT EXISTS fade_live_state (
    id         INTEGER PRIMARY KEY DEFAULT 1,
    status     TEXT NOT NULL,                 -- RUNNING | STOPPED | HALTED | OFF
    reason     TEXT,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    balance    DOUBLE PRECISION,
    balance_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS fade_live_events (
    id      BIGSERIAL PRIMARY KEY,
    at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind    TEXT NOT NULL,
    symbol  TEXT,
    detail  JSONB
);
"""


# ============================================================ pure logic (tested)

def enabled(env: dict | None = None) -> tuple[bool, str]:
    """Whether this process may trade real money, and why not. Strict on purpose: an unset
    DELTABOT_DRY_RUN reads as "not a dry run" elsewhere in live/, but here only an explicit "0" counts."""
    src = os.environ if env is None else env
    if (src.get("DELTABOT_DRY_RUN") or "").strip() != "0":
        return False, 'dry_run is not "0"'
    if (src.get("DELTA_ENV") or "").strip().lower() != "prod":
        return False, "DELTA_ENV is not prod"
    if not (src.get("DELTA_API_KEY") or "").strip() or not (src.get("DELTA_API_SECRET") or "").strip():
        return False, "no venue key"
    try:
        risk = float(src.get("DELTABOT_RISK_PER_TRADE") or "nan")
        start = float(src.get("DELTABOT_EQUITY") or "nan")
    except ValueError:
        return False, "sizing is not numeric"
    if not 0 < risk <= MAX_RISK_PCT:
        return False, f"risk_per_trade {risk} is outside (0, {MAX_RISK_PCT}]"
    if not start > FLOOR_USD:
        return False, f"equity_usd {start} is not above the ${FLOOR_USD:.0f} floor"
    return True, "on"


def leverage_ok(dist: float, maintenance_pct: float, leverage: int = LEVERAGE) -> bool:
    """Liquidation (1/leverage - maintenance) at least LIQUIDATION_BUFFER x the stop distance away."""
    return dist > 0 and 1.0 / leverage - maintenance_pct / 100.0 >= LIQUIDATION_BUFFER * dist


def contracts_for(balance: float, risk_pct: float, ref: float, stop: float, cv: float) -> int:
    """Whole contracts risking at most `risk_pct` of the balance between ref and stop, using at most
    MAX_POSITION_SHARE of it as notional at 1x."""
    if not (balance > 0 and stop > ref > 0 and cv > 0):
        return 0
    by_risk = risk_pct * balance / ((stop - ref) * cv)
    by_margin = MAX_POSITION_SHARE * balance * LEVERAGE / (ref * cv)
    return int(min(by_risk, by_margin) // 1)


def to_tick(price: float, tick: Decimal, up: bool) -> Decimal:
    """`price` on the tick grid, rounded the way that keeps the order inside its limit."""
    q = (Decimal(str(price)) / tick).to_integral_value(ROUND_CEILING if up else ROUND_FLOOR)
    return (q * tick).normalize()


def filled(row: dict | None) -> int:
    if not row:
        return 0
    try:
        return int(round(float(row.get("size") or 0))) - int(round(float(row.get("unfilled_size") or 0)))
    except (TypeError, ValueError):
        return 0


def fill_price(row: dict | None) -> float | None:
    v = (row or {}).get("average_fill_price")
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def fee(row: dict | None) -> float:
    try:
        return float((row or {}).get("paid_commission") or (row or {}).get("commission") or 0)
    except (TypeError, ValueError):
        return 0.0


def money(entry: float, exit_: float, contracts: int, cv: float, stop: float, fees: float) -> tuple[float, float | None]:
    """(pnl, r) of a short: price move times size less both fees, over the planned risk."""
    qty = contracts * cv
    pnl = (entry - exit_) * qty - fees
    risk = (stop - entry) * qty
    return pnl, (pnl / risk if risk > 0 else None)


def _ts(t: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(float(t), dt.timezone.utc)


# ============================================================ the account

class LiveFade:
    """The real account. `client` is a live.client.LiveClient on prod (a fake in tests). Its venue
    methods are synchronous and are called through asyncio.to_thread."""

    def __init__(self, client, *, risk_pct: float, start_usd: float, kill_switch_path: str | None = None,
                 protect_wait: float = PROTECT_WAIT, sleep=time.sleep):
        self.client = client
        self.risk_pct = float(risk_pct)
        self.start_usd = float(start_usd)
        self.kill_switch_path = kill_switch_path
        self.protect_wait = protect_wait
        self._sleep = sleep
        self._products: dict[str, dict] = {}
        self._last_balance_at = 0.0

    # -- venue reads -------------------------------------------------------------------------
    def product(self, symbol: str) -> dict:
        if symbol not in self._products:
            p = self.client.get_product(symbol)
            self._products[symbol] = {"id": int(p["id"]), "tick": Decimal(str(p["tick_size"])),
                                      "cv": float(p["contract_value"]),
                                      "mm": float(p.get("maintenance_margin") or 0)}
        return self._products[symbol]

    def balance(self) -> float:
        for row in self.client.get_balance():
            if str(row.get("asset_symbol", "")).upper() in ("USD", "USDT"):
                return float(row.get("balance") or 0)
        return 0.0

    def venue_size(self, product_id: int) -> int:
        from live.reconcile import _signed
        row = self.client.get_position(product_id)
        return _signed(row) if row else 0

    def stop_resting(self, product_id: int) -> bool:
        return any(r.get("reduce_only") and r.get("stop_order_type") == "stop_loss_order"
                   and r.get("product_id") in (None, product_id)
                   for r in self.client.get_pending_orders(product_id))

    def wait_protected(self, product_id: int) -> bool:
        deadline = time.monotonic() + self.protect_wait
        while not self.stop_resting(product_id):
            if time.monotonic() >= deadline:
                return False
            self._sleep(2.0)
        return True

    def last_close(self, product_id: int) -> dict | None:
        """The newest filled reduce-only BUY on this product: what closed a short."""
        for o in self.client.order_history(product_id, 20):
            if o.get("reduce_only") and filled(o) and str(o.get("side")).lower() == "buy":
                return o
        return None

    # -- venue writes ------------------------------------------------------------------------
    def buy_back(self, symbol: str, cid_base: str) -> tuple[dict | None, int]:
        """Close the short on `symbol`: reduce-only IOC limit 1% over the ask, then reduce-only IOC
        market for whatever is left. Returns (last filled row or None, contracts still open).
        reduce_only means a size that disagrees with the venue's can never OPEN a long."""
        from live.orders import OrderRequest, OrderType, Side, TimeInForce, client_order_id
        p = self.product(symbol)
        last = None
        size = abs(self.venue_size(p["id"]))
        if size:
            ask = float((self.client.get_ticker(symbol).get("quotes") or {}).get("best_ask") or 0)
            if ask > 0:
                row = self.client.place_order(OrderRequest(
                    product_id=p["id"], size=size, side=Side.BUY, order_type=OrderType.LIMIT,
                    client_order_id=client_order_id(cid_base, "limit"),
                    limit_price=to_tick(ask * (1 + SLIP), p["tick"], up=True),
                    time_in_force=TimeInForce.IOC, reduce_only=True, note="fade-live exit"))
                last = row if filled(row) else None
            size = abs(self.venue_size(p["id"]))
        if size:
            row = self.client.place_order(OrderRequest(
                product_id=p["id"], size=size, side=Side.BUY, order_type=OrderType.MARKET,
                client_order_id=client_order_id(cid_base, "market"),
                time_in_force=TimeInForce.IOC, reduce_only=True, note="fade-live exit (rest)"))
            last = row if filled(row) else last
            size = abs(self.venue_size(p["id"]))
        return last, size

    def cancel_leftovers(self, product_id: int) -> int:
        """Cancel resting reduce-only orders (the old bracket) -- ONLY once the coin is flat, so a
        stop is never taken away from a position that still exists."""
        if self.venue_size(product_id) != 0:
            return 0
        n = 0
        for r in self.client.get_pending_orders(product_id) + self.client.get_open_orders(product_id):
            if r.get("reduce_only") and r.get("id") is not None:
                self.client.cancel_order(int(r["id"]), product_id)
                n += 1
        return n

    # -- state -------------------------------------------------------------------------------
    async def state(self, con) -> str:
        s = await con.fetchval("SELECT status FROM fade_live_state WHERE id = 1")
        if s in (None, "OFF"):
            await con.execute("INSERT INTO fade_live_state (id, status, reason) VALUES (1, 'RUNNING', 'started') "
                              "ON CONFLICT (id) DO UPDATE SET status = 'RUNNING', reason = 'started', at = now() "
                              "WHERE fade_live_state.status = 'OFF'")
            return "RUNNING"
        return s

    async def set_state(self, con, status: str, reason: str) -> None:
        """STOPPED is final; a repeat of the same status and reason is not re-recorded."""
        cur = await con.fetchrow("SELECT status, reason FROM fade_live_state WHERE id = 1")
        if cur and (cur["status"] == "STOPPED" or (cur["status"], cur["reason"]) == (status, reason[:500])):
            return
        await con.execute("UPDATE fade_live_state SET status = $1, reason = $2, at = now() WHERE id = 1",
                          status, reason[:500])
        await event(con, status, None, {"reason": reason})
        log.error("fade live account %s: %s", status, reason)

    # -- one pass ----------------------------------------------------------------------------
    async def step(self, con, now: float) -> None:
        """Reconcile, exit, then enter -- in that order, so a repeated coin is flat before it is sold
        again. Never raises: a failure is recorded and the paper runner carries on."""
        try:
            await self.state(con)
            await self._reconcile(con, now)
            await self._exits(con, now)
            if await self.state(con) == "RUNNING":
                await self._entries(con, now)
            if now - self._last_balance_at >= BALANCE_EVERY:
                bal = await asyncio.to_thread(self.balance)
                await con.execute("UPDATE fade_live_state SET balance = $1, balance_at = now() WHERE id = 1", bal)
                self._last_balance_at = now
        except Exception as exc:                          # noqa: BLE001
            log.exception("fade live step failed")
            try:
                await event(con, "ERROR", None, {"error": f"{type(exc).__name__}: {exc}"[:400]})
            except Exception:                             # noqa: BLE001
                pass

    async def _reconcile(self, con, now: float) -> None:
        from live.client import VenueError
        from live.reconcile import _signed
        rows = await con.fetch("SELECT * FROM fade_live_positions WHERE status IN ('SENDING', 'OPEN') ORDER BY id")
        ours = {int(r["product_id"]) for r in rows if r["product_id"] is not None}
        for v in await asyncio.to_thread(self.client.get_positions):
            size = _signed(v)
            try:
                pid = int(v.get("product_id"))
            except (TypeError, ValueError):
                pid = None
            if size != 0 and pid not in ours:
                await self.set_state(con, "HALTED", f"a {v.get('product_symbol') or pid} position ({size} contracts) "
                                     "at Delta this bot has no record of; not touched")
        for r in rows:
            if r["status"] == "SENDING":
                # Sent, outcome never seen (a crash or an ambiguous write): ask by client order id.
                try:
                    o = await asyncio.to_thread(self.client.get_order_by_client_id, r["entry_cid"])
                except VenueError as exc:
                    await self.set_state(con, "HALTED", f"{r['symbol']} entry outcome unknown: {exc}")
                    continue
                if filled(o):
                    await self._opened(con, r, o, now)
                else:
                    await con.execute("UPDATE fade_live_positions SET status = 'FAILED', skip_reason = $2 "
                                      "WHERE id = $1", r["id"], "the entry never filled at Delta")
                continue
            if await asyncio.to_thread(self.venue_size, r["product_id"]) != 0:
                if r["flat_since"] is not None:
                    await con.execute("UPDATE fade_live_positions SET flat_since = NULL WHERE id = $1", r["id"])
                if not await asyncio.to_thread(self.stop_resting, r["product_id"]):
                    await self._close(con, r, now, "UNPROTECTED", "no stop resting at Delta")
                continue
            await self._closed_at_venue(con, r, now)

    async def _closed_at_venue(self, con, r, now: float) -> None:
        """Flat at Delta while we hold it OPEN: its stop (or a liquidation) closed it."""
        from live.ledger import close_facts
        o = await asyncio.to_thread(self.last_close, r["product_id"])
        if o is None:
            if r["flat_since"] is None:
                await con.execute("UPDATE fade_live_positions SET flat_since = $2 WHERE id = $1", r["id"], _ts(now))
                return
            if now - r["flat_since"].timestamp() < UNEXPLAINED_FLAT:
                return
        facts = close_facts(o) if o else {}
        ex = facts.get("exit_price") or None
        fees = (r["entry_fee"] or 0) + (facts.get("exit_fee") or 0)
        pnl, rr = money(r["entry_price"], ex, r["contracts"], r["contract_value"], r["stop_price"], fees) \
            if ex and r["entry_price"] else (None, None)
        reason = facts.get("exit_reason") or "UNKNOWN_EXIT"
        await con.execute(
            "UPDATE fade_live_positions SET status = 'CLOSED', exit_time = $2, exit_price = $3, exit_fee = $4, "
            "exit_reason = $5, pnl = $6, r = $7 WHERE id = $1",
            r["id"], _ts(now), ex, facts.get("exit_fee"), reason, pnl, rr)
        await asyncio.to_thread(self.cancel_leftovers, r["product_id"])
        await event(con, "CLOSED_AT_DELTA", r["symbol"], facts)
        log.warning("fade live %s closed at Delta: %s, %s", r["symbol"], reason, pnl)
        if reason != "STOP_LOSS":                       # live/ledger.py's name for the bracket stop
            await self.set_state(con, "HALTED", f"{r['symbol']} closed at Delta by {reason}, not its stop")

    async def _close(self, con, r, now: float, reason: str, why: str) -> None:
        """Buy back an OPEN position ourselves (24 h exit, or no stop resting)."""
        from live.orders import client_order_id
        row, left = await asyncio.to_thread(self.buy_back, r["symbol"],
                                            client_order_id("fade-live", str(r["id"]), reason.lower()))
        if left:
            await event(con, "EXIT_INCOMPLETE", r["symbol"], {"left": left, "reason": reason})
            await self.set_state(con, "HALTED", f"{r['symbol']} {reason} exit left {left} contracts open")
            return                                       # still OPEN; its stop is untouched
        await asyncio.to_thread(self.cancel_leftovers, r["product_id"])
        if row is None:
            await self._closed_at_venue(con, r, now)     # it was already flat: the stop got there first
            return
        ex = fill_price(row)
        pnl, rr = money(r["entry_price"], ex, r["contracts"], r["contract_value"], r["stop_price"],
                        (r["entry_fee"] or 0) + fee(row)) if ex and r["entry_price"] else (None, None)
        await con.execute(
            "UPDATE fade_live_positions SET status = 'CLOSED', exit_time = $2, exit_price = $3, exit_fee = $4, "
            "exit_reason = $5, pnl = $6, r = $7 WHERE id = $1", r["id"], _ts(now), ex, fee(row), reason, pnl, rr)
        log.warning("fade live %s closed (%s) at %s: %s", r["symbol"], reason, ex, pnl)
        if reason != "TIME":
            await self.set_state(con, "HALTED", f"{r['symbol']} closed by the bot: {why}")

    async def _exits(self, con, now: float) -> None:
        for r in await con.fetch("SELECT * FROM fade_live_positions WHERE status = 'OPEN' AND day <= $1 ORDER BY id",
                                 _ts(now - DAY - EXIT_AFTER)):
            await self._close(con, r, now, "TIME", "24 h exit")

    async def _entries(self, con, now: float) -> None:
        from live.guards import kill_switch_engaged
        day = int(now) // DAY * DAY
        if now >= day + ENTRY_WINDOW:
            return
        if await con.fetchval("SELECT status FROM fade_days WHERE day = $1", _ts(day)) != "ENTERED":
            return                                       # the paper picks are not made yet
        picks = await con.fetch("SELECT symbol, rank, atrp, entry_price FROM fade_positions "
                                "WHERE account = 'A' AND day = $1 ORDER BY rank", _ts(day))
        done = {r["symbol"] for r in await con.fetch("SELECT symbol FROM fade_live_positions WHERE day = $1", _ts(day))}
        for p in picks:
            if p["symbol"] in done:
                continue
            if kill_switch_engaged(self.kill_switch_path):
                await self._skip(con, day, p, "kill switch engaged")
            elif not p["entry_price"] or not p["atrp"]:
                await self._skip(con, day, p, "paper had no entry for this pick")
            else:
                await self._enter_one(con, day, p, now)
            if await self.state(con) != "RUNNING":
                return

    async def _skip(self, con, day: int, p, why: str) -> None:
        await con.execute(
            "INSERT INTO fade_live_positions (day, symbol, rank, status, skip_reason, paper_entry) "
            "VALUES ($1, $2, $3, 'SKIPPED', $4, $5) ON CONFLICT (day, symbol) DO NOTHING",
            _ts(day), p["symbol"], p["rank"], why, p["entry_price"])
        log.warning("fade live %s skipped: %s", p["symbol"], why)

    async def _enter_one(self, con, day: int, p, now: float) -> None:
        from live.client import AmbiguousWrite, VenueError, VenueRejected
        from live.fade import K_STOP
        from live.orders import OrderRequest, OrderType, Side, StopTriggerMethod, TimeInForce, client_order_id
        sym = p["symbol"]
        bal = await asyncio.to_thread(self.balance)
        if bal < FLOOR_USD:
            await self._skip(con, day, p, f"run stopped: balance ${bal:.2f} is under ${FLOOR_USD:.0f}")
            await self.set_state(con, "STOPPED", f"balance ${bal:.2f} under the ${FLOOR_USD:.0f} floor")
            return
        if bal > WRONG_ACCOUNT_RATIO * self.start_usd:
            await self._skip(con, day, p, f"balance ${bal:.2f} is not the ${self.start_usd:.0f} sub-account")
            await self.set_state(con, "HALTED", f"wallet ${bal:.2f} > {WRONG_ACCOUNT_RATIO} x ${self.start_usd:.0f}: "
                                 "wrong account?")
            return
        if await con.fetchval("SELECT count(*) FROM fade_live_positions WHERE status IN ('SENDING', 'OPEN')") >= MAX_OPEN:
            await self._skip(con, day, p, "two positions already open")
            return
        prod = await asyncio.to_thread(self.product, sym)
        if await asyncio.to_thread(self.venue_size, prod["id"]) != 0:
            await self._skip(con, day, p, "a position on this coin is still open at Delta")
            return
        t = await asyncio.to_thread(self.client.get_ticker, sym)
        q = t.get("quotes") or {}
        bid, ask, mark = float(q.get("best_bid") or 0), float(q.get("best_ask") or 0), float(t.get("mark_price") or 0)
        if min(bid, ask, mark) <= 0:
            await self._skip(con, day, p, "no bid, ask or mark")
            return
        dist = K_STOP * float(p["atrp"])
        stop = to_tick(bid * (1 + dist), prod["tick"], up=True)
        unit = float(stop) - bid
        if mark >= float(stop) or ask - bid > 0.25 * unit:
            await self._skip(con, day, p, f"book unusable: bid {bid:g} ask {ask:g} mark {mark:g} stop {stop}")
            return
        if not leverage_ok(dist, prod["mm"]):
            await self._skip(con, day, p, f"stop {dist:.0%} away is too far for 1x leverage")
            return
        n = contracts_for(bal, self.risk_pct, bid, float(stop), prod["cv"])
        if n < 1:
            await self._skip(con, day, p, f"below one contract at {self.risk_pct:.0%} risk")
            return
        await asyncio.to_thread(self.client.set_order_leverage, prod["id"], LEVERAGE)
        got = await asyncio.to_thread(self.client.get_order_leverage, prod["id"])
        if int(round(got)) != LEVERAGE:
            await self._skip(con, day, p, f"leverage reads {got:g}x, not {LEVERAGE}x")
            return
        limit = to_tick(bid * (1 - SLIP), prod["tick"], up=False)
        cap = to_tick(float(stop) + STOP_CAP_R * unit, prod["tick"], up=True)
        cid = client_order_id("fade-live", str(day), sym, "entry")
        # Written BEFORE the send: a crash after it leaves a SENDING row that _reconcile resolves by cid.
        row_id = await con.fetchval(
            "INSERT INTO fade_live_positions (day, symbol, rank, status, product_id, contract_value, contracts, "
            "leverage, balance_before, paper_entry, ref_price, limit_price, stop_price, entry_cid) "
            "VALUES ($1,$2,$3,'SENDING',$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) RETURNING id",
            _ts(day), sym, p["rank"], prod["id"], prod["cv"], n, LEVERAGE, bal, p["entry_price"], bid,
            float(limit), float(stop), cid)
        order = OrderRequest(product_id=prod["id"], size=n, side=Side.SELL, order_type=OrderType.LIMIT,
                             client_order_id=cid, limit_price=limit, time_in_force=TimeInForce.IOC,
                             bracket_stop_loss_price=stop, bracket_stop_loss_limit_price=cap,
                             stop_trigger_method=StopTriggerMethod.MARK, note="fade-live entry")
        try:
            o = await asyncio.to_thread(self.client.place_order, order)
        except VenueRejected as exc:
            await con.execute("UPDATE fade_live_positions SET status = 'FAILED', skip_reason = $2 WHERE id = $1",
                              row_id, f"Delta refused the order: {exc}"[:300])
            await event(con, "REJECTED", sym, {"error": str(exc)[:400]})
            return
        except (AmbiguousWrite, VenueError) as exc:
            await self.set_state(con, "HALTED", f"{sym} entry outcome unknown: {exc}")
            return                                       # left SENDING: _reconcile asks again
        if not filled(o):
            await con.execute("UPDATE fade_live_positions SET status = 'SKIPPED', skip_reason = $2 WHERE id = $1",
                              row_id, "nothing filled within 1% of the bid")
            return
        await self._opened(con, await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", row_id), o, now)

    async def _opened(self, con, r, o: dict, now: float) -> None:
        n, px = filled(o), fill_price(o)
        await con.execute("UPDATE fade_live_positions SET status = 'OPEN', contracts = $2, entry_price = $3, "
                          "entry_fee = $4, entry_time = $5 WHERE id = $1", r["id"], n, px, fee(o), _ts(now))
        log.warning("fade live SOLD %s x%d at %s (stop %s)", r["symbol"], n, px, r["stop_price"])
        if not await asyncio.to_thread(self.wait_protected, r["product_id"]):
            fresh = await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", r["id"])
            await self._close(con, fresh, now, "UNPROTECTED", f"no stop resting {self.protect_wait:.0f}s after the fill")


async def ensure_schema(con) -> None:
    await con.execute(SCHEMA)


async def event(con, kind: str, symbol: str | None, detail: dict) -> None:
    await con.execute("INSERT INTO fade_live_events (kind, symbol, detail) VALUES ($1, $2, $3)",
                      kind, symbol, json.dumps(detail, default=str))


async def record_off(con, reason: str) -> None:
    """Say in the database why the real account is not running (the report reads it)."""
    await con.execute("INSERT INTO fade_live_state (id, status, reason) VALUES (1, 'OFF', $1) "
                      "ON CONFLICT (id) DO UPDATE SET status = 'OFF', reason = $1, at = now() "
                      "WHERE fade_live_state.status IN ('OFF', 'RUNNING')", reason[:500])


def from_env(env: dict | None = None) -> tuple[LiveFade | None, str]:
    """(the real account, "on") if this process may trade; else (None, why not)."""
    ok, why = enabled(env)
    if not ok:
        return None, why
    from live.config import client_from_env
    src = os.environ if env is None else env
    acct = LiveFade(client_from_env(src), risk_pct=float(src["DELTABOT_RISK_PER_TRADE"]),
                    start_usd=float(src["DELTABOT_EQUITY"]))
    bal = acct.balance()
    if not FLOOR_USD <= bal <= WRONG_ACCOUNT_RATIO * acct.start_usd:
        return None, (f"wallet balance ${bal:.2f} is outside ${FLOOR_USD:.0f}-"
                      f"${WRONG_ACCOUNT_RATIO * acct.start_usd:.0f}: not the sub-account, or under the floor")
    log.warning("FADE LIVE ACCOUNT ON (prod): real orders, %.0f%% risk, balance $%.2f, floor $%.0f",
                100 * acct.risk_pct, bal, FLOOR_USD)
    return acct, "on"


# ============================================================ operator commands

def probe(symbol: str = "XRPUSD", env: dict | None = None) -> int:
    """ONE real contract through the exact order shape the account uses, then flat again.

        python -m live fade-live-probe [SYMBOL]

    Owner's yes required each time (docs/fade_live_prereg.md step 3). Costs a few cents of fees."""
    from live.config import client_from_env
    from live.orders import OrderRequest, OrderType, Side, StopTriggerMethod, TimeInForce, client_order_id
    src = os.environ if env is None else env
    client = client_from_env(src)
    if not client.is_prod:
        print("refusing: DELTA_ENV is not prod (this probe exists to test the prod key)")
        return 2
    acct = LiveFade(client, risk_pct=MAX_RISK_PCT, start_usd=float(src.get("DELTABOT_EQUITY") or 176))
    print("1. wallet rows:")
    for b in client.get_balance():
        print(f"   {b.get('asset_symbol')}: balance={b.get('balance')} available={b.get('available_balance')}")
    bal = acct.balance()
    if not FLOOR_USD <= bal <= WRONG_ACCOUNT_RATIO * acct.start_usd:
        print(f"refusing: balance ${bal:.2f} does not look like the sub-account")
        return 2
    held = [v for v in client.get_positions() if acct_signed(v)]
    if held:
        print(f"refusing: the account already holds positions: {[v.get('product_symbol') for v in held]}")
        return 2
    p = acct.product(symbol)
    print(f"2. {symbol}: product {p['id']}, tick {p['tick']}, contract {p['cv']}, maintenance {p['mm']}%")
    client.set_order_leverage(p["id"], LEVERAGE)
    print(f"3. leverage set, reads back {client.get_order_leverage(p['id'])}x")
    t = client.get_ticker(symbol)
    bid = float(t["quotes"]["best_bid"])
    stop = to_tick(bid * 1.25, p["tick"], up=True)
    cap = to_tick(bid * 1.25 + 1.5 * (bid * 0.25), p["tick"], up=True)
    cid = client_order_id("fade-live-probe", symbol, str(int(time.time())))
    o = client.place_order(OrderRequest(
        product_id=p["id"], size=1, side=Side.SELL, order_type=OrderType.LIMIT, client_order_id=cid,
        limit_price=to_tick(bid * (1 - SLIP), p["tick"], up=False), time_in_force=TimeInForce.IOC,
        bracket_stop_loss_price=stop, bracket_stop_loss_limit_price=cap, stop_trigger_method=StopTriggerMethod.MARK,
        note="fade-live probe"))
    print(f"4. sold {filled(o)} at {fill_price(o)} (bid was {bid}), fee {fee(o)}, state {o.get('state')}")
    rc = 0
    try:
        if filled(o):
            ok = acct.wait_protected(p["id"])
            print(f"5. stop resting at Delta: {'YES' if ok else 'NO -- the account would flatten and halt'}")
            rc = 0 if ok else 1
    finally:
        row, left = acct.buy_back(symbol, client_order_id("fade-live-probe", cid, "exit"))
        n = acct.cancel_leftovers(p["id"])
        print(f"6. bought back at {fill_price(row)}, fee {fee(row)}; {left} left open; {n} leftover orders cancelled")
        if left:
            print(f"!!! {symbol}: {left} contracts STILL OPEN -- close by hand on Delta")
            rc = 1
    print(f"7. balance now ${acct.balance():.2f}; {'PASS' if rc == 0 else 'FAIL'}")
    return rc


def acct_signed(v: dict) -> int:
    from live.reconcile import _signed
    return _signed(v)


async def resume(database_url: str) -> int:
    """Clear a HALT after the owner has looked (`python -m live fade-live-resume`). A STOP is final."""
    import asyncpg

    from app.persistence.db_auth import connect_kwargs
    con = await asyncpg.connect(**connect_kwargs(database_url))
    try:
        s = await con.fetchrow("SELECT status, reason FROM fade_live_state WHERE id = 1")
        if not s or s["status"] != "HALTED":
            print(f"nothing to resume (state {s['status'] if s else 'none'})")
            return 1
        await con.execute("UPDATE fade_live_state SET status = 'RUNNING', reason = 'resumed by owner', at = now() "
                          "WHERE id = 1")
        await event(con, "RESUMED", None, {"was": s["reason"]})
        print(f"resumed (was HALTED: {s['reason']})")
        return 0
    finally:
        await con.close()
