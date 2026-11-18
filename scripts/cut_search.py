"""Owner 2026-11-18: "best bet I see here is high margin quick exit with SL target for 40-50$ per trade, we cut the
trade if it goes against us, when to cut is your search".

Search, written before the run:
  ENTRY   SPIKE = the live entry (resting sell 5% above the 00:02 close; the bot's day rules: one trade a day, first fill
          wins, no orders while a position is open at 00:00), out after 24 h at the latest;
          QUICK = short both top-2 gainers at the 00:00 UTC close (STRICT 0.05% worse), out at 23:59 at the latest.
  SIZE    20% / 30% / 50% margin x 5x (position 1x / 1.5x / 2.5x today's $2,895.60 -> $2,878 / $4,317 / $7,195; fixed).
  TARGET  $40 / $50 (resting take-profit).
  CUT     stop 0.5 / 1 / 1.5 / 2 / 3 / 4 / 5 / 7 / 10% above the entry (fixed, no trail), and optionally a time cut:
          out at market if the target has not filled within 15 / 30 / 60 / 120 / 240 minutes (or no time cut).
HONESTY: the cut is CHOSEN on 2025 only (best STRICT $ per trade before fees); 2026-01..11-14 is never looked at when
choosing and is reported as the test. 270 cuts per entry x size x target, so the best 2025 number is flattered.
Fills: STRICT = stop at the worse of the stop and the minute's close +0.1%, target passed by 0.1%, stop wins a shared
minute, market exits 0.05% worse; LENIENT = at the levels. Liquidation (~27% above) caps a loss at 30.2% of the position.
Array implementation for the search; a minute-loop implementation must agree on every trade of every chosen cut and of
25 random cuts. Before fees; fees: SPIKE maker in, QUICK taker in; maker at the target, taker otherwise.

    PYTHONPATH=. python scripts/cut_search.py      # -> out/careful/cut_search.txt (+ cut_search_grid.csv)
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
from spike_verify import MAKER, TAKER, entry1  # noqa: E402

BAL, N20 = 2895.60, 2878.0
MARGINS = (0.20, 0.30, 0.50)
USD = (40.0, 50.0)
STOPS = (0.005, 0.01, 0.015, 0.02, 0.03, 0.04, 0.05, 0.07, 0.10)
TCUT = (None, 15, 30, 60, 120, 240)
LIQ_LOSS = (1 + 25 / 49) / 5
SPLIT = pd.Timestamp("2026-01-01")


def prep(days, kind, strict):
    """per candidate trade: entry minute, price, whether it filled inside the minute, and the arrays"""
    out = []
    for day, ps in days:
        for rank, sym, H, L, Cl in ps:
            if kind == "SPIKE":
                en = entry1(H, Cl, "live", strict)
                if en is None:
                    continue
                k, e, _, inside = en
                mins = 1440
            else:
                if not np.isfinite(Cl[0]):
                    continue
                k, e, inside, mins = 0, Cl[0] * (0.9995 if strict else 1.0), False, 1439
            last = min(len(Cl) - 1, k + mins)
            idx = np.arange(k + 1, last + 1)
            ok = np.isfinite(H[idx])
            out.append(dict(day=day, rank=rank, sym=sym, k=k, e=e, inside=inside, last=last, H=H, L=L, Cl=Cl,
                            hh=np.where(ok, H[idx], -np.inf), ll=np.where(ok, L[idx], np.inf)))
    return out


def sim_arr(t, s, tp, T, strict):
    k, e, H, Cl = t["k"], t["e"], t["H"], t["Cl"]
    sp = e * (1 + s)
    if t["inside"] and H[k] >= sp:
        return k, -((max(sp, Cl[k]) * 1.001 if strict else sp) / e - 1), "STOP"
    last = t["last"] if T is None else min(t["last"], k + T)
    n = last - k
    hh, ll = t["hh"][:n], t["ll"][:n]
    a = np.flatnonzero(hh >= sp)
    b = np.flatnonzero(ll <= e * (1 - tp) * (0.999 if strict else 1.0))
    ts, ta = (a[0] if a.size else None), (b[0] if b.size else None)
    if ta is not None and (ts is None or ta < ts or (ta == ts and not strict)):
        return k + 1 + ta, tp, "TARGET"
    if ts is not None:
        j = k + 1 + ts
        return j, -((max(sp, Cl[j]) * 1.001 if strict else sp) / e - 1), "STOP"
    fin = np.flatnonzero(np.isfinite(Cl[k:last + 1]))
    x = Cl[k + fin[-1]]
    return last, -((x * 1.0005 if strict else x) / e - 1), "CUT" if T is not None and last == k + T else "CLOCK"


def sim_loop(t, s, tp, T, strict):
    k, e, H, L, Cl = t["k"], t["e"], t["H"], t["L"], t["Cl"]
    sp = e * (1 + s)
    tl = e * (1 - tp) * (0.999 if strict else 1.0)
    if t["inside"] and H[k] >= sp:
        return k, -((max(sp, Cl[k]) * 1.001 if strict else sp) / e - 1), "STOP"
    last = t["last"] if T is None else min(t["last"], k + T)
    x = None
    for j in range(k, last + 1):
        if np.isfinite(Cl[j]):
            x = Cl[j]
        if j == k or not np.isfinite(H[j]):
            continue
        hs, ht = H[j] >= sp, L[j] <= tl
        if hs and (strict or not ht):
            return j, -((max(sp, Cl[j]) * 1.001 if strict else sp) / e - 1), "STOP"
        if ht:
            return j, tp, "TARGET"
    return last, -((x * 1.0005 if strict else x) / e - 1), "CUT" if T is not None and last == k + T else "CLOCK"


def run(trades, kind, s, tp, T, strict, check=False):
    rows = []
    for t in trades:
        j, ret, how = sim_arr(t, s, tp, T, strict)
        if check:
            b = sim_loop(t, s, tp, T, strict)
            assert b[0] == j and abs(b[1] - ret) < 1e-9 and b[2] == how, (t["day"], t["sym"], s, tp, T, (j, ret, how), b)
        if ret < -LIQ_LOSS:
            ret, how = -LIQ_LOSS, "LIQUIDATED"
        fee = (MAKER if kind == "SPIKE" else TAKER) + (MAKER if how == "TARGET" else TAKER)
        rows.append((t["day"], t["rank"], t["sym"], t["day"] + pd.Timedelta(minutes=t["k"]), t["day"] + pd.Timedelta(minutes=int(j)), ret, fee, how))
    d = pd.DataFrame(rows, columns=["day", "rank", "sym", "tin", "tout", "ret", "fee", "how"])
    if kind == "SPIKE":
        keep, open_until = [], pd.Timestamp.min
        for day, g in d.sort_values(["day", "tin", "rank"]).groupby("day", sort=True):
            if open_until >= day:
                continue
            keep.append(g.index[0])
            open_until = g.iloc[0].tout
        d = d.loc[keep]
    return d


def line(d, n):
    u, f = d.ret.values * n, d.fee.values * n
    w = u > 0
    cum = np.cumsum(u - f)
    dd = (np.maximum.accumulate(np.r_[0, cum])[1:] - cum).max() if len(u) else 0
    day = d.groupby("day").ret.sum()
    tstat = day.mean() / (day.std(ddof=1) / np.sqrt(len(day))) if len(day) > 2 else np.nan
    ex = d.how.value_counts(normalize=True)
    return (f"{len(u):4d} trades  win {100 * w.mean():3.0f}%  avg win ${u[w].mean():+4.0f}  avg loss ${u[~w].mean():+5.0f}  per trade ${u.mean():+6.1f} "
            f"(t {tstat:+.1f}; after fees ${(u - f).mean():+6.1f})  total ${u.sum():+8,.0f}  worst run ${-dd:,.0f} | "
            + " ".join(f"{k} {100 * v:.0f}%" for k, v in ex.items()))


def main():
    days = D.load()
    rng = np.random.default_rng(20261118)
    out = [__doc__.split("\n\n")[0], f"Data {days[0][0]:%Y-%m-%d}..{days[-1][0]:%Y-%m-%d}; choose on 2025, test on 2026.", ""]
    w = out.append
    grid = []
    for kind in ("SPIKE", "QUICK"):
        T = {f: prep(days, kind, f == "STRICT") for f in ("STRICT", "LENIENT")}
        for m in MARGINS:
            n = 5 * m * N20
            for usd in USD:
                tp = usd / n
                res = []
                for s in STOPS:
                    for tc in TCUT:
                        d = run(T["STRICT"], kind, s, tp, tc, True)
                        tr, te = d[d.day < SPLIT], d[d.day >= SPLIT]
                        res.append((s, tc, tr.ret.mean() * n, te.ret.mean() * n, d.ret.mean() * n))
                        grid.append(dict(entry=kind, margin=m, usd=usd, stop=s, tcut=tc, train=tr.ret.mean() * n, test=te.ret.mean() * n, all=d.ret.mean() * n))
                r = pd.DataFrame(res, columns=["s", "tc", "train", "test", "all"])
                best = r.sort_values("train", ascending=False).iloc[0]
                s, tc = best.s, (None if pd.isna(best.tc) else int(best.tc))
                w(f"===== {kind}  margin {m:.0%} (${n:,.0f} position)  target ${usd:.0f} (a {100 * tp:.2f}% fall)")
                w(f"  of {len(r)} cuts: {int((r.train > 0).sum())} positive on 2025, {int((r.test > 0).sum())} positive on 2026, "
                  f"{int(((r.train > 0) & (r.test > 0)).sum())} on both; median cut 2025 ${r.train.median():+.1f}, 2026 ${r.test.median():+.1f}")
                w(f"  CHOSEN on 2025: stop {100 * s:.1f}% above, time cut {tc if tc else 'none'} min")
                for fill in ("STRICT", "LENIENT"):
                    d = run(T[fill], kind, s, tp, tc, fill == "STRICT", check=True)
                    w(f"    {fill:7} 2025 (chosen on) {line(d[d.day < SPLIT], n)}")
                    w(f"    {'':7} 2026 TEST        {line(d[d.day >= SPLIT], n)}")
                top = r.sort_values("train", ascending=False).head(5)
                w("    next best on 2025 -> their 2026: " + "; ".join(f"{100 * x.s:.1f}%/{'-' if pd.isna(x.tc) else int(x.tc)}m ${x.train:+.1f} -> ${x.test:+.1f}" for x in top.iloc[1:].itertuples()))
                bt = r.sort_values("test", ascending=False).iloc[0]
                w(f"    (hindsight: the best cut ON 2026 itself was {100 * bt.s:.1f}%/{'-' if pd.isna(bt.tc) else int(bt.tc)}m ${bt.test:+.1f}; it made ${bt.train:+.1f} on 2025)")
                w("")
        # loop check on 25 random cuts
        for _ in range(25 if kind == "SPIKE" else 10):
            s, tc, m, usd, f = (STOPS[rng.integers(len(STOPS))], TCUT[rng.integers(len(TCUT))], MARGINS[rng.integers(3)], USD[rng.integers(2)],
                                ("STRICT", "LENIENT")[rng.integers(2)])
            run(T[f], kind, s, usd / (5 * m * N20), tc, f == "STRICT", check=True)
    w("loop and array implementations agreed on every trade checked.")
    pd.DataFrame(grid).to_csv("out/careful/cut_search_grid.csv", index=False)
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/cut_search.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
