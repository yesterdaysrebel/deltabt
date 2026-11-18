"""REPLAY: the REAL bot code (live/spike_live.SpikeLive, on live/fade_live.LiveFade and PostgreSQL) trading minute by
minute against a simulated Delta that is driven by Delta's own historical 1m candles (the careful dataset), and its
trades compared one by one with the research simulation of the same rules (owner 2026-11-15: "run the simulation as
well so that all the complicated pieces are tested before going live ... run multiple simulation runs").

What is real: every line of the bot -- the state machine, the reconcile, the capital snapshots, the entry (sizing,
leverage, resting post-only sell with its stop), the watcher (`fast`: fill -> OPEN, the other order cancelled, the
extra margin), the break-even move, the 5% trail, the buy-backs, the 24 h exit, the month-off rule, the floor.
What is simulated: the exchange (`ReplayDelta`) and the clock.

The exchange, per minute m of a coin (its 1m high / low / close):
  * a resting sell fills at its limit when the high trades 0.05% through it (the better-ranked order if two do);
    commission 0.02% + the 0.05% slippage the research charges on the entry;
  * the position's stop leg (at whatever price the bot last set) fires when the high reaches it: filled at the worse
    of the stop and that minute's close +0.1% (STRICT) or at the stop +0.1% (LENIENT); at or past the liquidation
    price (entry x (1 + IM - MM + extra margin / notional)) the whole margin is lost instead; commission 0.05%;
  * a market buy-back fills at the close +0.1%, commission 0.05%.
The clock: the day's entry pass at 00:03 (the 00:02 close is the last price); a watcher pass 30 s into a minute a fill
happens in; then one full pass at the end of EVERY minute while a position is open (and a heartbeat pass while the
orders rest). The bot never sees a price the live bot could not have seen.

The reference: the research rule (scripts/careful_be_trail.t1) with the bot's own parameters (10% margin, 5.1% extra,
+$30.61 x scale activation), as an account (first fill wins, month off after 2 full stops, $1,000 floor).

    DELTABOT_TEST_DSN=postgresql://... PYTHONPATH=. python scripts/replay_bot.py [--lenient] [--from 2026-01] [--to 2026-03]
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import math
import os
import random
import sys
import time
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, os.environ.get("REPLAY_BOT_ROOT") or str(Path(__file__).parent.parent))   # which checkout's bot code
import careful_data as D  # noqa: E402
import careful_sim_b as B  # noqa: E402

from live import fade, fade_live as L, spike_live as S  # noqa: E402
from live.client import AmbiguousWrite, VenueError, VenueRejected  # noqa: E402

DAY = 86400
DEFAULT = dict(im=5.0, mm=2.5, ims=1e-05, mms=5e-06, max_lev_notional=50000.0, limit=10 ** 12, cv=1.0)


def iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ====================================================================================== the simulated exchange
class ReplayDelta:
    """Delta's shapes, a wallet that moves by commissions and realised P&L, isolated-margin positions."""

    def __init__(self, balance: float, lenient: bool, fine: bool = True):
        self.bal, self.lenient, self.fine = balance, lenient, fine
        self.products: dict[str, dict] = {}
        self.spec: dict[int, dict] = {}
        self.px: dict[str, tuple[float, float]] = {}
        self.pos: dict[int, dict] = {}
        self.orders: dict[str, dict] = {}
        self.pending: list[dict] = []
        self.history: list[dict] = []
        self.lev: dict[int, int] = {}
        self.now = 0.0
        self._id = 1000
        self.log: list[tuple] = []                       # (time, kind, symbol, detail) of everything the exchange did
        self.fault = None                                # callable(method) -> None | raises
        self.miss = None                                 # callable() -> True when the by-client-id lookup misses

    # -- set-up
    def ensure_product(self, sym: str, price: float) -> None:
        if sym in self.products:
            return
        p = D.PRODUCTS.get(sym) or DEFAULT
        mag = math.floor(math.log10(price))
        if self.fine:
            tick = Decimal(10) ** (mag - 8)               # 9 significant digits: rounding is negligible
            cv = 10.0 ** (-3 - mag)                       # one contract ~ $0.001-0.01
        else:
            tick = Decimal(10) ** (mag - 4)               # ~1 bp ticks, Delta's real contract value
            cv = float(p["cv"])
        limit = int(float(p["limit"]) * float(p["cv"]) / cv) if p["limit"] < 10 ** 11 else 0
        pid = len(self.products) + 1
        self.products[sym] = dict(id=pid, symbol=sym, tick_size=str(tick), contract_value=repr(cv), maintenance_margin=str(p["mm"]),
                                  initial_margin=str(p["im"]), max_leverage_notional=str(p["max_lev_notional"]),
                                  initial_margin_scaling_factor=str(p["ims"]), position_size_limit=str(limit) if limit else "")
        self.spec[pid] = dict(sym=sym, cv=cv, im=p["im"] / 100, mm=p["mm"] / 100, ims=p["ims"] / 100, mms=p["mms"] / 100,
                              cap=p["max_lev_notional"])

    def set_price(self, sym: str, last: float) -> None:
        self.px[sym] = (last, last * 1.001)

    def _hit(self, name: str) -> None:
        if self.fault is not None:
            self.fault(name)

    # -- reads
    def get_product(self, s):
        self._hit("get_product")
        return self.products[s]

    def get_ticker(self, s):
        self._hit("get_ticker")
        last, ask = self.px[s]
        return {"close": repr(last), "mark_price": repr(last), "quotes": {"best_bid": repr(last), "best_ask": repr(ask)}}

    def get_balance(self):
        self._hit("get_balance")
        return [{"asset_symbol": "USD", "balance": repr(round(self.bal, 6))}]

    def get_position(self, pid):
        self._hit("get_position")
        p = self.pos.get(pid)
        return {"product_id": pid, "size": -p["qty"], "entry_price": repr(p["entry"])} if p else None

    def _liq(self, pid) -> float:
        p, s = self.pos[pid], self.spec[pid]
        n = p["qty"] * s["cv"] * p["entry"]
        over = max(0.0, n - s["cap"])
        im = max(s["im"] + s["ims"] * over, 1.0 / p["lev"])          # the position's own leverage (rule C: 5x) sets its margin
        return p["entry"] * (1 + im - (s["mm"] + s["mms"] * over) + p["extra"] / n)

    def _margined(self, pid):
        p, s = self.pos[pid], self.spec[pid]
        n = p["qty"] * s["cv"] * p["entry"]
        return {"product_id": pid, "product_symbol": s["sym"], "size": -p["qty"], "entry_price": repr(p["entry"]),
                "margin": repr(n / p["lev"] + p["extra"]), "liquidation_price": repr(self._liq(pid)), "auto_topup": False,
                "margin_mode": "isolated"}

    def get_positions(self):
        self._hit("get_positions")
        return [self._margined(pid) for pid in self.pos]

    def get_pending_orders(self, pid=None):
        self._hit("get_pending_orders")
        return [dict(o) for o in self.pending if pid in (None, o["product_id"])]

    def get_open_orders(self, pid=None):
        """Orders resting on the book (states=open): the entry sells -- never the stop legs."""
        self._hit("get_open_orders")
        return [dict(o) for o in self.orders.values() if o["state"] == "open" and pid in (None, o["product_id"])]

    def order_history(self, pid=None, page_size=20):
        self._hit("order_history")
        return [o for o in self.history if pid in (None, o["product_id"])][:page_size]

    def set_order_leverage(self, pid, lev):
        self._hit("set_order_leverage")
        self.lev[pid] = int(lev)

    def get_order_leverage(self, pid):
        return float(self.lev.get(pid, 20))

    def get_order_by_client_id(self, cid):
        self._hit("get_order_by_client_id")
        if self.miss is not None and self.miss():
            return None                                    # the by-client-id lookup misses an order that exists
        o = self.orders.get(cid)
        return dict(o) if o else None

    # -- writes
    def cancel_order(self, oid, pid):
        self._hit("cancel_order")
        if any(o["id"] == oid for o in self.pending):
            self.pending = [o for o in self.pending if o["id"] != oid]
            return {"id": oid}
        for o in self.orders.values():
            if o["id"] == oid and o["state"] == "open":
                o["state"] = "cancelled"
                self.log.append((self.now, "CANCELLED", self.spec[pid]["sym"], oid))
                return dict(o)
        # as Delta: an order that already filled, was already cancelled, or does not exist cannot be cancelled
        raise VenueRejected(404, "/v2/orders", {"error": {"code": "open_order_not_found"}})

    def place_order(self, order):
        self._hit("place_order")
        p = order.to_payload()
        pid = p["product_id"]
        sym = self.spec[pid]["sym"]
        self._id += 1
        if p["side"] == "sell" and not p.get("reduce_only"):
            assert p.get("time_in_force") == "gtc" and p.get("post_only") is True, p
            assert "bracket_take_profit_price" not in p and p.get("bracket_stop_trigger_method") == "last_traded_price", p
            row = dict(id=self._id, product_id=pid, side="sell", size=p["size"], unfilled_size=p["size"], average_fill_price=None,
                       paid_commission="0", state="open", client_order_id=p["client_order_id"], limit_price=p["limit_price"],
                       stop=p["bracket_stop_loss_price"], created_at=iso(self.now))
            self.orders[p["client_order_id"]] = row
            self.log.append((self.now, "RESTING", sym, (p["size"], p["limit_price"], p["bracket_stop_loss_price"], self.lev.get(pid))))
            return dict(row)
        assert p.get("reduce_only"), p                     # the bot's only other orders: reduce-only buys
        if p["order_type"] == "limit_order" and p.get("time_in_force") == "gtc":   # a take-profit step (owner 2026-11-18)
            assert p.get("post_only") is True, p
            self._hit("place_tp")
            row = dict(id=self._id, product_id=pid, side="buy", size=p["size"], unfilled_size=p["size"], average_fill_price=None,
                       paid_commission="0", state="open", reduce_only=True, client_order_id=p["client_order_id"],
                       limit_price=p["limit_price"], created_at=iso(self.now))
            if float(p["limit_price"]) >= self.px[sym][1]:
                row["state"] = "cancelled"                 # post-only that would cross: Delta cancels it
            self.orders[p["client_order_id"]] = row
            self.log.append((self.now, "TP_RESTING" if row["state"] == "open" else "TP_CROSSED", sym, (p["size"], p["limit_price"])))
            self._hit("place_tp:after")                    # rests, but the reply never arrives
            return dict(row)
        pos = self.pos.get(pid)
        n = min(int(p["size"]), pos["qty"]) if pos else 0
        row = dict(id=self._id, product_id=pid, side="buy", size=p["size"], unfilled_size=int(p["size"]) - n, state="closed",
                   average_fill_price=None, paid_commission="0", reduce_only=True, client_order_id=p["client_order_id"],
                   created_at=iso(self.now), updated_at=iso(self.now))
        if n:
            ask = self.px[sym][1]
            com = n * self.spec[pid]["cv"] * ask * 0.0005
            self.bal += (pos["entry"] - ask) * n * self.spec[pid]["cv"] - com
            row.update(average_fill_price=repr(ask), paid_commission=repr(com))
            pos["qty"] -= n
            if pos["qty"] == 0:
                del self.pos[pid]
            self.history.insert(0, row)
            self.log.append((self.now, "BOUGHT_BACK", sym, ask))
        self.orders[p["client_order_id"]] = row
        if n:
            self._hit("buy_back:after")                    # bought, but the reply never arrives
        return dict(row)

    def edit_stop_price(self, oid, pid, stop_price):
        self._hit("edit_stop_price")
        for o in self.pending:
            if o["id"] == oid:
                assert float(stop_price) < float(o["stop_price"]) + 1e-12, ("a stop moved UP", o["stop_price"], stop_price)
                o["stop_price"] = str(stop_price)
                self.log.append((self.now, "STOP_MOVED", self.spec[pid]["sym"], str(stop_price)))
                self._hit("edit_stop_price:after")         # moved, but the reply never arrives
                return {"id": oid}
        raise VenueRejected(404, "/v2/orders", {"error": {"code": "open_order_not_found"}})

    def add_position_margin(self, pid, delta):
        self._hit("add_position_margin")
        self.pos[pid]["extra"] += float(delta)
        self.log.append((self.now, "MARGIN_ADDED", self.spec[pid]["sym"], float(delta)))
        self._hit("add_position_margin:after")             # added, but the reply never arrives
        return self._margined(pid)

    # -- the market moves (called by the driver)
    def try_fill(self, cids_by_rank: list[str], high: float) -> str | None:
        """The better-ranked resting sell whose limit the minute's high traded 0.05% through fills at its limit."""
        for cid in cids_by_rank:
            o = self.orders.get(cid)
            if o and o["state"] == "open" and high >= float(o["limit_price"]) * 1.0005:
                pid, n, e = o["product_id"], int(o["size"]), float(o["limit_price"])
                com = n * self.spec[pid]["cv"] * e * 0.0007
                self.bal -= com
                o.update(unfilled_size=0, average_fill_price=repr(e), paid_commission=repr(com), state="closed", updated_at=iso(self.now))
                self.pos[pid] = dict(qty=n, entry=e, lev=self.lev.get(pid, 20), extra=0.0, com_in=com)
                self._id += 1
                self.pending.append(dict(id=self._id, product_id=pid, reduce_only=True, stop_order_type="stop_loss_order",
                                         stop_price=o["stop"], order_type="market_order", stop_trigger_method="last_traded_price"))
                self.log.append((self.now, "FILLED", self.spec[pid]["sym"], e))
                return cid
        return None

    def try_tp(self, sym: str, low: float) -> int:
        """Resting take-profit buys of `sym` whose limit the minute's low traded through (STRICT by 0.1%) fill at their
        limit, maker commission 0.02%. Returns how many filled."""
        pid = self.products[sym]["id"]
        got = 0
        for o in self.orders.values():
            pos = self.pos.get(pid)
            if not pos or o["product_id"] != pid or o["side"] != "buy" or o["state"] != "open" or "limit_price" not in o:
                continue
            lp = float(o["limit_price"])
            if low > lp * (1.0 if self.lenient else 0.999):
                continue
            n = min(int(o["unfilled_size"]), pos["qty"])
            cv = self.spec[pid]["cv"]
            com = n * cv * lp * 0.0002
            self.bal += (pos["entry"] - lp) * n * cv - com
            pos["qty"] -= n
            o.update(unfilled_size=int(o["unfilled_size"]) - n, average_fill_price=repr(lp), paid_commission=repr(com),
                     state="closed", updated_at=iso(self.now))
            self.history.insert(0, dict(o))
            self.log.append((self.now, "TP_FILLED", sym, (n, lp)))
            got += 1
            if pos["qty"] == 0:
                del self.pos[pid]
                self.pending = [x for x in self.pending if x["product_id"] != pid]
        return got

    def try_stop(self, sym: str, high: float, close: float) -> str | None:
        pid = self.products[sym]["id"]
        pos = self.pos.get(pid)
        leg = next((o for o in self.pending if o["product_id"] == pid and o["stop_order_type"] == "stop_loss_order"), None)
        if not pos or leg is None or high < float(leg["stop_price"]):
            return None
        sp, cv = float(leg["stop_price"]), self.spec[pid]["cv"]
        x = sp * 1.001 if self.lenient else max(sp, close) * 1.001
        qty = pos["qty"] * cv
        n_usd = qty * pos["entry"]
        kind = "stop_loss_order"
        if x >= self._liq(pid):                           # past liquidation: the whole isolated margin goes
            kind = "liquidation_order"
            posted = S.TRADE_MARGIN * pos["bal_before"] if pos.get("bal_before") else n_usd * (1 / pos["lev"] + S.FEE_RESERVE)
            x = pos["entry"] + (posted + pos["extra"] - pos["com_in"]) / qty    # the trade loses margin + extra in all
            com = 0.0
        else:
            com = qty * x * 0.0005
        self.bal += (pos["entry"] - x) * qty - com
        del self.pos[pid]
        self.pending = [o for o in self.pending if o["product_id"] != pid]
        self._id += 1
        self.history.insert(0, dict(id=self._id, product_id=pid, side="buy", size=pos["qty"], unfilled_size=0, average_fill_price=repr(x),
                                    paid_commission=repr(com), reduce_only=True, stop_order_type=kind, state="closed",
                                    created_at=iso(self.now), updated_at=iso(self.now)))
        self.log.append((self.now, "LIQUIDATED" if kind == "liquidation_order" else "STOPPED", sym, (sp, x)))
        return kind


# ====================================================================================== the reference (research rule)
def ref_sizing(sym, margin, price, extra):
    """careful_sim_b.sizing with the bot's leverage cap: initial margin never under 1 / S.MAX_LEVERAGE."""
    p = D.PRODUCTS.get(sym) or B.DEFAULT
    im, mm, ims, mms, cap = max(p["im"] / 100, 1.0 / S.MAX_LEVERAGE), p["mm"] / 100, p["ims"] / 100, p["mms"] / 100, p["max_lev_notional"]
    lo_, hi_ = 0.0, margin / (im + 0.0012)
    for _ in range(100):
        mid = (lo_ + hi_) / 2
        if mid * (max(im, p["im"] / 100 + ims * max(0.0, mid - cap)) + 0.0012) <= margin:
            lo_ = mid
        else:
            hi_ = mid
    n = min(lo_, p["limit"] * p["cv"] * price)
    over = max(0.0, n - cap)
    return n, max(im, p["im"] / 100 + ims * over) - (mm + mms * over) + extra / n


def ref_trade(sym, H, L, Cl, bal, le, extra_frac=None):
    """scripts/careful_be_trail.t1 with the bot's parameters. extra_frac=None -> the bot's 5.1%."""
    e = Cl[2] * 1.05
    k = next((j for j in range(3, 1440) if H[j] >= e * 1.0005), None)
    if k is None:
        return None
    margin = S.TRADE_MARGIN * bal
    extra = (S.EXTRA_MARGIN if extra_frac is None else extra_frac) * bal
    n, ld = ref_sizing(sym, margin, e, extra)
    cap, Q = margin + extra, e * (1 + ld)
    raw = lambda x: max(n * (-(x / e - 1) - 0.0007 - 0.0005 * x / e), -cap)  # noqa: E731
    first = e * (1 + S.STOP_ABOVE)
    stop, low, active = first, e, False
    steps = [[lv, fr, False] for lv, fr in S.LADDER]               # the take-profit ladder (owner 2026-11-18)
    booked, rem = 0.0, 1.0
    raw_all = raw
    raw = lambda x: booked + max(n * rem * (-(x / e - 1) - 0.0005 * x / e), -cap) - n * 0.0007 if S.LADDER else raw_all(x)  # noqa: E731
    act_px = e * (1 - (S.BREAKEVEN_AFTER_USD * bal / S.RUN_START_USD / n + S.COST))
    be = e * (1 - S.COST)
    last = min(2880, k + 1440)
    for j in range(k + 1, last + 1):
        hit = [st for st in steps if not st[2] and L[j] <= e * (1 - st[0]) * (1.0 if le else 0.999)]
        if le:                                                       # LENIENT: the steps fill before a stop in the minute
            for st in hit:
                st[2] = True
                booked += n * st[1] * (st[0] - 0.0002 * (1 - st[0]))
                rem -= st[1]
        if H[j] >= stop:
            x = stop * 1.001 if le else max(stop, Cl[j]) * 1.001
            if x >= Q:
                return "LIQ", -cap, k, j
            return ("STOP" if stop == first else "TRAIL"), raw(x), k, j
        if not le:
            for st in hit:
                st[2] = True
                booked += n * st[1] * (st[0] - 0.0002 * (1 - st[0]))
                rem -= st[1]
        low = min(low, L[j])
        if not S.BREAKEVEN_LOCK:                                    # owner 2026-11-16: a plain trail from the fill
            if not S.TRAIL_ONLY_PAST_ENTRY or low * (1 + S.TRAIL) <= e:   # rule C (2026-11-17): only once past the entry
                stop = min(stop, low * (1 + S.TRAIL))
        else:
            if not active and L[j] <= act_px:
                active = True
            if active:
                stop = min(stop, be, low * (1 + S.TRAIL))
        if Cl[j] >= stop:
            return ("STOP" if stop == first else "TRAIL"), raw(Cl[j] * 1.001), k, j
    return "24h", raw(Cl[last] * 1.001), k, last


def reference(days, le, start=S.RUN_START_USD, floor=L.FLOOR_USD, month_by_exit=False):
    """The research account. month_by_exit=True counts a full stop in the calendar month of its EXIT (as the bot
    does) instead of the pick day's month (as the research account did)."""
    bal, busy, out = start, -1, []
    stops: dict[str, int] = {}
    for day, picks in days:
        t0 = int(day.timestamp()) // 60
        m = day.strftime("%Y-%m")
        if bal < floor:
            break                                         # the bot is STOPPED from here on
        if (S.MONTH_STOP_LIMIT is not None and stops.get(m, 0) >= S.MONTH_STOP_LIMIT) or busy > t0 + 2:
            continue
        best = None
        for rank, sym, H, L_, Cl in picks:
            r = ref_trade(sym, H, L_, Cl, bal, le)
            if r and (best is None or r[2] < best[1][2]):
                best = (sym, r)
        if best is None:
            continue
        sym, r = best
        bal = max(bal + r[1], 0.0)
        busy = t0 + r[3]
        if r[0] in ("STOP", "LIQ"):
            mm = (day + pd.Timedelta(minutes=int(r[3]) + 1)).strftime("%Y-%m") if month_by_exit else m
            stops[mm] = stops.get(mm, 0) + 1
        out.append((day.date(), sym, r[2], r[3], r[0], r[1], bal))
    return out


# ====================================================================================== the driver
class Replay:
    def __init__(self, con, days, lenient, fine=True, fault=None, restart_every=None, heartbeat=120, seed=0, double=False, miss=0.0):
        self.con, self.days, self.lenient, self.double = con, days, lenient, double
        self.ex = ReplayDelta(S.RUN_START_USD, lenient, fine)
        if miss:
            self.ex.miss = lambda: self.rng.random() < miss
        self.fault_spec, self.restart_every, self.heartbeat = fault, restart_every, heartbeat
        self.rng = random.Random(seed)
        self.ctx = None                                   # the open position's (t0, sym, H, L, Cl)
        self.bars_for: dict[str, tuple] = {}              # symbol -> (t0, L, Cl, last complete minute)
        self.steps = self.faults_fired = self.restarts = self.unprotected = self.two_positions = self.double_fills = 0
        self.bot = self._new_bot()
        if fault:
            self.ex.fault = self._maybe_fail

    def _new_bot(self):
        return S.SpikeLive(self.ex, risk_pct=0.10, start_usd=S.RUN_START_USD, kill_switch_path="/nonexistent/kill",
                           protect_wait=0.0, sleep=lambda s: None, candles=self._candles)

    def _maybe_fail(self, name):
        kinds = self.fault_spec.get(name)
        if kinds and self.rng.random() < kinds[0]:
            self.faults_fired += 1
            if kinds[1] == "ambiguous":
                raise AmbiguousWrite(f"{name}: no reply (injected)")
            if kinds[1] == "rejected":
                raise VenueRejected(400, name, {"error": {"code": "injected"}})
            raise VenueError(f"{name}: venue down (injected)") if kinds[1] == "venue" else ConnectionError(f"{name}: timeout (injected)")

    def _candles(self, symbol, start, end):
        if symbol not in self.bars_for:
            return []
        t0, L_, Cl, upto = self.bars_for[symbol]
        a = max(0, (int(start) - t0) // 60)
        return [{"time": t0 + m * 60, "low": float(L_[m]), "close": float(Cl[m])} for m in range(a, upto + 1) if np.isfinite(L_[m])]

    async def step(self, now, fast=False):
        self.ex.now = now
        self.steps += 1
        if self.restart_every and self.steps % self.restart_every == 0:
            self.bot = self._new_bot()                    # the process restarted: nothing but the database survives
            self.restarts += 1
        if fast:
            await self.bot.fast(self.con, now)
        else:
            await self.bot.step(self.con, now)
        for pid in self.ex.pos:                           # SAFETY: a position at the exchange always has its stop
            if not any(o["product_id"] == pid and o["stop_order_type"] == "stop_loss_order" for o in self.ex.pending):
                self.unprotected += 1
        if len(self.ex.pos) > 1:
            self.two_positions += 1

    async def status(self):
        return await self.con.fetchval("SELECT status FROM fade_live_state WHERE id = 1")

    async def open_row(self):
        return await self.con.fetchrow("SELECT * FROM fade_live_positions WHERE status = 'OPEN'")

    async def minute(self, m: int) -> bool:
        """One minute of the open position: the exchange first, then the bot's pass at the minute's end.
        Returns True while the position is still open in the bot's table."""
        t0, sym, H, L_, Cl = self.ctx
        self.ex.set_price(sym, float(Cl[m]))
        self.ex.now = t0 + m * 60 + 30
        if self.lenient:                                  # LENIENT: the steps first; STRICT: the stop wins the minute
            self.ex.try_tp(sym, float(L_[m]))
            self.ex.try_stop(sym, float(H[m]), float(Cl[m]))
        else:
            if self.ex.try_stop(sym, float(H[m]), float(Cl[m])) is None:
                self.ex.try_tp(sym, float(L_[m]))
        self.bars_for[sym] = (t0, L_, Cl, m)
        await self.step(t0 + (m + 1) * 60)
        return await self.open_row() is not None

    async def run(self):
        ex = self.ex
        nxt = None                                        # next minute index of the open position
        for day, picks in self.days:
            t0 = int(day.timestamp())
            # 1. a position carried from an earlier day: its minutes up to today's 00:02
            while self.ctx is not None and self.ctx[0] + (nxt + 1) * 60 <= t0 + 180:
                if not await self.minute(nxt):
                    self.ctx = None
                nxt = (nxt or 0) + 1
            if await self.status() in ("HALTED",):
                break
            # 2. today's picks and the entry pass at 00:03
            ts = dt.datetime.fromtimestamp(t0, dt.timezone.utc)
            await self.con.execute("INSERT INTO fade_days (day, status) VALUES ($1, 'ENTERED') ON CONFLICT DO NOTHING", ts)
            for rank, sym, H, L_, Cl in picks:
                ex.ensure_product(sym, float(Cl[2]))
                if self.ctx is None or self.ctx[1] != sym:
                    ex.set_price(sym, float(Cl[2]))
                await self.con.execute(
                    "INSERT INTO fade_positions (account, day, symbol, rank, prev_change, prev_turnover, atrp, entry_time, "
                    "entry_price, stop_price, contracts, contract_value, equity_before, status) "
                    "VALUES ('A', $1, $2, $3, 0.3, 1e6, 0.10, $1, $4, $5, 5, 1, 250, 'OPEN')", ts, sym, rank, float(Cl[2]), float(Cl[2]) * 1.2)
            await self.step(t0 + 180)
            rest = await self.con.fetch("SELECT symbol, rank, entry_cid FROM fade_live_positions WHERE status = 'RESTING' AND day = $1 "
                                        "ORDER BY rank", ts)   # today's only (a failed cancel can leave yesterday's a pass longer)
            by_sym = {s: (H, L_, Cl) for _, s, H, L_, Cl in picks}
            m = 3
            while m < 1440:
                if self.ctx is not None and self.ctx[0] < t0:          # yesterday's position still running
                    if not await self.minute(nxt):
                        self.ctx = None
                    nxt += 1
                    m += 1
                    continue
                if self.ctx is not None:                               # today's position
                    if not await self.minute(nxt):
                        self.ctx = None
                    nxt += 1
                    m = nxt
                    continue
                if not rest:
                    break
                # resting orders only: the next minute a limit is traded through (else none today)
                hit = None
                for mm in range(m, 1440):
                    for r in rest:
                        o = ex.orders.get(r["entry_cid"])
                        if o and o["state"] == "open" and by_sym[r["symbol"]][0][mm] >= float(o["limit_price"]) * 1.0005:
                            hit = mm
                            break
                    if hit is not None:
                        break
                stop_at = hit if hit is not None else 1440
                hb = m + self.heartbeat
                while hb < stop_at:                                    # heartbeat passes while the orders rest
                    for r in rest:
                        ex.set_price(r["symbol"], float(by_sym[r["symbol"]][2][hb - 1]))
                    await self.step(t0 + hb * 60)
                    hb += self.heartbeat
                if hit is None:
                    break
                for r in rest:
                    ex.set_price(r["symbol"], float(by_sym[r["symbol"]][2][hit]))
                ex.now = t0 + hit * 60 + 20
                best_first = [r["entry_cid"] for r in rest]
                highs = {r["entry_cid"]: float(by_sym[r["symbol"]][0][hit]) for r in rest}
                got = []
                for c in best_first:                                   # the better-ranked first; with `double`, BOTH can fill
                    if (not got or self.double) and ex.try_fill([c], highs[c]):
                        got.append(c)
                if not got:                                            # the bot had cancelled it meanwhile (a halt): no fill
                    rest = [r for r in rest if (ex.orders.get(r["entry_cid"]) or {}).get("state") == "open"]
                    m = hit + 1
                    continue
                cid = got[0]
                self.double_fills += len(got) - 1
                sym = next(r["symbol"] for r in rest if r["entry_cid"] == cid)
                pos = ex.pos[ex.products[sym]["id"]]
                pos["bal_before"] = float(await self.con.fetchval("SELECT balance_before FROM fade_live_positions WHERE entry_cid = $1", cid))
                H, L_, Cl = by_sym[sym]
                self.ctx, nxt = (t0, sym, H, L_, Cl), hit + 1
                self.bars_for[sym] = (t0, L_, Cl, hit)
                await self.step(t0 + hit * 60 + 30, fast=True)         # the watcher sees the fill
                await self.step(t0 + (hit + 1) * 60)                   # and the pass at the end of the fill minute
                if await self.open_row() is None:
                    self.ctx = None
                rest = []
                m = hit + 1
            # 3. 24:00 UTC: whatever still rests is cancelled by the bot
            if await self.con.fetchval("SELECT count(*) FROM fade_live_positions WHERE status = 'RESTING'"):
                await self.step(t0 + DAY + 1)
            if await self.status() == "HALTED":
                break
        while self.ctx is not None and nxt <= 2880:                   # the last position runs out
            if not await self.minute(nxt):
                self.ctx = None
            nxt += 1
        return await self.ledger()

    async def ledger(self):
        rows = await self.con.fetch("SELECT * FROM fade_live_positions WHERE entry_time IS NOT NULL ORDER BY entry_time")
        out = []
        for r in rows:
            d0 = r["day"].timestamp()
            how = {"TIME": "24h", "LOCK": "TRAIL", "LIQUIDATION": "LIQ"}.get(r["exit_reason"], r["exit_reason"])
            if r["exit_reason"] == "STOP_LOSS":
                how = "STOP" if r["lock_price"] is None and r["trail_price"] is None else "TRAIL"
            k = int((r["entry_time"].timestamp() - d0) // 60)
            j = int((r["exit_time"].timestamp() - d0) // 60) - 1 if r["exit_time"] else None
            out.append((r["day"].date(), r["symbol"], k, j, how, r["pnl"], r["status"]))
        return out


async def fresh_db(dsn: str):
    import asyncpg
    con = await asyncpg.connect(dsn)
    await con.execute("DROP TABLE IF EXISTS fade_live_positions, fade_live_state, fade_live_events, "
                      "fade_positions, fade_days, fade_heartbeat, fade_paper_capital")
    await con.execute(fade.SCHEMA)
    await L.ensure_schema(con)
    return con


def compare(bot, ref, tol_rel=0.01, tol_abs=1.0):
    """-> (same, differences). A trade matches when day, coin, fill minute, exit minute and exit kind are equal and the
    P&L is within tol."""
    diffs, same = [], 0
    for i in range(max(len(bot), len(ref))):
        b = bot[i] if i < len(bot) else None
        r = ref[i] if i < len(ref) else None
        if b is None or r is None:
            diffs.append((i, b, r))
            continue
        ok = b[0] == r[0] and b[1] == r[1] and b[2] == r[2] and b[3] == r[3] and b[4] == r[4] and b[5] is not None \
            and abs(b[5] - r[5]) <= max(tol_abs, tol_rel * abs(r[5]))
        same += ok
        if not ok:
            diffs.append((i, b, r))
    return same, diffs


async def one(dsn, days, lenient, **kw):
    con = await fresh_db(dsn)
    try:
        rp = Replay(con, days, lenient, **kw)
        t = time.time()
        bot = await rp.run()
        st = await con.fetchrow("SELECT status, reason FROM fade_live_state WHERE id = 1")
        ev = await con.fetch("SELECT kind, count(*) AS n FROM fade_live_events GROUP BY kind ORDER BY kind")
        fills = sum(1 for x in rp.ex.log if x[1] == "FILLED")
        adds = sum(1 for x in rp.ex.log if x[1] == "MARGIN_ADDED")
        resting_left = sum(1 for o in rp.ex.orders.values() if o["side"] == "sell" and o["state"] == "open")
        return dict(bot=bot, bal=rp.ex.bal, status=st["status"], reason=st["reason"], events={e["kind"]: e["n"] for e in ev},
                    steps=rp.steps, secs=time.time() - t, faults=rp.faults_fired, restarts=rp.restarts,
                    open_left=len(rp.ex.pos), resting_left=resting_left, unprotected=rp.unprotected, two_positions=rp.two_positions,
                    fills=fills, margin_adds=adds, stop_moves=sum(1 for x in rp.ex.log if x[1] == "STOP_MOVED"),
                    double_fills=rp.double_fills)
    finally:
        await con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lenient", action="store_true")
    ap.add_argument("--from", dest="lo", default="2025-01")
    ap.add_argument("--to", dest="hi", default="2026-12")
    ap.add_argument("--real-ticks", action="store_true")
    a = ap.parse_args()
    dsn = os.environ["DELTABOT_TEST_DSN"]
    days = [d for d in D.load() if a.lo <= d[0].strftime("%Y-%m") <= a.hi]
    res = asyncio.run(one(dsn, days, a.lenient, fine=not a.real_ticks))
    ref = reference(days, a.lenient, month_by_exit=True)
    same, diffs = compare(res["bot"], ref)
    print(f"{a.lo}..{a.hi} {'LENIENT' if a.lenient else 'STRICT'}: bot trades {len(res['bot'])}, reference {len(ref)}, identical {same}, "
          f"different {len(diffs)}; bot wallet ${res['bal']:,.2f}, reference ${ref[-1][6] if ref else S.RUN_START_USD:,.2f}; "
          f"state {res['status']}; {res['steps']} bot passes in {res['secs']:.0f}s")
    print("events:", res["events"])
    for i, b, r in diffs[:12]:
        print("  DIFF", i, "\n     bot", b, "\n     ref", r)


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    main()
