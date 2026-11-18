"""Owner 2026-11-18: "split is going to make 50$ then lose it when SL hits". How often SPLIT $X books its half and then
the other half stops out, what those trades end at, and the same days under SPLIT+BE (stop to the entry once booked).
STRICT and LENIENT, history, bot day rules (scripts/spike_split_be.py). Before fees.

    PYTHONPATH=. python scripts/spike_split_breakdown.py      # -> out/careful/spike_split_breakdown.txt
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import spike_split_be as B  # noqa: E402


def main():
    hist = [(day, rank, sym, H, L, Cl) for day, ps in D.load() for rank, sym, H, L, Cl in ps]
    out = [__doc__.split("\n\n")[0], ""]
    w = out.append
    for m in (0.10, 0.20):
        n = 5 * m * B.N20
        for fill in ("STRICT", "LENIENT"):
            for x in (30.0, 50.0):
                s = B.run(hist, n, (0.5, x, None, False), fill == "STRICT")
                b = B.run(hist, n, (0.5, x, None, True), fill == "STRICT")
                now = B.run(hist, n, B.VARIANTS["as now"], fill == "STRICT")
                w(f"===== margin {m:.0%} {fill} SPLIT ${x:.0f} (${n:,.0f} position): {len(s)} trades")
                for how, g in s.groupby("how"):
                    u = g.ret * n
                    w(f"   {how:14} {len(g):4d} ({100 * len(g) / len(s):3.0f}%)  avg ${u.mean():+6.0f}  total ${u.sum():+8,.0f}")
                gave = s[s.how == "TARGET+STOP"]
                u = gave.ret * n
                w(f"   booked then stopped: {len(gave)} trades; {int((u < 0).sum())} ended a LOSS (the $ {x:.0f} lost and more), avg loss of those ${u[u < 0].mean():+.0f}")
                for nm, d in (("SPLIT", s), ("SPLIT+BE", b), ("as now", now)):
                    u = d.ret * n
                    w(f"   {nm:9} per trade ${u.mean():+6.1f}  total ${u.sum():+8,.0f}  win {100 * (u > 0).mean():.0f}%  avg win ${u[u > 0].mean():+.0f}  avg loss ${u[u <= 0].mean():+.0f}")
                for how, g in b.groupby("how"):
                    w(f"     SPLIT+BE {how:14} {len(g):4d} ({100 * len(g) / len(b):3.0f}%)  avg ${(g.ret * n).mean():+6.0f}")
                w("")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/spike_split_breakdown.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
