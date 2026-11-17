"""Owner 2026-11-17: "with target closer the SL should not hit right? let's say with 50% margin SL sitting right above
entry we hit target".

Grid written before the run. The live entry (resting sell 5% above the 00:02 close), 50% margin at 5x = a $7,195
position at today's $2,895.60 (2.5x the balance); take-profit $40 (0.56% below the entry) or $45 (0.63%); stop S above
the entry, S = 0.25, 0.5, 0.75, 1, 1.5, 2, 3%; no trail; out after 24 h. Liquidation (~27% above) is past every stop.
  * every fill: STRICT and LENIENT (scripts/spike_verify.py fills; trade1 and trade2 must agree on every trade);
    "needed" = the win rate at which wins pay for losses at that stop (before fees)
  * CONTROL: the same exit after a short at a random minute of the same days (does selling the spike help at all?)
  * the account: the bot's day rules (scripts/spike_high_margin_check.py), $2,895.60 compounding at 50% margin,
    5,000 random orders -> chance of going under the bot's $1,000 floor.
Before fees, with one line for fees.

    PYTHONPATH=. python scripts/spike_tight_stop.py      # -> out/careful/spike_tight_stop.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
from spike_high_margin_check import live_select, path  # noqa: E402
from spike_verify import MAKER, TAKER, trade1, trade2  # noqa: E402

M, N = 0.50, 2878.0 * 0.50 / 0.20
STOPS, USDS = (0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03), (40.0, 45.0)


def fills(days, c, strict):
    rows = []
    for day, ps in days:
        for rank, sym, H, L, Cl in ps:
            a, b = trade1(H, L, Cl, c, strict), trade2(H, L, Cl, c, strict)
            assert (a is None) == (b is None) and (a is None or (a[:2] == b[:2] and abs(a[2] - b[2]) < 1e-9 and a[3] == b[3])), (day, sym, a, b)
            if a:
                k, j, ret, how, _ = a
                rows.append(dict(day=day, rank=rank, sym=sym, k=k, j=j, tin=day + pd.Timedelta(minutes=k),
                                 tout=day + pd.Timedelta(minutes=j), ret=ret, how=how))
    return pd.DataFrame(rows)


def control(days, c, rng):
    r = []
    for day, ps in days:
        for rank, sym, H, L, Cl in ps:
            ok = np.flatnonzero(np.isfinite(Cl[3:1440]) & np.isfinite(H[3:1440])) + 3
            for k in rng.choice(ok, size=3):
                e = Cl[int(k)] * 0.9995
                r.append(trade1(H, L, Cl, c, True, ent=(int(k), e, e, False))[2])
    return np.array(r)


def main():
    days = D.load()
    rng = np.random.default_rng(20261117)
    out = [__doc__.split("\n\n")[0], f"Position ${N:,.0f}; $40 = {100 * 40 / N:.2f}%, $45 = {100 * 45 / N:.2f}% below the entry. $ per trade at today's balance.", ""]
    w = out.append
    for usd in USDS:
        tp = usd / N
        w(f"===== take-profit ${usd:.0f} ({100 * tp:.2f}%)")
        for s in STOPS:
            c = dict(entry="live", stop=s, base="entry", trail=0.0, be=False, mins=1440, tp=tp)
            S, Ln = fills(days, c, True), fills(days, c, False)
            u = S.ret * N
            win = u > 0
            need = s / (s + tp)
            same_min = 100 * ((S.how == "STOP") & (S.j == S.k)).mean()
            first5 = 100 * ((S.how == "STOP") & (S.j - S.k <= 5)).mean()
            fee = N * np.where(S.how == "TARGET", MAKER + MAKER, MAKER + TAKER)
            fee = np.where(S.j - S.k <= 15, N * MAKER, fee)
            tk = live_select(S).ret.to_numpy()
            under = np.mean([path(rng.permutation(tk), M)[3] is not None for _ in range(2000)])
            ctrl = control(days, c, rng)
            w(f"  stop {100 * s:4.2f}% (${N * s:4.0f}):  STRICT win {100 * win.mean():3.0f}% (needed ~{100 * need:.0f}%)  avg win ${u[win].mean():+4.0f}  "
              f"avg loss ${u[~win].mean():+5.0f}  per trade ${u.mean():+6.1f} | LENIENT win {100 * (Ln.ret > 0).mean():3.0f}% per trade ${N * Ln.ret.mean():+6.1f} "
              f"| stopped in the fill minute {same_min:.0f}%, within 5 min {first5:.0f}% | CONTROL ${N * ctrl.mean():+6.1f} "
              f"| bot rules: under $1,000 in {100 * under:.0f}% of orders | fees ${fee.mean():.1f}/trade")
        w("")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/spike_tight_stop.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
