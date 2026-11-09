"""75% of capital deployed, loss capped at 5% (owner 2026-11-09: "with 75% capital deployed can we not
cap risk at 5% and take trade? what are the numbers for quick?").

5% loss on 75% notional = a stop 6.67% above the entry for EVERY pick (not 2 x ATR14%). Targets tp x
that distance below the entry (0.2 = QUICK shape, 1.0 = 1:1, 2.0 = 2:1); None = hold to 24 h. Same
picks (top 2 previous-day gainers), entry at the daily open, first touch wins, a minute touching both is
the STOP, stop fills at max(stop, that minute's close), taker 0.05% each side, no funding. 1m candles
are the ones cached by scripts/fade_target_compare.py. Two positions at 75% each need 2x leverage:
liquidation ~47.5% away, > 3 x 6.67%, so no pick is skipped.

Decision rule, written before the run: a tight-stop variant is worth considering only if its mean R is
> 0 in BOTH years and the pooled mean is >= 2 standard errors above zero.

    PYTHONPATH=. .venv/bin/python scripts/fade_tight_stop.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import gainer_fade_2025_lab as L  # noqa: E402

DIST = 0.05 / 0.75
TPS = (0.2, 0.5, 1.0, 2.0, None)
FEE = 0.0005
START, RISK = 1500.0, 0.05


def name(tp):
    return "24 h hold" if tp is None else f"target {tp:g}x" + (" (QUICK)" if tp == 0.2 else " (1:1)" if tp == 1.0 else "")


def main() -> None:
    picks = pd.read_parquet(L.OUT / "fade_more_picks_rows.parquet")
    picks = picks[picks["rank"] <= 2][["symbol", "time", "window"]]
    daily = L.daily()[["symbol", "time", "open"]]
    picks = picks.merge(daily, on=["symbol", "time"])
    st = L._store()
    rows = []
    for sym, g in picks.groupby("symbol"):
        m = st.read(sym, "ltp", "1m")
        t = m.time.to_numpy("int64")
        hi, lo, cl = m.high.to_numpy(float), m.low.to_numpy(float), m.close.to_numpy(float)
        for x in g.itertuples():
            a, b = np.searchsorted(t, x.time), np.searchsorted(t, x.time + L.DAY)
            if b <= a:
                continue
            e = float(x.open)
            stop = e * (1 + DIST)
            s = np.nonzero(hi[a:b] >= stop)[0]
            si = s[0] if len(s) else 10**9
            rec = {"symbol": sym, "time": x.time, "window": x.window}
            for tp in TPS:
                ti = 10**9
                if tp is not None:
                    tg = e * (1 - tp * DIST)
                    q = np.nonzero(lo[a:b] <= tg)[0]
                    ti = q[0] if len(q) else 10**9
                if si <= ti and si < 10**9:
                    xp, how = max(stop, cl[a + si]), "stop"
                elif ti < 10**9:
                    xp, how = tg, "target"
                else:
                    xp, how = cl[b - 1], "time"
                rec[name(tp)] = (-(xp / e - 1) - FEE - FEE * xp / e) / DIST
                rec[name(tp) + "_how"] = how
            rows.append(rec)
    res = pd.DataFrame(rows).sort_values("time")
    days = {w: res[res.window == w].time.nunique() for w in ("2025", "2026")}
    print(f"stop {DIST:.2%} above entry; $1,500, 75% deployed (${0.75 * START:,.0f} a trade), loss capped at "
          f"${RISK * START:.0f}; {len(res)} trades")
    print(f"  {'exit':18s} {'year':5s} {'win':>5s} {'target':>7s} {'stop':>5s} {'avg win $':>9s} {'avg loss $':>10s} "
          f"{'mean R':>7s} {'z':>5s} {'$/month':>8s} {'end $':>8s} {'max drop':>8s}")
    for tp in TPS:
        n = name(tp)
        for w in ("2025", "2026"):
            d = res[res.window == w]
            v, how = d[n], d[n + "_how"]
            eq, peak, dd = START, START, 0.0
            for x in v:
                eq = max(eq + RISK * eq * x, 0.0)
                peak = max(peak, eq)
                dd = max(dd, 1 - eq / peak)
            print(f"  {n:18s} {w:5s} {100 * (v > 0).mean():4.0f}% {100 * (how == 'target').mean():6.0f}% "
                  f"{100 * (how == 'stop').mean():4.0f}% {RISK * START * v[v > 0].mean():+9.1f} "
                  f"{RISK * START * v[v <= 0].mean():+10.1f} {v.mean():+7.3f} {v.mean() / (v.std() / np.sqrt(len(v))):+5.1f} "
                  f"{(eq - START) / (days[w] / 30.4):8.0f} {eq:8,.0f} {100 * dd:7.0f}%")
        v = res[n]
        both = all(res[res.window == w][n].mean() > 0 for w in ("2025", "2026"))
        zz = v.mean() / (v.std() / np.sqrt(len(v)))
        print(f"  RULE {n}: >0 both years {both}, pooled z {zz:+.2f} -> {'WORTH CONSIDERING' if both and zz >= 2 else 'NO'}")


if __name__ == "__main__":
    main()
