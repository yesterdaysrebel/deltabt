"""Owner 2026-11-17 "run it on real fills": rule C (trail only past the entry, as first deployed) and rule C with the
10% trail from the fill (owner "use 10% from the fill"), and run 4 as it first ran, on the REAL picks: the paper
runner's own picks 2026-10-29..11-14 (careful dataset, source "real") and 11-15..11-17 (read from the prod paper
table, Delta 1m candles fetched here). Today's CROSS uses its ACTUAL live fill (0.154 at 05:02:51 UTC). STRICT and
LENIENT fills as scripts/spike_verify.py. Before fees; $ at 1x the current $2,895.60 balance (run 4: 4x and its stop).

    PYTHONPATH=. python scripts/real_picks_rule_c.py      # -> out/careful/real_picks_rule_c.txt
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
from spike_verify import CELLS, trade1  # noqa: E402

BAL = 2895.60
LATE = {"2026-11-15": ("EVAAUSD", "SOPHUSD"), "2026-11-16": ("EVAAUSD", "RAREUSD"), "2026-11-17": ("BLESSUSD", "CROSSUSD")}
ACTUAL = {("2026-11-17", "CROSSUSD"): (302, 0.154)}          # the live fill: minute 05:02, at the limit
RULES = {"run 4 as first live (2.25% stop, 5% trail, 4x)": (CELLS["LIVE"], 4.0),
         "rule C (trail only past the entry)": (CELLS["C"], 1.0),
         "rule C, 10% trail FROM THE FILL": (dict(CELLS["C"], be=False), 1.0)}


def fetch(sym, t0):
    end = min(t0 + 2 * 86400, int(time.time()) // 60 * 60)
    req = urllib.request.Request(f"https://api.india.delta.exchange/v2/history/candles?resolution=1m&symbol={sym}&start={t0}&end={end}",
                                 headers={"User-Agent": "deltabt-research"})
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = json.load(r).get("result") or []
    H, L, C = np.full(2881, np.nan), np.full(2881, np.nan), np.full(2881, np.nan)
    for x in rows:
        m = (int(x["time"]) - t0) // 60
        if 0 <= m < 2881:
            H[m], L[m], C[m] = x["high"], x["low"], x["close"]
    return H, L, C


def picks():
    out = []
    P = pd.read_csv("out/careful/picks.csv", parse_dates=["day"])
    real = {(d.strftime("%Y-%m-%d"), s) for d, s, src in zip(P.day, P.symbol, P.source) if src == "real"}
    for day, ps in D.load():
        for rank, sym, H, L, Cl in ps:
            if (day.strftime("%Y-%m-%d"), sym) in real:
                out.append((day.strftime("%Y-%m-%d"), rank, sym, H, L, Cl))
    for day, syms in LATE.items():
        t0 = int(pd.Timestamp(day, tz="UTC").timestamp())
        for rank, sym in enumerate(syms, 1):
            H, L, Cl = fetch(sym, t0)
            out.append((day, rank, sym, H, L, Cl))
            time.sleep(0.3)
    return out


def hm(day, m):
    return (pd.Timestamp(day) + pd.Timedelta(minutes=int(m))).strftime("%m-%d %H:%M")


def main():
    now_min = (int(time.time()) - int(pd.Timestamp("2026-11-17", tz="UTC").timestamp())) // 60
    ps = picks()
    out = [__doc__.split("\n\n")[0], f"{len(ps)} real picks, {ps[0][0]} .. {ps[-1][0]}.", ""]
    tot = {}
    for nm, (c, mult) in RULES.items():
        out.append(f"===== {nm}")
        for fill in ("STRICT", "LENIENT"):
            rows, busy, cand = [], None, []
            for day, rank, sym, H, L, Cl in ps:
                ent = None
                if (day, sym) in ACTUAL:
                    k, e = ACTUAL[(day, sym)]
                    ent = (k, e, e, True)
                a = trade1(H, L, Cl, c, fill == "STRICT", ent)
                cand.append((day, a[0] if a else 9999, rank, sym, Cl, ent, a))
            cand.sort(key=lambda x: (x[0], x[1], x[2]))         # the first FILL of the day wins (ties by rank)
            traded = set()
            for day, _, rank, sym, Cl, ent, a in cand:
                if day in traded:
                    if fill == "STRICT":
                        out.append(f"   {day} {sym:10} cancelled: the day's other pick filled first")
                    continue
                if busy is not None and busy > pd.Timestamp(day):
                    if fill == "STRICT":
                        out.append(f"   {day} {sym:10} no order: a position was still open at 00:00 (one at a time)")
                    continue
                if not a:
                    if fill == "STRICT":
                        out.append(f"   {day} {sym:10} no spike: the +5% sell never filled")
                    continue
                k, j, ret, how, _ = a
                t_in = pd.Timestamp(day) + pd.Timedelta(minutes=k)
                if busy is not None and t_in <= busy:
                    if fill == "STRICT":
                        out.append(f"   {day} {sym:10} filled {hm(day, k)} but a position was open: skipped (one at a time)")
                    continue
                busy = pd.Timestamp(day) + pd.Timedelta(minutes=j)
                traded.add(day)
                still = day == "2026-11-17" and how == "CLOCK" and j > now_min
                usd = ret * BAL * mult
                rows.append(usd)
                if fill == "STRICT":
                    e = Cl[2] * 1.05 if ent is None else ent[1]
                    out.append(f"   {day} {sym:10} sold {e:<10.6g} {hm(day, k)}  ->  {'STILL OPEN now ' if still else how:15} {hm(day, j)}  "
                               f"{100 * ret:+6.2f}%  ${usd:+6.0f}")
            r = np.array(rows)
            w = r > 0
            out.append(f"   {fill:7} {len(r)} trades, {int(w.sum())} wins ({100 * w.mean():.0f}%), avg win ${r[w].mean():+.0f}, avg loss ${r[~w].mean():+.0f}, "
                       f"per trade ${r.mean():+.0f}, total ${r.sum():+,.0f}")
            tot[(nm, fill)] = r.sum()
        out.append("")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/real_picks_rule_c.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
