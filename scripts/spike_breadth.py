"""The spike fade on coin-days it has never seen -- docs/spike_breadth_prereg.md (groups, rule and what counts are
frozen there).

    PYTHONPATH=. python scripts/spike_breadth.py picks     # -> out/careful/breadth_picks.csv
    PYTHONPATH=. python scripts/spike_breadth.py fetch     # Delta public 1m candles, cached, resumable
    PYTHONPATH=. python scripts/spike_breadth.py report    # -> out/careful/spike_breadth.txt
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

OUT = Path("out/careful")
PICKS = OUT / "breadth_picks.csv"
N = 2881
FEE_IN, FEE_OUT = 0.000212, 0.000531


def cmd_picks():
    import gainer_fade_2025_lab as G
    rng = np.random.default_rng(20261116)
    g = G.panel()
    g = g[np.isfinite(g.atrp) & (g.atrp > 0)]
    rows = []
    for t, d in g.groupby("time"):
        el = d[d.eligible].sort_values("prev_change", ascending=False)
        top5 = el.head(5)
        for rank, r in enumerate(top5.itertuples(), 1):
            if rank >= 3 and 2.0 * r.atrp <= 0.325:
                rows.append((t, r.symbol, "RANK 3-5", rank))
        rest = el.iloc[5:]
        rest = rest[2.0 * rest.atrp <= 0.325]
        for s in rng.permutation(rest.symbol.to_numpy())[:2]:
            rows.append((t, s, "OTHER GAINERS", 0))
        non = d[d.liquid & (d.prev_change <= 0) & (2.0 * d.atrp <= 0.325)]
        for s in rng.permutation(non.symbol.to_numpy())[:2]:
            rows.append((t, s, "NON-GAINERS", 0))
    P = pd.DataFrame(rows, columns=["time", "symbol", "group", "rank"])
    P["day"] = pd.to_datetime(P.time, unit="s")
    old = pd.read_csv(OUT / "picks.csv", parse_dates=["day"])
    key = set(zip(old.day, old.symbol))
    P = P[[(d, s) not in key for d, s in zip(P.day, P.symbol)]]          # never a coin-day already in the picks
    P.to_csv(PICKS, index=False)
    print(P.groupby("group").size().to_string(), "\ndays", P.day.nunique(), P.day.min().date(), "..", P.day.max().date())


def cmd_fetch():
    import careful_dataset as CD
    CD.CACHE.mkdir(parents=True, exist_ok=True)
    P = pd.read_csv(PICKS)
    jobs = sorted(set(zip(P.symbol, P.time)))

    def one(j):
        try:
            return len(CD.fetch(j[0], int(j[1])))
        except Exception as exc:  # noqa: BLE001
            return f"ERROR {j} {exc}"

    with ThreadPoolExecutor(3) as ex:
        for k, r in enumerate(ex.map(one, jobs), 1):
            if k % 250 == 0 or isinstance(r, str):
                print(f"[{k}/{len(jobs)}] {r}", flush=True)
    print("done", len(jobs), flush=True)


def arrays(sym, t0):
    f = OUT / "candles" / f"{sym}_{t0}.json"
    if not f.exists():
        return None
    hi, lo, cl = np.full(N, -np.inf), np.full(N, np.inf), np.full(N, np.nan)
    for c in json.loads(f.read_text()):
        k = (int(c["time"]) - t0) // 60
        if 0 <= k < N:
            hi[k], lo[k], cl[k] = float(c["high"]), float(c["low"]), float(c["close"])
    if not np.isfinite(cl).any() or not np.isfinite(cl[:3]).any():
        return None
    return hi, lo, pd.Series(cl).ffill().to_numpy()


def stat(x, col):
    if len(x) < 20:
        return f"{len(x):5d} trades  -"
    r = x[col]
    day = r.groupby(x.day).sum()
    t = day.mean() / (day.std(ddof=1) / np.sqrt(len(day)))
    return f"{len(x):5d} trades  win {100 * (r > 0).mean():3.0f}%  avg win {100 * r[r > 0].mean():+6.2f}%  avg loss {100 * r[r <= 0].mean():+6.2f}%  mean {100 * r.mean():+.3f}%  t {t:+5.2f}"


def cmd_report():
    from spike_flip_back import trade
    P = pd.read_csv(PICKS, parse_dates=["day"])
    rows, missing = [], 0
    for x in P.itertuples():
        a = arrays(x.symbol, int(x.time))
        if a is None or not np.isfinite(a[2][2]):
            missing += 1
            continue
        s, ln = trade(*a, None, 8, "STRICT"), trade(*a, None, 8, "LENIENT")
        rows.append((x.day, x.symbol, x.group, x.rank, s is not None, s[0] if s else np.nan, ln[0] if ln else np.nan, s[1] if s else ""))
    R = pd.DataFrame(rows, columns=["day", "sym", "group", "rank", "filled", "strict", "lenient", "how"])
    out = ["SPIKE FADE (sell +5%, stop 10%, out after 8 h) ON COIN-DAYS IT HAS NEVER SEEN -- docs/spike_breadth_prereg.md. Before fees unless said.",
           f"coin-days: {len(P)}; without usable candles {missing}"]
    for grp in ("RANK 3-5", "OTHER GAINERS", "NON-GAINERS"):
        g = R[R.group == grp]
        f = g[g.filled]
        out.append(f"\n===== {grp}: {len(g)} coin-days, {len(f)} filled ({100 * len(f) / max(len(g), 1):.0f}%)")
        out.append(f"   STRICT            {stat(f, 'strict')}")
        out.append(f"   LENIENT           {stat(f, 'lenient')}")
        out.append(f"   STRICT 2025       {stat(f[f.day.dt.year == 2025], 'strict')}")
        out.append(f"   STRICT 2026       {stat(f[f.day.dt.year >= 2026], 'strict')}")
        net = f.assign(net=f.strict - FEE_IN - FEE_OUT * (1 - f.strict))
        out.append(f"   STRICT, real fees {stat(net, 'net')}   stopped {100 * (f.how == 'STOP').mean():.0f}%")
        if grp == "RANK 3-5":
            for rk in (3, 4, 5):
                out.append(f"   rank {rk}            {stat(f[f['rank'] == rk], 'strict')}")
    txt = "\n".join(out)
    print(txt)
    (OUT / "spike_breadth.txt").write_text(txt + "\n")
    R.to_pickle(OUT / "spike_breadth.pkl")


if __name__ == "__main__":
    {"picks": cmd_picks, "fetch": cmd_fetch, "report": cmd_report}[sys.argv[1]]()
