"""Owner 2026-11-18: "we will cut the trade at 30-40$". The live spike rule ("C from the fill") plus a resting take-
profit at $30 / $35 / $40, at 10% and 20% margin x 5x, vs the rule as it runs. Same machinery and checks as
scripts/spike_split_be.py (two implementations agree; equal to spike_verify.trade1 with tp). History 2025-01..2026-11-14
with the bot's day rules, split 2025 / 2026; real picks 10-29..11-18 (today's MMT at its actual fill). Before fees,
then after.

    PYTHONPATH=. python scripts/spike_cut_30_40.py      # -> out/careful/spike_cut_30_40.txt
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import real_picks_rule_c as R  # noqa: E402
import spike_split_be as B  # noqa: E402


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
            for nm, v in (("as now", (0, None, None, False)), ("cut $30", (1.0, 30.0, None, False)), ("cut $35", (1.0, 35.0, None, False)),
                          ("cut $40", (1.0, 40.0, None, False))):
                h = B.run(hist, n, v, fill == "STRICT")
                r = B.run(real, n, v, fill == "STRICT")
                y = " | ".join(f"{yr} ${(h.ret[h.day.dt.year == yr] * n).mean():+.1f}" for yr in (2025, 2026))
                w(f"  {fill:7} {nm:8} history {B.line(h, n)}  [{y}]")
                w(f"  {'':7} {'':8} real    {B.line(r, n)}")
        w("")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/spike_cut_30_40.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
