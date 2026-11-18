"""Owner 2026-11-18: "if it does not reach the target in 1 hour we cut the trade" (after "we will cut the trade at
30-40$"). The live spike rule ("C from the fill": 10% stop, 10% trail from the fill) plus a resting take-profit at
$30 / $35 / $40, and OUT AT MARKET 60 minutes after the fill if the target has not filled (STRICT: the 60th minute's
close 0.05% worse). Also 30 / 120 minutes for context, chosen before the run, and the 1-hour cut alone (no target).
10% and 20% margin x 5x. Machinery and checks of scripts/spike_split_be.py with its time limit set to the cut (two
implementations agree; equal to spike_verify.trade1 with the same tp and minutes). History 2025-01..2026-11-14 with the
bot's day rules, 2025 / 2026 shown; real picks 10-29..11-18 (today's MMT at its actual fill). Before fees, then after.

    PYTHONPATH=. python scripts/spike_cut_1h.py      # -> out/careful/spike_cut_1h.txt
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import real_picks_rule_c as R  # noqa: E402
import spike_split_be as B  # noqa: E402

C0 = dict(B.C)


def main():
    hist = [(day, rank, sym, H, L, Cl) for day, ps in D.load() for rank, sym, H, L, Cl in ps]
    real = [(pd.Timestamp(d), r, s, H, L, Cl) for d, r, s, H, L, Cl in R.picks()]
    H, L, Cl = R.fetch("MMTUSD", int(pd.Timestamp("2026-11-18", tz="UTC").timestamp()))
    real.append((pd.Timestamp("2026-11-18"), 1, "MMTUSD", H, L, Cl))
    out = [__doc__.split("\n\n")[0], ""]
    w = out.append
    for m in (0.10, 0.20):
        n = 5 * m * B.N20
        w(f"===== MARGIN {m:.0%}: position ${n:,.0f}, the 10% stop ~ -${0.1 * n:.0f}")
        for fill in ("STRICT", "LENIENT"):
            for mins, nm, v in ((1440, "as now", (0, None, None, False)), (60, "1h cut, no target", (0, None, None, False)),
                                (60, "$30 + 1h cut", (1.0, 30.0, None, False)), (60, "$35 + 1h cut", (1.0, 35.0, None, False)),
                                (60, "$40 + 1h cut", (1.0, 40.0, None, False)), (30, "$40 + 30m cut", (1.0, 40.0, None, False)),
                                (120, "$40 + 2h cut", (1.0, 40.0, None, False))):
                B.MINS, B.C = mins, dict(C0, mins=mins)
                h = B.run(hist, n, v, fill == "STRICT")
                r = B.run(real, n, v, fill == "STRICT")
                y = " | ".join(f"{yr} ${(h.ret[h.day.dt.year == yr] * n).mean():+.1f}" for yr in (2025, 2026))
                mm = r[r.sym == "MMTUSD"]
                w(f"  {fill:7} {nm:18} history {B.line(h, n)}  [{y}]")
                w(f"  {'':7} {'':18} real    {B.line(r, n)}  | today MMT ${(mm.ret.sum() * n):+.0f}")
        w("")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/spike_cut_1h.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
