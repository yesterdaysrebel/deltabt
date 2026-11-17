"""Owner 2026-11-17: "leave 40$ target how about quick with 10% margin 5x leverage, we give it room by adding margin
later, avoid liquidation as well".

QUICK's picks and entry (short the top-2 gainers at the 00:00 UTC close), 10% of the balance as margin at 5x per
trade (position 0.5x the balance: $1,448 at $2,895.60), extra margin posted after the fill so liquidation is far away.
Exits, written before the run:
  A   QUICK as it runs: stop 2 x 14-day ATR% above, take-profit 0.2 x that distance below, out at 23:59
  B27 no stop, no target, out at 23:59; liquidation +27% (the live bot's extra margin) -> loses the posted margin
  B50 the same with liquidation +50% (more extra margin: ~26% of the balance per trade)
  C   the live spike exits: 10% stop, 10% trail from the fill, out after 24 h; liquidation +27% (past the stop)
  D   10% stop, no trail, out at 23:59
Both picks (2 trades a day) and the top pick only. For reference: the live spike rule ("C from the fill", resting sell
5% above) at the same size. Trades from scripts/spike_verify.py trade1 and trade2 (must agree), entry at the 00:00
close (STRICT 0.05% worse), STRICT / LENIENT fills; a liquidation loses the margin posted (liquidation % + 2.5%
maintenance of the position; a STRICT stop fill past +27% is
capped there too). Before fees; fees per trade shown. Account: $2,895.60 compounding, 10% margin per trade.

    PYTHONPATH=. python scripts/quick_room.py      # -> out/careful/quick_room.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
from spike_verify import CELLS, MAKER, TAKER, trade1, trade2  # noqa: E402

BAL, POS = 2895.60, 0.5            # position = 0.5 x the balance (10% margin x 5x)
MM = 0.025


def cells(atrp):
    return {"A  QUICK as it runs (2xATR stop, small target, 23:59)": (dict(entry="x", stop=2 * atrp, base="entry", trail=0.0, be=False, mins=1439, tp=0.4 * atrp), None),
            "B27 no stop, no target, 23:59, liquidation +27%": (dict(entry="x", stop=0.27, base="entry", trail=0.0, be=False, mins=1439, tp=None), 0.27),
            "B50 no stop, no target, 23:59, liquidation +50%": (dict(entry="x", stop=0.50, base="entry", trail=0.0, be=False, mins=1439, tp=None), 0.50),
            "C  10% stop + 10% trail from the fill, 24 h": (dict(entry="x", stop=0.10, base="entry", trail=0.10, be=False, mins=1440, tp=None), None),
            "D  10% stop, 23:59": (dict(entry="x", stop=0.10, base="entry", trail=0.0, be=False, mins=1439, tp=None), None)}


def run(days, atr, key, strict):
    rows = []
    for day, ps in days:
        for rank, sym, H, L, Cl in ps:
            if not np.isfinite(Cl[0]):
                continue
            c, liq = cells(atr[(day, sym)])[key]
            e = Cl[0] * (0.9995 if strict else 1.0)
            ent = (0, e, e, False)
            a, b = trade1(H, L, Cl, c, strict, ent), trade2(H, L, Cl, c, strict, ent)
            assert a[:2] == b[:2] and abs(a[2] - b[2]) < 1e-9 and a[3] == b[3], (key, day, sym, a, b)
            ret, how = a[2], a[3]
            if liq is not None and how == "STOP":
                ret, how = -(liq + MM), "LIQUIDATED"
            elif ret < -(0.27 + MM):                    # a stop filled past the +27% liquidation: the margin posted is the most lost
                ret, how = -(0.27 + MM), "LIQUIDATED"
            fee = TAKER + (0.0 if a[1] <= 15 else (MAKER if how == "TARGET" else TAKER))
            rows.append((day, rank, sym, ret, how, fee))
    return pd.DataFrame(rows, columns=["day", "rank", "sym", "ret", "how", "fee"])


def spike(days, strict):
    rows = []
    for day, ps in days:
        for rank, sym, H, L, Cl in ps:
            a = trade1(H, L, Cl, CELLS["C from the fill"], strict)
            if a:
                ret, how = (a[2], a[3]) if a[2] >= -(0.27 + MM) else (-(0.27 + MM), "LIQUIDATED")
                rows.append((day, rank, sym, ret, how, MAKER + TAKER))
    return pd.DataFrame(rows, columns=["day", "rank", "sym", "ret", "how", "fee"])


def line(d):
    u = d.ret * POS * BAL
    w = u > 0
    hy = " ".join(f"{(d.ret[(d.day >= a) & (d.day < b)] * POS * BAL).mean():+.0f}" for a, b in
                  (("2025-01-01", "2025-07-01"), ("2025-07-01", "2026-01-01"), ("2026-01-01", "2026-07-01"), ("2026-07-01", "2027-01-01")))
    day = d.ret.groupby(d.day).sum()
    t = day.mean() / (day.std(ddof=1) / np.sqrt(len(day)))
    ex = d.how.value_counts(normalize=True)
    return (f"{len(d):4d} trades  win {100 * w.mean():3.0f}%  avg win ${u[w].mean():+4.0f}  avg loss ${u[~w].mean():+5.0f}  per trade ${u.mean():+5.1f} "
            f"(t {t:+.2f}; fees ${(d.fee * POS * BAL).mean():.1f})  worst ${u.min():+5.0f} | half-years {hy} | "
            + " ".join(f"{k} {100 * v:.0f}%" for k, v in ex.items()))


def account(d, fees):
    bal, peak, mdd, lo = BAL, BAL, 0.0, BAL
    for _, g in d.groupby("day"):
        bal += bal * POS * (g.ret - (g.fee if fees else 0)).sum()
        peak, lo = max(peak, bal), min(lo, bal)
        mdd = max(mdd, 1 - bal / peak)
    return f"end ${bal:,.0f}, lowest ${lo:,.0f}, worst fall {100 * mdd:.0f}%"


def main():
    days = D.load()
    P = pd.read_csv("out/careful/picks_atr.csv", parse_dates=["day"])
    atr = {(d, s): a for d, s, a in zip(P.day, P.symbol, P.atrp)}
    out = [__doc__.split("\n\n")[0], f"{days[0][0]:%Y-%m-%d}..{days[-1][0]:%Y-%m-%d}; $ per trade on a ${POS * BAL:,.0f} position (at ${BAL:,.2f}). Before fees.", ""]
    w = out.append
    for key in cells(0.05):
        w(f"===== {key}")
        for fill in ("STRICT", "LENIENT"):
            d = run(days, atr, key, fill == "STRICT")
            top = d[d["rank"] == d.groupby("day")["rank"].transform("min")]
            w(f"  {fill:7} both picks {line(d)}")
            w(f"  {'':7} top pick   {line(top)}")
            if fill == "STRICT":
                w(f"  {'':7} account, both picks: {account(d, False)} | with fees {account(d, True)}")
        w("")
    w("===== REFERENCE: the live spike rule (resting sell 5% above, 10% stop, 10% trail from the fill), same $1,448 position, every fill")
    for fill in ("STRICT", "LENIENT"):
        w(f"  {fill:7} {line(spike(days, fill == 'STRICT'))}")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/quick_room.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
