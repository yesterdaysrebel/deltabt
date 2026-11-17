"""Owner 2026-11-17: "focus on just quick leave everything out of results: run quick with 20% margin 5x leverage, add
margin once filled to move liquidation, add SL, target as it is today".

QUICK only. Short the day's top-2 gainers at the 00:00 UTC close (STRICT 0.05% worse). 20% of the balance as isolated
margin at 5x per trade (position 1x the balance: $2,896 at $2,895.60), then extra margin 25/49 of it (as the live bot)
-> liquidation ~27% above the entry. SL = QUICK's stop, k x 14-day ATR% above the entry (k = 2 as today; 1 and 3 shown
too); target as today = 0.2 x the stop distance below; out at 23:59 UTC. A stop that would fill past liquidation loses
the posted margin (29.5% of the position). Both picks (QUICK today) and the top pick only.
CHECK: the exits must match the paper bot's own QUICK ledger (out/fade_quick_full/quick_paper.json) on most trades.
Fills STRICT / LENIENT as scripts/spike_verify.py; trade1 and trade2 must agree. Before fees; fees per trade shown.
Account: $2,895.60 compounding, 20% margin per trade; lowest point, worst fall, first time under the bot's $1,000 floor;
1,000 random orders of the days -> chance of going under $1,000.

    PYTHONPATH=. python scripts/quick_20m.py      # -> out/careful/quick_20m.txt
    PYTHONPATH=. python scripts/quick_20m.py 0.10  # owner "show me the numbers for 10% at 5x" -> quick_10m.txt
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
from spike_verify import MAKER, TAKER, trade1, trade2  # noqa: E402

BAL, POS, LIQ_LOSS = 2895.60, 1.0, 0.27 + 0.025


def run(days, atr, k, strict):
    rows = []
    for day, ps in days:
        for rank, sym, H, L, Cl in ps:
            if not np.isfinite(Cl[0]):
                continue
            a0 = atr[(day, sym)]
            c = dict(entry="x", stop=k * a0, base="entry", trail=0.0, be=False, mins=1439, tp=0.2 * k * a0)
            e = Cl[0] * (0.9995 if strict else 1.0)
            ent = (0, e, e, False)
            a, b = trade1(H, L, Cl, c, strict, ent), trade2(H, L, Cl, c, strict, ent)
            assert a[:2] == b[:2] and abs(a[2] - b[2]) < 1e-9 and a[3] == b[3], (day, sym, a, b)
            ret, how = a[2], a[3]
            if ret < -LIQ_LOSS:
                ret, how = -LIQ_LOSS, "LIQUIDATED"
            fee = TAKER + (0.0 if a[1] <= 15 else (MAKER if how == "TARGET" else TAKER))
            rows.append((day, rank, sym, ret, how, fee, a[4]))
    return pd.DataFrame(rows, columns=["day", "rank", "sym", "ret", "how", "fee", "dist"])


def line(d):
    u = d.ret * POS * BAL
    w = u > 0
    day = d.ret.groupby(d.day).sum()
    t = day.mean() / (day.std(ddof=1) / np.sqrt(len(day)))
    hy = " ".join(f"{(d.ret[(d.day >= a) & (d.day < b)] * POS * BAL).mean():+.0f}" for a, b in
                  (("2025-01-01", "2025-07-01"), ("2025-07-01", "2026-01-01"), ("2026-01-01", "2026-07-01"), ("2026-07-01", "2027-01-01")))
    mo = (d.ret * POS * BAL).groupby(d.day.dt.strftime("%Y-%m")).sum()
    ex = d.how.value_counts(normalize=True)
    return (f"{len(d):4d} trades  win {100 * w.mean():3.0f}%  avg win ${u[w].mean():+4.0f}  avg loss ${u[~w].mean():+5.0f}  per trade ${u.mean():+5.1f} "
            f"(t {t:+.2f}; fees ${(d.fee * POS * BAL).mean():.1f}, after fees ${((d.ret - d.fee) * POS * BAL).mean():+.1f})  worst ${u.min():+.0f} | "
            f"half-years {hy} | months up {int((mo > 0).sum())}/{len(mo)}, avg month ${mo.mean():+.0f} | " + " ".join(f"{k} {100 * v:.0f}%" for k, v in ex.items()))


def path(groups, fees):
    bal, peak, mdd, lo, under = BAL, BAL, 0.0, BAL, None
    for day, g in groups:
        bal += bal * POS * (g.ret - (g.fee if fees else 0)).sum()
        peak, lo = max(peak, bal), min(lo, bal)
        mdd = max(mdd, 1 - bal / peak)
        if under is None and bal < 1000:
            under = day
    return bal, lo, mdd, under


def account(d, rng):
    g = list(d.groupby("day"))
    out = []
    for fees in (False, True):
        bal, lo, mdd, under = path(g, fees)
        odds = np.mean([path([g[i] for i in rng.permutation(len(g))], fees)[3] is not None for _ in range(1000)])
        out.append(f"{'with fees' if fees else 'before fees'}: end ${bal:,.0f}, lowest ${lo:,.0f}, worst fall {100 * mdd:.0f}%"
                   + (f", UNDER $1,000 {under:%Y-%m-%d}" if under is not None else ", never under $1,000") + f", random orders under $1,000 {100 * odds:.0f}%")
    return " | ".join(out)


def main():
    global POS
    marg = float(sys.argv[1]) if len(sys.argv) > 1 else 0.20
    POS = 5 * marg
    days = D.load()
    P = pd.read_csv("out/careful/picks_atr.csv", parse_dates=["day"])
    atr = {(d, s): a for d, s, a in zip(P.day, P.symbol, P.atrp)}
    rng = np.random.default_rng(20261117)
    out = [__doc__.split("\n\n")[0], f"MARGIN {marg:.0%} at 5x. {days[0][0]:%Y-%m-%d}..{days[-1][0]:%Y-%m-%d}. $ per trade on a ${POS * BAL:,.0f} position. Before fees unless said.", ""]
    w = out.append
    q = run(days, atr, 2.0, True)
    paper = {(pd.Timestamp(x["day"][:10]), x["symbol"]): x["exit_reason"] for x in json.load(open("out/fade_quick_full/quick_paper.json")) if x.get("exit_reason")}
    mine = {(x.day, x.sym): x.how for x in q.itertuples()}
    both = [k for k in paper if k in mine]
    same = sum(1 for k in both if {"TARGET": "TARGET", "STOP": "STOP"}.get(paper[k], "CLOCK") == {"LIQUIDATED": "STOP"}.get(mine[k], mine[k]))
    w(f"CHECK vs the paper bot's own QUICK ledger: {same} of {len(both)} shared trades end the same way (TARGET / STOP / end of day)")
    w(f"   median stop distance {100 * q.dist.median():.1f}% (target {100 * 0.2 * q.dist.median():.1f}%) -> on the ${POS * BAL:,.0f} position: stop ~${q.dist.median() * POS * BAL:.0f}, target ~${0.2 * q.dist.median() * POS * BAL:.0f}")
    w("")
    for k in (2.0, 1.0, 3.0):
        w(f"===== SL {k:.0f} x ATR{'  (QUICK today)' if k == 2.0 else ''}, target 0.2 x the stop distance, out 23:59")
        for fill in ("STRICT", "LENIENT"):
            d = q if (k == 2.0 and fill == "STRICT") else run(days, atr, k, fill == "STRICT")
            top = d[d["rank"] == d.groupby("day")["rank"].transform("min")]
            w(f"  {fill:7} both picks {line(d)}")
            w(f"  {'':7} top pick   {line(top)}")
            if fill == "STRICT":
                w(f"  {'':7} account both picks: {account(d, rng)}")
                w(f"  {'':7} account top pick:   {account(top, rng)}")
        w("")
    txt = "\n".join(out)
    print(txt)
    Path(f"out/careful/quick_{round(marg * 100)}m.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
