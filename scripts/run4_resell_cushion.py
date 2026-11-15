"""Owner 2026-11-15: "even with a SL we make some money so account does not lose much" -- the re-sell as a CUSHION.
Per run-4 full stop: the first stop's loss in R (its $ / the re-sell's planned loss at the same $3,184 size -- about
-1), the re-sell's R, and the combined result. How often does the re-sell soften / recover / double the loss?
Re-sell variants: when x target (stop 2.25% above the sell), from scripts/run4_resell_search.resell (checked).

    PYTHONPATH=. python scripts/run4_resell_cushion.py
"""
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402
import run4_resell_search as S  # noqa: E402

C.checks()
ev = S.events()
first = []                                   # first stop's loss in units of a 2.25% stop's planned loss at the same size
for day, sym, hi, lo, cl, ks in ev:
    first.append(-1.0)                       # the re-sell stop and the first stop are both ~2.25% at ~20x: -1 R each
print(f"{len(ev)} run-4 stop-losses (each about -1 R; at 49% margin x 20x on $3,184 one R is about $750)\n")
print("Each stop-loss + its re-sell, STRICT (lenient in brackets):")
print(f"  {'re-sell':44s} | {'none':>5s} | {'softens':>11s} | {'wins back all':>13s} | {'loses again':>11s} | {'avg per stop-loss':>19s}")
for w in (("now", 0), ("off", 0.03), ("off", 0.05), ("off", 0.10)):
    for t in (0.025, 0.05, None):
        out = []
        for le in (False, True):
            rs = [S.resell(e, w, ("fixed", 0.0225), t, le) for e in ev]
            R = np.array([r[0] if r else 0.0 for r in rs])
            none = sum(r is None for r in rs)
            got = R[[r is not None for r in rs]]
            out.append((none, (got > 0).mean(), (got >= 1).mean(), (got <= -0.9).mean(), (-1 + R).mean()))
        (n0, s0, f0, l0, a0), (_, s1, f1, l1, a1) = out
        nm = S.name(w, ("fixed", 0.0225), t).replace("stop +2.25%", "").strip()
        nm = " ".join(nm.split())
        print(f"  {nm:44s} | {n0:5d} | {s0:4.0%} ({s1:3.0%}) | {f0:6.0%} ({f1:3.0%}) | {l0:4.0%} ({l1:3.0%}) | "
              f"{a0:+5.2f} R ({a1:+5.2f})")
print("\n  no re-sell at all: every stop-loss = -1.00 R")
print("  softens = the re-sell made money; wins back all = made >= 1 R; loses again = a second full stop")
