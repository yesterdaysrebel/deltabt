"""Rule A account simulation: flat risk vs the 10/5/3 drawdown ladder (owner 2026-11-09).

Trade rows come from scripts/fade_more_picks.py (out/gainer_fade/fade_more_picks_rows.parquet): the top
previous-UTC-day gainers, shorted at the daily open, stop 2 x ATR14% above, exit at the first touch of the
stop (fill max(stop, that minute's close)) or the 24 h close; taker 0.05% a side; no funding. Column
"A (24 h)" is each trade's net R. Only stops a 1x account can take (<= 32.5% away).

Account rules, as the real bot (live/fade_live.py) would size:
  - Each day's picks open together at 00:00 UTC, so all of a day's trades are sized off that morning's
    balance (compounding day to day, not trade to trade).
  - Risk per trade = the step's % of the balance, capped so a position is <= 45% of the balance at 1x
    (risk <= 0.45 x stop distance); if a day's positions together exceed the balance they are scaled down.
  - Ladder 10/5/3: 10% while the balance is < 20% below its high-water mark, 5% from 20%, 3% from 35%;
    back to 10% only at a new high. The high-water mark starts at $1,500 each year.

Controls:
  POSITIVE  mean net R per trade of rule A on ALL top-2 picks must reproduce the pre-registered judge
            (out/gainer_fade/judge_2025.txt: 2025 +0.009R, 2026 +0.076R) within 0.002R.
  NEGATIVE  the same trades in 1,000 random orders: how often the ladder beats flat 10% / flat 5% on end
            balance and on worst drop. If the ladder's edge needs the real order, it fades when shuffled.

    PYTHONPATH=. .venv/bin/python scripts/fade_ladder_account.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import gainer_fade_2025_lab as L  # noqa: E402

COL = "A (24 h)"
START = 1500.0
MAX_DIST = 0.325
POSITION_CAP = 0.45
LADDER = ((0.0, 0.10), (0.20, 0.05), (0.35, 0.03))
SIZINGS = {"flat 3%": ((0.0, 0.03),), "flat 5%": ((0.0, 0.05),), "flat 10%": ((0.0, 0.10),),
           "ladder 10/5/3": LADDER}


def step_risk(under: float, steps) -> float:
    k = steps[0][1]
    for threshold, risk in steps:
        if under >= threshold:
            k = risk
    return k


def simulate(days: list[tuple[np.ndarray, np.ndarray]], steps) -> dict:
    """`days`: per trading day, (stop distances, net R) of that day's trades, in time order."""
    eq = peak = low = START
    worst = 0.0
    path = []
    for dist, r in days:
        k = step_risk(1 - eq / peak, steps)
        ks = np.minimum(k, POSITION_CAP * dist)
        notional = float(np.sum(ks / dist))
        ks = ks * min(1.0, 1.0 / notional)
        eq = max(eq + eq * float(np.sum(ks * r)), 0.0)
        peak, low = max(peak, eq), min(low, eq)
        worst = max(worst, 1 - eq / peak)
        path.append(eq)
    return {"end": eq, "low": low, "peak": peak, "worst_drop": worst, "path": path}


def day_list(d: pd.DataFrame) -> list[tuple[np.ndarray, np.ndarray]]:
    return [(g.dist.to_numpy(float), g[COL].to_numpy(float)) for _, g in d.groupby("time")]


def main() -> None:
    rows = pd.read_parquet(L.OUT / "fade_more_picks_rows.parquet").sort_values("time")
    top2 = rows[rows["rank"] <= 2]

    print("POSITIVE CONTROL (all top-2 picks, mean net R per trade vs judge_2025.txt):")
    ok = True
    for w, want in (("2025", 0.009), ("2026", 0.076)):
        got = top2[top2.window == w][COL].mean()
        ok &= abs(got - want) <= 0.002
        print(f"  {w}: {got:+.4f}R (judge {want:+.3f}R) {'OK' if abs(got - want) <= 0.002 else 'MISMATCH'}")
    if not ok:
        sys.exit("positive control failed: the trade rows do not reproduce the judged rule A")

    real = top2[top2.dist <= MAX_DIST].copy()
    real["mon"] = pd.to_datetime(real.time, unit="s").dt.strftime("%b")
    print(f"\nACCOUNT: ${START:,.0f}, top 2, stops <= {MAX_DIST:.1%}, 1x with the 45% position cap, compounding by day")
    print(f"  {'sizing':14s} | {'2025 end':>9s} {'low':>6s} {'worst':>6s} | {'2026 end':>9s} {'low':>6s} {'worst':>6s}")
    res = {}
    for name, steps in SIZINGS.items():
        a = simulate(day_list(real[real.window == "2025"]), steps)
        b = simulate(day_list(real[real.window == "2026"]), steps)
        res[name] = (a, b)
        print(f"  {name:14s} | {a['end']:9,.0f} {a['low']:6,.0f} {a['worst_drop']:6.0%} | "
              f"{b['end']:9,.0f} {b['low']:6,.0f} {b['worst_drop']:6.0%}")

    print("\nLADDER 10/5/3, month-end balance:")
    for w in ("2025", "2026"):
        d = real[real.window == w]
        path = simulate(day_list(d), LADDER)["path"]
        months = [g.mon.iloc[0] for _, g in d.groupby("time")]
        ends = {}
        for m, e in zip(months, path):
            ends[m] = e
        print(f"  {w}: " + "  ".join(f"{m} ${e:,.0f}" for m, e in ends.items()))

    print("\nNEGATIVE CONTROL (same trades, 1,000 random day orders): share of orders where the ladder ...")
    rng = np.random.default_rng(7)
    for w in ("2025", "2026"):
        days = day_list(real[real.window == w])
        wins = {"end > flat 10%": 0, "end > flat 5%": 0, "drop < flat 10%": 0, "drop < flat 5%": 0}
        for _ in range(1000):
            order = [days[i] for i in rng.permutation(len(days))]
            lad, f10, f5 = (simulate(order, SIZINGS[n]) for n in ("ladder 10/5/3", "flat 10%", "flat 5%"))
            wins["end > flat 10%"] += lad["end"] > f10["end"]
            wins["end > flat 5%"] += lad["end"] > f5["end"]
            wins["drop < flat 10%"] += lad["worst_drop"] < f10["worst_drop"]
            wins["drop < flat 5%"] += lad["worst_drop"] < f5["worst_drop"]
        print(f"  {w}: " + ", ".join(f"{k} {v / 10:.0f}%" for k, v in wins.items()))


if __name__ == "__main__":
    main()
