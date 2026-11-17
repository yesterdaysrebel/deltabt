"""Owner 2026-11-17: "your numbers are wrong -- I ran it with delta exchange calculator for most recent trades:
Quick CROSSUSD 8106 lots = 81060 with 20x leverage, 17 Nov 5:30 AM, 0.14726 0.164682 0.143776, reached profit of more
than 200$; same for many more".

Checks the real picks 2026-10-29..11-17 (the paper runner's own, scripts/real_picks_rule_c.py) minute by minute with
Delta's 1m candles: QUICK short at the 00:00 UTC close (05:30 IST), the owner's size (notional = 4.12x the balance,
i.e. CROSS 81,060 at 0.14726 = $11,937 on $2,895.60) and the size tested before (10% margin x 20x = 2x the balance).
At 20x isolated, Delta liquidates ~2.5% above the entry (5% initial - 2.5% maintenance margin), losing the margin.
For each pick: the best profit of the day and WHEN; the highest price BEFORE that and whether it passed liquidation;
when a $40 target is first touched and whether liquidation came first. Touch prices, no slippage, before fees.

    PYTHONPATH=. python scripts/quick_recent_check.py      # -> out/careful/quick_recent_check.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import real_picks_rule_c as R  # noqa: E402

BAL = 2895.60
SIZES = {"owner's size (4.1x)": 81060 * 0.14726 / BAL, "tested size (2x)": 2.0}
LIQ = 0.025


def hm(day, m):
    return (pd.Timestamp(day) + pd.Timedelta(minutes=int(m))).strftime("%H:%M")


def main():
    out = [__doc__.split("\n\n")[0], ""]
    w = out.append
    for nm, mult in SIZES.items():
        notional = mult * BAL
        tp = 40 / notional
        w(f"===== {nm}: position ${notional:,.0f}, margin at 20x ${notional / 20:,.0f}; $40 = a {100 * tp:.2f}% fall; liquidation +{100 * LIQ:.1f}% "
          f"(loses ${notional / 20:,.0f})")
        w(f"  {'day':10} {'coin':12} {'entry':>9} | {'best profit':>11} at  | {'high before it':>14} | {'$40 reached':>11} | {'high before $40':>15} | result with a $40 target")
        res = []
        for day, rank, sym, H, L, Cl in R.picks():
            if not np.isfinite(Cl[0]):
                continue
            e = Cl[0]
            ok = np.isfinite(H[1:1440])
            hh = np.where(ok, H[1:1440], -np.inf)
            ll = np.where(ok, L[1:1440], np.inf)
            jb = int(np.argmin(ll))
            best = (e - ll[jb]) / e * notional
            hi_before_best = hh[:jb + 1].max() / e - 1
            liq_m = np.flatnonzero(hh >= e * (1 + LIQ))
            tgt_m = np.flatnonzero(ll <= e * (1 - tp))
            jl = int(liq_m[0]) if liq_m.size else None
            jt = int(tgt_m[0]) if tgt_m.size else None
            hi_before_t = hh[:jt + 1].max() / e - 1 if jt is not None else np.nan
            if jt is not None and (jl is None or jt < jl):
                r, how = 40.0, f"+$40 at {hm(day, jt + 1)}"
            elif jl is not None and (jt is None or jl <= jt):
                r, how = -notional / 20, f"LIQUIDATED at {hm(day, jl + 1)} (-${notional / 20:,.0f})" + (" [same minute as $40: order unknown]" if jt == jl else "")
            else:
                x = Cl[:1440][np.isfinite(Cl[:1440])][-1]
                r, how = (e - x) / e * notional, "neither: out at 23:59"
            res.append(r)
            w(f"  {day:10} {sym:12} {e:>9.6g} | ${best:>+9,.0f} {hm(day, jb + 1)} | {100 * hi_before_best:>+12.1f}% | "
              f"{(hm(day, jt + 1) if jt is not None else 'never'):>11} | {100 * hi_before_t:>+14.1f}% | {how}")
        r = np.array(res)
        w(f"  -> {len(r)} picks: {int((r == 40).sum())} made $40, {int((r < -100).sum())} liquidated, total ${r.sum():+,.0f}, per trade ${r.mean():+.1f}")
        w("")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/quick_recent_check.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
