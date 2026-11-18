"""Owner 2026-11-18 (after MMT went ~$178 in profit and the trail closed it at -$99.6): "I would try to increase margin,
book 20-30$".

The live spike rule ("C from the fill": resting sell 5% above the 00:02 close, stop 10% above the entry, 10% trail from
the fill, out after 24 h) PLUS a resting take-profit at $20 / $25 / $30, at 10% / 20% / 30% / 50% margin x 5x
(position 0.5x / 1x / 1.5x / 2.5x the balance; fixed $ size at today's balance, no compounding, so $ targets stay $).
"none" = the rule as it runs. Liquidation ~27% above (extra margin 25/49) -> loses 30.2% of the position.
History: the bot's own day rules (scripts/spike_high_margin_check.live_select: one trade a day, first fill wins, no
orders while a position is open at 00:00). trade1 and trade2 must agree. STRICT / LENIENT fills as spike_verify.
Real: the 13 trades on the real picks 10-29..11-17 (scripts/spike_usd_target.real). Before fees; then after
(maker in, maker at the target, taker otherwise).

    PYTHONPATH=. python scripts/spike_small_usd.py      # -> out/careful/spike_small_usd.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import real_picks_rule_c as R  # noqa: E402
import spike_high_margin_check as K  # noqa: E402
import spike_usd_target as S  # noqa: E402
from spike_verify import CELLS, MAKER, TAKER, trade1, trade2  # noqa: E402

C = CELLS["C from the fill"]
MARGINS = (0.10, 0.20, 0.30, 0.50)
USD = (None, 20.0, 25.0, 30.0)
N20 = 2878.0


def trades(days, n, usd, strict):
    c = C if usd is None else dict(C, tp=usd / n)
    rows = []
    for day, ps in days:
        for rank, sym, H, L, Cl in ps:
            a, b = trade1(H, L, Cl, c, strict), trade2(H, L, Cl, c, strict)
            if a is None:
                assert b is None
                continue
            assert a[:2] == b[:2] and abs(a[2] - b[2]) < 1e-9 and a[3] == b[3], (day, sym, a, b)
            k, j, ret, how, _ = a
            e = Cl[2] * 1.05
            if np.nanmax(H[k:j + 1]) >= e * (1 + K.LIQ_UP) and ret < -K.LIQ_LOSS:
                ret, how = -K.LIQ_LOSS, "LIQUIDATED"
            rows.append(dict(day=day, rank=rank, sym=sym, tin=day + pd.Timedelta(minutes=k), tout=day + pd.Timedelta(minutes=j), ret=ret, how=how))
    return K.live_select(pd.DataFrame(rows))


def line(ret, how, n):
    u = ret * n
    f = np.where(how == "TARGET", MAKER + MAKER, MAKER + TAKER) * n
    w = u > 0
    cum = np.cumsum(u - f)
    dd = (np.maximum.accumulate(np.r_[0, cum])[1:] - cum).max()
    return (f"{len(u):4d} trades  win {100 * w.mean():3.0f}%  avg win ${u[w].mean():+5.0f}  avg loss ${u[~w].mean():+5.0f}  "
            f"per trade ${u.mean():+6.1f} (after fees ${(u - f).mean():+6.1f})  total after fees ${(u - f).sum():+7,.0f}  "
            f"worst ${u.min():+5.0f}  worst run ${-dd:,.0f}")


def main():
    days = D.load()
    out = [__doc__.split("\n\n")[0], ""]
    w = out.append
    S.PICKS = R.picks()
    for m in MARGINS:
        n = 5 * m * N20
        S.NOTIONAL = n
        w(f"===== MARGIN {m:.0%}: position ${n:,.0f}; $20 = {100 * 20 / n:.2f}% fall, $30 = {100 * 30 / n:.2f}%; the 10% stop ~ -${0.1 * n:,.0f} + slippage")
        for fill in ("STRICT", "LENIENT"):
            st = fill == "STRICT"
            S.USD = tuple(u for u in USD if u)
            r = S.real(st)
            for usd in USD:
                d = trades(days, n, usd, st)
                lab = "as now " if usd is None else f"TP ${usd:.0f} "
                w(f"  {fill:7} {lab} history {line(d.ret.values, d.how.values, n)}")
                rr = r.ret.values if usd is None else r[f"tp{usd:.0f}"].values
                rh = np.where(rr >= usd / n - 1e-12, "TARGET", "x") if usd else np.array(["x"] * len(rr))
                w(f"  {'':7} {'':7} real    {line(rr, rh, n)}")
        w("")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/spike_small_usd.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
