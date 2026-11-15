"""Shared by the three careful-sim implementations: the frozen data ONLY (no trading logic). See docs/careful_sim_spec.md."""
import json
import numpy as np
import pandas as pd

START = 3184.0
PRODUCTS = json.load(open("out/delta_margin/product_limits_2026-11-15.json"))
VARIANTS = {
    "run4":             dict(resell=False, floor=1000.0, bank=False),
    "run4+resell":      dict(resell=True,  floor=1000.0, bank=False),
    "run4+resell+bank": dict(resell=True,  floor=1000.0, bank=True),
    "run4 no floor":    dict(resell=False, floor=50.0,   bank=False),
    "run4+resell no floor": dict(resell=True, floor=50.0, bank=False),
}


def load(source=None):
    """-> list of days: (day Timestamp, [(rank, symbol, hi, lo, cl), ...] sorted by rank). Picks without Delta candles
    are left out (counted by the caller via picks.csv)."""
    P = pd.read_csv("out/careful/picks.csv", parse_dates=["day"])
    z = np.load("out/careful/dataset.npz")
    hi, lo, cl = z["hi"], z["lo"], z["cl"]
    days = {}
    for i, x in enumerate(P.itertuples()):
        if x.minutes == 0 or not np.isfinite(cl[i, 2]) or (source and x.source != source):
            continue
        days.setdefault(x.day, []).append((int(x.rank), x.symbol, hi[i], lo[i], cl[i]))
    return [(d, sorted(v, key=lambda t: t[0])) for d, v in sorted(days.items())]
