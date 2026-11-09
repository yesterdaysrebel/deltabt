"""Fade exit targets side by side (owner 2026-11-09: "why not make what you risk? run backtest on it").

Same picks, stop and fills as the 2026-10-28 win-rate scan (scripts/winrate_scan.py), re-run on the
fade's own picks only (its row cache is gone with the container): short the top 2 previous-UTC-day
gainers at the daily open, stop 2 x ATR14% above, take-profit tp x the stop distance below; the first
touch wins, a minute touching both counts as the STOP, a stop fills at max(stop, that minute's close);
else the 24 h close. Taker 0.05% each side. tp = None is rule A. Funding left out (as in the scan).
Coins listed today only (the delisted re-check moved 2025 by -0.003R; noted, not redone).

Decision rule, written before the run: target 1.0x replaces QUICK only if its mean R is above QUICK's
in BOTH years AND the pooled paired difference (same trades) is above zero by at least 2 standard
errors. Otherwise QUICK stays. "real" = only stops a 1x account can take (<= 32.5% away).

    PYTHONPATH=. .venv/bin/python scripts/fade_target_compare.py
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import gainer_fade_2025_lab as L  # noqa: E402

TPS = (0.2, 0.3, 0.5, 1.0, None)
FEE = 0.0005
MAX_DIST = 0.325
START, RISK = 1200.0, 0.05


def name(tp):
    return "A (24 h)" if tp is None else f"{tp:g}x" + (" QUICK" if tp == 0.2 else " =risk" if tp == 1.0 else "")


def one(x):
    st = L._store()
    try:
        m = st.load(x.symbol, "ltp", "1m", int(x.time), int(x.time) + L.DAY - 60)
    except Exception as exc:                                         # noqa: BLE001
        return None, f"{x.symbol}: {exc}"
    m = m[(m.time >= x.time) & (m.time < x.time + L.DAY)]
    if m.empty:
        return None, None
    h, lo, c = m.high.to_numpy(float), m.low.to_numpy(float), m.close.to_numpy(float)
    e, dist = float(x.open), 2.0 * float(x.atrp)
    stop = e * (1 + dist)
    s = np.nonzero(h >= stop)[0]
    si = s[0] if len(s) else 10**9
    rec = {"symbol": x.symbol, "time": x.time, "window": x.window, "dist": dist}
    for tp in TPS:
        ti = 10**9
        if tp is not None:
            tg = e * (1 - tp * dist)
            t_ = np.nonzero(lo <= tg)[0]
            ti = t_[0] if len(t_) else 10**9
        if si <= ti and si < 10**9:
            xp, how = max(stop, c[si]), "stop"
        elif ti < 10**9:
            xp, how = tg, "target"
        else:
            xp, how = c[-1], "time"
        rec[name(tp)] = (-(xp / e - 1) - FEE - FEE * xp / e) / dist
        rec[name(tp) + "_how"] = how
    return rec, None


def account(r: pd.Series) -> tuple[float, float]:
    """$1,200 at 5% risk, compounding, trades in time order: (end, max drawdown)."""
    eq, peak, dd = START, START, 0.0
    for v in r:
        eq += RISK * eq * v
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    return eq, dd


def main() -> None:
    p = L.panel()
    picks = (p[p.eligible & np.isfinite(p.atrp) & (p.atrp > 0)]
             .sort_values(["time", "prev_change"], ascending=[True, False]).groupby("time").head(2))
    print(f"{len(picks)} pick-days; fetching 1m candles ...", flush=True)
    rows, errs = [], []
    with ThreadPoolExecutor(3) as ex:
        for k, (rec, err) in enumerate(ex.map(one, picks.itertuples()), 1):
            if rec:
                rows.append(rec)
            if err:
                errs.append(err)
            if k % 100 == 0:
                print(f"  {k}/{len(picks)}", flush=True)
    res = pd.DataFrame(rows).sort_values("time")
    res.to_parquet(L.OUT / "fade_target_compare_rows.parquet", index=False)
    print(f"{len(res)} trades simulated, {len(errs)} fetch errors {errs[:3]}")
    names = [name(tp) for tp in TPS]
    for label, df in (("all picks", res), ("real (stop <= 32.5%)", res[res.dist <= MAX_DIST])):
        print(f"\n=== {label} ===")
        for w in ("2025", "2026"):
            d = df[df.window == w]
            print(f"[{w}] {len(d)} trades")
            print(f"  {'exit':12s} {'win':>5s} {'target':>7s} {'stop':>5s} {'mean R':>8s} {'± SE':>6s} {'avg win':>8s} "
                  f"{'avg loss':>8s}   $1,200 @5% -> end (max drop)")
            for n in names:
                v = d[n]
                hw = d[n + "_how"]
                end, dd = account(v)
                print(f"  {n:12s} {100 * (v > 0).mean():4.0f}% {100 * (hw == 'target').mean():6.0f}% "
                      f"{100 * (hw == 'stop').mean():4.0f}% {v.mean():+8.3f} {v.std() / np.sqrt(len(v)):6.3f} "
                      f"{v[v > 0].mean():+8.2f} {v[v <= 0].mean():+8.2f}   ${end:,.0f} ({100 * dd:.0f}%)")
        q, one_r = df["0.2x QUICK"], df["1x =risk"]
        diff = one_r - q
        by = {w: (one_r[df.window == w].mean(), q[df.window == w].mean()) for w in ("2025", "2026")}
        both = all(a > b for a, b in by.values())
        z = diff.mean() / (diff.std() / np.sqrt(len(diff)))
        print(f"  1.0x minus QUICK, paired: {diff.mean():+.3f}R per trade (z {z:+.2f}); above in both years: {both}")
        print(f"  RULE: 1.0x replaces QUICK -> {'YES' if both and z >= 2 else 'NO'}")


if __name__ == "__main__":
    main()
