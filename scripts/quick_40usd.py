"""Owner 2026-11-17: "if we run quick with 50% margin and make 40$ per trade that is possible".

QUICK's picks and entry (short the day's top-2 gainers at the 00:00 close), 50% of today's $2,895.60 as isolated
margin per trade at leverage L = 2, 3, 5 (position = 0.5 x L x balance), a take-profit of exactly $40 on that position
(a 40 / position % fall), QUICK's stop (2 x 14-day ATR% above the entry) or liquidation if nearer, out at 23:59 UTC.
Lock: none, or break-even after a 0.5% move (the best earlier cell, scripts/quick_margin50.py).
  both picks  2 trades a day, 50% each (the whole balance posted)
  top pick    rank 1 only (50% posted)
The two implementations of scripts/quick_margin50.py (minute loop / arrays) must agree on every trade. Before fees,
then with fees (taker in, maker at the target, taker otherwise; exits inside 15 min free).
Account: $2,895.60 compounding, the target stays $40 x balance / $2,895.60; first time under the bot's $1,000 floor;
and 2,000 random orders of the trading days -> chance of going under $1,000.

    PYTHONPATH=. python scripts/quick_40usd.py      # -> out/careful/quick_40usd.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import quick_margin50 as Q  # noqa: E402

BAL, USD = 2895.60, 40.0
Q.START, Q.MARGIN = BAL, 0.50


def day_path(byday, fees, order=None):
    bal, peak, mdd, lo, under = BAL, BAL, 0.0, BAL, None
    seq = byday if order is None else [byday[i] for i in order]
    for i, (day, g) in enumerate(seq):
        b0 = bal
        bal += b0 * (g.acct - (g.fee if fees else 0.0)).sum()
        peak, lo = max(peak, bal), min(lo, bal)
        mdd = max(mdd, 1 - bal / peak) if peak > 0 else 1.0
        if under is None and bal < 1000:
            under = day
            break
    return bal, lo, mdd, under


def main():
    days = D.load()
    P = pd.read_csv("out/careful/picks_atr.csv", parse_dates=["day"])
    atr = {(d, s): a for d, s, a in zip(P.day, P.symbol, P.atrp)}
    rng = np.random.default_rng(20261117)
    out = [__doc__.split("\n\n")[0], f"{days[0][0]:%Y-%m-%d}..{days[-1][0]:%Y-%m-%d}. $ at ${BAL:,.2f}.", ""]
    w = out.append
    for lev in (2, 3, 5):
        pos = Q.MARGIN * lev * BAL
        Q.TP = USD / pos
        w(f"===== {lev}x: position ${pos:,.0f} ({0.5 * lev:.1f}x the balance); $40 = a {100 * Q.TP:.2f}% fall; liquidation ~{100 * (1 / lev - 0.025):.0f}% above")
        for lock in (None, 0.005):
            for fill in ("STRICT", "LENIENT"):
                d = Q.run(days, atr, lev, lock, fill == "STRICT")     # asserts the two implementations agree
                for nm, sub in (("both picks", d), ("top pick", d[d.groupby("day").cumcount() == 0])):
                    usd = sub.acct * BAL
                    win = usd > 0
                    feeusd = (sub.fee * BAL).mean()
                    hy = " ".join(f"{(sub.acct[(sub.day >= a) & (sub.day < b)] * BAL).mean():+.0f}" for a, b in
                                  (("2025-01-01", "2025-07-01"), ("2025-07-01", "2026-01-01"), ("2026-01-01", "2026-07-01"), ("2026-07-01", "2027-01-01")))
                    ex = sub.how.value_counts(normalize=True)
                    lab = f"{'no lock' if lock is None else 'lock 0.5%'} {fill:7} {nm:10}"
                    w(f"  {lab} {len(sub)} trades  win {100 * win.mean():3.0f}%  avg win ${usd[win].mean():+4.0f}  avg loss ${usd[~win].mean():+5.0f}  "
                      f"per trade ${usd.mean():+5.1f} (fees ${feeusd:.1f}) | half-years {hy} | " + " ".join(f"{k} {100 * v:.0f}%" for k, v in ex.items()))
                    if fill == "STRICT":
                        byday = list(sub.groupby("day"))
                        for fees in (False, True):
                            bal, lo, mdd, under = day_path(byday, fees)
                            odds = np.mean([day_path(byday, fees, rng.permutation(len(byday)))[3] is not None for _ in range(2000)])
                            w(f"  {'':{len(lab)}}  account{' + fees' if fees else '       '}: end ${bal:,.0f}, lowest ${lo:,.0f}, worst fall {100 * mdd:.0f}%"
                              + (f", UNDER $1,000 {under:%Y-%m-%d}" if under is not None else ", never under $1,000")
                              + f" | random orders: under $1,000 in {100 * odds:.0f}%")
        w("")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/quick_40usd.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
