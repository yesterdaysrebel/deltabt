"""Could the steady fade pass and keep a funded "prop firm" account? (owner 2026-11-14: "my target is 2000$ a month,
you figure out how we make it with 1600 capital"; "explore more, maybe external resources").

    PYTHONPATH=. python scripts/prop_firm_sim.py

Trades: Delta rule A picks 2025-01..2026-10 with the funding filter (>= 0.05% the day before), 3x, stop just before
liquidation (~28% up), take at a 10% fall (+30% ROE), else 24 h; real-bot fills (as out/opt1_opt2.txt).
Typical crypto prop-firm rules (third-party reviews, Nov 2026; they differ by firm and change often):
  CHALLENGE  reach +8.5% (also shown: +10%) before losing 6% or 10% of the START balance; a day losing 4% or more
             fails it.
  FUNDED     the same loss limits; every 30 days the profit above the start is paid out (80% to the trader) and the
             balance goes back to the start.
A run starts on every trading day of the sample (overlapping). Losses are counted on CLOSED trades per day only: a
firm counts open (floating) losses too, so real breach rates are HIGHER than shown.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
import fade_ladder_5x as L5  # noqa: E402
import fade_profit_ladder as PL  # noqa: E402
import fade_rr12 as R12  # noqa: E402

L = 3


def daily_roe() -> pd.Series:
    """Per day: the list of ROEs (return on margin) of that day's filtered trades."""
    tab = json.load(open("out/delta_margin/perp_margins_2026-11-12.json"))
    d = L5.trades().reset_index(drop=True)
    F = pd.read_parquet("out/binance_paths/delta_funding_daily.parquet")
    f = [F.at[t - pd.Timedelta(days=1), s] if (t - pd.Timedelta(days=1)) in F.index and s in F.columns else np.nan
         for t, s in zip(d.date, d.symbol)]
    keep = np.array(f) >= 0.0005
    opens, ps = R12.paths(d)
    out = {}
    for t in pd.date_range(d.date.min(), d.date.max()):
        out[t] = []
    for i in np.nonzero(keep)[0]:
        a = ps[i]
        hi = np.where(np.isfinite(a[0]), a[0], -np.inf)
        lo = np.where(np.isfinite(a[1]), a[1], np.inf)
        cl = pd.Series(a[2].astype(float)).ffill().to_numpy()
        e = cl[2]
        if not (np.isfinite(e) and np.isfinite(cl[-1])):
            continue
        mm = max(0.025, tab.get(d.symbol.iat[i], [0, 2.5])[1] / 100)
        liq = 1 / L - mm
        sl, tp = 0.9 * liq, 0.30 / L
        first = lambda m: (3 + np.nonzero(m[3:])[0][0]) if m[3:].any() else 10 ** 9   # noqa: E731
        ms, mt = first(hi >= e * (1 + sl)), first(lo <= e * (1 - tp) * (1 - PL.THR))
        if ms == mt == 10 ** 9:
            xp = cl[-1]
        elif ms <= mt:
            xp = max(e * (1 + sl), cl[ms])
            if xp >= e * (1 + liq):
                out[d.date.iat[i]].append(-1.0)
                continue
        else:
            xp = e * (1 - tp)
        out[d.date.iat[i]].append(max(L * (-(xp / e - 1) - PL.FEE - PL.FEE * xp / e - PL.EXTRA), -1.0))
    return pd.Series(out)


def challenge(days, k0, mg, target, max_loss, day_loss):
    bal = 1.0
    for k in range(k0, len(days)):
        pnl = sum(mg * bal * r for r in days.iat[k])
        if pnl <= -day_loss:
            return "fail", k - k0
        bal += pnl
        if bal <= 1 - max_loss:
            return "fail", k - k0
        if bal >= 1 + target:
            return "pass", k - k0
    return "open", len(days) - k0


def funded(days, k0, mg, max_loss, day_loss, months=12, split=0.8):
    bal, paid, alive = 1.0, [], True
    for m in range(months):
        for k in range(k0 + 30 * m, min(k0 + 30 * (m + 1), len(days))):
            pnl = sum(mg * bal * r for r in days.iat[k])
            bal += pnl
            if pnl <= -day_loss or bal <= 1 - max_loss:
                return paid, False, m
        if k0 + 30 * (m + 1) > len(days):
            break
        paid.append(split * max(bal - 1, 0))
        bal = min(bal, 1.0) if bal > 1 else bal
    return paid, alive, months


def main() -> None:
    days = daily_roe()
    n = len(days)
    print(f"PROP-FIRM SIMULATION on the filtered 3x fade, Delta {days.index[0].date()}..{days.index[-1].date()} "
          f"({sum(len(x) for x in days)} trades). Balance in units of the account; $ shown for a $100,000 account.\n")
    for max_loss, day_loss in ((0.06, 0.04), (0.10, 0.05)):
        print(f"RULES: max loss {100 * max_loss:.0f}% of the start, daily loss limit {100 * day_loss:.0f}%")
        for mg in (0.01, 0.02, 0.03, 0.05, 0.08):
            row = f"  margin {100 * mg:2.0f}% a trade (a stop costs ~{100 * mg * L * 0.277:3.1f}%): "
            for target in (0.085, 0.10):
                res = [challenge(days, k, mg, target, max_loss, day_loss) for k in range(0, n - 30)]
                done = [r for r in res if r[0] != "open"]
                ps = [r for r in done if r[0] == "pass"]
                row += (f"challenge +{100 * target:.1f}%: pass {100 * len(ps) / max(len(done), 1):3.0f}% "
                        f"(median {np.median([r[1] for r in ps]) if ps else 0:3.0f} days, unfinished {100 * (len(res) - len(done)) / len(res):2.0f}%) | ")
            fr = [funded(days, k, mg, max_loss, day_loss) for k in range(0, n - 365)]
            pay = np.array([np.sum(p) / 12 for p, _, _ in fr])
            lost = np.mean([not a for _, a, _ in fr])
            row += (f"funded 12 months: account lost {100 * lost:3.0f}% of runs, average payout "
                    f"${100000 * pay.mean():,.0f}/month per $100k (median ${100000 * np.median(pay):,.0f})")
            print(row)
        print()


if __name__ == "__main__":
    main()
