"""Gainer-fade REAL-MONEY account, SPIKE SELL (run 4): "sell the spike" at up to 20x (owner 2026-11-15: "we want to
deploy this version to prod, decommission rule A"; hard stop "Month: 2 stops only"; paper accounts kept; stop on the
LAST TRADED price, extra isolated margin after the fill, every stop's fill logged against its trigger). After the
careful simulation (docs/careful_sim_spec.md) the owner chose, 2026-11-15: **"deploy with 10% margin"** and exit
**"C approved"** = break-even, then a 5% trail. Rules fixed in docs/fade_live_spike_prereg.md; this module is them.

THE RULES (per UTC day, on the paper runner's two rule-A picks):
  entry      a resting post-only LIMIT SELL 5% above the last traded price at entry time (00:00-00:30 UTC), GTC,
             with an exchange-held stop +10% from the limit (last-traded trigger; +2.25% until 2026-11-17). NO
             take-profit order.
             Not filled by 24:00 UTC -> cancelled ("no spike").
  size       20% of the balance as isolated margin per resting order (owner 2026-11-16; was 10%), at the most
             leverage up to 5x that Delta allows for that notional on that coin (owner 2026-11-17, rule C; was 20x):
             ~1x the balance of position, a full 10% stop ~10% of the balance (~$290 on $2,896).
  one at a   the first pick to fill is the trade; the other pick's resting order is cancelled at once.
   time      No new resting orders while a position is open.
  after the  the rest of an order that filled in part is cancelled; the stop must cover the position (else bought
   fill      back + HALT, as run 3); isolated margin checked (as run 3); then EXTRA isolated margin of 25/49 of the trade margin
             (10.2% of the balance) is added (POST /v2/positions/change_margin) -- at 5x liquidation from ~+17.5% to ~+27%,
             well past the 10% stop (at 20x it was ~+2.5% to ~+5%).
  break-even REMOVED by the owner 2026-11-16 ("change lock to trailing SL"; BREAKEVEN_LOCK = False). Exit C had: once a
             1m low nets +$30.61 x balance / $3,184 (about a 0.6% fall) the stop goes to break-even; if the last
             price is already at or above that level the position is bought back at once ("LOCK").
  the trail  RULE C, TRAIL FROM THE FILL (owner 2026-11-17, "use 10% from the fill"): the stop trails 10% above the
             lowest 1m low since the fill as soon as that is lower than the stop it has (so first after any new low),
             never up; (rule C as first deployed the same day moved it only to a level at or below the ENTRY); if the last price is already at or above the new level: bought back ("LOCK"). Moved on
             the bot's next pass (30 s), as the backtest moved it at the minute's close. (2026-11-16..17 it was a 5%
             trail from the fill with a 2.25% stop.)
  exits      the stop at Delta; 24 h after the FILL the bot buys back ("TIME").
  hard stop  NONE per month (owner 2026-11-16: "remove that setting", after being shown that with it removed the
             tested account ends at the $1,000 floor in 2025). Full stops are still counted and logged
             (`month_stops`); MONTH_STOP_LIMIT = N puts "no new entries until the 1st after N full stops" back.
  kept       run 3's machinery: $1,000 floor (STOPPED until a refill), HALT on anything unexplained, the capital
             check, reconcile, isolated-margin check, buy-backs that never over-buy, exits-only without the switch.
Backtest of exit C WITH the month-off rule (scripts/careful_be_trail.py + scripts/careful_margin.py, two
implementations agreeing on every trade, Delta's own 1m prices 2025-01..2026-11): from $3,184 the full history ends
$1,801 (strict) .. $3,890 (lenient), deepest drop 67% .. 53%.
Backtest of the rules AS THEY NOW ARE -- plain 5% trail, NO month-off rule, 10% margin (scripts/careful_timed_trail.py
and the checks recorded in CONTEXT.md, 2026-11-16; two implementations agreeing on every trade): from $3,184 the full
history ends at the $1,000 FLOOR, $948 (strict) .. $994 (lenient), reached between June and August 2025; started
2026-01-01 it ends $5,046 .. $17,595; 10 .. 7 of 22 monthly starts end under $1,000 and 12 .. 15 above the start.
With the month-off rule kept the same exit ended $6,074 .. $8,386.
At 20% margin (owner 2026-11-16), same rules, from $3,184: the full history reaches the $1,000 floor by April 2025
($951 .. $985); started 2026-01-01 it also ends at the floor; 19 .. 16 of 22 monthly starts end under $1,000. Per
trade at a fixed $3,184: win 29-30%, average win +$706 .. +$717, average loss -$277 .. -$305, a liquidation -$962,
longest losing run 11 trades. The owner chose these rules knowing this.
RULE C (owner 2026-11-17, "approved on C", after the first live trade, CROSS, was stopped 27 s after its fill at the
2.25% stop and then fell below the entry -- as 405 of the 447 losers in the history did): stop 10%, trail 10% only once
past the entry, 24 h, 5x. scripts/spike_verify.py (two implementations agreeing on every trade, 617 trades
2025-01..2026-11, before fees): win 59%, average win +8.1%, average loss -9.2% of the position, +1.07% per trade STRICT
(+1.50% LENIENT), positive in all four half-years, 15 of 23 months up; a random-minute short with the same exit -0.09%.
At a fixed $3,184 and a stop costing 9%: win +$247, loss -$270, +$43 a trade. Compounding from $3,184 the strict
account fell to $897 at its lowest (worst fall 89%) before ending far higher. Chosen from ~70 variants: expect less.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time

from live import fade_live as L
from live.fade_live import DAY, MAX_DIST, _ts, event, fill_price, filled, to_tick

log = logging.getLogger(__name__)

RUN_VERSION = 4
#: The funded sub-account when run 4 was chosen (owner deposit, 2026-11-15). The host's DELTABOT_EQUITY still says
#: 1500 (run 3); the new-run wallet check (floor .. 1.25 x start) uses the larger, so run 4 can begin on $3,184
#: without a host replacement. Floor ($1,000) and refill rules are run 3's.
RUN_START_USD = 3184.0
SPIKE_ABOVE = 0.05
#: Owner 2026-11-16 ("We'll use 20% margin", after the per-trade and account numbers at 5/10/15/20%; was 10%, owner
#: 2026-11-15): 20% of the balance per trade. The extra margin keeps the tested liquidation distance: 25/49 of the
#: trade margin (the ratio every careful_* test used) = 10.2% of the balance -> liquidation ~+5% from the entry.
TRADE_MARGIN = 0.20
EXTRA_MARGIN = TRADE_MARGIN * 25 / 49
#: Owner 2026-11-17 ("approved on C", after the first live trade -- CROSS -- was stopped 27 s after its fill and then
#: fell): leverage at most 5x, so 20% margin is a position of ~1x the balance and a full 10% stop costs ~10% of it
#: (was 20x / 2.25%: a position of ~4x and a stop of ~9%). Liquidation sits ~+17.5% before, ~+27% after the extra margin.
MAX_LEVERAGE = 5
STOP_ABOVE = 0.10
#: Exit C (owner 2026-11-15, "C approved"): once the trade's best point (a 1m low) nets this much x balance/$3,184 the
#: stop goes to break-even. Tested as +$150 on a 49%-margin position; at 10% margin the same price fall is 150 x 10/49.
BREAKEVEN_AFTER_USD = 150.0 * TRADE_MARGIN / 0.49
#: The stop after that move nets this (0 = break-even after costs). Named "lock" in the table and the events (run 4's
#: first version locked +$200 here).
LOCK_USD = 0.0
#: Owner 2026-11-16 ("change lock to trailing SL"; "1 approved"): NO break-even step. The stop stays at +2.25% and
#: trails TRAIL above the lowest 1m low since the fill as soon as that is lower than the stop it has (the price must
#: have fallen ~2.6% for the first move). True restores exit C (break-even at ~0.6%, then the trail). Tested:
#: scripts/careful_timed_trail.py, variant "timed trail 0m / 5.00%" (out/careful/timed_trail.txt).
BREAKEVEN_LOCK = False
#: ... and then trails this far above the lowest price since the fill, only when that is lower than the stop it has;
#: never up. Moved by the bot (PUT /v2/orders), not Delta's native trailing stop: a native trail would start 5% above
#: the CURRENT price -- far looser than break-even -- which is not the tested rule.
TRAIL = 0.10
#: True: the trail moves the stop only to a level AT OR BELOW the entry (first after a ~9.1% fall) -- rule C as approved
#: 2026-11-17 ("approved on C"; win 59%, +$233 / -$266, +1.07%/trade strict). Owner 2026-11-17 ("use 10% from the
#: fill", after the trail table): False -- the 10% trail tightens from the fill on: win 49%, average win +8.25% vs
#: average loss -6.39% of the position, +0.77%/trade strict (+1.26% lenient) (out/careful/trail_from_fill.txt).
TRAIL_ONLY_PAST_ENTRY = False
#: Month-off rule: no new entries until the 1st after this many full stops in a calendar month. None = no monthly
#: limit (owner 2026-11-16). It was 2 until then; the tested results in the docstring above are WITH the limit of 2.
MONTH_STOP_LIMIT: int | None = None
#: Costs used to place the break-even levels: maker entry 0.02% + 0.05% slippage, taker exit 0.05%.
COST = 0.0002 + 0.0005 + 0.0005
#: Delta's fee reserve in an order's margin (measured: 83,682 SOPH contracts on 50% of $1,534.98).
FEE_RESERVE = 0.0012
#: Option A (owner 2026-11-15): while an entry rests, a watcher checks it this often, so the extra margin lands within
#: seconds of a fill instead of on the next 30 s pass (liquidation is ~+2.5% until it lands).
FAST_POLL = 2.5
#: Defaults when a product row lacks a field (never assume more leverage than 20x).
DEFAULT_CAP = 50_000.0


def spike_size(balance: float, price: float, cv: float, im_pct: float, cap: float, ims_pct: float,
               contract_limit: float | None, share: float = TRADE_MARGIN, max_lev: int | None = None) -> tuple[int, int, float]:
    """(contracts, leverage, notional) for `share` of the balance as margin: the most leverage <= max_lev (default
    MAX_LEVERAGE) that Delta
    allows for the resulting notional (IM = im + ims per $ above cap, all in %), within the coin's contract limit.
    (0, 0, 0) when nothing fits."""
    max_lev = MAX_LEVERAGE if max_lev is None else max_lev   # read at call time (rule C lowered it to 5x)
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
    """Owner 2026-11-15 ("Scale can be kept, cap is no"): the dollar levels are sized for the $3,184 start and move
    with the balance, i.e. the same price fall at any balance."""
    return balance / RUN_START_USD if balance > 0 else 1.0


def activation_price(entry: float, notional: float, balance: float = RUN_START_USD) -> float:
    """The price at which a short of `notional` nets +BREAKEVEN_AFTER_USD x scale(balance) after COST: a 1m low at or
    under it moves the stop to break-even."""
    return entry * (1 - (BREAKEVEN_AFTER_USD * scale(balance) / notional + COST))


def lock_price(entry: float, notional: float, balance: float = RUN_START_USD) -> float:
    """The stop after the move: nets +LOCK_USD x scale(balance) after COST (LOCK_USD = 0: break-even)."""
    return entry * (1 - (LOCK_USD * scale(balance) / notional + COST))


SCHEMA = """
ALTER TABLE fade_live_positions ADD COLUMN IF NOT EXISTS order_id BIGINT;
ALTER TABLE fade_live_positions ADD COLUMN IF NOT EXISTS lock_price DOUBLE PRECISION;
ALTER TABLE fade_live_positions ADD COLUMN IF NOT EXISTS extra_margin DOUBLE PRECISION;
ALTER TABLE fade_live_positions ADD COLUMN IF NOT EXISTS trail_price DOUBLE PRECISION;
"""


class SpikeLive(L.LiveFade):
    """Spike Sell (run 4). Status flow of a row: SENDING -> RESTING -> OPEN -> CLOSED (or SKIPPED / FAILED)."""

    run_version = RUN_VERSION
    #: TIME and LOCK are the bot's own exits; a LIQUIDATION (a jump past the stop, a tested risk of this rule) counts as
    #: a full stop for the month and does not HALT -- the backtest kept trading after one (prereg rule 7).
    #: DOUBLE_FILL: the other pick's resting sell filled before its cancel landed -- bought back at once (one at a time).
    planned_exits = ("TIME", "LOCK", "LIQUIDATION", "DOUBLE_FILL")

    def __init__(self, client, *, candles=None, **kw):
        super().__init__(client, **kw)
        self.start_usd = max(self.start_usd, RUN_START_USD)
        self._candles = candles                        # (symbol, start, end) -> [{"time","low","close"}]; None = public API
        self._public = None                            # the public candle client, made once
        self._product_extra: dict[str, dict] = {}
        self._mutex = asyncio.Lock()                    # the 30 s pass and the fast watcher never act at the same time
        self._schema_done = False

    async def _schema(self, con) -> None:
        """The run's extra columns, once per process (not an ALTER TABLE lock every 2.5 s)."""
        if not self._schema_done:
            await con.execute(SCHEMA)
            self._schema_done = True

    @staticmethod
    def _ours(r) -> bool:
        """A row of THIS run (run 3's rows carry no run number): only these get the extra margin, the break-even
        move, the trail, the 24-h-after-the-fill exit, and count toward the month's stops."""
        try:
            return json.loads(r["detail"] or "{}").get("run") == RUN_VERSION
        except (TypeError, ValueError):
            return False

    async def _stand_down(self, con) -> str | None:
        """Why no entry may fill into a new position now (then resting sells are cancelled), or None."""
        from live.guards import kill_switch_engaged
        if not self.entries:
            return "entries are off"
        if kill_switch_engaged(self.kill_switch_path):
            return "kill switch engaged"
        st = await con.fetchval("SELECT status FROM fade_live_state WHERE id = 1")
        return f"the account is {st}" if st in ("HALTED", "STOPPED", "OFF") else None

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
        if self._public is None:
            from deltabt.data.client import DeltaClient
            self._public = DeltaClient(per_second=2.0)
        return self._public.candles(symbol, "1m", start, end, drop_forming=False) or []

    def last_price(self, symbol: str, ticker: dict | None = None) -> float:
        t = self.client.get_ticker(symbol) if ticker is None else ticker
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
                await self._schema(con)
                rows = await con.fetch("SELECT id FROM fade_live_positions WHERE status = 'RESTING' "
                                       "OR (status = 'OPEN' AND extra_margin IS NULL AND entry_time IS NOT NULL) ORDER BY id")
                for x in rows:
                    try:                                 # one row that fails must not keep the watcher from the next
                        r = await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", x["id"])
                        if r["status"] == "RESTING":
                            await self._resting(con, r, now)
                        elif r["status"] == "OPEN" and r["extra_margin"] is None and self._ours(r):
                            await self._add_extra(con, r, now)     # protective: also when entries are off
                    except Exception:                    # noqa: BLE001 -- the 30 s pass records failures and halts
                        log.exception("spike fast watcher: row %s failed", x["id"])
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
        await self._schema(con)
        first: Exception | None = None
        for rid in [x["id"] for x in await con.fetch(
                "SELECT id FROM fade_live_positions WHERE status IN ('SENDING', 'RESTING') ORDER BY id")]:
            r = await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", rid)   # a fill may have settled it
            if r["status"] in ("SENDING", "RESTING") and self._ours(r):                     # run 3's rows: its own rule
                try:
                    await self._resting(con, r, now)
                except Exception as exc:                 # noqa: BLE001 -- one row must not cost the others their pass
                    first = first or exc
        await super()._reconcile(con, now)               # the open position's stop, strangers, closes at Delta: always
        if first is not None:
            raise first

    async def _sent_unknown(self, con, r, now: float) -> None:
        """This run's entries REST (GTC): a SENDING row is settled by `_resting` -- found (by its id or among the open
        orders) -> RESTING / filled; a short held on the coin -> filled; found nowhere for 2 minutes -> FAILED. Run 3's
        IOC rule would mark it FAILED at once, while the sell may still rest."""
        if not self._ours(r):
            await super()._sent_unknown(con, r, now)

    async def _other_trade(self, con, r) -> str | None:
        """The coin that makes `r` surplus: a position open now, or the day's other pick having already filled (the
        first fill is the day's one trade, even after it has closed)."""
        return await con.fetchval(
            "SELECT symbol FROM fade_live_positions WHERE id <> $1 AND (status = 'OPEN' OR (day = $2 AND entry_time IS NOT NULL)) "
            "ORDER BY id LIMIT 1", r["id"], r["day"])

    async def _find(self, r) -> tuple[dict | None, bool]:
        """(the entry order as Delta has it, sure). Asked two ways: by its client id, then among the product's OPEN
        orders (a resting sell is there even when the by-id lookup misses it). (None, True) = Delta answered both
        and has no such order resting; (None, False) = a read failed, nothing may be concluded."""
        from live.client import VenueError
        sure = True
        try:
            o = await asyncio.to_thread(self.client.get_order_by_client_id, r["entry_cid"])
        except VenueError:
            o, sure = None, False
        if o is not None:
            return o, True
        try:
            for x in await asyncio.to_thread(self.client.get_open_orders, r["product_id"]):
                if x.get("client_order_id") == r["entry_cid"]:
                    return x, True
        except VenueError:
            return None, False
        return None, sure

    async def _from_position(self, r) -> dict | None:
        """When the order cannot be found: the position itself. A short held on the coin = the order filled (a fill
        row is made from the position: its size and entry price; the fee is not known). None = flat."""
        pos = await asyncio.to_thread(self.client.get_position, r["product_id"])
        from live.reconcile import _signed
        n = abs(_signed(pos)) if pos else 0
        if not n:
            return None
        return {"id": r["order_id"], "size": n, "unfilled_size": 0, "average_fill_price": pos.get("entry_price"),
                "paid_commission": "0", "state": "closed", "from_position": True}

    async def _cancel(self, oid, product_id) -> None:
        """Cancel, where 'Delta refuses: the order is already gone (filled or cancelled)' is an answer, not a failure:
        what became of it is read afterwards."""
        from live.client import VenueRejected
        try:
            await asyncio.to_thread(self.client.cancel_order, int(oid), product_id)
        except VenueRejected as exc:
            msg = str(exc).lower()
            if exc.status not in (400, 404) or not any(k in msg for k in ("not_found", "not found", "already", "closed", "cancel")):
                raise                                    # refused for another reason (auth, schema): a real failure

    async def _after_cancel(self, r) -> dict | None:
        """What the entry order became once its cancel was sent: Delta's row for it; if Delta no longer shows it, the
        position decides (a short held = it filled; flat = cancelled, nothing filled). None = a read failed: nothing
        is concluded and the next pass asks again."""
        o, sure = await self._find(r)
        if o is not None:
            return o
        if not sure:
            return None
        return await self._from_position(r) or {"size": 0, "unfilled_size": 0, "state": "cancelled"}

    async def _resting(self, con, r, now: float, o: dict | None = None) -> None:
        """A resting entry (or one whose send was not observed): filled -> OPEN; past 24:00, or once the day's trade
        exists, or while the account may not enter -> cancelled. `o` is the order row when the caller already holds it
        (place_order's own reply: no read-after-write lookup that could miss it)."""
        if o is None:
            o, sure = await self._find(r)
            if o is None and not sure:
                return                                   # Delta could not be read: nothing is concluded this pass
            if o is None:
                o = await self._from_position(r)         # not resting: did it fill?
            if o is None and r["status"] == "SENDING":
                sent = json.loads(r["detail"] or "{}").get("sent_at")
                if sent is not None and now - float(sent) < 120:
                    return                               # just sent: give Delta's lists time to show it
                await con.execute("UPDATE fade_live_positions SET status = 'FAILED', skip_reason = $2 WHERE id = $1", r["id"],
                                  "the entry is not at Delta: not found by its id, not among the open orders, no position")
                return
            if o is None:
                # A sell we SAW resting that Delta no longer shows and that left no position. Its lists can lag or miss:
                # nothing is concluded for 2 minutes, then it is cancelled by the id from the send (harmless if it is
                # gone, decisive if it is not) and written off.
                since = json.loads(r["detail"] or "{}").get("missing_since")
                if since is None:
                    await con.execute("UPDATE fade_live_positions SET detail = COALESCE(detail, '{}'::jsonb) || $2::jsonb "
                                      "WHERE id = $1", r["id"], json.dumps({"missing_since": int(now)}))
                    return
                if now - float(since) < 120:
                    return
                if r["order_id"] is not None:
                    await self._cancel(r["order_id"], r["product_id"])
                o = await self._from_position(r)         # one last look: did it fill meanwhile?
                if o is None:
                    await con.execute("UPDATE fade_live_positions SET status = 'SKIPPED', skip_reason = $2 WHERE id = $1", r["id"],
                                      "the resting sell was not at Delta for 2 minutes: cancelled by its id, nothing filled")
                    return
        oid = o.get("id") if o.get("id") is not None else r["order_id"]   # the id Delta gave at the send
        state = str(o.get("state") or "").lower()
        if filled(o) > 0:
            if state in ("open", "pending") and oid is not None:
                await self._cancel(oid, r["product_id"])                  # the unfilled rest
                again = await self._after_cancel(r)                       # more may have filled before the cancel landed
                if again is None or filled(again) < filled(o):
                    return                               # the final size is not known yet: the next look reads it
                o = again
            await self._filled(con, r, o, now)
            return
        other = await self._other_trade(con, r)
        stand_down = await self._stand_down(con)         # read now: a halt earlier in this very pass counts
        why = ("no 5% spike by 24:00 UTC: the resting sell was cancelled" if now >= r["day"].timestamp() + DAY else
               f"{other} filled first: one position at a time" if other else
               f"resting sell cancelled: {stand_down}" if stand_down else None)
        if state in ("cancelled", "closed", "rejected"):
            await con.execute("UPDATE fade_live_positions SET status = 'SKIPPED', skip_reason = $2 WHERE id = $1", r["id"],
                              (why or f"entry {state} at Delta without a fill: {o.get('cancellation_reason') or ''}")[:300])
            return
        await con.execute("UPDATE fade_live_positions SET status = 'RESTING', order_id = $2 WHERE id = $1 "
                          "AND (status <> 'RESTING' OR order_id IS NULL)", r["id"], int(oid) if oid is not None else None)
        if not why or oid is None:
            return
        await self._cancel(oid, r["product_id"])
        again = await self._after_cancel(r)
        if again is None or str(again.get("state") or "").lower() in ("open", "pending") and filled(again) == 0:
            return                                       # not confirmed gone yet: still RESTING, the next pass asks again
        if filled(again) > 0:                            # it filled in the moment before the cancel
            await self._filled(con, r, again, now)
            return
        await con.execute("UPDATE fade_live_positions SET status = 'SKIPPED', skip_reason = $2 WHERE id = $1", r["id"], why)

    async def _filled(self, con, r, o: dict, now: float) -> None:
        """A resting sell filled. If the day's trade already exists this is a DOUBLE FILL (both picks spiked before the
        cancel landed): recorded and bought back at once -- one position at a time. Otherwise it is the trade: the
        other pick's order is cancelled FIRST (one call, so it cannot fill while this one is checked), then this one
        is recorded (its stop and margin checked) and its extra margin added, then the other order is read back."""
        other = await self._other_trade(con, r)
        others = await con.fetch("SELECT * FROM fade_live_positions WHERE status IN ('RESTING', 'SENDING') AND id <> $1", r["id"])
        if not other:
            for x in others:
                if x["order_id"] is not None:
                    try:
                        await self._cancel(x["order_id"], x["product_id"])
                    except Exception:                    # noqa: BLE001 -- settled below / on the next pass
                        log.exception("spike %s: the other resting order could not be cancelled yet", x["symbol"])
        # recorded at the FILL's own time when Delta states it: a stop that fired before the bot first saw the fill is
        # then still this position's close (not an order "from before it opened"), and the fill minute is the true one
        await self._opened(con, await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", r["id"]), o,
                           self._fill_time(r, o, now))
        row = await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", r["id"])
        if row["status"] == "OPEN" and other:
            await event(con, "DOUBLE_FILL", r["symbol"], {"already_traded": other, "contracts": filled(o)})
            await self._close(con, row, now, "DOUBLE_FILL", f"{other} had filled first: one position at a time")
            return
        if row["status"] == "OPEN":
            try:
                await self._add_extra(con, row, now)
            except Exception:                            # noqa: BLE001 -- retried by the watcher and the next pass
                log.exception("spike %s: the extra margin could not be added yet", r["symbol"])
        for x in others:                                 # what became of the other order (cancelled, or a double fill)
            try:
                await self._resting(con, await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", x["id"]), now)
            except Exception:                            # noqa: BLE001 -- the next pass tries again
                log.exception("spike %s: the other resting order could not be settled yet", x["symbol"])

    @staticmethod
    def _fill_time(r, o: dict, now: float) -> float:
        """When the order filled, by Delta's stamp (`updated_at` of a filled order), if it lies inside the order's own
        day and not in the future; else the time of this look."""
        from live.ledger import parse_venue_time
        at = parse_venue_time(o.get("updated_at")) if not o.get("from_position") else None
        return float(at) if at is not None and r["day"].timestamp() <= at <= now else now

    async def _check_margin(self, con, row_id: int, now: float) -> bool:
        """Run 3's isolated-margin check; `margin_ok` is recorded only when it PASSED (the base marks that it looked
        before it has a verdict)."""
        ok = await super()._check_margin(con, row_id, now)
        if ok:
            await con.execute("UPDATE fade_live_positions SET detail = COALESCE(detail, '{}'::jsonb) || $2::jsonb WHERE id = $1",
                              row_id, json.dumps({"margin_ok": int(now)}))
        return ok

    async def _add_extra(self, con, r, now: float) -> None:
        from live.client import AmbiguousWrite, VenueError
        r = await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", r["id"])
        if r["extra_margin"] is not None or r["status"] != "OPEN" or not self._ours(r):
            return                                       # already added (or no longer open, or not this run's): never twice
        if r["detail"] and '"extra_margin_tried"' in r["detail"]:
            return                                       # refused or unclear once: logged, not retried blindly
        first = await con.fetchval("SELECT id FROM fade_live_positions WHERE status = 'OPEN' ORDER BY entry_time, id LIMIT 1")
        if first != r["id"]:
            return                                       # a second open position (a double fill being closed): never topped up
        if not r["detail"] or '"margin_ok"' not in r["detail"]:
            # run 3's isolated-margin check has not PASSED yet (a failed read at the fill): it goes first, here and now
            if not await self._check_margin(con, r["id"], now):
                return                                   # not isolated as planned: bought back and HALTED
        want = round(EXTRA_MARGIN * float(r["balance_before"]), 2)
        # read BEFORE the attempt is recorded: a failed read here costs nothing and the next look tries again
        before = await asyncio.to_thread(self.margined_position, r["product_id"])
        try:
            m0 = float(before.get("margin"))
        except (TypeError, ValueError):
            m0 = None
        await con.execute("UPDATE fade_live_positions SET detail = COALESCE(detail, '{}'::jsonb) || $2::jsonb WHERE id = $1",
                          r["id"], json.dumps({"extra_margin_tried": int(now)}))
        try:
            got = await asyncio.to_thread(self.client.add_position_margin, r["product_id"], want)
        except AmbiguousWrite as exc:
            try:
                after = await asyncio.to_thread(self.margined_position, r["product_id"])
                landed = m0 is not None and float(after.get("margin")) >= m0 + 0.9 * want
            except Exception:                            # noqa: BLE001 -- the read-back failed too: unclear, logged, not resent
                after, landed = {}, False
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
        await self._schema(con)
        mine = 0
        for r in await con.fetch("SELECT * FROM fade_live_positions WHERE status = 'OPEN' ORDER BY entry_time, id"):
            if not self._ours(r):                        # a run-3 position still open: its own rule (24 h after its day)
                if r["day"].timestamp() <= now - DAY - L.EXIT_AFTER:
                    await self._close(con, r, now, "TIME", "24 h exit")
                continue
            mine += 1
            if mine > 1:                                 # a second position still open (a double fill whose buy-back
                await event(con, "DOUBLE_FILL", r["symbol"], {"still_open": True})   # failed): closed on every pass until gone
                await self._close(con, r, now + 1, "DOUBLE_FILL", "a second position: one at a time")   # +1: new order ids
                continue
            if r["entry_time"] is not None and r["entry_time"].timestamp() <= now - DAY:
                await self._close(con, r, now, "TIME", "24 h after the fill")
                continue
            if r["extra_margin"] is None:                # the watcher's job, done here too if it has not landed
                await self._add_extra(con, r, now)
                r = await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", r["id"])
                if r["status"] != "OPEN":
                    continue                             # the margin check bought it back
            if BREAKEVEN_LOCK and r["lock_price"] is None and r["entry_price"]:
                await self._lock(con, r, now)
            elif r["entry_price"]:
                await self._trail(con, r, now)

    async def _lows_since_fill(self, r, now: float) -> list[float]:
        """The 1m lows after the fill minute (the fill minute itself is not used, as in the backtest)."""
        start = int(r["entry_time"].timestamp()) // 60 * 60
        bars = await asyncio.to_thread(self.candles, r["symbol"], start, int(now))
        return [float(b["low"]) for b in bars if b.get("low") not in (None, "") and int(b["time"]) > start]

    async def _move_stop(self, r, price, tick) -> str | None:
        """Move the resting stop to `price` and read it back. None when it reads back at the new price, else why not
        (nothing is recorded by the caller then: the next pass tries again)."""
        try:
            so = await asyncio.to_thread(self.stop_order, r["product_id"])
            if so is None or so.get("id") is None:
                return "no resting stop order to move"
            await asyncio.to_thread(self.client.edit_stop_price, int(so["id"]), r["product_id"], price)
            moved = await asyncio.to_thread(self.stop_order, r["product_id"])
            if moved is None or abs(float(moved.get("stop_price") or 0) - float(price)) > float(tick) * 1.5:
                return "the stop does not read back at the new price"
            return None
        except Exception as exc:                         # noqa: BLE001
            return f"{type(exc).__name__}: {exc}"

    async def _lock(self, con, r, now: float) -> None:
        """The break-even move. `lock_price` is written only once the stop HAS moved (or the position was bought back
        at market by this rule): a failure anywhere leaves the row as it was and the next pass tries again."""
        e = float(r["entry_price"])
        lows = await self._lows_since_fill(r, now)
        notional = e * r["contracts"] * r["contract_value"]
        if not lows or notional <= 0 or min(lows) > activation_price(e, notional, float(r["balance_before"])):
            return                                       # the best point has not netted +$30.61 x scale yet
        prod = await asyncio.to_thread(self.product, r["symbol"])
        lp = to_tick(lock_price(e, notional, float(r["balance_before"])), prod["tick"], up=True)
        last = await asyncio.to_thread(self.last_price, r["symbol"])
        if last > 0 and last >= float(lp):               # (no price this pass: never bought back blind -- the stop is still moved)
            await event(con, "LOCK_AT_MARKET", r["symbol"], {"lock": float(lp), "last": last})
            await self._close(con, r, now, "LOCK", "the price was already back at the break-even level")
            # only if THIS buy-back closed it: a stop that got there first stays a full stop (lock_price NULL)
            await con.execute("UPDATE fade_live_positions SET lock_price = $2 WHERE id = $1 AND status = 'CLOSED' "
                              "AND exit_reason = 'LOCK'", r["id"], float(lp))
            return
        err = await self._move_stop(r, lp, prod["tick"])
        if err:
            await event(con, "LOCK_FAILED", r["symbol"], {"lock": float(lp), "error": err[:300]})
            return
        await con.execute("UPDATE fade_live_positions SET lock_price = $2 WHERE id = $1", r["id"], float(lp))
        await event(con, "LOCK_MOVED", r["symbol"], {"stop_from": r["stop_price"], "stop_to": float(lp), "last": last})
        log.warning("spike %s: stop moved to %s (break-even)", r["symbol"], lp)

    async def _trail(self, con, r, now: float) -> None:
        """Move the stop to the lowest low since the fill x (1 + TRAIL) when that is tighter (lower) than the stop now;
        if the last price is already at or above it, buy back at once ("LOCK"), as the backtest. With BREAKEVEN_LOCK
        this runs after the break-even move; without it (owner 2026-11-16) from the fill, starting from the original
        stop -- `trail_price` is written only once the stop HAS moved, so a NULL `trail_price` and `lock_price` still
        means "the original stop"."""
        lows = await self._lows_since_fill(r, now)
        if not lows:
            return
        prod = await asyncio.to_thread(self.product, r["symbol"])
        current = float(r["trail_price"] or r["lock_price"] or r["stop_price"])
        new = to_tick(min(lows) * (1 + TRAIL), prod["tick"], up=True)
        if TRAIL_ONLY_PAST_ENTRY and float(new) > float(r["entry_price"]):
            return                                       # would still be a loss: the 10% stop stays (rule C, 2026-11-17)
        if float(new) >= current - float(prod["tick"]) * 0.5:
            return                                       # not tighter: the break-even stop (or the last trail) stays
        last = await asyncio.to_thread(self.last_price, r["symbol"])
        if last > 0 and last >= float(new):              # (no price this pass: never bought back blind -- the stop is still moved)
            await event(con, "TRAIL_AT_MARKET", r["symbol"], {"trail": float(new), "last": last})
            await self._close(con, r, now, "LOCK", "the price was already back above the trailing level")
            return
        err = await self._move_stop(r, new, prod["tick"])
        if err:
            await event(con, "TRAIL_FAILED", r["symbol"], {"trail": float(new), "error": err[:300]})
            return
        await con.execute("UPDATE fade_live_positions SET trail_price = $2 WHERE id = $1", r["id"], float(new))
        await event(con, "TRAIL_MOVED", r["symbol"], {"stop_from": current, "stop_to": float(new), "last": last})

    async def _closed_at_venue(self, con, r, now: float) -> None:
        await super()._closed_at_venue(con, r, now)
        x = await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", r["id"])
        if x["status"] == "CLOSED" and x["exit_reason"] == "STOP_LOSS" and x["exit_price"]:
            trig = float(x["trail_price"] or x["lock_price"] or x["stop_price"])
            await event(con, "STOP_FILL", x["symbol"], {"trigger": trig, "fill": float(x["exit_price"]),
                                                       "slip_pct": round(100 * (float(x["exit_price"]) / trig - 1), 4),
                                                       "locked": x["lock_price"] is not None,
                                                       "trailed": x["trail_price"] is not None})

    # -- entries -------------------------------------------------------------------------------------------------
    async def month_stops(self, con, now: float) -> int:
        month = _ts(now).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return int(await con.fetchval(
            "SELECT count(*) FROM fade_live_positions WHERE status = 'CLOSED' AND exit_reason IN ('STOP_LOSS', 'LIQUIDATION') "
            "AND lock_price IS NULL AND trail_price IS NULL AND exit_time >= $1 AND detail->>'run' = $2",
            month, str(RUN_VERSION)) or 0)

    async def _enter_one(self, con, day: int, p, now: float) -> None:
        from live.client import AmbiguousWrite, VenueError, VenueRejected
        from live.fade import K_STOP
        from live.orders import OrderRequest, OrderType, Side, StopTriggerMethod, TimeInForce, client_order_id
        await self._schema(con)
        sym = p["symbol"]
        bal = await asyncio.to_thread(self.balance)
        if bal < L.FLOOR_USD:
            await self._skip(con, day, p, f"run stopped: balance ${bal:.2f} is under ${L.FLOOR_USD:.0f}")
            await self.set_state(con, "STOPPED", f"balance ${bal:.2f} under the ${L.FLOOR_USD:.0f} floor: refill "
                                 "and run fade-live-capital")
            return
        if MONTH_STOP_LIMIT is not None and await self.month_stops(con, now) >= MONTH_STOP_LIMIT:
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
        ref = self.last_price(sym, t)                     # the same ticker read: the last traded price
        if ref <= 0 or bid <= 0:
            await self._skip(con, day, p, "no last price or bid")
            return
        limit = to_tick(ref * (1 + SPIKE_ABOVE), prod["tick"], up=True)
        if bid >= float(limit) * 0.999:
            await self._skip(con, day, p, f"already 5% up at entry time (bid {bid:g} vs limit {limit})")
            return
        n, lev, notional = spike_size(bal, float(limit), prod["cv"], prod["im"], prod["cap"], prod["ims"], prod["limit"])
        if n < 1 or lev < 1:
            await self._skip(con, day, p, f"below one contract at {TRADE_MARGIN:.0%} margin")
            return
        notional = n * float(limit) * prod["cv"]
        stop = to_tick(float(limit) * (1 + STOP_ABOVE), prod["tick"], up=False)
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
            float(limit), float(stop), None, round(n * prod["cv"] * (float(stop) - float(limit)) / bal, 4), cid,
            json.dumps({"bid": bid, "run": RUN_VERSION, "notional": round(notional, 2), "sent_at": int(now)}))
        order = OrderRequest(product_id=prod["id"], size=n, side=Side.SELL, order_type=OrderType.LIMIT,
                             client_order_id=cid, limit_price=limit, time_in_force=TimeInForce.GTC, post_only=True,
                             bracket_stop_loss_price=stop,     # no take-profit: the 10% stop, then the trail once past the entry
                             stop_trigger_method=StopTriggerMethod.LAST_TRADED, note="spike-live entry")
        try:
            o = await asyncio.to_thread(self.client.place_order, order)
        except VenueRejected as exc:
            await con.execute("UPDATE fade_live_positions SET status = 'FAILED', skip_reason = $2 WHERE id = $1",
                              row_id, f"Delta refused the order: {exc}"[:300])
            await event(con, "REJECTED", sym, {"error": str(exc)[:400]})
            return
        except (AmbiguousWrite, VenueError) as exc:
            await self.set_state(con, "HALTED", f"{sym} entry outcome unknown ({exc}): the bot looks for it at Delta and "
                                 "cancels it while halted -- check Delta's open orders too, then fade-live-resume")
            return                                       # left SENDING: _resting finds it (or writes it off after 2 min)
        log.warning("spike %s: resting sell x%d at %s (stop %s, no target, %dx)", sym, n, limit, stop, lev)
        # settled from Delta's own reply: a lookup right after the write could miss an order that IS resting
        await self._resting(con, await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", row_id), now, o=o)


# ============================================================ operator command

def probe(symbol: str = "XRPUSD", env: dict | None = None) -> int:
    """ONE real contract through Spike Sell's order shapes, then flat again (`python -m live fade-live-probe [SYMBOL]`).
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
        print(f"   cancelled: {(client.get_order_by_client_id(cid1) or {}).get('state')}")
    # 2. one contract sold with a last-traded stop
    cid2 = client_order_id("spike-probe", symbol, str(int(time.time())), "sell")
    stop = to_tick(bid * 1.20, p["tick"], up=True)
    o2 = client.place_order(OrderRequest(product_id=p["id"], size=1, side=Side.SELL, order_type=OrderType.LIMIT,
                                         client_order_id=cid2, limit_price=to_tick(bid * (1 - SLIP), p["tick"], up=False),
                                         time_in_force=TimeInForce.IOC, bracket_stop_loss_price=stop,
                                         stop_trigger_method=StopTriggerMethod.LAST_TRADED, note="spike probe sell"))
    print(f"2. sold {filled(o2)} at {fill_price(o2)}")
    if not filled(o2):
        print("   !!! nothing sold: the stop, the stop edit and the margin top-up were NOT tested")
        rc = 1
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
            if bad:
                rc = 1                                   # not isolated margin: the gate before the first real order
    finally:
        row, left = acct.buy_back(symbol, client_order_id("spike-probe", cid2, "exit"))
        n = acct.cancel_leftovers(p["id"])
        print(f"5. bought back at {fill_price(row)}; {left} left open; {n} leftover orders cancelled")
        if left:
            print(f"!!! {symbol}: {left} contracts STILL OPEN -- close by hand on Delta")
            rc = 1
    print(f"6. balance now ${acct.balance():.2f}; {'PASS' if rc == 0 else 'FAIL'}")
    return rc
