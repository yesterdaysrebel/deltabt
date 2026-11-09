"""Run the REAL-MONEY account's code (live/fade_live.py, run 3) through Delta 2025 and 2026, day by day, against a
simulated Delta, and check it against an independent re-implementation of the rules.

    DELTABOT_TEST_DSN=postgresql://... PYTHONPATH=. .venv/bin/python scripts/fade_live_sim.py [--faults]

What is real: LiveFade itself -- its state machine, sizing, ladder, cap, floor, refills (via record_capital, as the
owner would run it), capital check, reconcile, exits -- against PostgreSQL. What is simulated: Delta. Each trade is
a row of out/gainer_fade/fade_costed_rows.parquet (the bot's real first-minute entry, the 24 h close or the stop,
Delta's funding); the simulated venue fills the entry at the bid, charges 0.05% of the entry notional each side,
closes a stopped trade at 06:00 UTC and a held one at the 24 h buy-back, and pays funding into the wallet hour by
hour while the position is open. Prices are scaled so a trade's wallet result is exactly risk x R_A (R_A = the
research R with funding), with contracts of 0.0001 coin so whole-contract rounding is negligible.

The reference (`reference`) is written from the rules text, not from LiveFade: day-sized off the morning balance,
risk = ladder step (10/5/3 at 20%/35% under the high-water mark) capped at 30% x stop distance, under $1,000 at the
day's check -> no trades that day, refilled to $1,500 (mark reset) unless refills would pass $3,000.

--faults: a new LiveFade every day (a deploy/restart), and 3% of venue READS raise (orders are never failed: an
ambiguous write is a different, already-tested path). The result must equal the clean run.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import math
import os
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from live import fade as F            # noqa: E402
from live import fade_live as L       # noqa: E402

DAY, HOUR = 86400, 3600
FEE = 0.0005
CV = 0.0001
STOP_HOUR = 6
START = 1500.0


# ============================================================ the reference (rules text)

#: The rules as NUMBERS from docs/fade_live_ladder_prereg.md, written here, never imported from the bot: a wrong
#: constant in live/ must show up as a gap (the independent review of 2026-11-09 found the first version shared them).
RULE_LADDER = ((0.00, 0.10), (0.20, 0.05), (0.35, 0.03))
RULE_CAP = 0.30
RULE_FLOOR = 1000.0
RULE_REFILL_TO = 1500.0
RULE_BUDGET = 3000.0


def reference(days: list[pd.DataFrame], skip_refill_day: bool = True) -> dict:
    eq = hw = START
    added, refills, trades, path, risks, hws = 0.0, 0, 0, [], [], []
    done = False
    for day in days:
        if done:
            path.append(eq)
            continue
        if eq < RULE_FLOOR:
            amt = RULE_REFILL_TO - eq
            if added + amt > RULE_BUDGET + 1e-9:
                done = True
                path.append(eq)
                continue
            added += amt
            refills += 1
            eq = hw = RULE_REFILL_TO
            if skip_refill_day:                       # the bot STOPs at this check; the owner refills later that day
                path.append(eq)
                continue
        under = (hw - eq) / hw
        k = [r for th, r in RULE_LADDER if under >= th - 1e-12][-1]
        dist, r = day.dist.to_numpy(float), day.R.to_numpy(float)
        ks = np.minimum(k, RULE_CAP * dist)
        eq = eq + eq * float(np.sum(ks * r))
        risks += [k] * len(r)
        trades += len(r)
        hw = max(hw, eq)
        path.append(eq)
        hws.append(hw)
    return {"end": eq, "added": added, "refills": refills, "trades": trades, "path": path, "risks": risks,
            "done": done, "hws": hws}


# ============================================================ the simulated venue

class SimDelta:
    def __init__(self, faults: float = 0.0, seed: int = 7):
        self.bal = START
        self.products: dict[str, dict] = {}
        self.book: dict[str, tuple[float, float, float]] = {}
        self.pos: dict[int, int] = {}
        self.entry: dict[int, float] = {}
        self.pending: list[dict] = []
        self.history: list[dict] = []
        self.by_cid: dict[str, dict] = {}
        self.lev: dict[int, int] = {}
        self.funding: dict[int, float] = {}            # pid -> $ per hour while open
        self.rng = random.Random(seed)
        self.faults = faults
        self._id = 1000
        self.read_faults = 0

    def add(self, sym):
        if sym not in self.products:
            self.products[sym] = dict(id=len(self.products) + 1, tick_size="0.00000001", contract_value=str(CV),
                                      maintenance_margin="2.5")
        return self.products[sym]["id"]

    def _sym(self, pid):
        return next(s for s, p in self.products.items() if p["id"] == pid)

    def _read(self):
        if self.faults and self.rng.random() < self.faults:
            self.read_faults += 1
            raise ConnectionError("simulated venue read failure")

    # reads
    def get_product(self, s): self._read(); return self.products[s]
    def get_ticker(self, s):
        self._read()
        b, a, m = self.book[s]
        return {"mark_price": repr(m), "quotes": {"best_bid": repr(b), "best_ask": repr(a)}}
    def get_balance(self): self._read(); return [{"asset_symbol": "USD", "balance": repr(self.bal)}]
    def get_position(self, pid):
        self._read()
        return {"product_id": pid, "size": self.pos[pid]} if self.pos.get(pid) else None
    def get_positions(self):
        self._read()
        return [{"product_id": p, "product_symbol": self._sym(p), "size": n} for p, n in self.pos.items() if n]
    def get_pending_orders(self, pid=None):
        self._read()
        return [o for o in self.pending if pid in (None, o["product_id"])]
    def get_open_orders(self, pid=None): return []
    def order_history(self, pid=None, page_size=20):
        self._read()
        return [o for o in self.history if pid in (None, o["product_id"])][:page_size]
    def get_order_by_client_id(self, cid): self._read(); return self.by_cid.get(cid)
    def get_order_leverage(self, pid): self._read(); return float(self.lev.get(pid, 20))
    # writes (never failed)
    def set_order_leverage(self, pid, lev): self.lev[pid] = lev
    def cancel_order(self, oid, pid): self.pending = [o for o in self.pending if o["id"] != oid]

    def _settle(self, pid, n, price):
        e = self.entry[pid]
        self.bal += (e - price) * n * CV - FEE * e * n * CV
        self.funding.pop(pid, None)

    def place_order(self, order):
        p = order.to_payload()
        pid, size = p["product_id"], p["size"]
        bid, ask, _ = self.book[self._sym(pid)]
        self._id += 1
        if p["side"] == "sell" and not p.get("reduce_only"):
            assert p["order_type"] == "limit_order" and float(p["limit_price"]) <= bid
            assert "bracket_stop_loss_limit_price" not in p, "run 3 uses a market stop"
            self.pos[pid] = self.pos.get(pid, 0) - size
            self.entry[pid] = bid
            fee = FEE * bid * size * CV
            self.bal -= fee
            row = dict(id=self._id, product_id=pid, side="sell", size=size, unfilled_size=0,
                       average_fill_price=repr(bid), paid_commission=repr(fee), state="closed",
                       client_order_id=p["client_order_id"])
            self._id += 1
            self.pending.append(dict(id=self._id, product_id=pid, reduce_only=True, stop_order_type="stop_loss_order",
                                     stop_price=p["bracket_stop_loss_price"], order_type="market_order"))
        else:
            assert p.get("reduce_only"), "every buy closes a short"
            n = min(size, -self.pos.get(pid, 0))
            self.pos[pid] = self.pos.get(pid, 0) + n
            fee = FEE * self.entry.get(pid, ask) * n * CV
            row = dict(id=self._id, product_id=pid, side="buy", size=size, unfilled_size=size - n,
                       average_fill_price=repr(ask) if n else None, paid_commission=repr(fee) if n else "0",
                       state="closed", reduce_only=True, client_order_id=p["client_order_id"])
            if n:
                self._settle(pid, n, ask)
                self.history.insert(0, row)
        self.by_cid[p["client_order_id"]] = row
        return row

    def fire_stop(self, sym, price):
        pid = self.products[sym]["id"]
        n = -self.pos.get(pid, 0)
        if not n:
            return
        self.pos[pid] = 0
        self.pending = [o for o in self.pending if o["product_id"] != pid]
        fee = FEE * self.entry[pid] * n * CV
        self._settle(pid, n, price)
        self._id += 1
        self.history.insert(0, dict(id=self._id, product_id=pid, side="buy", size=n, unfilled_size=0,
                                    average_fill_price=repr(price), paid_commission=repr(fee), reduce_only=True,
                                    stop_order_type="stop_loss_order", state="closed"))

    def pay_funding(self, hours: float):
        for pid, per_hour in self.funding.items():
            if self.pos.get(pid):
                self.bal += per_hour * hours


# ============================================================ the run

def load(window: str) -> pd.DataFrame:
    """A window's trades. "2025-stress" is 2025 with 0.10R taken off every trade, so the refill budget runs out
    (the path the real years never reach)."""
    d = pd.read_parquet("out/gainer_fade/fade_costed_rows.parquet")
    d = d.dropna(subset=["A_bot"]).copy()
    if window == "2025-stress":
        d = d[d.window == "2025"].copy()
        d["A_bot"] -= 0.10
    else:
        d = d[d.window == window].copy()
    d["fund"] = d.A_bot_fund.fillna(0.0)
    d["R"] = d.A_bot + d.fund
    d["stopped"] = d.A_bot_how == "stop"
    return d.sort_values(["time", "symbol"])


async def run(window: str, faults: bool) -> dict:
    import asyncpg
    con = await asyncpg.connect(os.environ["DELTABOT_TEST_DSN"])
    await con.execute("DROP TABLE IF EXISTS fade_live_positions, fade_live_state, fade_live_events, fade_positions, "
                      "fade_days, fade_heartbeat, fade_paper_capital")
    await con.execute(F.SCHEMA)
    await L.ensure_schema(con)
    rows = load(window)
    days = [g for _, g in rows.groupby("time")]
    venue = SimDelta(faults=0.03 if faults else 0.0)

    def new_acct():
        return L.LiveFade(venue, risk_pct=0.10, start_usd=START, kill_switch_path="/nonexistent", protect_wait=0.0,
                          sleep=lambda s: None)
    acct = new_acct()
    ts = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc)
    held: dict[int, list] = {}                              # day -> trades opened that day (row, symbol, exit price)
    owner_log, path, hw_path = [], [], []
    budget_spent = False
    t_last = None

    def advance(t):
        """Pay funding for the time since the last event, before anything happens at `t`."""
        nonlocal t_last
        if t_last is not None and t > t_last:
            venue.pay_funding((t - t_last) / HOUR)
        t_last = max(t, t_last or t)

    async def step(t):
        advance(t)
        await acct.step(con, t)

    by_day = {int(g.time.iloc[0]): g for g in days}
    first_day, last_day = min(by_day), max(by_day)
    for d in range(first_day, last_day + DAY, DAY):         # EVERY calendar day: exits run with or without picks
        day_rows = by_day.get(d)
        if faults:
            acct = new_acct()                               # a deploy/restart every day
        # yesterday's held trades: the book shows each one's 24 h close price for the buy-back
        for prev in [k for k in held if k < d]:
            for r, sym, x in held.pop(prev):
                venue.book[sym] = (x, x, x)
        for k in range(4):                                  # exits (retried if a read fails)
            await step(d + 10 + 20 * k)
        if (d - DAY) in by_day:                             # the previous trading day is now fully settled
            path.append(venue.bal)
            hw_path.append(await con.fetchval("SELECT high_water FROM fade_live_state WHERE id = 1"))
        if day_rows is None:                                # no picks today: the day's passes only
            for h in range(1, 24):
                await step(d + h * HOUR)
            continue
        # the paper picks for today
        await con.execute("INSERT INTO fade_days (day, status) VALUES ($1, 'ENTERED')", ts(d))
        today = []
        for rank, r in enumerate(day_rows.itertuples(), 1):
            venue.add(r.symbol)
            e = 1.0
            venue.book[r.symbol] = (e, e * 1.0005, e)
            x = e * (1 - r.dist * r.A_bot - 2 * FEE)        # wallet result per trade = risk$ x A_bot exactly
            await con.execute(
                "INSERT INTO fade_positions (account, day, symbol, rank, prev_change, prev_turnover, atrp, entry_time, "
                "entry_price, stop_price, contracts, contract_value, equity_before, status) VALUES "
                "('A', $1, $2, $3, 0.3, 1e6, $4, $1, $5, $6, 1, 1, 250, 'OPEN')",
                ts(d), r.symbol, rank, r.dist / 2, e, e * (1 + r.dist))
            today.append((r, r.symbol, x))
        for k in range(6):                                  # entries within the 30-minute window
            await step(d + 120 + 60 * k)
        for r, sym, x in today:                             # funding per hour while open
            pid = venue.products[sym]["id"]
            if venue.pos.get(pid):
                # paid hour by hour from now to the close, adding up to the trade's recorded funding exactly
                close_at = d + STOP_HOUR * HOUR if r.stopped else d + DAY + 10
                venue.funding[pid] = r.fund * r.dist * 1.0 * (-venue.pos[pid]) * CV / ((close_at - t_last) / HOUR)
        # the day: hourly passes; stops fire at 06:00
        for h in range(1, 24):
            if h == STOP_HOUR:
                for r, sym, x in today:
                    if r.stopped:
                        advance(d + h * HOUR)
                        venue.fire_stop(sym, x)
            await step(d + h * HOUR)
            st = await con.fetchrow("SELECT status, balance FROM fade_live_state WHERE id = 1")
            if h == 3 and st and st["status"] == "STOPPED" and not budget_spent:   # the owner refills the same day
                before = venue.bal
                venue.bal = START
                rc, msg = await L.record_capital(con, acct, d + 3 * HOUR + 60)
                owner_log.append((pd.Timestamp(d, unit="s").date(), rc, msg))
                if rc:                                      # refused (the budget): the owner takes the money back
                    venue.bal = before
                    budget_spent = True
        held[d] = [(r, s, x) for r, s, x in today if not r.stopped]
    # close the last day
    for prev in list(held):
        for r, sym, x in held.pop(prev):
            venue.book[sym] = (x, x, x)
    last = last_day + DAY
    for k in range(4):
        await step(last + 10 + 20 * k)
    path.append(venue.bal)
    hw_path.append(await con.fetchval("SELECT high_water FROM fade_live_state WHERE id = 1"))
    st = await con.fetchrow("SELECT * FROM fade_live_state WHERE id = 1")
    pos = await con.fetch("SELECT * FROM fade_live_positions ORDER BY day, rank")
    events = await con.fetch("SELECT kind, count(*) n FROM fade_live_events GROUP BY kind ORDER BY kind")
    await con.close()
    return {"venue": venue, "state": st, "positions": pos, "events": {e["kind"]: e["n"] for e in events},
            "owner": owner_log, "path": path, "hw_path": hw_path, "days": days, "rows": rows}


CHECKS: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> str:
    CHECKS.append((name, ok))
    return f"  [{'PASS' if ok else 'FAIL'}] {name}"


def report(window: str, res: dict, ref: dict, label: str) -> list[str]:
    pos = res["positions"]
    closed = [p for p in pos if p["status"] == "CLOSED"]
    skipped = [p for p in pos if p["status"] == "SKIPPED"]
    other = [p for p in pos if p["status"] not in ("CLOSED", "SKIPPED")]
    st = res["state"]
    rows = res["rows"].set_index(["time", "symbol"])
    dr = []
    for p in closed:
        key = (int(p["day"].timestamp()), p["symbol"])
        if key in rows.index and p["r"] is not None:
            dr.append(p["r"] - float(rows.loc[key, "A_bot"]))
    dr = np.abs(np.array(dr)) if dr else np.array([0.0])
    bot_path, ref_path = np.array(res["path"]), np.array(ref["path"])
    rel = np.abs(bot_path / ref_path - 1)
    first = next((i for i, v in enumerate(rel) if v > 0.005), None)
    if first is not None:
        dd = res["days"][first]
        print(f"[debug {window}] first day >0.5% apart: #{first} {pd.Timestamp(int(dd.time.iloc[0]), unit='s').date()} "
              f"bot {bot_path[max(first-2,0):first+2]} ref {ref_path[max(first-2,0):first+2]} "
              f"hw bot {res['hw_path'][max(first-2,0):first+2]} ref {ref['hws'][max(first-2,0):first+2]}")
    out = [f"\n=== {window} — {label} ===",
           f"bot:       end ${res['venue'].bal:,.2f}  refills {len([o for o in res['owner'] if o[1] == 0])} "
           f"(${st['added_usd']:,.2f})  trades {len(closed)}  skipped {len(skipped)}  not closed {len(other)}  "
           f"state {st['status']} ({st['reason']})",
           f"reference: end ${ref['end']:,.2f}  refills {ref['refills']} (${ref['added']:,.2f})  trades {ref['trades']}"
           f"{'  budget spent' if ref['done'] else ''}",
           f"day-end balance, bot vs reference: max gap {100 * rel.max():.3f}%, mean {100 * rel.mean():.4f}%",
           f"per-trade R recorded by the bot vs the research R (before funding): max |diff| {dr.max():.4f}, "
           f"mean {dr.mean():.5f}",
           f"risk steps: bot {pd.Series([p['risk_pct'] for p in closed]).value_counts().sort_index().to_dict()}  "
           f"reference {pd.Series(ref['risks']).value_counts().sort_index().to_dict()}",
           f"events: {res['events']}"]
    taken = [(p["stop_price"] - p["entry_price"]) * p["contracts"] * p["contract_value"] / p["balance_before"]
             for p in closed if p["entry_price"] and p["balance_before"]]
    out.append(f"risk actually taken per trade (the 30% cap binds below a 33% stop): mean {100 * np.mean(taken):.1f}%, "
               f"median {100 * np.median(taken):.1f}%, max {100 * np.max(taken):.1f}% of the balance")
    bot_refills = len([o for o in res["owner"] if o[1] == 0])
    out += [check(f"{window} {label}: every day-end balance within 0.1% of the reference (faults: 2%)",
                  rel.max() <= (0.02 if label.startswith("faults") else 0.001)),
            check(f"{window} {label}: same trades, refills and ladder steps as the reference",
                  len(closed) == ref["trades"] and bot_refills == ref["refills"] and
                  pd.Series([p["risk_pct"] for p in closed]).value_counts().sort_index().to_dict()
                  == pd.Series(ref["risks"]).value_counts().sort_index().to_dict()),
            check(f"{window} {label}: nothing left open, no unexplained HALT",
                  not other and (st["status"] in ("RUNNING", "STOPPED") and not res["events"].get("HALTED"))),
            check(f"{window} {label}: each recorded R equals the research R", dr.max() < 1e-6)]
    if ref["done"]:
        out.append(check(f"{window} {label}: the run ends when the $3,000 budget would be passed",
                         st["status"] == "STOPPED" and any(o[1] == 1 and "budget" in o[2] for o in res["owner"])))
    if skipped:
        out.append("skipped: " + "; ".join(sorted({p['skip_reason'][:60] for p in skipped})))
    for o in res["owner"]:
        out.append(f"owner {o[0]}: rc {o[1]} {o[2]}")
    if label.startswith("faults"):
        out.append(f"venue reads failed: {res['venue'].read_faults}")
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--faults", action="store_true")
    ap.add_argument("--out", default="out/gainer_fade/live_sim.txt")
    a = ap.parse_args()
    lines = [__doc__.strip().splitlines()[0]]
    for window in ("2025", "2026", "2025-stress"):
        clean = await run(window, faults=False)
        ref = reference(clean["days"], skip_refill_day=True)
        lines += report(window, clean, ref, "clean")
        if a.faults:
            fz = await run(window, faults=True)
            lines += report(window, fz, ref, "faults: restart daily, 3% of reads fail")
            lines.append(f"faults vs clean end balance: {100 * abs(fz['venue'].bal / clean['venue'].bal - 1):.4f}%")
    bad = [n for n, ok in CHECKS if not ok]
    lines.append(f"\n{len(CHECKS) - len(bad)}/{len(CHECKS)} checks pass" + (f"; FAILED: {bad}" if bad else ""))
    text = "\n".join(lines)
    print(text)
    Path(a.out).write_text(text + "\n")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    asyncio.run(main())
