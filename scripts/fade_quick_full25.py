"""QUICK as is (2x ATR14% stop, target 0.2x the stop, 24 h) with 25% of capital as margin per pick, on six years of
rule A picks (owner 2026-11-12). Companion of scripts/fade_quick_full_replay.py (paper's own picks).

    PYTHONPATH=. python scripts/fade_quick_full25.py

Binance rule A picks 2021-26 (scripts/fade_owner_exits.load), per-coin leverage from Delta's table, liquidation loses
the margin (scripts/fade_quick_leverage.run, margin=0.25). Ideal fills (00:00 open, touch) and realistic (00:03
close, 0.1% through, 0.1% spread). Leverage 1x, 2x, 3x, 5x, 10x, full.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import fade_owner_exits as X  # noqa: E402
import fade_quick_leverage as Q  # noqa: E402


def main() -> None:
    p0, hi0, lo0, cl = X.load()
    im0, mm0, _ = Q.margins(p0)
    for nm, e, thr, ex in (("IDEAL fills", 0, 0.0, 0.0), ("REALISTIC fills", 3, 0.001, 0.001)):
        if e:
            p = p0.copy()
            p["open"] = cl[:, e - 1]
            hi, lo = hi0.copy(), lo0.copy()
            hi[:, :e], lo[:, :e] = -np.inf, np.inf
        else:
            p, hi, lo = p0, hi0, lo0
        ok = np.isfinite(p.open.to_numpy()) & (p.open.to_numpy() > 0)
        p, hi, lo, c, im, mm = p[ok].reset_index(drop=True), hi[ok], lo[ok], cl[ok], im0[ok], mm0[ok]
        print(f"\n{nm}: QUICK, 25% of capital as margin per pick, from $1,500")
        for lev in (1, 2, 3, 5, 10, "full"):
            rb, liq, L = Q.run(p, hi, lo, c, im, mm, lev, 2, "quick", False, thr, ex, margin=0.25)
            s, dd = Q.account(p, rb)
            ye = s.groupby(s.index.year).last()
            up = int((ye.to_numpy() > np.r_[1500.0, ye.to_numpy()[:-1]]).sum())
            dead = s[s < 1]
            gone = f"  account at $0 by {dead.index[0].date()}" if len(dead) else ""
            print(f"  {str(lev):>4s}{'x' if lev != 'full' else ''}  -> ${s.iloc[-1]:>9,.0f}  worst drop {100 * dd:3.0f}%  "
                  f"liquidated {100 * liq.mean():4.1f}%  win {100 * (rb > 0).mean():3.0f}%  years up {up}/6 | "
                  + " ".join(f"{v:>8,.0f}" for v in ye.to_numpy()) + gone)


if __name__ == "__main__":
    main()
