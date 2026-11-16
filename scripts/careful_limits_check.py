"""The product-table check (owner 2026-11-15: "are you sure about the drop?"): the careful sims sized 161 of 173 coins
on DEFAULT terms (20x up to $50,000) because out/delta_margin/product_limits_2026-11-15.json held only 12 coins.
Delta's live list (out/delta_margin/product_limits_live_all.json, same units, the 12 identical) has 15 picked coins at
20x only up to $10,000 and 2 at 10x. Re-run the key results with the live terms for every coin, the rule still capped at
20x (B.sizing and C.notional_for cap leverage at the coin's own maximum; above-20x coins are held to 20x here).

    PYTHONPATH=. python scripts/careful_limits_check.py
"""
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402

LIVE = json.load(open("out/delta_margin/product_limits_live_all.json"))
for s, p in LIVE.items():
    if p["im"] < 5.0:                                   # the rule is "up to 20x": a 50-200x coin is held to 20x
        p.update(im=5.0, mm=min(p["mm"], 2.5))


def run(tag):
    import careful_big_one as BO
    import careful_goal_2000 as G
    import replay_bot as R
    days = D.load()
    out = {}
    for mg, ex, tg in ((0.75, 0.20, 1000.0), (0.50, 0.25, 1000.0)):
        BO.MARGIN, BO.EXTRA, BO.TARGET = mg, ex, tg
        for le in (False, True):
            rs = [BO.t1(p[0][1], p[0][2], p[0][3], p[0][4], D.START, le) for d, p in days]
            rs = [r for r in rs if r]
            g = [k for k, d in (G.run_75(days, i, le) for i in range(len(days)))]
            out[(mg, le)] = (np.mean([r[1] for r in rs]), np.mean([r[0] == "TARGET" for r in rs]), g.count("GOAL"), g.count("FLOOR"))
    for le in (False, True):
        a = R.reference(days, le, month_by_exit=True)
        out[("C10", le)] = a[-1][6]
    return out


def main():
    base = run("saved")
    D.PRODUCTS.clear()
    D.PRODUCTS.update(LIVE)
    live = run("live")
    print("Saved table (12 coins, the rest 20x to $50k)  vs  Delta's live terms for all 173 coins (held to max 20x):\n")
    for mg in (0.75, 0.50):
        for le in (False, True):
            b, l_ = base[(mg, le)], live[(mg, le)]
            print(f"   {mg:.0%} margin, +$1,000, {'LENIENT' if le else 'STRICT '}: per trade ${b[0]:+6.0f} -> ${l_[0]:+6.0f}; target first {b[1]:.0%} -> {l_[1]:.0%}; "
                  f"+$2,000 goal first {b[2]} -> {l_[2]} of 615; floor first {b[3]} -> {l_[3]}")
    for le in (False, True):
        print(f"   exit C at 10% margin (approved), full history, {'LENIENT' if le else 'STRICT '}: ${base[('C10', le)]:,.2f} -> ${live[('C10', le)]:,.2f}")


if __name__ == "__main__":
    main()
