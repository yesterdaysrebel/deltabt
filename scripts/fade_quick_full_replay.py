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


if __name__ == "__main__":
    main()
