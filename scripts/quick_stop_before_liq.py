"""Owner 2026-11-17: "so avoid liquidations, cut losses it's simple dumb".

QUICK (short the top-2 gainers at the 00:00 UTC close) at the owner's size (CROSS 81,060 x 0.14726 = $11,937 = 4.12x
the $2,895.60 balance, 20x isolated: liquidation ~2.5% above), a $40 take-profit (a 0.34% fall), and a STOP below
liquidation: 0.5%, 1%, 1.5%, 2% above the entry. Out at 23:59 UTC otherwise. Also at the 2x size tested before.
History 2025-01..2026-11 (both picks) and the 26 real picks 2026-10-29..11-17. Fills as scripts/quick_margin50.py
(STRICT: stop at the worse of the stop and the minute's close +0.1%, target passed by 0.1%, stop wins a shared minute;
LENIENT: at the levels); its two implementations (sim1, sim2) must agree. Before fees, then fees per trade.

    PYTHONPATH=. python scripts/quick_stop_before_liq.py      # -> out/careful/quick_stop_before_liq.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import quick_margin50 as Q  # noqa: E402
import real_picks_rule_c as R  # noqa: E402
from spike_verify import MAKER, TAKER  # noqa: E402

BAL = 2895.60
SIZES = {"owner's size $11,937": 81060 * 0.14726, "2x size $5,791": 2 * BAL}
STOPS = (0.005, 0.01, 0.015, 0.02)


def one(H, L, Cl, s, strict):
    e = Cl[0] * (0.9995 if strict else 1.0)
    a = Q.sim1(H, L, Cl, e, e * (1 + s), False, None, strict)
    b = Q.sim2(H, L, Cl, e, e * (1 + s), False, None, strict)
    assert a[0] == b[0] and a[2] == b[2] and abs(a[1] - b[1]) < 1e-9, (a, b)
    return a


def stats(rows, notional):
    r = np.array([x[1] for x in rows]) * notional
    how = pd.Series([x[2] for x in rows]).value_counts(normalize=True)
    fee = np.mean([notional * (TAKER + (0 if x[0] <= 15 else (MAKER if x[2] == "TARGET" else TAKER))) for x in rows])
    w = r > 0
    return (f"{len(r):4d} trades  win {100 * w.mean():3.0f}%  avg win ${r[w].mean():+4.0f}  avg loss ${r[~w].mean():+5.0f}  "
            f"per trade ${r.mean():+6.1f}  total ${r.sum():+8,.0f}  (fees ${fee:.1f}/trade) | "
            + " ".join(f"{k} {100 * v:.0f}%" for k, v in how.items()))


def main():
    days = D.load()
    recent = [(day, H, L, Cl) for day, rank, sym, H, L, Cl in R.picks() if np.isfinite(Cl[0])]
    out = [__doc__.split("\n\n")[0], ""]
    w = out.append
    for nm, notional in SIZES.items():
        Q.TP = 40 / notional
        w(f"===== {nm}: $40 = a {100 * Q.TP:.2f}% fall")
        for s in STOPS:
            w(f"  stop {100 * s:.1f}% above (${notional * s:,.0f} + slippage); win rate needed at the stop {100 * s / (s + Q.TP):.0f}%")
            for fill in ("STRICT", "LENIENT"):
                st = fill == "STRICT"
                hist = [one(H, L, Cl, s, st) for day, ps in days for rank, sym, H, L, Cl in ps if np.isfinite(Cl[0])]
                rec = [one(H, L, Cl, s, st) for day, H, L, Cl in recent]
                w(f"    {fill:7} history {stats(hist, notional)}")
                w(f"    {'':7} recent  {stats(rec, notional)}")
        w("")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/quick_stop_before_liq.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
