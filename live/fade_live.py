"""Gainer-fade REAL-MONEY account. Version 3 (owner, 2026-11-09: "Let's run rule a ladder in paper and in
real"; "approved on 1-6"): rule A, $1,500, sized by the drawdown ladder 10/5/3 (steps at 20% and 35% below
the high-water mark), each position up to 60% of the balance as notional on at most 30% of it as isolated
margin (1x-3x, live.fade.lever_plan; amendment 1), a MARKET stop. Below $1,000 at an
entry check the run STOPS until the owner refills it (`python -m live fade-live-capital`), which resets the
high-water mark so the ladder restarts at 10%; refills are capped at $3,000 for the run. Version 1
(2026-11-02, rule A flat 3%, $176) and the unmerged version 2 (QUICK) are superseded. The rules were fixed
before any order in docs/fade_live_ladder_prereg.md; this module is them, nothing more.

It rides the paper runner (live/fade.py): once the paper picks for a UTC day are entered, each pick is
SOLD on Delta with an immediate-or-cancel limit 1% under the bid and an exchange-held stop at the paper
stop distance; 24 h later it is bought back reduce-only. Risk per trade = the ladder's step for the
current balance against the best balance so far (live/fade.py ladder_risk), never above
DELTABOT_RISK_PER_TRADE. A take-profit leg is sent only if TP_FRACTION is set (it is not, for rule A). Every venue call goes through
live.client.LiveClient (one send per order, ambiguous writes looked up by client order id, never
resent) and every order through live.orders.OrderRequest.

ENTRIES only if all of: DELTABOT_DRY_RUN is exactly "0", DELTA_ENV is "prod", both key variables are set,
and sizing is in range. With prod keys but entries off, the account still runs EXITS-ONLY: positions it
already holds keep their reconcile and 24 h buy-back (so switching entries off, or a restart, never strands
a position). Without prod keys this module only creates its (empty) tables.

A new run (no state row, or one from an earlier version) begins only if the wallet looks like the
sub-account (between the floor and 1.25 x DELTABOT_EQUITY); after that the band is never checked again,
so growth or a restart cannot switch the exits off.

What stops it:
  STOPPED  the wallet was under FLOOR_USD at an entry check. Only `fade-live-capital` after a refill
           (wallet >= REFILL_MIN_USD, refills within REFILL_BUDGET_USD) starts it again.
  HALTED   something it cannot explain: a position it has no record of, a position without a resting
           stop covering it, an order whose outcome it cannot establish, an exit it could not complete,
           ERROR_HALT_AFTER failed passes in a row, or a wallet change no trade explains (a deposit or
           withdrawal: record it with `fade-live-capital`). No new entries until the owner looks
           (`python -m live fade-live-resume` clears it).
Neither touches open positions: their stops rest at Delta, and their 24 h exits still run.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import math
import os
import time
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

log = logging.getLogger(__name__)

ACCOUNT = "LIVE"
#: docs/fade_live_ladder_prereg.md -- the owner's choices of 2026-11-09 ($1,500 start, ladder 10/5/3,
#: below $1,000 the run stops until a refill; refills reset the high-water mark; $3,000 of refills at most).
RUN_VERSION = 3
FLOOR_USD = 1000.0
REFILL_MIN_USD = 1400.0
REFILL_BUDGET_USD = 3000.0
MAX_RISK_PCT = 0.10
#: Rule A has no take-profit. (QUICK would set 0.2: the leg sits this fraction of the stop distance below.)
TP_FRACTION: float | None = None
#: A wallet holding more than this x DELTABOT_EQUITY is not the sub-account.
WRONG_ACCOUNT_RATIO = 1.25
#: Position size and leverage come from live.fade.lever_plan (amendment 1, 2026-11-09): notional <= 60% of the
#: balance, isolated margin <= 30%, 1x-3x, liquidation >= 2x the stop distance away (measured from the worst
#: allowed fill). The probe runs at PROBE_LEVERAGE and must show isolated margin (margin_problem).
PROBE_LEVERAGE = 2                                   # the probe proves the leveraged shape: isolated margin
#: Position fields recorded on every real entry (and printed by the probe): what proves isolated margin.
MARGIN_FIELDS = ("margin", "margin_mode", "liquidation_price", "bankruptcy_price", "entry_price", "size",
                 "isolated_margin", "position_margin", "auto_topup")
#: Delta's maintenance margin when the product does not state one (never assume less).
MIN_MM_PCT = 2.5
#: Stops further than this are not traded (the universe every test was run on).
MAX_DIST = 0.325
#: The worst price an entry or a 24 h exit may fill at, as a fraction beyond the touch.
SLIP = 0.01
#: The stop is a MARKET stop (owner 2026-11-09): at 1x a capped stop-limit that a gap jumps past leaves the
#: position open until the 30 s loop buys it back at market anyway; a market stop gets there first.
#: The widest bid-ask spread an entry accepts, as a fraction of the bid (logged on every entry row).
MAX_SPREAD = 0.01
MAX_OPEN = 2
#: An entry later than this after 00:00 UTC is not the trade paper made: skip it.
ENTRY_WINDOW = 30 * 60
#: Seconds past 00:00 UTC before yesterday's positions are bought back.
EXIT_AFTER = 5
#: How long a fresh fill may wait for its stop to show as resting at Delta.
PROTECT_WAIT = 30.0
#: A position flat at Delta with no closing order found is called closed after this long.
UNEXPLAINED_FLAT = 300
#: How often the wallet balance is snapshotted (report, high-water mark, capital check).
BALANCE_EVERY = 300.0
#: A wallet move no trade explains, beyond max(this, CAPITAL_TOL_PCT x balance), seen on two snapshots in a
#: row, is a deposit or withdrawal: HALT until the owner records it. Funding (not tracked per trade) stays far
#: inside it: two positions <= 60% of the balance x a 1%/8 h rate = 0.6%.
CAPITAL_TOL_USD = 10.0
CAPITAL_TOL_PCT = 0.02
#: Marks a halt that happened while the run was STOPPED (kept in the STOPPED reason).
HALT_NOTE = "| HALT while stopped:"
#: Failed passes in a row (30 s apart) before the account HALTs its entries.
ERROR_HALT_AFTER = 10
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
    target_price   DOUBLE PRECISION,          -- the bracket take-profit (rule QUICK); NULL on rule A rows
    entry_cid      TEXT,
    entry_time     TIMESTAMPTZ,
    entry_price    DOUBLE PRECISION,          -- average fill
    entry_fee      DOUBLE PRECISION,
    flat_since     TIMESTAMPTZ,               -- first pass that found it flat with no closing order
    exit_time      TIMESTAMPTZ,
    exit_price     DOUBLE PRECISION,
    exit_fee       DOUBLE PRECISION,
    exit_reason    TEXT,                      -- TIME | STOP_LOSS | TAKE_PROFIT | LIQUIDATION | UNPROTECTED | MANUAL_CLOSE | UNKNOWN_EXIT
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
    balance    DOUBLE PRECISION,              -- the last ACCEPTED wallet balance (the capital check's base)
    balance_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS fade_live_events (
    id      BIGSERIAL PRIMARY KEY,
    at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind    TEXT NOT NULL,
    symbol  TEXT,
    detail  JSONB
);
ALTER TABLE fade_live_positions ADD COLUMN IF NOT EXISTS target_price DOUBLE PRECISION;
ALTER TABLE fade_live_positions ADD COLUMN IF NOT EXISTS risk_pct DOUBLE PRECISION;
ALTER TABLE fade_live_state ADD COLUMN IF NOT EXISTS high_water DOUBLE PRECISION;
ALTER TABLE fade_live_state ADD COLUMN IF NOT EXISTS run_version INTEGER;
ALTER TABLE fade_live_state ADD COLUMN IF NOT EXISTS added_usd DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE fade_live_positions ADD COLUMN IF NOT EXISTS exit_cids TEXT[] NOT NULL DEFAULT '{}';
"""

#: How a bracket leg's close is recorded (live/ledger.py) -- both are the plan working, not a fault.
BRACKET_EXITS = ("STOP_LOSS", "TAKE_PROFIT")
#: explained(): a close since the base has no pnl -- the wallet cannot be split into trades and capital.
UNKNOWN = float("nan")


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


def can_close(env: dict | None = None) -> bool:
    """Prod keys are present: enough to reconcile and buy back what the account holds, entries or not."""
    src = os.environ if env is None else env
    return ((src.get("DELTA_ENV") or "").strip().lower() == "prod"
            and bool((src.get("DELTA_API_KEY") or "").strip()) and bool((src.get("DELTA_API_SECRET") or "").strip()))


def capital_gap(before: float, after: float, explained: float) -> float:
    """The wallet move between two snapshots that no trade explains."""
    return after - before - explained


def capital_suspect(gap: float, balance: float) -> bool:
    return abs(gap) > max(CAPITAL_TOL_USD, CAPITAL_TOL_PCT * balance)


def high_water_after(status: str, balance: float, high_water: float | None, change: float) -> float:
    """The high-water mark once the owner records a capital change of `change` (balance is after it).
    A refill (the run was STOPPED) restarts the ladder: the mark is the new balance. Otherwise the mark
    moves by the same dollars, so the ladder's drawdown in dollars is kept (never below the balance)."""
    if status == "STOPPED" or high_water is None:
        return balance
    return max(balance, high_water + change)


def contracts_for(balance: float, risk_pct: float, ref: float, stop: float, cv: float,
                  maintenance_pct: float) -> tuple[int, int]:
    """(whole contracts, leverage): the notional live.fade.lever_plan allows for `risk_pct` of the balance between
    ref and stop. (0, 0) when there is nothing to trade, or liquidation could not sit 2x the stop away."""
    from live.fade import lever_plan
    if not (balance > 0 and stop > ref > 0 and cv > 0):
        return 0, 0
    # liquidation is measured from the worst fill the IOC allows (SLIP under the bid): a little more conservative
    worst = ref * (1 - SLIP)
    share, lev = lever_plan(risk_pct, (stop - ref) / ref, max(maintenance_pct or 0, MIN_MM_PCT),
                            liq_dist=(stop - worst) / worst)
    return int(math.floor(share * balance / (ref * cv) + 1e-9)), lev   # 249.99999 is 250 contracts


def target_for(ref: float, stop: float, tick: Decimal, fraction: float = 0.2) -> Decimal:
    """The short's take-profit: `fraction` x (stop - ref) below `ref`, rounded DOWN on the tick grid so a
    target fill is never a smaller win than paper's."""
    return to_tick(ref - fraction * (stop - ref), tick, up=False)


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


def margin_problem(pos: dict, entry: float, contracts: int, cv: float, lev: int) -> str | None:
    """Why a fresh short is NOT on isolated margin as planned, or None. Isolated at `lev` means: no auto top-up,
    margin about notional / lev (within 15%), and a liquidation price between 1/lev - 10% and 1/lev above the entry
    (maintenance makes it a little closer than 1/lev). A missing field is a problem: the bound must be shown."""
    if str(pos.get("auto_topup")).lower() in ("true", "1"):
        return "auto top-up is on (wallet money would feed the position)"
    mode = str(pos.get("margin_mode") or "").lower()
    if mode and mode != "isolated":
        return f"margin mode is {mode}"
    try:
        margin = float(pos.get("margin"))
        liq = float(pos.get("liquidation_price"))
    except (TypeError, ValueError):
        return f"margin or liquidation price not reported ({ {k: pos.get(k) for k in MARGIN_FIELDS} })"
    notional = entry * contracts * cv
    if not (0.85 * notional / lev <= margin <= 1.15 * notional / lev):
        return f"margin {margin:g} is not about {notional / lev:g} (notional / {lev}x)"
    up = liq / entry - 1
    if not (1.0 / lev - 0.10 <= up <= 1.0 / lev + 0.005):
        return f"liquidation {liq:g} is {up:.1%} above the entry, not about {1.0 / lev:.0%}"
    return None


def combine_fills(rows: list[dict]) -> dict | None:
    """Several fills of one close as one row: contracts summed, price weighted by contracts, fees summed."""
    rows = [r for r in rows if filled(r) and fill_price(r)]
    if not rows:
        return None
    n = sum(filled(r) for r in rows)
    px = sum(filled(r) * fill_price(r) for r in rows) / n
    return {"size": n, "unfilled_size": 0, "average_fill_price": repr(px), "paid_commission": repr(sum(fee(r) for r in rows)),
            "client_order_id": rows[-1].get("client_order_id"), "reduce_only": True, "side": "buy"}


def money(entry: float, exit_: float, contracts: int, cv: float, stop: float, fees: float) -> tuple[float, float | None]:
    """(pnl, r) of a short: price move times size less both fees, over the planned risk."""
    qty = contracts * cv
    pnl = (entry - exit_) * qty - fees
    risk = (stop - entry) * qty
    return pnl, (pnl / risk if risk > 0 else None)


def exit_legs(row_id: int, reason: str, attempt: int) -> tuple[str, list[str]]:
    """(cid base, the stored "REASON:cid" entries) of one buy-back attempt on position `row_id`. A new attempt
    gets new ids, so a retry is never refused by Delta as a duplicate (reduce-only: it can never over-buy)."""
    from live.orders import client_order_id
    base = client_order_id("fade-live", str(row_id), reason.lower(), str(attempt))
    return base, [f"{reason}:{client_order_id(base, leg)}" for leg in ("limit", "market")]


def own_exit(stored: list[str] | None, cid: str | None) -> str | None:
    """"TIME" or "UNPROTECTED" if `cid` is one of this position's own buy-back orders (see exit_legs)."""
    if not cid:
        return None
    for entry in stored or ():
        reason, _, c = entry.partition(":")
        if c == cid:
            return reason
    return None


def _ts(t: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(float(t), dt.timezone.utc)


# ============================================================ the account

class LiveFade:
    """The real account. `client` is a live.client.LiveClient on prod (a fake in tests). Its venue
    methods are synchronous and are called through asyncio.to_thread."""

    def __init__(self, client, *, risk_pct: float, start_usd: float, kill_switch_path: str | None = None,
                 protect_wait: float = PROTECT_WAIT, sleep=time.sleep, entries: bool = True):
        self.client = client
        self.risk_pct = float(risk_pct)
        self.start_usd = float(start_usd)
        self.kill_switch_path = kill_switch_path
        self.protect_wait = protect_wait
        self.entries = entries                         # False = exits-only: reconcile and buy back, never sell
        self._sleep = sleep
        self._products: dict[str, dict] = {}
        self._last_balance_at = 0.0
        self._fails = 0
        self._suspect = False

    # -- venue reads -------------------------------------------------------------------------
    def product(self, symbol: str) -> dict:
        if symbol not in self._products:
            p = self.client.get_product(symbol)
            self._products[symbol] = {"id": int(p["id"]), "tick": Decimal(str(p["tick_size"])),
                                      "cv": float(p["contract_value"]),
                                      "mm": float(p.get("maintenance_margin") or 0)}
        return self._products[symbol]

    def balance(self) -> float:
        """The wallet's USD balance. Raises if Delta does not report one: a missing row must never read as
        $0 (that would STOP the run on a glitch)."""
        for row in self.client.get_balance():
            if str(row.get("asset_symbol", "")).upper() in ("USD", "USDT") and row.get("balance") not in (None, ""):
                return float(row["balance"])
        raise RuntimeError("Delta reported no USD balance row")

    def venue_size(self, product_id: int) -> int:
        from live.reconcile import _signed
        row = self.client.get_position(product_id)
        return _signed(row) if row else 0

    def leg_resting(self, product_id: int, kind: str) -> bool:
        return any(r.get("reduce_only") and r.get("stop_order_type") == kind
                   and r.get("product_id") in (None, product_id)
                   for r in self.client.get_pending_orders(product_id))

    def stop_resting(self, product_id: int) -> bool:
        return self.leg_resting(product_id, "stop_loss_order")

    def stop_covers(self, product_id: int) -> bool:
        """A resting stop that covers the whole position. A stop row that states a size smaller than the
        position (a partial entry fill with a full-size-unaware leg, or the reverse) does not count; a row
        with no size is Delta's close-the-position bracket and covers it."""
        held = abs(self.venue_size(product_id))
        for r in self.client.get_pending_orders(product_id):
            if not (r.get("reduce_only") and r.get("stop_order_type") == "stop_loss_order"
                    and r.get("product_id") in (None, product_id)):
                continue
            try:
                size = int(round(float(r.get("size") or 0)))
            except (TypeError, ValueError):
                size = 0
            if size == 0 or size >= held:
                return True
        return False

    def target_resting(self, product_id: int) -> bool:
        return self.leg_resting(product_id, "take_profit_order")

    def wait_protected(self, product_id: int) -> bool:
        deadline = time.monotonic() + self.protect_wait
        while not self.stop_covers(product_id):
            if time.monotonic() >= deadline:
                return False
            self._sleep(2.0)
        return True

    def last_close(self, product_id: int, opened: dt.datetime | None = None,
                   others: set[str] = frozenset()) -> dict | None:
        """The newest filled reduce-only BUY on this product that closed THIS position: never another position's
        buy-back on the same coin (`others`, their exit ids), and never an order stamped before `opened`."""
        from live.ledger import parse_venue_time
        for o in self.client.order_history(product_id, 20):
            if not (o.get("reduce_only") and filled(o) and str(o.get("side")).lower() == "buy"):
                continue
            if o.get("client_order_id") and o["client_order_id"] in others:
                continue
            at = parse_venue_time(o.get("updated_at") or o.get("created_at"))
            if opened is not None and at is not None and at < opened:
                continue
            return o
        return None

    # -- venue writes ------------------------------------------------------------------------
    def buy_back(self, symbol: str, cid_base: str) -> tuple[dict | None, int]:
        """Close the short on `symbol`: reduce-only IOC limit 1% over the ask, then reduce-only IOC
        market for whatever is left. Returns (the fills combined -- size-weighted price, summed fees -- or None,
        contracts still open). reduce_only means a size that disagrees with the venue's can never OPEN a long."""
        from live.orders import OrderRequest, OrderType, Side, TimeInForce, client_order_id
        p = self.product(symbol)
        fills: list[dict] = []
        size = abs(self.venue_size(p["id"]))
        if size:
            ask = float((self.client.get_ticker(symbol).get("quotes") or {}).get("best_ask") or 0)
            if ask > 0:
                row = self.client.place_order(OrderRequest(
                    product_id=p["id"], size=size, side=Side.BUY, order_type=OrderType.LIMIT,
                    client_order_id=client_order_id(cid_base, "limit"),
                    limit_price=to_tick(ask * (1 + SLIP), p["tick"], up=True),
                    time_in_force=TimeInForce.IOC, reduce_only=True, note="fade-live exit"))
                if filled(row):
                    fills.append(row)
            size = abs(self.venue_size(p["id"]))
        if size:
            row = self.client.place_order(OrderRequest(
                product_id=p["id"], size=size, side=Side.BUY, order_type=OrderType.MARKET,
                client_order_id=client_order_id(cid_base, "market"),
                time_in_force=TimeInForce.IOC, reduce_only=True, note="fade-live exit (rest)"))
            if filled(row):
                fills.append(row)
            size = abs(self.venue_size(p["id"]))
        return combine_fills(fills), size

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
    async def state(self, con, now: float | None = None) -> str:
        """The run's status. With entries on: a missing row, or one from an earlier version, begins a new
        run (wallet band checked once, here); an OFF row of this version goes back to RUNNING as it was."""
        row = await con.fetchrow("SELECT status, run_version FROM fade_live_state WHERE id = 1")
        if not self.entries:
            return row["status"] if row else "OFF"
        if row is None or row["run_version"] != RUN_VERSION:
            return await self._begin(con, time.time() if now is None else now)
        if row["status"] == "HALTED" and await con.fetchval(
                "SELECT reason LIKE $1 FROM fade_live_state WHERE id = 1", f"run {RUN_VERSION} cannot start%"):
            return await self._begin(con, time.time() if now is None else now)   # the owner may have topped up
        if row["status"] == "OFF":
            await con.execute("UPDATE fade_live_state SET status = 'RUNNING', reason = 'entries back on', at = now() "
                              "WHERE id = 1 AND status = 'OFF'")
            await event(con, "RUNNING", None, {"reason": "entries back on"})
            return "RUNNING"
        return row["status"]

    async def _begin(self, con, now: float) -> str:
        """A new run: the wallet must look like the funded sub-account; the high-water mark, the capital
        base and the refill tally start here."""
        bal = await asyncio.to_thread(self.balance)
        prev = await con.fetchrow("SELECT reason FROM fade_live_state WHERE id = 1")
        top = WRONG_ACCOUNT_RATIO * self.start_usd
        if FLOOR_USD <= bal <= top:
            status, reason = "RUNNING", f"run {RUN_VERSION} started at ${bal:.2f}"
        else:
            status, reason = "HALTED", (f"run {RUN_VERSION} cannot start: wallet ${bal:.2f} is outside "
                                        f"${FLOOR_USD:.0f}-${top:.0f} (not the sub-account, or not funded)")
        await con.execute(
            "INSERT INTO fade_live_state (id, status, reason, at, balance, balance_at, high_water, run_version, "
            "added_usd) VALUES (1, $1, $2, now(), $3, $4, $3, $5, 0) ON CONFLICT (id) DO UPDATE SET status = $1, "
            "reason = $2, at = now(), balance = $3, balance_at = $4, high_water = $3, run_version = $5, added_usd = 0",
            status, reason, bal, _ts(now - 0.001), RUN_VERSION)   # read before this pass's own trades
        if status == "RUNNING" or prev is None or prev["reason"] != reason:
            await event(con, "NEW_RUN", None, {"version": RUN_VERSION, "balance": bal, "status": status, "reason": reason})
            log.warning("fade live: %s", reason)
        return status

    async def high_water(self, con, balance: float) -> float:
        """The run's high-water mark (raised only by accepted balance snapshots and set by the owner's
        capital command), or `balance` if there is none yet."""
        hw = await con.fetchval("SELECT high_water FROM fade_live_state WHERE id = 1")
        return float(hw) if hw is not None else float(balance)

    async def set_state(self, con, status: str, reason: str) -> None:
        """STOPPED ends with a refill only (`fade-live-capital`); a repeat of the same status and reason is
        not re-recorded."""
        cur = await con.fetchrow("SELECT status, reason FROM fade_live_state WHERE id = 1")
        if cur and cur["status"] == "STOPPED":
            if status == "HALTED" and HALT_NOTE not in (cur["reason"] or ""):
                # Stays STOPPED, but the halt is kept in the reason: fade-live-capital refuses until it is cleared.
                await con.execute("UPDATE fade_live_state SET reason = $1 WHERE id = 1",
                                  f"{cur['reason']} {HALT_NOTE} {reason}"[:500])
                await event(con, "HALTED", None, {"reason": reason, "while": "STOPPED"})
                log.error("fade live account (STOPPED) also HALTED: %s", reason)
            return
        if cur and cur["status"] == status == "HALTED":
            log.error("fade live account still HALTED (first reason kept); also: %s", reason)
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
            first: Exception | None = None
            for part in (self.state, self._reconcile):    # a failure here must not stop the 24 h exits
                try:
                    await (part(con, now))
                except Exception as exc:                  # noqa: BLE001
                    first = first or exc
            await self._exits(con, now)
            if first is not None:
                raise first
            if self.entries and await self.state(con, now) == "RUNNING":
                await self._entries(con, now)
            if now - self._last_balance_at >= BALANCE_EVERY:
                await self._snapshot(con, now)
                self._last_balance_at = now
            self._fails = 0
        except Exception as exc:                          # noqa: BLE001
            self._fails += 1
            log.exception("fade live step failed (%d in a row)", self._fails)
            try:
                await event(con, "ERROR", None, {"error": f"{type(exc).__name__}: {exc}"[:400], "in_a_row": self._fails})
                if self._fails >= ERROR_HALT_AFTER and self.entries:
                    await self.set_state(con, "HALTED", f"{self._fails} failed passes in a row; last: "
                                         f"{type(exc).__name__}: {exc}"[:400])
            except Exception:                             # noqa: BLE001
                pass

    async def explained(self, con, since: dt.datetime) -> float | None:
        """What the bot's own trades did to the wallet after `since` (a base is taken at the END of a pass, so an
        event stamped at that same instant is already in it): entry fees of positions opened after it,
        and each close since (its pnl, which already includes the entry fee, plus that fee back). None if a
        position is flat at Delta but not yet closed here (wait). A close whose pnl could not be worked out
        (UNKNOWN_EXIT, already a HALT) makes the result UNKNOWN: the snapshot then takes the wallet as the new
        base rather than freeze."""
        rows = await con.fetch(
            "SELECT status, entry_time, entry_fee, exit_time, pnl, flat_since FROM fade_live_positions "
            "WHERE (entry_time > $1 OR exit_time > $1 OR (status = 'OPEN' AND flat_since IS NOT NULL))", since)
        total = 0.0
        for r in rows:
            if r["status"] == "OPEN" and r["flat_since"] is not None:
                return None
            fee_in = float(r["entry_fee"] or 0)
            if r["entry_time"] is not None and r["entry_time"] > since:
                total -= fee_in
            if r["status"] == "CLOSED" and r["exit_time"] is not None and r["exit_time"] > since:
                if r["pnl"] is None:
                    return UNKNOWN
                total += float(r["pnl"]) + fee_in
        return total

    async def _snapshot(self, con, now: float) -> None:
        """Accept the wallet balance as the new base (and raise the high-water mark) unless it moved by more
        than the bot's trades explain on two snapshots in a row: then it is a deposit or withdrawal, and
        the account HALTs (STOPPED stays STOPPED) with the old base kept for `fade-live-capital`."""
        bal = await asyncio.to_thread(self.balance)
        st = await con.fetchrow("SELECT status, balance, balance_at FROM fade_live_state WHERE id = 1")
        if st is None:
            return
        if st["balance"] is not None and st["balance_at"] is not None:
            exp = await self.explained(con, st["balance_at"])
            if exp is None:
                return                                   # a close is still being read: keep the base
            if math.isnan(exp):
                self._suspect = False
                await event(con, "BASE_RESET", None, {"balance": bal, "why": "a close with no pnl since the base"})
                await con.execute("UPDATE fade_live_state SET balance = $1, balance_at = $2 WHERE id = 1", bal, _ts(now))
                return
            gap = capital_gap(float(st["balance"]), bal, exp)
            if capital_suspect(gap, float(st["balance"])):
                if not self._suspect:
                    self._suspect = True                 # a close may land between the read and the record
                    return
                self._suspect = False
                if st["status"] not in ("STOPPED", "OFF"):
                    await self.set_state(con, "HALTED", f"wallet moved ${gap:+.2f} that no trade explains (a "
                                         "deposit or withdrawal?): record it with fade-live-capital")
                return
        self._suspect = False
        await con.execute("UPDATE fade_live_state SET balance = $1, balance_at = $2, "
                          "high_water = GREATEST(COALESCE(high_water, $1), $1) WHERE id = 1", bal, _ts(now))

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
                if not await asyncio.to_thread(self.stop_covers, r["product_id"]):
                    await self._close(con, r, now, "UNPROTECTED", "no stop covering it resting at Delta")
                elif r["entry_price"] and "venue_position" not in (r["detail"] or ""):
                    await self._check_margin(con, r["id"], now)  # the check after the fill did not complete
                continue
            await self._closed_at_venue(con, r, now)

    async def _closed_at_venue(self, con, r, now: float) -> None:
        """Flat at Delta while we hold it OPEN: its stop, its take-profit (or a liquidation) closed it."""
        from live.ledger import close_facts
        others = {e.partition(":")[2] for x in await con.fetch(
            "SELECT exit_cids FROM fade_live_positions WHERE product_id = $1 AND id <> $2", r["product_id"], r["id"])
            for e in (x["exit_cids"] or ())}
        o = await asyncio.to_thread(self.last_close, r["product_id"], r["entry_time"], others)
        if o is None:
            if r["flat_since"] is None:
                await con.execute("UPDATE fade_live_positions SET flat_since = $2 WHERE id = $1", r["id"], _ts(now))
                return
            if now - r["flat_since"].timestamp() < UNEXPLAINED_FLAT:
                return
        facts = close_facts(o) if o else {}
        own = own_exit(r["exit_cids"], (o or {}).get("client_order_id"))
        if own:                                          # our own buy-back, recorded late (a failure mid-exit)
            facts["exit_reason"] = own
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
        if reason not in BRACKET_EXITS + ("TIME",):
            await self.set_state(con, "HALTED", f"{r['symbol']} closed at Delta by {reason}, not its bracket")

    async def _close(self, con, r, now: float, reason: str, why: str) -> None:
        """Buy back an OPEN position ourselves (24 h exit, or no stop resting)."""
        base, legs = exit_legs(r["id"], reason, int(now))
        await con.execute("UPDATE fade_live_positions SET exit_cids = exit_cids || $2::text[] WHERE id = $1",
                          r["id"], legs)                 # written BEFORE the send: a late record finds them
        row, left = await asyncio.to_thread(self.buy_back, r["symbol"], base)
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
        if await con.fetchval("SELECT status FROM fade_days WHERE day = $1", _ts(day)) != "ENTERED":
            return                                       # the paper picks are not made yet
        picks = await con.fetch("SELECT symbol, rank, atrp, entry_price FROM fade_positions "
                                "WHERE account = 'A' AND day = $1 ORDER BY rank", _ts(day))
        done = {r["symbol"] for r in await con.fetch("SELECT symbol FROM fade_live_positions WHERE day = $1", _ts(day))}
        late = now >= day + ENTRY_WINDOW
        for p in picks:
            if p["symbol"] in done:
                continue
            if late:                                     # recorded, so a missed trade shows in the report
                await self._skip(con, day, p, f"entry window ({ENTRY_WINDOW // 60} min after 00:00 UTC) passed")
            elif kill_switch_engaged(self.kill_switch_path):
                await self._skip(con, day, p, "kill switch engaged")
            elif not p["entry_price"] or not p["atrp"]:
                await self._skip(con, day, p, "paper had no entry for this pick")
            else:
                await self._enter_one(con, day, p, now)
            st = await self.state(con)
            if st != "RUNNING":
                for q in picks:                          # the day's other picks: recorded, not traded
                    if q["symbol"] not in done and q["symbol"] != p["symbol"]:
                        await self._skip(con, day, q, f"run {st.lower()}")
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
            await self.set_state(con, "STOPPED", f"balance ${bal:.2f} under the ${FLOOR_USD:.0f} floor: refill "
                                 "and run fade-live-capital")
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
        spread = (ask - bid) / bid
        if mark >= float(stop) or ask < bid or unit <= 0:
            await self._skip(con, day, p, f"book unusable: bid {bid:g} ask {ask:g} mark {mark:g} stop {stop}")
            return
        if spread > MAX_SPREAD:
            await self._skip(con, day, p, f"spread {spread:.2%} is wider than {MAX_SPREAD:.0%} (bid {bid:g} ask {ask:g})")
            return
        if dist > MAX_DIST:
            await self._skip(con, day, p, f"stop {dist:.0%} away is past the {MAX_DIST:.1%} limit")
            return
        from live.fade import ladder_risk
        hw = await self.high_water(con, bal)
        risk = min(self.risk_pct, ladder_risk(bal, hw))
        n, lev = contracts_for(bal, risk, bid, float(stop), prod["cv"], prod["mm"])
        if lev < 1:
            await self._skip(con, day, p, f"stop {dist:.0%} away: liquidation cannot sit 2x further")
            return
        if n < 1:
            await self._skip(con, day, p, f"below one contract at {risk:.0%} risk")
            return
        try:
            await asyncio.to_thread(self.client.set_order_leverage, prod["id"], lev)
            got = await asyncio.to_thread(self.client.get_order_leverage, prod["id"])
        except VenueRejected as exc:                      # Delta refuses this leverage on this coin: skip it, go on
            await self._skip(con, day, p, f"leverage {lev}x could not be set: {exc}"[:300])
            return                                       # (a blip raises past here: retried on the next pass)
        if int(round(got)) != lev:
            await self._skip(con, day, p, f"leverage reads {got:g}x, not {lev}x")
            return
        limit = to_tick(bid * (1 - SLIP), prod["tick"], up=False)
        target = None
        if TP_FRACTION is not None:
            target = target_for(bid, float(stop), prod["tick"], TP_FRACTION)
            if not 0 < target < limit:
                await self._skip(con, day, p, f"take-profit {target} is not below the entry limit {limit}")
                return
        cid = client_order_id("fade-live", str(day), sym, "entry")
        # Written BEFORE the send: a crash after it leaves a SENDING row that _reconcile resolves by cid.
        row_id = await con.fetchval(
            "INSERT INTO fade_live_positions (day, symbol, rank, status, product_id, contract_value, contracts, "
            "leverage, balance_before, paper_entry, ref_price, limit_price, stop_price, target_price, risk_pct, "
            "entry_cid, detail) VALUES ($1,$2,$3,'SENDING',$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16) RETURNING id",
            _ts(day), sym, p["rank"], prod["id"], prod["cv"], n, lev, bal, p["entry_price"], bid,
            float(limit), float(stop), None if target is None else float(target), risk, cid,
            json.dumps({"ask": ask, "mark": mark, "spread_bps": round(1e4 * spread, 1), "high_water": hw}))
        order = OrderRequest(product_id=prod["id"], size=n, side=Side.SELL, order_type=OrderType.LIMIT,
                             client_order_id=cid, limit_price=limit, time_in_force=TimeInForce.IOC,
                             bracket_stop_loss_price=stop, bracket_take_profit_price=target,
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

    async def _check_margin(self, con, row_id: int, now: float) -> bool:
        """Isolated margin is what bounds a gap through the stop to this position's margin: check it once per
        position (here after the fill; again from _reconcile if that read failed). Not as planned -> bought back,
        HALT. Returns False if it closed the position."""
        r = await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", row_id)
        pos = await asyncio.to_thread(self.client.get_position, r["product_id"]) or {}
        await con.execute("UPDATE fade_live_positions SET detail = COALESCE(detail, '{}'::jsonb) || $2::jsonb "
                          "WHERE id = $1", row_id, json.dumps({"venue_position": {
                              k: pos.get(k) for k in MARGIN_FIELDS if k in pos}}, default=str))
        bad = margin_problem(pos, r["entry_price"], r["contracts"], r["contract_value"], int(r["leverage"] or 1))
        if bad:
            fresh = await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", row_id)
            await self._close(con, fresh, now, "MARGIN", f"not isolated margin as planned: {bad}")
            return False
        return True

    async def _opened(self, con, r, o: dict, now: float) -> None:
        n, px = filled(o), fill_price(o)
        await con.execute("UPDATE fade_live_positions SET status = 'OPEN', contracts = $2, entry_price = $3, "
                          "entry_fee = $4, entry_time = $5 WHERE id = $1", r["id"], n, px, fee(o), _ts(now))
        log.warning("fade live SOLD %s x%d at %s (stop %s)", r["symbol"], n, px, r["stop_price"])
        if not await asyncio.to_thread(self.wait_protected, r["product_id"]):
            fresh = await con.fetchrow("SELECT * FROM fade_live_positions WHERE id = $1", r["id"])
            await self._close(con, fresh, now, "UNPROTECTED", f"no stop resting {self.protect_wait:.0f}s after the fill")
            return
        await self._check_margin(con, r["id"], now)
        # A missing take-profit is not a danger (the stop and the 24 h exit still hold): say so, keep going.
        if r["target_price"] is not None and not await asyncio.to_thread(self.target_resting, r["product_id"]):
            await event(con, "TAKE_PROFIT_MISSING", r["symbol"], {"target": r["target_price"]})
            log.error("fade live %s: no take-profit resting at Delta", r["symbol"])


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


def _num_env(src, name: str, default: float) -> float:
    try:
        return float(src.get(name) or default)
    except ValueError:
        return default


def from_env(env: dict | None = None) -> tuple[LiveFade | None, str]:
    """(the account, "on") if this process may trade; (an EXITS-ONLY account, why entries are off) if it has
    prod keys but may not trade; (None, why) without prod keys. Makes no venue call: a Delta outage at
    start-up must not leave positions without their exits (the wallet is checked when a run begins)."""
    ok, why = enabled(env)
    if not ok and not can_close(env):
        return None, why
    from live.config import client_from_env
    src = os.environ if env is None else env
    acct = LiveFade(client_from_env(src), risk_pct=_num_env(src, "DELTABOT_RISK_PER_TRADE", 0.0),
                    start_usd=_num_env(src, "DELTABOT_EQUITY", 1500.0), entries=ok)
    if ok:
        log.warning("FADE LIVE ACCOUNT ON (prod): real orders, up to %.0f%% risk, floor $%.0f",
                    100 * acct.risk_pct, FLOOR_USD)
        return acct, "on"
    log.warning("fade live account EXITS-ONLY (%s): open positions are still reconciled and bought back", why)
    return acct, f"exits only: {why}"


# ============================================================ operator commands

def probe(symbol: str = "XRPUSD", env: dict | None = None) -> int:
    """ONE real contract through the exact order shape the account uses, then flat again.

        python -m live fade-live-probe [SYMBOL]

    Owner's yes required each time (docs/fade_live_ladder_prereg.md). Costs a few cents of fees. The account
    must be flat; its fee moves the wallet, so run `fade-live-capital` after it if the account is running."""
    from live.config import client_from_env
    from live.orders import OrderRequest, OrderType, Side, StopTriggerMethod, TimeInForce, client_order_id
    src = os.environ if env is None else env
    client = client_from_env(src)
    if not client.is_prod:
        print("refusing: DELTA_ENV is not prod (this probe exists to test the prod key)")
        return 2
    acct = LiveFade(client, risk_pct=MAX_RISK_PCT, start_usd=float(src.get("DELTABOT_EQUITY") or 1500))
    print("1. wallet rows:")
    for b in client.get_balance():
        print(f"   {b.get('asset_symbol')}: balance={b.get('balance')} available={b.get('available_balance')}")
    bal = acct.balance()
    if not 0 < bal <= WRONG_ACCOUNT_RATIO * acct.start_usd:
        print(f"refusing: balance ${bal:.2f} does not look like the sub-account")
        return 2
    held = [v for v in client.get_positions() if acct_signed(v)]
    if held:
        print(f"refusing: the account already holds positions: {[v.get('product_symbol') for v in held]}")
        return 2
    p = acct.product(symbol)
    print(f"2. {symbol}: product {p['id']}, tick {p['tick']}, contract {p['cv']}, maintenance {p['mm']}%")
    client.set_order_leverage(p["id"], PROBE_LEVERAGE)
    got = client.get_order_leverage(p["id"])
    print(f"3. leverage set to {PROBE_LEVERAGE}x, reads back {got}x")
    if int(round(got)) != PROBE_LEVERAGE:
        print("refusing: the leverage did not take")
        return 1
    t = client.get_ticker(symbol)
    bid = float(t["quotes"]["best_bid"])
    stop = to_tick(bid * 1.25, p["tick"], up=True)
    # Far legs on purpose (25% each way): the probe proves the order SHAPE, it must not trade on a leg.
    # A take-profit leg only if the rule has one (rule A does not); far away on purpose: the probe proves the shape.
    target = to_tick(bid * 0.75, p["tick"], up=False) if TP_FRACTION is not None else None
    cid = client_order_id("fade-live-probe", symbol, str(int(time.time())))
    o = client.place_order(OrderRequest(
        product_id=p["id"], size=1, side=Side.SELL, order_type=OrderType.LIMIT, client_order_id=cid,
        limit_price=to_tick(bid * (1 - SLIP), p["tick"], up=False), time_in_force=TimeInForce.IOC,
        bracket_stop_loss_price=stop, bracket_take_profit_price=target,
        stop_trigger_method=StopTriggerMethod.MARK, note="fade-live probe"))
    print(f"4. sold {filled(o)} at {fill_price(o)} (bid was {bid}), fee {fee(o)}, state {o.get('state')}")
    rc = 0
    try:
        if filled(o):
            pos = client.get_position(p["id"]) or {}
            print(f"5a. position at Delta: {pos}")
            bad = margin_problem(pos, fill_price(o) or bid, filled(o), p["cv"], PROBE_LEVERAGE)
            print(f"5b. isolated margin at {PROBE_LEVERAGE}x: {'YES' if not bad else 'NO -- ' + bad}")
            ok = acct.wait_protected(p["id"])
            legs = [{k: r.get(k) for k in ("stop_order_type", "size", "stop_price", "limit_price", "order_type")}
                    for r in client.get_pending_orders(p["id"])]
            print(f"5. stop covering the position at Delta: {'YES' if ok else 'NO -- the account would flatten and halt'}"
                  f"; resting legs: {legs}")
            tp = True
            if target is not None:
                tp = acct.target_resting(p["id"])
                print(f"5b. take-profit resting at Delta ({target}): {'YES' if tp else 'NO'}")
            rc = 0 if ok and tp and not bad else 1
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
    """Clear a HALT after the owner has looked (`python -m live fade-live-resume`). A STOP ends only with a
    refill (`fade-live-capital`)."""
    import asyncpg

    from app.persistence.db_auth import connect_kwargs
    con = await asyncpg.connect(**connect_kwargs(database_url))
    try:
        s = await con.fetchrow("SELECT status, reason FROM fade_live_state WHERE id = 1")
        if s and s["status"] == "STOPPED":
            if HALT_NOTE in (s["reason"] or ""):
                kept = s["reason"].split(HALT_NOTE)[0].strip()
                await con.execute("UPDATE fade_live_state SET reason = $1 WHERE id = 1", kept)
                await event(con, "RESUMED", None, {"was": s["reason"], "still": "STOPPED"})
                print(f"halt note cleared; still STOPPED ({kept}): refill, then fade-live-capital")
                return 0
            print("the run is STOPPED under the floor: refill the sub-account, then run fade-live-capital")
            return 1
        if s and s["status"] == "HALTED" and (s["reason"] or "").startswith("wallet moved"):
            print("halted on a wallet move no trade explains: run fade-live-capital to record it")
            return 1
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


async def record_capital(con, acct: LiveFade, now: float) -> tuple[int, str]:
    """The owner's record of a deposit or withdrawal (`python -m live fade-live-capital`), run once the
    account is flat. The change is the wallet now less the last accepted balance less what the bot's trades
    did since. STOPPED + a refill to >= REFILL_MIN_USD within REFILL_BUDGET_USD: the run starts again with
    the high-water mark at the new balance (the ladder back at 10%). Otherwise the mark moves by the same
    dollars. Deposits count against the refill budget; withdrawals do not give it back."""
    if await con.fetchval("SELECT count(*) FROM fade_live_positions WHERE status IN ('SENDING', 'OPEN')"):
        return 1, "positions are open: run this after their 24 h exit, when the account is flat"
    st = await con.fetchrow("SELECT status, reason, balance, balance_at, high_water, added_usd, run_version "
                            "FROM fade_live_state WHERE id = 1")
    if st is None or st["run_version"] != RUN_VERSION or st["balance"] is None:
        return 1, "no run of this version has started: nothing to record against"
    if HALT_NOTE in (st["reason"] or ""):
        return 1, (f"something HALTED while the run was stopped -- look first: {st['reason']}; then "
                   "fade-live-resume (it clears the note), then this again")
    held = [v.get("product_symbol") for v in await asyncio.to_thread(acct.client.get_positions) if acct_signed(v)]
    if held:
        return 1, f"Delta shows open positions {held}: close or explain them first"
    bal = await asyncio.to_thread(acct.balance)
    exp = await acct.explained(con, st["balance_at"]) if st["balance_at"] is not None else 0.0
    if exp is None or math.isnan(exp):
        return 1, "a close is still being read (or had no pnl) since the last balance: try again in 5 minutes"
    change = capital_gap(float(st["balance"]), bal, exp)
    added = float(st["added_usd"] or 0) + max(change, 0.0)
    hw = high_water_after(st["status"], bal, st["high_water"], change)
    if st["status"] == "STOPPED":
        if bal < REFILL_MIN_USD:
            return 1, f"wallet ${bal:.2f} is under the ${REFILL_MIN_USD:.0f} a refill must reach: top up first"
        if added > REFILL_BUDGET_USD + 0.01:
            await event(con, "REFILL_REFUSED", None, {"balance": bal, "change": change, "added_before": st["added_usd"]})
            return 1, (f"refills would reach ${added:,.2f}, over the ${REFILL_BUDGET_USD:,.0f} budget: the run is "
                       "over (docs/fade_live_ladder_prereg.md)")
        status, reason = "RUNNING", f"refilled ${change:+,.2f} by the owner; high-water mark set to ${hw:,.2f}"
    else:
        status = "RUNNING" if st["status"] == "HALTED" and (st["reason"] or "").startswith("wallet moved") \
            else st["status"]
        reason = f"capital change ${change:+,.2f} recorded by the owner" if status != st["status"] else st["reason"]
    await con.execute(
        "UPDATE fade_live_state SET status = $1, reason = $2, at = now(), balance = $3, balance_at = $4, "
        "high_water = $5, added_usd = $6 WHERE id = 1", status, reason, bal, _ts(now), hw, added)
    await event(con, "CAPITAL", None, {"change": round(change, 2), "balance": bal, "high_water": hw,
                                       "added_usd": round(added, 2), "was": st["status"], "now": status})
    return 0, (f"recorded ${change:+,.2f}: balance ${bal:,.2f}, high-water mark ${hw:,.2f}, refills so far "
               f"${added:,.2f} of ${REFILL_BUDGET_USD:,.0f}; state {status}")


async def capital(database_url: str, env: dict | None = None) -> int:
    """`python -m live fade-live-capital`: record a refill, deposit or withdrawal (see record_capital)."""
    import asyncpg

    from app.persistence.db_auth import connect_kwargs
    from live.config import client_from_env
    src = os.environ if env is None else env
    acct = LiveFade(client_from_env(src), risk_pct=_num_env(src, "DELTABOT_RISK_PER_TRADE", 0.0),
                    start_usd=_num_env(src, "DELTABOT_EQUITY", 1500.0))
    con = await asyncpg.connect(**connect_kwargs(database_url))
    try:
        rc, msg = await record_capital(con, acct, time.time())
        print(msg)
        return rc
    finally:
        await con.close()
