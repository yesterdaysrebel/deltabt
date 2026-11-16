"""Why the moving stop (lock after a 1% fall, then the 0.8% trail) cannot save the full stop-losses: for every full
stop under the final rules (fixed $3,184 per trade), how far it went in our favour before the stop and how fast.

    PYTHONPATH=. python scripts/run4_stop_anatomy.py
"""
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402
import run4_trailing as T  # noqa: E402

C.checks()
hist, *_ = C.paths_history()
rows = []
for day, sym, hi, lo, cl, k0 in hist + C.paths_real():
    r = T.short(sym, hi, lo, cl, k0, 0.49 * C.START, 500.0, 200.0, 0.25 * C.START, False, 0.008, None)
    if r is None:
        continue
    e = cl[k0 - 1] * 1.05
    best = 1 - np.min(lo[r[2] + 1:r[3]]) / e if r[3] > r[2] + 1 else 0.0   # best fall before the exit minute
    rows.append((r[0], r[1], best, r[3] - r[2]))
stops = [x for x in rows if x[0] in ("STOP", "LIQ")]
print(f"fills {len(rows)}; full stops {len(stops)} ({len(stops)/len(rows):.0%}), total ${sum(x[1] for x in stops):+,.0f} strict\n")
b = np.array([x[2] for x in stops]); m = np.array([x[3] for x in stops])
print("best move in our favour before the stop:")
for lo_, hi_ in ((-1, .0025), (.0025, .005), (.005, .0075), (.0075, .01)):
    print(f"   {max(lo_, 0):.2%} .. {hi_:.2%}: {((b >= lo_) & (b < hi_)).sum():4d}")
print(f"   reached 1% (would have locked): {(b >= .01).sum()}  <- impossible by construction: those become LOCKED, not stops")
print("\nminutes from the fill to the stop:")
for a, z in ((1, 1), (2, 5), (6, 30), (31, 240), (241, 1440)):
    print(f"   {a:4d} .. {z:4d} min: {((m >= a) & (m <= z)).sum():4d}")
print(f"\nmedian {np.median(m):.0f} min; stopped within 5 min of the fill: {(m <= 5).mean():.0%}")
print(f"never traded below the entry at all: {(b <= 0).sum()}")

print("\nCould an EARLIER stop move save them? Move the stop to break-even (net ~$0) after a smaller fall, then the")
print("same lock at 1% and trail; per trade on a fixed $3,184, history + 20 real picks, STRICT .. LENIENT:")
allp = hist + C.paths_real()
for be in (None, 0.0075, 0.005, 0.0025):
    out = []
    for le in (False, True):
        rs = [T.short(s, h, l, c, k, 0.49 * C.START, 500.0, 200.0, 0.25 * C.START, le, 0.008, be) for _, s, h, l, c, k in allp]
        rs = [r for r in rs if r]
        out.append((sum(r[1] for r in rs), sum(r[0] == "TARGET" for r in rs), sum(r[0] in ("STOP", "LIQ") for r in rs),
                    sum(r[0] == "BE" for r in rs)))
    (s0, t0, f0, b0), (s1, t1, f1, b1) = out
    nm = "none (as built)" if be is None else f"after a {be:.2%} fall"
    print(f"   {nm:22s} total ${s0:+9,.0f} .. ${s1:+9,.0f} | targets {t0}..{t1} | full stops {f0}..{f1} | break-even exits {b0}..{b1}")
