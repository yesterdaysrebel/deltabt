"""The pre-registered test of the owner's fade made mechanical -- docs/turn_fade_prereg.md. Rule, fills, control,
data and pass bar are in that file and are not repeated or changed here.

    PYTHONPATH=. python scripts/turn_fade.py           # -> out/turn_fade.txt
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import pullback_5m_trend as P  # noqa: E402

TAKER = 0.0005 * 1.18
RULES = {"PRIMARY": (0.02, 2, 7, 0.25, 0.66), "SECONDARY": (0.01, 1, 7, 0.20, 9.0), "PLAIN FADE": (0.02, 0, 99, -9.0, 9.0)}


def signals(g, rule):
    """-> array of +1 / -1 / 0 per bar (long = fading a fall)"""
    mv, a, b, p0, p1 = rule
    H, L, C = (g[c].to_numpy(float) for c in ("high", "low", "close"))
    n = len(C)
    sig = np.zeros(n, int)
    if n < 320:
        return sig
    win_h = np.lib.stride_tricks.sliding_window_view(H, 15)            # row j = bars j .. j+14
    win_l = np.lib.stride_tricks.sliding_window_view(L, 15)
    idx = np.arange(14, n)
    hi, lo = win_h.max(1), win_l.min(1)
    ago_lo, ago_hi = 14 - win_l.argmin(1), 14 - win_h.argmax(1)
    c, c1 = C[idx], C[idx - 1]
    c15 = np.r_[np.nan, C[: n - 15]][: len(idx)]                       # close 15 bars before bar idx
    rng = np.where(hi > lo, hi - lo, np.nan)
    plain = rule[1] == 0 and rule[2] == 99
    with np.errstate(invalid="ignore"):
        long_ = (c / c15 - 1 <= -mv) & (plain | ((ago_lo >= a) & (ago_lo <= b) & ((c - lo) / rng >= p0) & ((c - lo) / rng <= p1) & (c > c1)))
        short = (c / c15 - 1 >= mv) & (plain | ((ago_hi >= a) & (ago_hi <= b) & ((hi - c) / rng >= p0) & ((hi - c) / rng <= p1) & (c < c1)))
    sig[idx[long_]] = 1
    sig[idx[short]] = -1
    sig[:300] = 0
    return sig


def bracket(g, sig, mode, bad=None, liquid_only=True, flat_bad=None):
    O, H, L, C, V, T = (g[c].to_numpy(float) for c in ("open", "high", "low", "close", "volume", "time"))
    n, out, free = len(C), [], 0
    for i in np.flatnonzero(sig):
        k = i + 1
        if i < free or k + 15 >= n or not (V[k] > 0 and V[i] > 0 and V[i - 1] > 0 and V[i - 2] > 0):
            continue
        day = T[k] // 86400
        if (bad and day in bad) or (liquid_only and flat_bad and day in flat_bad):
            continue
        s = int(sig[i])
        e = O[k] * (1 + s * {"EXACT": 0.0, "MID": 0.0002, "STRICT": 0.0005}[mode])
        sp, tp = e * (1 - s * 0.01), e * (1 + s * 0.015)
        need = tp * (1 + s * 0.001) if mode == "STRICT" else tp
        x = None
        for j in range(k, k + 15):
            if (L[j] <= sp) if s > 0 else (H[j] >= sp):
                if mode == "STRICT":
                    x = (min(sp, C[j]) * 0.999) if s > 0 else (max(sp, C[j]) * 1.001)
                else:
                    x = sp
                    if j > k and ((O[j] < sp) if s > 0 else (O[j] > sp)):
                        x = O[j]
                    if mode == "MID":
                        x *= 1 - s * 0.0005
                break
            if (H[j] >= need) if s > 0 else (L[j] <= need):
                x = tp
                break
        if x is None:
            x = C[k + 14] * ((1 - s * 0.0005) if mode == "STRICT" else 1.0)
        out.append((T[k], s, s * (x / e - 1)))
        free = i + 15
    return out


def random_like(sig, rng):
    out = np.zeros(len(sig), int)
    nz = sig[sig != 0]
    if len(nz) and len(sig) > 340:
        out[rng.choice(np.arange(300, len(sig) - 20), size=min(len(nz), len(sig) - 320), replace=False)] = rng.permutation(nz)[: min(len(nz), len(sig) - 320)]
    return out


def stat(rows, fee=0.0):
    d = pd.DataFrame(rows, columns=["t", "s", "r"])
    if len(d) < 30:
        return f"{len(d):6d} trades  -"
    r = d.r - fee
    day = r.groupby(d.t // 86400).sum()
    t = day.mean() / (day.std(ddof=1) / np.sqrt(len(day)))
    return f"{len(d):6d} trades  win {100 * (r > 0).mean():3.0f}%  avg win {100 * r[r > 0].mean():+.2f}%  avg loss {100 * r[r <= 0].mean():+.2f}%  mean {100 * r.mean():+.4f}%  t {t:+5.2f}"


def gen(folder, seen):
    for c in json.loads(Path("out/pullback/universe.json").read_text())["coins"]:
        sym = c["symbol"]
        p = Path(folder) / f"{sym}.parquet"
        if not p.exists():
            continue
        d = pd.read_parquet(p).sort_values("time").reset_index(drop=True)
        flat = (d.high == d.low).groupby(d.time // 86400).mean()
        bad = {dd for dd in flat.index if (sym, int(dd)) in seen}
        for g in P.segments(d):
            yield g, bad, set(flat.index[flat > P.FLAT_MAX])


def gen_a():
    for p in sorted(Path("out/manual/candles").glob("*.parquet")):
        d = pd.read_parquet(p).sort_values("time").reset_index(drop=True)
        for _, g in d.groupby((d.time.diff() != 60).cumsum()):
            if len(g) > 400:
                yield g.reset_index(drop=True), None, None


def main():
    rng = np.random.default_rng(20261116)
    seen = P.seen_coin_days()
    acc = {}
    for ds, g_iter in (("U", gen("out/pullback", seen)), ("O", gen("out/breakout_oos", seen)), ("A", gen_a())):
        for g, bad, flat_bad in g_iter:
            for rn, rule in RULES.items():
                sig = signals(g, rule)
                for cut, liq in (("LIQUID", True), ("ALL", False)):
                    if ds == "A" and cut == "LIQUID":
                        continue
                    for mode in ("EXACT", "MID", "STRICT"):
                        acc.setdefault((ds, cut, rn, mode), []).extend(bracket(g, sig, mode, bad, liq, flat_bad))
                    if rn != "PLAIN FADE":
                        for _ in range(5):
                            acc.setdefault((ds, cut, rn, "RANDOM"), []).extend(bracket(g, random_like(sig, rng), "EXACT", bad, liq, flat_bad))
    out = ["FADE A SHARP MOVE ONCE IT HAS TURNED -- the pre-registered test (docs/turn_fade_prereg.md). Target 1.5%, stop 1%, out in 15 min. Before fees unless said."]
    for cut in ("LIQUID", "ALL"):
        for rn in ("PRIMARY", "SECONDARY"):
            out.append(f"\n===== {cut} days, {rn} rule")
            for nm, keys in (("U + O pooled", ("U", "O")), ("U  2026-08..11", ("U",)), ("O  2026-03..07", ("O",))):
                get = lambda mode, r=rn: [x for k in keys for x in acc.get((k, cut, r, mode), [])]  # noqa: E731
                out.append(f"   {nm:16} EXACT        {stat(get('EXACT'))}")
                if len(keys) == 2:
                    e = pd.DataFrame(get("EXACT"), columns=["t", "s", "r"])
                    out.append(f"   {'':16} longs        {stat(e[e.s > 0].values.tolist())}")
                    out.append(f"   {'':16} shorts       {stat(e[e.s < 0].values.tolist())}")
                    out.append(f"   {'':16} MID          {stat(get('MID'))}")
                    out.append(f"   {'':16} STRICT       {stat(get('STRICT'))}")
                    out.append(f"   {'':16} real fees    {stat(get('EXACT'), TAKER)}")
                    out.append(f"   {'':16} random       {stat(get('RANDOM'))}")
                    out.append(f"   {'':16} plain fade   {stat([x for k in keys for x in acc.get((k, cut, 'PLAIN FADE', 'EXACT'), [])])}")
    out.append("\n===== A  the owner's own coin-days (reference only, where the idea comes from)")
    for rn in ("PRIMARY", "SECONDARY", "PLAIN FADE"):
        out.append(f"   {rn:12} EXACT  {stat(acc[('A', 'ALL', rn, 'EXACT')])}")
        if rn != "PLAIN FADE":
            out.append(f"   {'':12} random {stat(acc[('A', 'ALL', rn, 'RANDOM')])}")
    e = pd.DataFrame([x for k in ("U", "O") for x in acc.get((k, "LIQUID", "PRIMARY", "EXACT"), [])], columns=["t", "s", "r"])
    m = pd.DataFrame([x for k in ("U", "O") for x in acc.get((k, "LIQUID", "PRIMARY", "MID"), [])], columns=["t", "s", "r"])
    rd = pd.DataFrame([x for k in ("U", "O") for x in acc.get((k, "LIQUID", "PRIMARY", "RANDOM"), [])], columns=["t", "s", "r"])
    pf = pd.DataFrame([x for k in ("U", "O") for x in acc.get((k, "LIQUID", "PLAIN FADE", "EXACT"), [])], columns=["t", "s", "r"])
    out.append("")
    if len(e) < 200:
        out.append(f"VERDICT (LIQUID, PRIMARY): NOT ENOUGH TRADES TO JUDGE ({len(e)} < 200)")
    else:
        day = e.r.groupby(e.t // 86400).sum()
        t = day.mean() / (day.std(ddof=1) / np.sqrt(len(day)))
        mean_of = lambda k: np.mean([x[2] for x in acc.get((k, "LIQUID", "PRIMARY", "EXACT"), [(0, 0, np.nan)])])  # noqa: E731
        checks = [("EXACT mean > 0 with t >= 2", e.r.mean() > 0 and t >= 2), ("EXACT > 0 on U and on O", mean_of("U") > 0 and mean_of("O") > 0),
                  ("MID mean > 0", m.r.mean() > 0), ("beats random and the plain fade", e.r.mean() > rd.r.mean() and e.r.mean() > pf.r.mean())]
        out += [f"   {'PASS' if ok else 'FAIL'}  {nm}" for nm, ok in checks]
        out.append(f"VERDICT (LIQUID, PRIMARY): {'PASS' if all(ok for _, ok in checks) else 'FAIL'}")
    txt = "\n".join(out)
    print(txt)
    Path("out/turn_fade.txt").write_text(txt + "\n")
    pd.to_pickle(acc, "out/manual/turn_fade.pkl")


if __name__ == "__main__":
    main()
