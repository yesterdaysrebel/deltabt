"""Owner 2026-11-18: "with new rule 10% margin and 5x leverage how many trades make 30-40$".

The live spike rule ("C from the fill") at 10% margin x 5x: $1,439 position (today's $2,895.60 x 0.5 x the live
sizing). The bot's day rules (spike_high_margin_check.live_select). For every trade: the FINAL result in $ buckets, and
whether it was $30 / $40 in profit AT SOME POINT before it closed (scripts/spike_usd_target.reached: STRICT = a 1m low
passes the level by 0.1% before the exit minute; LENIENT = touch). History 2025-01..2026-11-14 and the real picks
10-29..11-18 (today's MMT at its actual fill). trade1 / trade2 must agree. Before fees.

    PYTHONPATH=. python scripts/spike_10m_buckets.py [margin]     # -> out/careful/spike_10m_buckets.txt (20%: ..._20m.txt)
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import real_picks_rule_c as R  # noqa: E402
import spike_high_margin_check as K  # noqa: E402
import spike_split_be as B  # noqa: E402
import spike_usd_target as S  # noqa: E402
from spike_verify import CELLS, trade1, trade2  # noqa: E402

C = CELLS["C from the fill"]
MARGIN = float(sys.argv[1]) if len(sys.argv) > 1 else 0.10   # owner "with 20% margin and 5x how are the numbers?" -> 0.20
N = 5 * MARGIN * 2878.0
BUCKETS = [(-1e9, -100, "lost more than $100"), (-100, 0, "lost $0-100"), (0, 30, "made $0-30"), (30, 40, "made $30-40"),
           (40, 100, "made $40-100"), (100, 1e9, "made over $100")]


def trades(src, strict):
    rows = []
    for day, rank, sym, H, L, Cl in src:
        key = (day.strftime("%Y-%m-%d"), sym)
        ent = None
        if key in B.ACTUAL:
            k, e = B.ACTUAL[key]
            ent = (k, e, e, True)
        a, b = trade1(H, L, Cl, C, strict, ent), trade2(H, L, Cl, C, strict, ent)
        if a is None:
            continue
        assert a[:2] == b[:2] and abs(a[2] - b[2]) < 1e-9 and a[3] == b[3], (key, a, b)
        e = ent[1] if ent else Cl[2] * 1.05
        r30, r40 = (S.reached(H, L, a, e, u / N, strict) is not None for u in (30, 40))
        rows.append(dict(day=day, rank=rank, sym=sym, tin=day + pd.Timedelta(minutes=a[0]), tout=day + pd.Timedelta(minutes=a[1]),
                         usd=a[2] * N, r30=r30, r40=r40))
    return K.live_select(pd.DataFrame(rows))


def report(d, lab, w):
    w(f"  {lab}: {len(d)} trades, per trade ${d.usd.mean():+.1f}, total ${d.usd.sum():+,.0f}")
    for lo, hi, nm in BUCKETS:
        m = (d.usd > lo) & (d.usd <= hi) if lo >= 0 else (d.usd > lo) & (d.usd < hi) if hi <= 0 else (d.usd >= lo) & (d.usd < hi)
        w(f"    {nm:22} {int(m.sum()):4d}  ({100 * m.mean():3.0f}%)")
    for u in (30, 40):
        hit = d[f"r{u}"].astype(bool)
        g = d.usd[hit]
        w(f"    was ${u} in profit at some point: {int(hit.sum())} ({100 * hit.mean():.0f}%) -> of those, ended >= ${u} {int((g >= u).sum())}, "
          f"ended $0-{u} {int(((g > 0) & (g < u)).sum())}, ended a LOSS {int((g <= 0).sum())}")


def main():
    hist = [(day, rank, sym, H, L, Cl) for day, ps in D.load() for rank, sym, H, L, Cl in ps]
    real = [(pd.Timestamp(d), r, s, H, L, Cl) for d, r, s, H, L, Cl in R.picks()]
    H, L, Cl = R.fetch("MMTUSD", int(pd.Timestamp("2026-11-18", tz="UTC").timestamp()))
    real.append((pd.Timestamp("2026-11-18"), 1, "MMTUSD", H, L, Cl))
    out = [__doc__.split("\n\n")[0], f"Position ${N:,.0f}: $30 = a {100 * 30 / N:.2f}% fall, $40 = {100 * 40 / N:.2f}%; the 10% stop ~ -${0.1 * N:.0f}.", ""]
    w = out.append
    for fill in ("STRICT", "LENIENT"):
        w(f"===== {fill}")
        report(trades(hist, fill == "STRICT"), "HISTORY 2025-01..2026-11-14", w)
        d = trades(real, fill == "STRICT")
        report(d, "REAL PICKS 10-29..11-18", w)
        if fill == "STRICT":
            w("    " + "; ".join(f"{x.day:%m-%d} {x.sym} ${x.usd:+.0f}" for x in d.itertuples()))
        w("")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/spike_10m_buckets" + ("" if MARGIN == 0.10 else f"_{round(MARGIN * 100)}m") + ".txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
