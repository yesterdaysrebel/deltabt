"""Owner 2026-11-18: "Why not ladder at 3% 5% then trail rest". The ladder of scripts/spike_ladder.py with steps at 3% and
5% (50% / 25%, and two other splits for context), against the live ladder (5% / 10%: 50 / 25) and no ladder. Same
machinery and checks (two implementations agree on every trade), 10% margin, before fees; history 2025 / 2026 and the
real picks 10-29..11-18.

    PYTHONPATH=. python scripts/spike_ladder_3_5.py      # -> out/careful/spike_ladder_3_5.txt
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import real_picks_rule_c as R  # noqa: E402
import spike_ladder as LD  # noqa: E402

V = {"no ladder (before today)": [],
     "LIVE: 5% 50 / 10% 25 / rest trails": [(0.05, .50), (0.10, .25)],
     "3% 50 / 5% 25 / rest trails": [(0.03, .50), (0.05, .25)],
     "3% 25 / 5% 25 / rest trails": [(0.03, .25), (0.05, .25)],
     "3% 33 / 5% 33 / rest trails": [(0.03, .33), (0.05, .33)]}


def main():
    hist = [(day, rank, sym, H, L, Cl) for day, ps in D.load() for rank, sym, H, L, Cl in ps]
    real = [(pd.Timestamp(d), r, s, H, L, Cl) for d, r, s, H, L, Cl in R.picks()]
    H, L, Cl = R.fetch("MMTUSD", int(pd.Timestamp("2026-11-18", tz="UTC").timestamp()))
    real.append((pd.Timestamp("2026-11-18"), 1, "MMTUSD", H, L, Cl))
    out = [__doc__.split("\n\n")[0], ""]
    w = out.append
    for fill in ("STRICT", "LENIENT"):
        w(f"===== {fill}")
        for nm, steps in V.items():
            h, r = LD.run(hist, steps, False, fill == "STRICT"), LD.run(real, steps, False, fill == "STRICT")
            w(f"  {nm:36} history {LD.line(h)}")
            w(f"  {'':36} real    {LD.line(r)}")
        w("")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/spike_ladder_3_5.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
