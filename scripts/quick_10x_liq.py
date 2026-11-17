"""Owner 2026-11-17 "numbers for 10% at 10x leverage": QUICK (today's SL / target / 23:59), 10% margin x 10x = 1x the
balance per pick, only the live bot's extra margin (25/49) -> liquidation ~12.6% above, inside QUICK's stop on most picks.
STRICT fills; trade1 and trade2 must agree.  PYTHONPATH=. python scripts/quick_10x_liq.py"""
import sys; sys.path.insert(0, "scripts")
import numpy as np, pandas as pd, careful_data as D
from spike_verify import trade1, trade2, MAKER, TAKER
BAL, POS = 2895.60, 1.0                 # 10% margin x 10x = 1x the balance per pick
MARGIN = 0.10 * (1 + 25 / 49)           # + the live bot's extra margin -> 15.1% of the balance per pick
LIQ = MARGIN / POS - 0.025              # liquidation ~12.6% above
days = D.load()
P = pd.read_csv("out/careful/picks_atr.csv", parse_dates=["day"])
atr = {(d, s): a for d, s, a in zip(P.day, P.symbol, P.atrp)}
rows = []
for day, ps in days:
    for rank, sym, H, L, Cl in ps:
        if not np.isfinite(Cl[0]):
            continue
        st = 2 * atr[(day, sym)]
        c = dict(entry="x", stop=min(st, LIQ), base="entry", trail=0.0, be=False, mins=1439, tp=0.2 * st)
        e = Cl[0] * 0.9995
        a, b = trade1(H, L, Cl, c, True, (0, e, e, False)), trade2(H, L, Cl, c, True, (0, e, e, False))
        assert a[:2] == b[:2] and abs(a[2] - b[2]) < 1e-9 and a[3] == b[3]
        ret, how = a[2], a[3]
        if how == "STOP" and (st >= LIQ or ret < -MARGIN):
            ret, how = -MARGIN, "LIQUIDATED"
        fee = TAKER + (0.0 if a[1] <= 15 else (MAKER if how == "TARGET" else TAKER))
        rows.append((day, rank, ret, how, fee))
d = pd.DataFrame(rows, columns=["day", "rank", "ret", "how", "fee"])
print(f"liquidation {100*LIQ:.1f}% above; QUICK's stop is past it on {100*(2*pd.Series([atr[k] for k in atr]) >= LIQ).mean():.0f}% of picks")
rng = np.random.default_rng(1)
for nm, x in (("both picks", d), ("top pick", d[d["rank"] == d.groupby("day")["rank"].transform("min")])):
    u = x.ret * POS * BAL; w = u > 0
    after = ((x.ret - x.fee) * POS * BAL).mean()
    hy = " ".join(f"{(x.ret[(x.day >= a) & (x.day < b)] * POS * BAL).mean():+.0f}" for a, b in (("2025-01-01","2025-07-01"),("2025-07-01","2026-01-01"),("2026-01-01","2026-07-01"),("2026-07-01","2027-01-01")))
    print(f"STRICT {nm}: {len(x)} trades, win {100*w.mean():.0f}%, avg win ${u[w].mean():+.0f}, avg loss ${u[~w].mean():+.0f}, per trade ${u.mean():+.1f}, after fees ${after:+.1f}, half-years {hy}; "
          + ", ".join(f"{k} {100*v:.0f}%" for k, v in x.how.value_counts(normalize=True).items()))
    g = [(dd, gg) for dd, gg in x.groupby("day")]
    def path(order):
        bal, peak, mdd, lo = BAL, BAL, 0.0, BAL
        for i in order:
            bal += bal * POS * (g[i][1].ret - g[i][1].fee).sum()
            peak, lo = max(peak, bal), min(lo, bal); mdd = max(mdd, 1 - bal / peak)
        return bal, lo, mdd
    bal, lo, mdd = path(range(len(g)))
    under = np.mean([path(rng.permutation(len(g)))[1] < 1000 for _ in range(1000)])
    print(f"   account with fees: end ${bal:,.0f}, lowest ${lo:,.0f}, worst fall {100*mdd:.0f}%; random orders under $1,000 {100*under:.0f}%")
