"""Paper QUICK's own picks replayed as a FULL-LEVERAGE bot with 25% of capital as margin per trade
(owner 2026-11-12: "run tests on quick with full leverage and 25% captial per trade, run it a bot simulation on the
trades took in paper and on the data").

    python3 scripts/fade_quick_full_replay.py out/fade_quick_full/quick_paper.json

Input: every QUICK row (taken and skipped) read from the bot's database (read-only). For each pick, Delta's own 1m
candles (public /v2/history/candles, trade price and MARK:) over the 24 h. Rules as paper QUICK: short at the paper
entry price (the 00:00 open), stop at paper's stop (2x ATR14%), target 0.2x the stop distance, else out at 24 h.
  margin   25% of the balance at the day's start, per pick
  leverage Delta's max for the coin (out/delta_margin/perp_margins_2026-11-12.json): notional = L x margin
  liquid.  when the 1m MARK high reaches entry x (1 + 1/L - maintenance): the margin is lost
  fills    target when the 1m trade low touches it (as paper), stop at the worse of the stop and the minute's close,
           fee 0.0531% a side (measured on the real account)
Also run at the leverage the live bot would allow (liquidation >= 2x the stop away) for contrast. Standard library
only; candles cached in out/fade_quick_full/.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

API = "https://api.india.delta.exchange/v2/history/candles"
CACHE = Path("out/fade_quick_full")
FEE = 0.000531
MARGIN = 0.25
START = 1500.0


def ts(s: str) -> int:
    return int(datetime.fromisoformat(s).timestamp())


def candles(symbol: str, t0: int) -> list[dict]:
    f = CACHE / f"{symbol.replace(':', '_')}_{t0}.json"
    if f.exists():
        return json.loads(f.read_text())
    url = f"{API}?resolution=1m&symbol={symbol}&start={t0}&end={t0 + 86400}"
    req = urllib.request.Request(url, headers={"User-Agent": "deltabt-research"})
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = json.load(r).get("result") or []
    rows = sorted(rows, key=lambda x: x["time"])
    f.write_text(json.dumps(rows))
    time.sleep(0.2)
    return rows


def replay(pick: dict, lev: float, mm: float):
    t0 = ts(pick["day"])
    ltp, mark = candles(pick["symbol"], t0), candles("MARK:" + pick["symbol"], t0)
    if not ltp:
        return None
    e, stop = pick["entry_price"], pick["stop_price"]
    tgt = e - 0.2 * (stop - e)
    liq = e * (1 + 1 / lev - mm)
    mk = {c["time"]: c for c in mark}
    for c in ltp:
        m = mk.get(c["time"])
        if m and m["high"] >= liq:
            return "LIQUIDATED", liq, c["time"] - t0
        if c["high"] >= stop:
            return "STOP", max(stop, c["close"]), c["time"] - t0
        if c["low"] <= tgt:
            return "TARGET", tgt, c["time"] - t0
    return "TIME", ltp[-1]["close"], 86400


def main() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    picks = [p for p in json.load(open(sys.argv[1])) if p.get("entry_price") and p.get("stop_price")]
    tab = json.load(open("out/delta_margin/perp_margins_2026-11-12.json"))
    for mode in ("FULL", "SAFE"):
        print(f"\n{mode} leverage, 25% of capital as margin per pick, from ${START:,.0f}")
        bal, days = START, {}
        for p in picks:
            days.setdefault(p["day"], []).append(p)
        worst = 0.0
        peak = bal
        for d in sorted(days):
            start_bal, pnl = bal, 0.0
            for p in days[d]:
                im, mm = (x / 100 for x in tab.get(p["symbol"], [5.0, 2.5]))
                stop_dist = p["stop_price"] / p["entry_price"] - 1
                L = 1 / im if mode == "FULL" else max(1, min(1 / im, int(1 / (2 * stop_dist + mm))))
                if mode == "SAFE" and 1 / L - mm < 2 * stop_dist:
                    print(f"  {d[:10]} {p['symbol']:10s} skipped: no leverage keeps liquidation 2x the stop away")
                    continue
                res = replay(p, L, mm)
                if res is None:
                    print(f"  {d[:10]} {p['symbol']:10s} no Delta candles")
                    continue
                how, xp, secs = res
                margin = MARGIN * start_bal
                notional = L * margin
                if how == "LIQUIDATED":
                    t = -margin
                else:
                    ret = -(xp / p["entry_price"] - 1) - FEE - FEE * xp / p["entry_price"]
                    t = max(notional * ret, -margin)
                pnl += t
                paper = f"paper {p['status']}" + (f" {p['exit_reason']} {p['r']:+.2f}R" if p["status"] == "CLOSED" else f" ({p['skip_reason']})")
                print(f"  {d[:10]} {p['symbol']:10s} {int(L):3d}x  stop {100 * stop_dist:5.1f}% liq {100 * (1 / L - mm):5.1f}%  "
                      f"{how:10s} after {secs / 3600:5.2f} h  ${t:+9.2f}  [{paper}]")
            bal = max(bal + pnl, 0.0)
            peak = max(peak, bal)
            worst = max(worst, 1 - bal / peak)
            print(f"    {d[:10]} day ${pnl:+9.2f} -> balance ${bal:,.2f}")
        print(f"  END ${bal:,.2f} ({100 * (bal / START - 1):+.1f}%), worst drop {100 * worst:.0f}%")


if __name__ == "__main__" and "--tight" not in sys.argv and "--usd" not in sys.argv:
    main()


def tight() -> None:
    """Owner 2026-11-12: "we can use tight SL? avoid liquidations". Paper QUICK's picks with a FIXED tight stop, the most
    leverage (up to Delta's max) that keeps liquidation >= 2x the stop away, 25% of capital as margin per pick.
    Target 0.2x the stop (QUICK) or 1x the stop. Entry at paper's price (the 00:00 open: optimistic)."""
    picks = [p for p in json.load(open(sys.argv[1])) if p.get("entry_price") and p.get("stop_price")]
    tab = json.load(open("out/delta_margin/perp_margins_2026-11-12.json"))
    print("TIGHT FIXED STOPS on paper QUICK's picks, 25% margin per pick, liquidation >= 2x the stop away, from $1,500")
    for stop_pct in (0.01, 0.02, 0.04, 0.08):
        for tk in (0.2, 1.0):
            bal, peak, worst, n, hits = START, START, 0.0, 0, {"STOP": 0, "TARGET": 0, "TIME": 0, "LIQUIDATED": 0}
            days = {}
            for p in picks:
                days.setdefault(p["day"], []).append(p)
            for d in sorted(days):
                start_bal, pnl = bal, 0.0
                for p in days[d]:
                    im, mm = (x / 100 for x in tab.get(p["symbol"], [5.0, 2.5]))
                    L = max(1, min(int(1 / im), int(1 / (2 * stop_pct + mm))))
                    q = dict(p, stop_price=p["entry_price"] * (1 + stop_pct))
                    res = replay_k(q, L, mm, tk)
                    if res is None:
                        continue
                    how, xp = res
                    hits[how] += 1
                    n += 1
                    margin = MARGIN * start_bal
                    if how == "LIQUIDATED":
                        pnl -= margin
                    else:
                        ret = -(xp / p["entry_price"] - 1) - FEE - FEE * xp / p["entry_price"]
                        pnl += max(L * margin * ret, -margin)
                bal = max(bal + pnl, 0.0)
                peak = max(peak, bal)
                worst = max(worst, 1 - bal / peak)
            print(f"  stop {100 * stop_pct:4.1f}%  target {100 * tk * stop_pct:4.1f}%  leverage up to "
                  f"{max(1, min(20, int(1 / (2 * stop_pct + 0.025)))):2d}x  -> ${bal:>9,.2f}  worst drop {100 * worst:3.0f}%  "
                  f"of {n}: target {hits['TARGET']}, stop {hits['STOP']}, 24 h {hits['TIME']}, liquidated {hits['LIQUIDATED']}")


def replay_k(pick: dict, lev: float, mm: float, tk: float):
    t0 = ts(pick["day"])
    ltp, mark = candles(pick["symbol"], t0), candles("MARK:" + pick["symbol"], t0)
    if not ltp:
        return None
    e, stop = pick["entry_price"], pick["stop_price"]
    tgt = e - tk * (stop - e)
    liq = e * (1 + 1 / lev - mm)
    mk = {c["time"]: c for c in mark}
    for c in ltp:
        m = mk.get(c["time"])
        if m and m["high"] >= liq:
            return "LIQUIDATED", liq
        if c["high"] >= stop:
            return "STOP", max(stop, c["close"])
        if c["low"] <= tgt:
            return "TARGET", tgt
    return "TIME", ltp[-1]["close"]


if __name__ == "__main__" and "--tight" in sys.argv:
    tight()


def usd() -> None:
    """The $ target idea (owner 2026-11-12) on paper QUICK's picks with Delta 1m + MARK candles: wallet $1,640, 25%
    margin, leverage L, stop = the widest keeping liquidation 2x away, target = the move that earns $X. No compounding."""
    picks = [p for p in json.load(open(sys.argv[1])) if p.get("entry_price")]
    tab = json.load(open("out/delta_margin/perp_margins_2026-11-12.json"))
    wallet = 1640.0
    print("$ TARGET on paper QUICK's picks, $1,640 wallet, 25% margin per pick, widest safe stop (no compounding)")
    for lev in (5, 10, 20):
        for target in (100, 200, 300, 400):
            total, hits = 0.0, {"STOP": 0, "TARGET": 0, "TIME": 0, "LIQUIDATED": 0}
            for p in picks:
                im, mm = (x / 100 for x in tab.get(p["symbol"], [5.0, 2.5]))
                L = min(lev, 1 / im)
                stop = (1 / L - mm) / 2
                notional = 0.25 * wallet * L
                q = dict(p, stop_price=p["entry_price"] * (1 + stop))
                res = replay_k(q, L, mm, (target / notional) / stop)
                if res is None:
                    continue
                how, xp = res
                hits[how] += 1
                ret = -(xp / p["entry_price"] - 1) - FEE - FEE * xp / p["entry_price"]
                total += -0.25 * wallet if how == "LIQUIDATED" else max(notional * ret, -0.25 * wallet)
            print(f"  {lev:2d}x target ${target}: total ${total:+9.2f} over {sum(hits.values())} picks | "
                  f"target {hits['TARGET']}, stop {hits['STOP']}, 24 h {hits['TIME']}, liquidated {hits['LIQUIDATED']}")


if __name__ == "__main__" and "--usd" in sys.argv:
    usd()
