"""Gainer-fade REAL-MONEY account, RUN 4: "sell the spike" at 20x (owner 2026-11-15: "we want to deploy this version to
prod, decommission rule A"; choices: hard stop "Month: 2 stops only", "50% margin as tested", paper accounts kept;
then "approved" for: stop triggered on the LAST TRADED price, extra isolated margin after the fill, every stop's fill
logged against its trigger). Rules fixed in docs/fade_live_spike_prereg.md; this module is them, nothing more.

THE RULES (per UTC day, on the paper runner's two rule-A picks):
  entry      a resting post-only LIMIT SELL 5% above the last traded price at entry time (00:00-00:30 UTC), GTC,
             with an exchange-held bracket: stop +2.25% from the limit (last-traded trigger) and a take-profit
             where the trade nets +$500 after costs. Not filled by 24:00 UTC -> cancelled ("no spike").
  size       49% of the balance as isolated margin per resting order (two may rest: 2 x 49% + Delta's fee reserve
             fit the wallet), at the most leverage up to 20x that Delta allows for that notional on that coin.
  one at a   the first pick to fill is the trade; the other pick's resting order is cancelled at once.
   time      No new resting orders while a position is open.
  after the  the rest of an order that filled in part is cancelled; the stop must cover the position (else bought
   fill      back + HALT, as run 3); isolated margin checked (as run 3); then EXTRA isolated margin of 25% of the
             balance is added (POST /v2/positions/change_margin) -- liquidation from ~+2.5% to ~+5%.
  the lock   once a 1m low since the fill reaches -1% from the entry, the stop is moved (PUT /v2/orders) to the
             price that nets +$200; if the last price is already above that level the position is bought back at
             once ("LOCK"). The move happens on the bot's next pass (30 s), as the backtest moved it at the minute's
             close.
  exits      take-profit or stop at Delta; 24 h after the FILL the bot buys back ("TIME").
  hard stop  after 2 full stops (STOP_LOSS or LIQUIDATION without a lock) in a calendar month (UTC), no new entries
             until the 1st.
  kept       run 3's machinery: $1,000 floor (STOPPED until a refill), HALT on anything unexplained, the capital
             check, reconcile, isolated-margin check, buy-backs that never over-buy, exits-only without the switch.
Backtest of exactly these rules (scripts/clean_compare.py, verified by scripts/verify_independent.py, 604/604 fills
identical): 20 real picks 2026-10-29..11-14 +$1,463..+$2,172 on $3,184; full Delta history 2025-01..2026-10
-$127..-$37 per trade (strict..lenient stop fills). The owner chose to run it knowing this (CONTEXT.md).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time

from live import fade_live as L
from live.fade_live import DAY, MAX_DIST, _ts, event, fee, fill_price, filled, to_tick

log = logging.getLogger(__name__)

RUN_VERSION = 4
#: The funded sub-account when run 4 was chosen (owner deposit, 2026-11-15). The host's DELTABOT_EQUITY still says
#: 1500 (run 3); the new-run wallet check (floor .. 1.25 x start) uses the larger, so run 4 can begin on $3,184
#: without a host replacement. Floor ($1,000) and refill rules are run 3's.
RUN_START_USD = 3184.0
SPIKE_ABOVE = 0.05
TRADE_MARGIN = 0.49
EXTRA_MARGIN = 0.25
MAX_LEVERAGE = 20
STOP_ABOVE = 0.0225
TARGET_USD = 500.0
LOCK_AFTER = 0.01
LOCK_USD = 200.0
#: Owner 2026-11-15 ("Yes add it"): after the lock the stop trails 0.8% above the lowest price since the fill, only when
#: that is tighter than the stop it has (in practice from a ~1.58% fall, just short of the ~1.76% target); never up.
#: Moved by the bot (PUT /v2/orders), not Delta's native trailing stop: set at the lock, a native trail would start
#: 0.8% above the CURRENT price -- looser than the +$200 lock -- which is not the tested rule.
TRAIL = 0.008
MONTH_STOP_LIMIT = 2
#: Costs used to place the take-profit and the lock: maker entry 0.02% + 0.05% slippage, taker exit 0.05%.
COST = 0.0002 + 0.0005 + 0.0005
#: Delta's fee reserve in an order's margin (measured: 83,682 SOPH contracts on 50% of $1,534.98).
FEE_RESERVE = 0.0012
#: Option A (owner 2026-11-15): while an entry rests, a watcher checks it this often, so the extra margin lands within
#: seconds of a fill instead of on the next 30 s pass (liquidation is ~+2.5% until it lands).
FAST_POLL = 2.5
#: Defaults when a product row lacks a field (never assume more leverage than 20x).
DEFAULT_CAP = 50_000.0


def spike_size(balance: float, price: float, cv: float, im_pct: float, cap: float, ims_pct: float,
               contract_limit: float | None, share: float = TRADE_MARGIN, max_lev: int = MAX_LEVERAGE) -> tuple[int, int, float]:
    """(contracts, leverage, notional) for `share` of the balance as margin: the most leverage <= max_lev that Delta
    allows for the resulting notional (IM = im + ims per $ above cap, all in %), within the coin's contract limit.
    (0, 0, 0) when nothing fits."""
    margin = share * balance
    if not (margin > 0 and price > 0 and cv > 0):
        return 0, 0, 0.0
    for lev in range(max_lev, 0, -1):
        notional = margin / (1.0 / lev + FEE_RESERVE)
        im_eff = (im_pct + ims_pct * max(0.0, notional - cap)) / 100.0
        if 1.0 / lev + 1e-12 >= im_eff:
            n = int(math.floor(notional / (price * cv) + 1e-9))
            if contract_limit:
                n = min(n, int(contract_limit))
            return n, lev, n * price * cv
    return 0, 0, 0.0


def scale(balance: float) -> float:
    """Owner 2026-11-15 ("Scale can be kept, cap is no"): the $500 target and $200 lock are sized for the $3,184 start
    and move with the balance (at $1,592 they are $250 / $100), i.e. the same price fall at any balance."""
    return balance / RUN_START_USD if balance > 0 else 1.0


def target_price(entry: float, notional: float, balance: float = RUN_START_USD) -> float:
    """The take-profit for a short of `notional` that nets +TARGET_USD x scale(balance) after COST."""
    return entry * (1 - (TARGET_USD * scale(balance) / notional + COST))


def lock_price(entry: float, notional: float, balance: float = RUN_START_USD) -> float:
    """The stop that nets +LOCK_USD x scale(balance) after COST."""
    return entry * (1 - (LOCK_USD * scale(balance) / notional + COST))


SCHEMA = """
ALTER TABLE fade_live_positions ADD COLUMN IF NOT EXISTS order_id BIGINT;
ALTER TABLE fade_live_positions ADD COLUMN IF NOT EXISTS lock_price DOUBLE PRECISION;
ALTER TABLE fade_live_positions ADD COLUMN IF NOT EXISTS extra_margin DOUBLE PRECISION;
ALTER TABLE fade_live_positions ADD COLUMN IF NOT EXISTS trail_price DOUBLE PRECISION;
"""


class SpikeLive(L.LiveFade):
    """Run 4. Status flow of a row: SENDING -> RESTING -> OPEN -> CLOSED (or SKIPPED / FAILED)."""

    run_version = RUN_VERSION
    #: TIME and LOCK are the bot's own exits; a LIQUIDATION (a jump past the stop, a tested risk of this rule) counts as
    #: a full stop for the month and does not HALT -- the backtest kept trading after one (prereg rule 7).
    planned_exits = ("TIME", "LOCK", "LIQUIDATION")

    def __init__(self, client, *, candles=None, **kw):
        super().__init__(client, **kw)
        self.start_usd = max(self.start_usd, RUN_START_USD)
        self._candles = candles                        # (symbol, start, end) -> [{"time","low","close"}]; None = public API
        self._product_extra: dict[str, dict] = {}
        self._mutex = asyncio.Lock()                    # the 30 s pass and the fast watcher never act at the same time

    # -- venue reads ---------------------------------------------------------------------------------------------
    def product(self, symbol: str) -> dict:
        p = super().product(symbol)
        if symbol not in self._product_extra:
            raw = self.client.get_product(symbol)

            def num(k, d):
                try:
                    return float(raw.get(k)) if raw.get(k) not in (None, "") else d
                except (TypeError, ValueError):
                    return d
            self._product_extra[symbol] = {"im": num("initial_margin", 100.0 / MAX_LEVERAGE),
                                           "cap": num("max_leverage_notional", DEFAULT_CAP),
                                           "ims": num("initial_margin_scaling_factor", 0.0),
                                           "limit": num("position_size_limit", 0.0) or None}
        return {**p, **self._product_extra[symbol]}

    def candles(self, symbol: str, start: int, end: int) -> list[dict]:
        if self._candles is not None:
            return self._candles(symbol, start, end)
        from deltabt.data.client import DeltaClient
        return DeltaClient(per_second=2.0).candles(symbol, "1m", start, end, drop_forming=False) or []

    def last_price(self, symbol: str) -> float:
        t = self.client.get_ticker(symbol)
        for k in ("close", "mark_price"):
            try:
                v = float(t.get(k))
                if v > 0:
                    return v
            except (TypeError, ValueError):
                continue
        return 0.0

    def stop_order(self, product_id: int) -> dict | None:
        for r in self.client.get_pending_orders(product_id):
            if r.get("reduce_only") and r.get("stop_order_type") == "stop_loss_order" and r.get("product_id") in (None, product_id):
                return r
        return None

    # -- the pass --------------------------------------------------------------------------------------------------
    async def step(self, con, now: float) -> None:
        async with self._mutex:
            await super().step(con, now)

    async def fast(self, con, now: float) -> int:
        """Option A: settle resting entries (a fill -> OPEN + the extra margin at once) and top up any open position
        that is still without its extra margin. Returns how many rows it looked at. Never raises."""
        async with self._mutex:
            try:
                await con.execute(SCHEMA)
                rows = await con.fetch("SELECT id FROM fade_live_positions WHERE status = 'RESTING' "
                                       "OR (status = 'OPEN' AND extra_margin IS NULL AND entry_time IS NOT NULL) ORDER BY id")
                for x in rows:
                    r = await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", x["id"])
                    if r["status"] == "RESTING":
                        await self._resting(con, r, now)
                    elif r["status"] == "OPEN" and r["extra_margin"] is None and self.entries:
                        await self._add_extra(con, r, now)
                return len(rows)
            except Exception:                            # noqa: BLE001 -- the 30 s pass records and halts; this only helps
                log.exception("spike fast watcher pass failed")
                return 0

    async def watch(self, pool, poll: float = FAST_POLL) -> None:
        """Run `fast` every `poll` seconds on its own pooled connection (started by the paper runner)."""
        while True:
            try:
                async with pool.acquire() as con:
                    await self.fast(con, time.time())
            except Exception:                            # noqa: BLE001
                log.exception("spike fast watcher could not reach the database")
            await asyncio.sleep(poll)

    async def _reconcile(self, con, now: float) -> None:
        await con.execute(SCHEMA)
        for rid in [x["id"] for x in await con.fetch(
                "SELECT id FROM fade_live_positions WHERE status IN ('SENDING', 'RESTING') ORDER BY id")]:
            r = await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", rid)   # a fill may have settled it
            if r["status"] in ("SENDING", "RESTING"):
                await self._resting(con, r, now)
        await super()._reconcile(con, now)

    async def _resting(self, con, r, now: float) -> None:
        """A resting entry (or one whose send was not observed): filled -> OPEN; past 24:00 -> cancelled."""
        from live.client import VenueError
        try:
            o = await asyncio.to_thread(self.client.get_order_by_client_id, r["entry_cid"])
        except VenueError as exc:
            if r["status"] == "SENDING":
                await self.set_state(con, "HALTED", f"{r['symbol']} entry outcome unknown: {exc}")
            return
        if o is None:
            if r["status"] == "SENDING":
                await con.execute("UPDATE fade_live_positions SET status = 'FAILED', skip_reason = $2 WHERE id = $1",
                                  r["id"], "the entry never reached Delta")
            return
        state = str(o.get("state") or "").lower()
        n = filled(o)
        if n > 0:
            if state in ("open", "pending") and o.get("id") is not None:
                await asyncio.to_thread(self.client.cancel_order, int(o["id"]), r["product_id"])   # the unfilled rest
            await self._filled(con, r, o, now)
            return
        day_end = r["day"].timestamp() + DAY
        if state in ("cancelled", "closed", "rejected"):
            await con.execute("UPDATE fade_live_positions SET status = 'SKIPPED', skip_reason = $2 WHERE id = $1",
                              r["id"], f"entry {state} at Delta without a fill: {o.get('cancellation_reason') or ''}"[:300])
            return
        await con.execute("UPDATE fade_live_positions SET status = 'RESTING', order_id = $2 WHERE id = $1 AND status <> 'RESTING'",
                          r["id"], int(o["id"]) if o.get("id") is not None else None)
        if now >= day_end:
            await asyncio.to_thread(self.client.cancel_order, int(o["id"]), r["product_id"])
            await con.execute("UPDATE fade_live_positions SET status = 'SKIPPED', skip_reason = $2 WHERE id = $1",
                              r["id"], "no 5% spike by 24:00 UTC: the resting sell was cancelled")

    async def _filled(self, con, r, o: dict, now: float) -> None:
        # the other pick's resting order goes first: one position at a time, and its margin is needed for the extra
        for x in await con.fetch("SELECT * FROM fade_live_positions WHERE status IN ('RESTING', 'SENDING') AND id <> $1", r["id"]):
            if x["order_id"] is not None:
                await asyncio.to_thread(self.client.cancel_order, int(x["order_id"]), x["product_id"])
            await con.execute("UPDATE fade_live_positions SET status = 'SKIPPED', skip_reason = $2 WHERE id = $1",
                              x["id"], f"{r['symbol']} filled first: one position at a time")
        await self._opened(con, await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", r["id"]), o, now)
        row = await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", r["id"])
        if row["status"] != "OPEN":
            return                                       # bought back (unprotected / margin): run 3's rules HALTed it
        await self._add_extra(con, row, now)

    async def _add_extra(self, con, r, now: float) -> None:
        from live.client import AmbiguousWrite, VenueError
        r = await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", r["id"])
        if r["extra_margin"] is not None or r["status"] != "OPEN":
            return                                       # already added (or no longer open): never twice
        if r["detail"] and '"extra_margin_tried"' in r["detail"]:
            return                                       # refused or unclear once: logged, not retried blindly
        await con.execute("UPDATE fade_live_positions SET detail = COALESCE(detail, '{}'::jsonb) || $2::jsonb WHERE id = $1",
                          r["id"], json.dumps({"extra_margin_tried": int(now)}))
        want = round(EXTRA_MARGIN * float(r["balance_before"]), 2)
        before = await asyncio.to_thread(self.margined_position, r["product_id"])
        try:
            m0 = float(before.get("margin"))
        except (TypeError, ValueError):
            m0 = None
        try:
            got = await asyncio.to_thread(self.client.add_position_margin, r["product_id"], want)
        except AmbiguousWrite as exc:
            after = await asyncio.to_thread(self.margined_position, r["product_id"])
            try:
                landed = m0 is not None and float(after.get("margin")) >= m0 + 0.9 * want
            except (TypeError, ValueError):
                landed = False
            await event(con, "EXTRA_MARGIN_UNCLEAR", r["symbol"], {"want": want, "landed": landed, "error": str(exc)[:300]})
            got = after if landed else None
        except VenueError as exc:
            await event(con, "EXTRA_MARGIN_REFUSED", r["symbol"], {"want": want, "error": str(exc)[:300]})
            got = None
        if got is None:
            log.error("spike %s: extra margin NOT added (liquidation stays ~2.5%% away)", r["symbol"])
            return
        await con.execute("UPDATE fade_live_positions SET extra_margin = $2, detail = COALESCE(detail, '{}'::jsonb) || $3::jsonb "
                          "WHERE id = $1", r["id"], want, json.dumps({"after_extra_margin": {
                              k: got.get(k) for k in ("margin", "liquidation_price", "margin_mode")}}, default=str))
        await event(con, "EXTRA_MARGIN", r["symbol"], {"added": want, "liquidation_price": got.get("liquidation_price")})

    async def _exits(self, con, now: float) -> None:
        for r in await con.fetch("SELECT * FROM fade_live_positions WHERE status = 'OPEN' ORDER BY id"):
            if r["entry_time"] is not None and r["entry_time"].timestamp() <= now - DAY:
                await self._close(con, r, now, "TIME", "24 h after the fill")
            elif r["lock_price"] is None and r["entry_price"]:
                await self._lock(con, r, now)
            elif r["entry_price"]:
                await self._trail(con, r, now)

    async def _lock(self, con, r, now: float) -> None:
        from live.client import VenueError
        e = float(r["entry_price"])
        start = int(r["entry_time"].timestamp()) // 60 * 60
        bars = await asyncio.to_thread(self.candles, r["symbol"], start, int(now))
        lows = [float(b["low"]) for b in bars if b.get("low") not in (None, "") and int(b["time"]) > start]
        if not lows or min(lows) > e * (1 - LOCK_AFTER):
            return
        prod = await asyncio.to_thread(self.product, r["symbol"])
        notional = e * r["contracts"] * r["contract_value"]
        lp = to_tick(lock_price(e, notional, float(r["balance_before"])), prod["tick"], up=True)
        last = await asyncio.to_thread(self.last_price, r["symbol"])
        await con.execute("UPDATE fade_live_positions SET lock_price = $2 WHERE id = $1", r["id"], float(lp))
        if last <= 0 or last >= float(lp):
            await event(con, "LOCK_AT_MARKET", r["symbol"], {"lock": float(lp), "last": last})
            fresh = await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", r["id"])
            await self._close(con, fresh, now, "LOCK", "the price was already back above the +$200 level")
            return
        so = await asyncio.to_thread(self.stop_order, r["product_id"])
        try:
            if so is None or so.get("id") is None:
                raise VenueError("no resting stop order to move")
            await asyncio.to_thread(self.client.edit_stop_price, int(so["id"]), r["product_id"], lp)
            moved = await asyncio.to_thread(self.stop_order, r["product_id"])
            ok = moved is not None and abs(float(moved.get("stop_price") or 0) - float(lp)) <= float(prod["tick"]) * 1.5
        except (VenueError, TypeError, ValueError) as exc:
            ok = False
            await event(con, "LOCK_FAILED", r["symbol"], {"lock": float(lp), "error": str(exc)[:300]})
        if not ok:
            await con.execute("UPDATE fade_live_positions SET lock_price = NULL WHERE id = $1", r["id"])   # retried next pass
            return
        await event(con, "LOCK_MOVED", r["symbol"], {"stop_from": r["stop_price"], "stop_to": float(lp), "last": last})
        log.warning("spike %s: stop moved to %s (locks +$%.0f)", r["symbol"], lp, LOCK_USD * scale(float(r["balance_before"])))

    async def _trail(self, con, r, now: float) -> None:
        """After the lock: move the stop to the lowest low since the fill x (1 + TRAIL) when that is tighter (lower)
        than the stop now; if the last price is already at or above it, buy back at once ("LOCK"), as the backtest."""
        from live.client import VenueError
        start = int(r["entry_time"].timestamp()) // 60 * 60
        bars = await asyncio.to_thread(self.candles, r["symbol"], start, int(now))
        lows = [float(b["low"]) for b in bars if b.get("low") not in (None, "") and int(b["time"]) > start]
        if not lows:
            return
        prod = await asyncio.to_thread(self.product, r["symbol"])
        current = float(r["trail_price"] or r["lock_price"])
        new = to_tick(min(lows) * (1 + TRAIL), prod["tick"], up=True)
        if float(new) >= current - float(prod["tick"]) * 0.5:
            return                                       # not tighter: the lock (or the last trail) stays
        last = await asyncio.to_thread(self.last_price, r["symbol"])
        if last <= 0 or last >= float(new):
            await event(con, "TRAIL_AT_MARKET", r["symbol"], {"trail": float(new), "last": last})
            await self._close(con, r, now, "LOCK", "the price was already back above the trailing level")
            return
        so = await asyncio.to_thread(self.stop_order, r["product_id"])
        try:
            if so is None or so.get("id") is None:
                raise VenueError("no resting stop order to move")
            await asyncio.to_thread(self.client.edit_stop_price, int(so["id"]), r["product_id"], new)
            moved = await asyncio.to_thread(self.stop_order, r["product_id"])
            ok = moved is not None and abs(float(moved.get("stop_price") or 0) - float(new)) <= float(prod["tick"]) * 1.5
        except (VenueError, TypeError, ValueError) as exc:
            ok = False
            await event(con, "TRAIL_FAILED", r["symbol"], {"trail": float(new), "error": str(exc)[:300]})
        if ok:
            await con.execute("UPDATE fade_live_positions SET trail_price = $2 WHERE id = $1", r["id"], float(new))
            await event(con, "TRAIL_MOVED", r["symbol"], {"stop_from": current, "stop_to": float(new), "last": last})

    async def _closed_at_venue(self, con, r, now: float) -> None:
        await super()._closed_at_venue(con, r, now)
        x = await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", r["id"])
        if x["status"] == "CLOSED" and x["exit_reason"] == "STOP_LOSS" and x["exit_price"]:
            trig = float(x["trail_price"] or x["lock_price"] or x["stop_price"])
            await event(con, "STOP_FILL", x["symbol"], {"trigger": trig, "fill": float(x["exit_price"]),
                                                       "slip_pct": round(100 * (float(x["exit_price"]) / trig - 1), 4),
                                                       "locked": x["lock_price"] is not None})

    # -- entries -------------------------------------------------------------------------------------------------
    async def month_stops(self, con, now: float) -> int:
        month = _ts(now).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return int(await con.fetchval(
            "SELECT count(*) FROM fade_live_positions WHERE status = 'CLOSED' AND exit_reason IN ('STOP_LOSS', 'LIQUIDATION') "
            "AND lock_price IS NULL AND exit_time >= $1", month) or 0)

    async def _enter_one(self, con, day: int, p, now: float) -> None:
        from live.client import AmbiguousWrite, VenueError, VenueRejected
        from live.fade import K_STOP
        from live.orders import OrderRequest, OrderType, Side, StopTriggerMethod, TimeInForce, client_order_id
        await con.execute(SCHEMA)
        sym = p["symbol"]
        bal = await asyncio.to_thread(self.balance)
        if bal < L.FLOOR_USD:
            await self._skip(con, day, p, f"run stopped: balance ${bal:.2f} is under ${L.FLOOR_USD:.0f}")
            await self.set_state(con, "STOPPED", f"balance ${bal:.2f} under the ${L.FLOOR_USD:.0f} floor: refill "
                                 "and run fade-live-capital")
            return
        if await self.month_stops(con, now) >= MONTH_STOP_LIMIT:
            await self._skip(con, day, p, f"{MONTH_STOP_LIMIT} full stops this month: no entries until the 1st")
            return
        if await con.fetchval("SELECT count(*) FROM fade_live_positions WHERE status = 'OPEN'"):
            await self._skip(con, day, p, "a position is open: one at a time")
            return
        if float(p["atrp"]) * K_STOP > MAX_DIST:
            await self._skip(con, day, p, f"stop {K_STOP * float(p['atrp']):.0%} away is past the {MAX_DIST:.1%} limit")
            return
        prod = await asyncio.to_thread(self.product, sym)
        if await asyncio.to_thread(self.venue_size, prod["id"]) != 0:
            await self._skip(con, day, p, "a position on this coin is still open at Delta")
            return
        t = await asyncio.to_thread(self.client.get_ticker, sym)
        q = t.get("quotes") or {}
        bid = float(q.get("best_bid") or 0)
        ref = await asyncio.to_thread(self.last_price, sym)
        if ref <= 0 or bid <= 0:
            await self._skip(con, day, p, "no last price or bid")
            return
        limit = to_tick(ref * (1 + SPIKE_ABOVE), prod["tick"], up=True)
        if bid >= float(limit) * 0.999:
            await self._skip(con, day, p, f"already 5% up at entry time (bid {bid:g} vs limit {limit})")
            return
        n, lev, notional = spike_size(bal, float(limit), prod["cv"], prod["im"], prod["cap"], prod["ims"], prod["limit"])
        if n < 1 or lev < 1:
            await self._skip(con, day, p, "below one contract at 49% margin")
            return
        notional = n * float(limit) * prod["cv"]
        stop = to_tick(float(limit) * (1 + STOP_ABOVE), prod["tick"], up=False)
        target = to_tick(target_price(float(limit), notional, bal), prod["tick"], up=False)
        try:
            await asyncio.to_thread(self.client.set_order_leverage, prod["id"], lev)
            got = await asyncio.to_thread(self.client.get_order_leverage, prod["id"])
        except VenueRejected as exc:
            await self._skip(con, day, p, f"leverage {lev}x could not be set: {exc}"[:300])
            return
        if int(round(got)) != lev:
            await self._skip(con, day, p, f"leverage reads {got:g}x, not {lev}x")
            return
        cid = client_order_id("fade-live", str(day), sym, "spike-entry")
        row_id = await con.fetchval(
            "INSERT INTO fade_live_positions (day, symbol, rank, status, product_id, contract_value, contracts, "
            "leverage, balance_before, paper_entry, ref_price, limit_price, stop_price, target_price, risk_pct, "
            "entry_cid, detail) VALUES ($1,$2,$3,'SENDING',$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16) RETURNING id",
            _ts(day), sym, p["rank"], prod["id"], prod["cv"], n, lev, bal, p["entry_price"], ref,
            float(limit), float(stop), float(target), round(n * prod["cv"] * (float(stop) - float(limit)) / bal, 4), cid,
            json.dumps({"bid": bid, "run": RUN_VERSION, "notional": round(notional, 2)}))
        order = OrderRequest(product_id=prod["id"], size=n, side=Side.SELL, order_type=OrderType.LIMIT,
                             client_order_id=cid, limit_price=limit, time_in_force=TimeInForce.GTC, post_only=True,
                             bracket_stop_loss_price=stop, bracket_take_profit_price=target,
                             stop_trigger_method=StopTriggerMethod.LAST_TRADED, note="spike-live entry")
        try:
            o = await asyncio.to_thread(self.client.place_order, order)
        except VenueRejected as exc:
            await con.execute("UPDATE fade_live_positions SET status = 'FAILED', skip_reason = $2 WHERE id = $1",
                              row_id, f"Delta refused the order: {exc}"[:300])
            await event(con, "REJECTED", sym, {"error": str(exc)[:400]})
            return
        except (AmbiguousWrite, VenueError) as exc:
            await self.set_state(con, "HALTED", f"{sym} entry outcome unknown: {exc}")
            return                                       # left SENDING: _resting asks again
        log.warning("spike %s: resting sell x%d at %s (stop %s, target %s, %dx)", sym, n, limit, stop, target, lev)
        await self._resting(con, await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", row_id), now)
        _ = (o, fee, fill_price, time)                   # the reply is read again by client order id in _resting


# ============================================================ operator command

def probe(symbol: str = "XRPUSD", env: dict | None = None) -> int:
    """ONE real contract through run 4's order shapes, then flat again (`python -m live fade-live-probe [SYMBOL]`).
    Owner's yes required each time (docs/fade_live_spike_prereg.md). Costs a few cents of fees. The account must be
    flat; run `fade-live-capital` after it if the account is running (its fees move the wallet).
      1. a resting POST-ONLY GTC sell 20% above the market with a last-traded bracket is accepted, then cancelled
      2. one contract sold (IOC, as run 3's probe) with a LAST-TRADED stop 20% above: the leg's trigger is read back
      3. the stop is MOVED (PUT /v2/orders) to 25% above and read back
      4. $1 of isolated margin is ADDED (POST /v2/positions/change_margin): margin and liquidation are read back
      5. bought back, leftovers cancelled"""
    import os

    from live.config import client_from_env
    from live.fade_live import SLIP, WRONG_ACCOUNT_RATIO, acct_signed, margin_problem
    from live.orders import OrderRequest, OrderType, Side, StopTriggerMethod, TimeInForce, client_order_id
    src = os.environ if env is None else env
    client = client_from_env(src)
    if not client.is_prod:
        print("refusing: DELTA_ENV is not prod (this probe exists to test the prod key)")
        return 2
    acct = SpikeLive(client, risk_pct=L.MAX_RISK_PCT, start_usd=float(src.get("DELTABOT_EQUITY") or 1500))
    bal = acct.balance()
    print(f"0. wallet ${bal:.2f}")
    if not 0 < bal <= WRONG_ACCOUNT_RATIO * acct.start_usd:
        print(f"refusing: balance ${bal:.2f} does not look like the sub-account")
        return 2
    if [v for v in client.get_positions() if acct_signed(v)]:
        print("refusing: the account already holds positions")
        return 2
    p = acct.product(symbol)
    client.set_order_leverage(p["id"], 2)
    bid = float(client.get_ticker(symbol)["quotes"]["best_bid"])
    rc = 0
    # 1. resting post-only far above the market
    cid1 = client_order_id("spike-probe", symbol, str(int(time.time())), "rest")
    far = to_tick(bid * 1.20, p["tick"], up=True)
    o1 = client.place_order(OrderRequest(product_id=p["id"], size=1, side=Side.SELL, order_type=OrderType.LIMIT,
                                         client_order_id=cid1, limit_price=far, time_in_force=TimeInForce.GTC, post_only=True,
                                         bracket_stop_loss_price=to_tick(float(far) * 1.0225, p["tick"], up=False),
                                         stop_trigger_method=StopTriggerMethod.LAST_TRADED, note="spike probe rest"))
    print(f"1. resting post-only sell at {far}: state {o1.get('state')}, id {o1.get('id')}")
    if str(o1.get("state")).lower() not in ("open", "pending"):
        rc = 1
    if o1.get("id") is not None:
        client.cancel_order(int(o1["id"]), p["id"])
        print(f"   cancelled: {client.get_order_by_client_id(cid1).get('state')}")
    # 2. one contract sold with a last-traded stop
    cid2 = client_order_id("spike-probe", symbol, str(int(time.time())), "sell")
    stop = to_tick(bid * 1.20, p["tick"], up=True)
    o2 = client.place_order(OrderRequest(product_id=p["id"], size=1, side=Side.SELL, order_type=OrderType.LIMIT,
                                         client_order_id=cid2, limit_price=to_tick(bid * (1 - SLIP), p["tick"], up=False),
                                         time_in_force=TimeInForce.IOC, bracket_stop_loss_price=stop,
                                         stop_trigger_method=StopTriggerMethod.LAST_TRADED, note="spike probe sell"))
    print(f"2. sold {filled(o2)} at {fill_price(o2)}")
    try:
        if filled(o2):
            ok = acct.wait_protected(p["id"])
            leg = acct.stop_order(p["id"]) or {}
            trig = leg.get("stop_trigger_method")
            print(f"   stop resting: {ok}; leg {{stop_price: {leg.get('stop_price')}, stop_trigger_method: {trig}}}")
            if not ok or trig != "last_traded_price":
                print("   !!! the stop is not on the LAST TRADED price" if trig != "last_traded_price" else "   !!! no stop")
                rc = 1
            # 3. move the stop
            if leg.get("id") is not None:
                new = to_tick(bid * 1.25, p["tick"], up=True)
                client.edit_stop_price(int(leg["id"]), p["id"], new)
                back = acct.stop_order(p["id"]) or {}
                moved = abs(float(back.get("stop_price") or 0) - float(new)) <= float(p["tick"]) * 1.5
                print(f"3. stop moved to {new}: reads back {back.get('stop_price')} -> {'YES' if moved else 'NO'}")
                rc = rc or (0 if moved else 1)
            # 4. add $1 of margin
            before = acct.margined_position(p["id"])
            after = client.add_position_margin(p["id"], 1.0)
            print(f"4. margin {before.get('margin')} -> {after.get('margin')}; liquidation {before.get('liquidation_price')}"
                  f" -> {after.get('liquidation_price')}")
            try:
                if not float(after.get("margin")) > float(before.get("margin")):
                    rc = 1
            except (TypeError, ValueError):
                rc = 1
            bad = margin_problem(before, fill_price(o2) or bid, 1, p["cv"], 2)
            print(f"   isolated before the top-up: {'YES' if not bad else 'NO -- ' + bad}")
    finally:
        row, left = acct.buy_back(symbol, client_order_id("spike-probe", cid2, "exit"))
        n = acct.cancel_leftovers(p["id"])
        print(f"5. bought back at {fill_price(row)}; {left} left open; {n} leftover orders cancelled")
        if left:
            print(f"!!! {symbol}: {left} contracts STILL OPEN -- close by hand on Delta")
            rc = 1
    print(f"6. balance now ${acct.balance():.2f}; {'PASS' if rc == 0 else 'FAIL'}")
    return rc
