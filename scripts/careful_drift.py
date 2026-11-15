"""Is there any drift to harvest? Raw short returns (no stop, no target, before costs) on the careful dataset:
  A  from the 00:02 price (every pick)              to +15 min / 1 h / 4 h / 12 h / 24 h
  B  from the +5% limit fill (picks that spike 5%)  to +5 / 15 min / 1 h / 4 h / 24 h after the fill
Short return = -(P_later / P_entry - 1). Mean, its standard error, median, share > 0; 2025 vs 2026.
Round-trip cost at 20x full size is about 0.12% (+0.1% slippage on market exits).

    PYTHONPATH=. python scripts/careful_drift.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402

picks = [(d, s, H, L, Cl) for d, ps in D.load() for r, s, H, L, Cl in ps]


def show(name, rows, horizons):
    df = pd.DataFrame(rows)
    print(name, f"({len(df)} entries)")
    for h in horizons:
        for yr, g in (("all", df), ("2025", df[df.day < "2026"]), ("2026", df[df.day >= "2026"])):
            x = g[h].dropna().to_numpy()
            print(f"   +{h:>6s} {yr:4s}: mean {x.mean():+.2%} (se {x.std() / np.sqrt(len(x)):.2%}, t {x.mean() / (x.std() / np.sqrt(len(x))):+.1f})  "
                  f"median {np.median(x):+.2%}  short wins {np.mean(x > 0):.0%}  worst 5% {np.percentile(x, 5):+.1%}  best 5% {np.percentile(x, 95):+.1%}")
        print()


A, Bm = [], []
for d, s, H, L, Cl in picks:
    p0 = Cl[2]
    A.append(dict(day=d, **{n: -(Cl[2 + m] / p0 - 1) for n, m in (("15m", 15), ("1h", 60), ("4h", 240), ("12h", 720), ("24h", 1440))},
                  mae=np.max(H[3:1443]) / p0 - 1))
    e = p0 * 1.05
    k = next((j for j in range(3, 1440) if H[j] >= e * 1.0005), None)
    if k is not None:
        Bm.append(dict(day=d, **{n: -(Cl[min(k + m, 2880)] / e - 1) for n, m in (("5m", 5), ("15m", 15), ("1h", 60), ("4h", 240), ("24h", 1440))},
                       mae=np.max(H[k + 1:k + 1441]) / e - 1))
show("A  short at the 00:02 price", A, ["15m", "1h", "4h", "12h", "24h"])
show("B  short at the +5% limit", Bm, ["5m", "15m", "1h", "4h", "24h"])
for nm, rows in (("A", A), ("B", Bm)):
    m = np.array([r["mae"] for r in rows])
    print(f"{nm}: worst rise against the short within 24 h: median {np.median(m):+.1%}; over +2.25% in {(m > .0225).mean():.0%}, "
          f"over +5% in {(m > .05).mean():.0%}, over +10% in {(m > .10).mean():.0%}, over +20% in {(m > .20).mean():.0%}")
