"""Streak and loss rules on rule A ladder, judged per docs/fade_streak_rules_prereg.md (committed before this ran).

Trade rows (nothing re-simulated): Binance 2021-24 and 2025-26 (`R_A`), Delta 2025/2026 with funding + bot entry,
all loaded by scripts/fade_gates.load_sets(). Sizing as scripts/fade_ladder_account.py.

    PYTHONPATH=. .venv/bin/python scripts/fade_streak_rules.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_gates as G  # noqa: E402
import fade_ladder_account as LA  # noqa: E402

DAY = 86400
GRIDS = {"R1 loss-streak pause": [("N", n, "D", d) for n in (3, 4, 5) for d in (1, 2)],
         "R2 win-streak size-up": [("W", w) for w in (3, 4, 5)],
         "R3 big-loss pause": [("X", x) for x in (1.0, 1.2)],
         "R4 per-coin cooldown": [("C", c) for c in (3, 7, 14)]}


def run(trades: pd.DataFrame, rule: str | None = None, p: tuple = ()) -> dict:
    """Ladder 10/5/3 with one rule on top. Returns R per unit risk, end balance, worst drop, trades taken."""
    prm = dict(zip(p[0::2], p[1::2]))
    eq = peak = LA.START
    worst, units, earned, taken = 0.0, 0.0, 0.0, 0
    loss_run = win_run = 0
    pause_until = -1
    stopped_at: dict[str, int] = {}
    boost = False
    for t, day in trades.sort_values("time", kind="stable").groupby("time"):
        if t < pause_until:
            continue
        if rule == "R4 per-coin cooldown":
            day = day[[t - stopped_at.get(s, -10**12) > prm["C"] * DAY for s in day.symbol]]
            if day.empty:
                continue
        k = LA.step_risk(1 - eq / peak, LA.LADDER) * (1.5 if boost else 1.0)
        dist, r = day.dist.to_numpy(float), day.R_A.to_numpy(float)
        ks = np.minimum(k, LA.POSITION_CAP * dist)
        ks = ks * min(1.0, 1.0 / float(np.sum(ks / dist)))
        eq = max(eq + eq * float(np.sum(ks * r)), 0.0)
        units += float(ks.sum())
        earned += float(np.sum(ks * r))
        taken += len(r)
        peak = max(peak, eq)
        worst = max(worst, 1 - eq / peak)
        big = False
        for sym, x, how in zip(day.symbol, r, day["A_how"] if "A_how" in day else ["?"] * len(r)):
            loss_run, win_run = (loss_run + 1, 0) if x <= 0 else (0, win_run + 1)
            big |= x <= -prm.get("X", 9e9)
            if how == "stop" or x <= -0.9:
                stopped_at[sym] = t
        boost = rule == "R2 win-streak size-up" and win_run >= prm["W"]
        if rule == "R1 loss-streak pause" and loss_run >= prm["N"]:
            pause_until, loss_run = t + (1 + prm["D"]) * DAY, 0
        if rule == "R3 big-loss pause" and big:
            pause_until = t + 2 * DAY
    return {"rpu": earned / units if units else np.nan, "end": eq, "drop": worst, "n": taken}


def main() -> None:
    sets = G.load_sets()
    for k, v in list(sets.items()):
        v = v.dropna(subset=["R_A"]).copy()        # Delta picks with no trade in the first 15 min (no bot entry)
        if "A_how" not in v and "A_bot_how" in v:
            v["A_how"] = v["A_bot_how"]
        sets[k] = v
    dev = [k for k in sets if k.startswith("dev")]
    held = [k for k in sets if k.startswith("held")]
    base = {k: run(sets[k]) for k in sets}
    print(f"{'':34s} " + " | ".join(f"{k.split(' ', 1)[1]:>24s}" for k in dev + held))
    print(f"{'plain ladder (R/unit, end, drop)':34s} " + " | ".join(
        f"{base[k]['rpu']:+.3f} ${base[k]['end']:>7,.0f} {base[k]['drop']:4.0%}" for k in dev + held))
    for rule, grid in GRIDS.items():
        res = {p: {k: run(sets[k], rule, p) for k in dev} for p in grid}
        pooled = {p: sum(res[p][k]["rpu"] * res[p][k]["n"] for k in dev) / sum(res[p][k]["n"] for k in dev) for p in grid}
        best = max(grid, key=lambda p: pooled[p])
        better = sum(res[best][k]["rpu"] > base[k]["rpu"] for k in dev)
        print(f"\n{rule}: dev pooled R/unit by setting: " + ", ".join(f"{p[1::2]} {pooled[p]:+.3f}" for p in grid))
        print(f"  chosen {dict(zip(best[0::2], best[1::2]))}: beats the ladder in {better}/3 dev years -> "
              f"{'PROMOTED' if better >= 2 else 'not promoted'}")
        h = {k: run(sets[k], rule, best) for k in held}
        print(f"  {'held-out (R/unit, end, drop)':32s} " + " | ".join(
            f"{h[k]['rpu']:+.3f} ${h[k]['end']:>7,.0f} {h[k]['drop']:4.0%}" for k in held))
        ok = sum(h[k]["rpu"] > base[k]["rpu"] and h[k]["end"] >= base[k]["end"] and h[k]["drop"] <= base[k]["drop"] + 0.05
                 for k in held)
        verdict = "PASS" if better >= 2 and ok >= 3 else "FAIL"
        print(f"  held-out sets where it beats the ladder on all three: {ok}/4 -> {verdict}")


if __name__ == "__main__":
    main()
