"""The pre-registered confirmation run of the %R breakout with a 3-to-1 bracket -- docs/breakout_3r_prereg.md.
Rule, fills, control, data hygiene and pass bar are in that file and are not repeated or changed here.

    PYTHONPATH=. python scripts/breakout_3r.py         # -> out/breakout_oos/result.txt
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import hand_rule_test as R  # noqa: E402
import pullback_5m_trend as P  # noqa: E402
from deltabt.indicators import supertrend, wpr  # noqa: E402

OUT = Path("out/breakout_oos")
TAKER, MAKER = 0.0005 * 1.18, 0.0002 * 1.18
CAP, DRAWS = 1440, 5


def bracket(g, sig, stp, mode):
    """-> list of (entry time, side, return before fees, minutes held, how, real fee as a fraction of the position)"""
    O, H, L, C, V, T = (g[c].to_numpy(float) for c in ("open", "high", "low", "close", "volume", "time"))
    n, out, free = len(C), [], 0
    for i in np.flatnonzero(sig):
        k = i + 1
        if k < free or k >= n or not V[k] > 0:
            continue
        s = int(sig[i])
        e = O[k] * (1 + s * {"EXACT": 0.0, "MID": 0.0002, "STRICT": 0.0005}[mode])
        sp, tp = e * (1 - s * stp), e * (1 + s * 3 * stp)
        need = tp * (1 + s * 0.001) if mode == "STRICT" else tp
        last = min(n - 1, k + CAP - 1)
        x = how = None
        for j in range(k, last + 1):
            if (L[j] <= sp) if s > 0 else (H[j] >= sp):
                if mode == "STRICT":
                    x = (min(sp, C[j]) * 0.999) if s > 0 else (max(sp, C[j]) * 1.001)
                else:
                    x = sp
                    if j > k and ((O[j] < sp) if s > 0 else (O[j] > sp)):
                        x = O[j]
                    if mode == "MID":
                        x *= 1 - s * 0.0005
                how = "STOP"
                break
            if (H[j] >= need) if s > 0 else (L[j] <= need):
                x, how = tp, "TARGET"
                break
        if x is None:
            j = last
            x, how = C[j] * ((1 - s * 0.0005) if mode == "STRICT" else 1.0), "TIME"
        hold = j - k + 1
        fee = TAKER + (0.0 if hold <= 15 else (MAKER if how == "TARGET" else TAKER) * x / e)
        free = j + 1
        out.append((T[k], s, s * (x / e - 1), hold, how, fee))
    return out


def random_like(sig, rng):
    out = np.zeros(len(sig), int)
    nz = sig[sig != 0]
    out[rng.choice(np.arange(300, len(sig) - 1), size=len(nz), replace=False)] = rng.permutation(nz)
    return out


def stat(d, col="r"):
    if len(d) < 30:
        return f"{len(d):6d} trades  -"
    day = d.groupby(d.t // 86400)[col].sum()
    t = day.mean() / (day.std(ddof=1) / np.sqrt(len(day)))
    return (f"{len(d):6d} trades  win {100 * (d[col] > 0).mean():3.0f}%  avg win {100 * d[col][d[col] > 0].mean():+.2f}%  avg loss {100 * d[col][d[col] <= 0].mean():+.2f}%  "
            f"mean {100 * d[col].mean():+.4f}%  t {t:+5.2f}")


def main():
    rng = np.random.default_rng(20261116)
    seen = P.seen_coin_days()
    acc, bars, kept_days, all_days = {}, 0, 0, 0
    for c in json.loads(Path("out/pullback/universe.json").read_text())["coins"]:
        sym = c["symbol"]
        p = OUT / f"{sym}.parquet"
        if not p.exists():
            continue
        d = pd.read_parquet(p).sort_values("time").reset_index(drop=True)
        bars += len(d)
        flat = (d.high == d.low).groupby(d.time // 86400).mean()
        bad = set(flat.index[flat > P.FLAT_MAX]) | {dd for dd in flat.index if (sym, int(dd)) in seen}
        all_days, kept_days = all_days + len(flat), kept_days + len(flat) - len(bad)
        for g in P.segments(d):
            w = wpr(g.high, g.low, g.close, 140)
            up = -supertrend(g.high, g.low, g.close, 2.0, 10)[1]
            sig = R.triggers(w, up, np.zeros(len(g)))["breakout"]
            for stp in (0.02, 0.01):
                for mode in ("EXACT", "MID", "STRICT"):
                    acc.setdefault(("sig", stp, mode), []).extend((sym, *r) for r in bracket(g, sig, stp, mode) if r[0] // 86400 not in bad)
                for _ in range(DRAWS):
                    acc.setdefault(("rand", stp, "EXACT"), []).extend((sym, *r) for r in bracket(g, random_like(sig, rng), stp, "EXACT") if r[0] // 86400 not in bad)
    T = {k: pd.DataFrame(v, columns=["sym", "t", "s", "r", "hold", "how", "fee"]) for k, v in acc.items()}
    out = ["%R BREAKOUT WITH A 3-TO-1 BRACKET -- the pre-registered confirmation run (docs/breakout_3r_prereg.md)",
           f"data: 30 coins, 2026-03-01..07-31, {bars:,} one-minute bars, {kept_days:,} of {all_days:,} coin-days kept (liquid, not in data A or B). Before fees unless said.", ""]
    verdict = None
    for stp, nm in ((0.02, "stop 2% / target 6%   (JUDGED)"), (0.01, "stop 1% / target 3%   (secondary)")):
        e, r = T[("sig", stp, "EXACT")], T[("rand", stp, "EXACT")]
        half = (e.t.min() + e.t.max()) / 2
        out.append(f"===== {nm}")
        out.append(f"   EXACT, all          {stat(e)}   hold: median {e.hold.median():.0f} min; target {100 * (e.how == 'TARGET').mean():.0f}% stop {100 * (e.how == 'STOP').mean():.0f}% 24h {100 * (e.how == 'TIME').mean():.0f}%")
        out.append(f"   EXACT, first half   {stat(e[e.t < half])}")
        out.append(f"   EXACT, second half  {stat(e[e.t >= half])}")
        out.append(f"   signal longs        {stat(e[e.s > 0])}")
        out.append(f"   random longs        {stat(r[r.s > 0])}")
        out.append(f"   signal shorts       {stat(e[e.s < 0])}")
        out.append(f"   random shorts       {stat(r[r.s < 0])}")
        out.append(f"   MID fills           {stat(T[('sig', stp, 'MID')])}")
        out.append(f"   STRICT fills        {stat(T[('sig', stp, 'STRICT')])}")
        out.append(f"   EXACT, real fees    {stat(e.assign(net=e.r - e.fee), 'net')}")
        pc = e.groupby("sym").r.agg(["mean", "size"])
        pc = pc[pc["size"] >= 30]
        out.append(f"   coins with 30+ trades: {len(pc)}, positive on {int((pc['mean'] > 0).sum())}")
        if stp == 0.02:
            day = e.groupby(e.t // 86400).r.sum()
            t = day.mean() / (day.std(ddof=1) / np.sqrt(len(day)))
            verdict = [("EXACT mean > 0 with t >= 2", e.r.mean() > 0 and t >= 2),
                       ("EXACT mean > 0 in each half", e.r[e.t < half].mean() > 0 and e.r[e.t >= half].mean() > 0),
                       ("MID mean > 0", T[("sig", stp, "MID")].r.mean() > 0),
                       ("signal longs > random longs", e.r[e.s > 0].mean() > r.r[r.s > 0].mean()),
                       ("signal shorts > random shorts", e.r[e.s < 0].mean() > r.r[r.s < 0].mean())]
        out.append("")
    out += [f"   {'PASS' if ok else 'FAIL'}  {nm}" for nm, ok in verdict]
    out.append(f"VERDICT: {'PASS' if all(ok for _, ok in verdict) else 'FAIL'}")
    txt = "\n".join(out)
    print(txt)
    (OUT / "result.txt").write_text(txt + "\n")
    pd.to_pickle(T, OUT / "trades.pkl")


if __name__ == "__main__":
    main()
