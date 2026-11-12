"""Cut a rule A short when we "sense the position is going to flip", instead of (or before) the hard stop
(owner 2026-11-12: "instead of hard Sl, can we not cut position with some logic if we sense the position is going to
flip?").

    PYTHONPATH=. python scripts/fade_flip_exits.py

Rule A picks on Binance 2021-26 (scripts/fade_owner_exits.load: 3,608, 1m high/low/close) + BTC 1m closes.
Signals are read on 15-minute closes after entry (never on the minute's own extreme), and the exit is the NEXT
minute's close + 0.05% slippage, so no signal sees its own fill. Flip detectors:
  newhigh   15m close above entry AND above every 15m close since entry over the last H hours (pump resumes)
  above_ma  15m close above the 4 h mean of 1m closes by >= k x ATR (trend turned up)
  higher_lows  three rising 1 h lows in a row and the 15m close above entry
  btc_up    BTC up >= 1% since entry while the coin is above entry (market lifts it)
  adverse   at the 1 h mark only: price above entry by >= 0.5 x ATR (the disputed "early adverse cut")
Each runs (a) ON TOP of the 2x-ATR stop, and (b) INSTEAD of it, with only a 3x-ATR disaster stop (no hard stop at 2x).
All R are in units of the 2x-ATR distance, so every row is comparable to the baseline (2x-ATR stop, 24 h).
PASS BAR, written first: better than the baseline in >= 5 of 6 years AND paired z >= 2. With ~20 rows, one z of 2
appears by chance about 1 time in 2: a pass is a lead for paper, not proof.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_families_search as S  # noqa: E402
import fade_owner_exits as X  # noqa: E402

T, FEE, SLIP = X.T, X.FEE, 0.0005
YEARS = X.YEARS


def btc_paths(p):
    b = np.load(S.OUT / "btc.npz")
    m = {int(d): pd.Series(c).ffill().to_numpy(dtype=float) for d, c in zip(b["days"], b["close"])}
    out = np.full((len(p), T), np.nan)
    for i, t in enumerate(p.time.to_numpy()):
        c = m.get(int(t))
        if c is not None:
            out[i] = c[:T]
    return out


def signals(p, cl, btc):
    entry = p.open.to_numpy()[:, None]
    atr = (p.dist.to_numpy() / 2)[:, None]
    q = np.zeros_like(cl, dtype=bool)
    q[:, 14::15] = True                                                 # 15-minute closes
    df = pd.DataFrame(cl.T)
    out = {}
    for H in (1, 2, 4):
        run_max = df.rolling(H * 60, min_periods=1).max().shift(1).to_numpy().T
        out[f"newhigh {H}h"] = q & (cl > entry) & (cl >= run_max)
    ma = df.rolling(240, min_periods=60).mean().to_numpy().T
    for k in (0.25, 0.5):
        out[f"above_ma 4h +{k}ATR"] = q & (cl >= ma + k * atr * entry)
    lows = df.rolling(60, min_periods=60).min().to_numpy().T
    hl = (lows > np.roll(lows, 60, axis=1)) & (np.roll(lows, 60, axis=1) > np.roll(lows, 120, axis=1))
    hl[:, :180] = False
    out["higher_lows 3x1h"] = q & hl & (cl > entry)
    b0 = btc[:, [0]]
    out["btc_up 1%"] = q & (btc / b0 - 1 >= 0.01) & (cl > entry)
    ad = np.zeros_like(cl, dtype=bool)
    ad[:, 59] = cl[:, 59] >= entry[:, 0] * (1 + 0.5 * atr[:, 0])
    out["adverse at 1h +0.5ATR"] = ad
    return out


def sim(p, hi, cl, stop_mult, sig=None):
    """Net return per unit notional, then R in units of the 2x-ATR distance."""
    entry, dist = p.open.to_numpy(), p.dist.to_numpy()
    n = len(p)
    stop = entry * (1 + dist * stop_mult / 2)
    s = X.first(hi >= stop[:, None])
    x = np.full(n, T - 1)
    xp = cl[:, T - 1].copy()
    hit = s < T
    x[hit] = s[hit]
    xp[hit] = np.maximum(stop[hit], cl[np.arange(n)[hit], s[hit]])
    if sig is not None:
        m = X.first(sig)
        m1 = np.minimum(m + 1, T - 1)
        early = (m < T - 1) & (m1 < x)
        x = np.where(early, m1, x)
        xp = np.where(early, cl[np.arange(n), m1] * (1 + SLIP), xp)
    ret = -(xp / entry - 1) - FEE - FEE * xp / entry
    return ret / dist


def line(name, r, base, yrs):
    d = r - base
    z = d.mean() / (d.std(ddof=1) / np.sqrt(len(d))) if d.std() > 0 else 0.0
    better = sum(r[yrs == y].mean() > base[yrs == y].mean() for y in YEARS)
    ok = better >= 5 and z >= 2
    print(f"  {name:44s} {r.mean():+.3f}R  win {100 * (r > 0).mean():3.0f}%  worst {r.min():+5.2f}R  diff {d.mean():+.3f}R "
          f"z {z:+5.2f}  better {better}/6 | " + " ".join(f"{r[yrs == y].mean():+.3f}" for y in YEARS) + f"  {'PASS' if ok else 'fail'}")


def main() -> None:
    p, hi, lo, cl = X.load()
    yrs = p.year.to_numpy()
    btc = btc_paths(p)
    base = sim(p, hi, cl, 2)
    print(f"RULE A, Binance 2021-26, n {len(p)}. Baseline 2x-ATR stop, 24 h: {base.mean():+.3f}R, win {100 * (base > 0).mean():.0f}%, "
          f"worst {base.min():+.2f}R | " + " ".join(f"{y} {base[yrs == y].mean():+.3f}" for y in YEARS))
    no_stop = sim(p, hi, cl, 3)
    line("3x-ATR disaster stop only (no 2x stop)", no_stop, base, yrs)
    sig = signals(p, cl, btc)
    print("\n(a) FLIP EXIT ON TOP OF THE 2x-ATR STOP")
    for k, v in sig.items():
        line(k, sim(p, hi, cl, 2, v), base, yrs)
    print("\n(b) FLIP EXIT INSTEAD OF THE 2x-ATR STOP (3x-ATR disaster stop kept)")
    for k, v in sig.items():
        line(k, sim(p, hi, cl, 3, v), base, yrs)


if __name__ == "__main__":
    main()
