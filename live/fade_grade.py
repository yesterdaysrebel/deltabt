"""Weekly grade of the gainer-fade model (docs/fade_model_switch_on_rule.md).

Runs ONLY as a separate process -- `python -m live grade-model` on a fade stack (live/learning.py
dispatches here) -- never inside the trading runner: tests/live_exec/test_model_never_trades.py
keeps this module, like live/learning.py, unreachable from trading code.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

from live import fade as F
from live.fade_learning import FEATURES

log = logging.getLogger(__name__)

MIN_TRAIN, MIN_TEST = 400, 50
N_RANDOM = 2000
WEEK = 7 * F.DAY


def _model():
    from deltabt.signal_model import model
    return model()


def grade_week(rows: pd.DataFrame, week_start: int, seed: int = 0) -> dict:
    """Train on labelled candidates before week_start - 1 day, test on the week: each day the
    model's own top 2 vs the rule's top 2 (rank 1-2) vs 2 random candidates, by mean R_A and R_QUICK."""
    rows = rows.dropna(subset=["r_a", "r_quick"])
    train = rows[rows.day < week_start - F.DAY]
    test = rows[(rows.day >= week_start) & (rows.day < week_start + 7 * F.DAY)]
    res = {"n_train": len(train), "n_test": len(test), "days": int(test.day.nunique())}
    if len(train) < MIN_TRAIN or len(test) < MIN_TEST or train.r_a.gt(0).nunique() < 2:
        res["summary"] = (f"not graded: {len(train)} training rows (need {MIN_TRAIN}), "
                          f"{len(test)} this week (need {MIN_TEST})")
        return res
    # a reading with no value in any training row is left out (the model refuses an all-empty
    # input; the same trap stopped the metals grade until #141)
    use = [f for f in FEATURES if train[f].notna().any()]
    res["readings_left_out"] = [f for f in FEATURES if f not in use]
    m = _model().fit(train[use], (train.r_a > 0).astype(int))
    test = test.assign(score=m.predict_proba(test[use])[:, 1])
    model_pk = test.sort_values(["day", "score"], ascending=[True, False]).groupby("day").head(F.TOP_N)
    rule_pk = test[test["rank"] <= F.TOP_N]
    rng = np.random.default_rng(seed)
    by_day = {d: g for d, g in test.groupby("day")}
    rand = {"a": [], "quick": []}
    for _ in range(N_RANDOM):
        ra, rq = [], []
        for g in by_day.values():
            idx = rng.choice(len(g), min(F.TOP_N, len(g)), replace=False)
            ra += list(g.r_a.to_numpy()[idx])
            rq += list(g.r_quick.to_numpy()[idx])
        rand["a"].append(np.mean(ra))
        rand["quick"].append(np.mean(rq))
    for k in ("a", "quick"):
        col = f"r_{k}"
        res[f"model_{k}"] = float(model_pk[col].mean())
        res[f"rule_{k}"] = float(rule_pk[col].mean())
        res[f"random_{k}"] = float(np.mean(rand[k]))
        res[f"random_p_{k}"] = float((np.array(rand[k]) >= model_pk[col].mean()).mean())
    res["picks"] = [[int(r.day), r.symbol, round(float(r.score), 4), round(float(r.r_a), 4),
                     round(float(r.r_quick), 4), int(r["rank"])] for _, r in model_pk.iterrows()]
    res["summary"] = (f"model's daily top 2: A {res['model_a']:+.3f}R / quick {res['model_quick']:+.3f}R vs the "
                      f"rule's {res['rule_a']:+.3f}R / {res['rule_quick']:+.3f}R vs random "
                      f"{res['random_a']:+.3f}R / {res['random_quick']:+.3f}R; random did as well "
                      f"{100 * res['random_p_a']:.0f}% of the time (A). It does not choose trades.")
    return res



async def grade_pending(con, now: float) -> list[str]:
    """Grade every complete week (Monday-Sunday UTC) not graded yet, oldest first."""
    first = await con.fetchval("SELECT min(day) FROM fade_candidates WHERE r_a IS NOT NULL")
    if first is None:
        return ["not graded: nothing labelled yet"]
    raw = await con.fetch("SELECT day, symbol, rank, readings, r_a, r_quick FROM fade_candidates WHERE r_a IS NOT NULL")
    rows = pd.DataFrame([{"day": int(x["day"].timestamp()), "symbol": x["symbol"], "r_a": x["r_a"],
                          "r_quick": x["r_quick"],
                          **(json.loads(x["readings"]) if isinstance(x["readings"], str) else x["readings"]),
                          "rank": x["rank"]} for x in raw])
    for f in FEATURES:
        if f not in rows:
            rows[f] = np.nan
    rows[FEATURES] = rows[FEATURES].astype(float)
    ts = pd.Timestamp(int(first.timestamp()), unit="s", tz="UTC")
    week = int((ts.normalize() - pd.Timedelta(days=ts.weekday())).timestamp())
    out = []
    while week + WEEK <= now:
        if not await con.fetchval("SELECT 1 FROM fade_grades WHERE week_start = $1", F._ts(week)):
            res = grade_week(rows, week)
            await con.execute("INSERT INTO fade_grades (week_start, n_train, n_test, days, summary, detail) "
                              "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING", F._ts(week), res["n_train"],
                              res["n_test"], res.get("days", 0), res["summary"], json.dumps(res, default=float))
            out.append(f"week of {pd.Timestamp(week, unit='s').date()}: {res['summary']}")
        week += WEEK
    return out or ["nothing new to grade"]


def main(database_url: str) -> int:
    import asyncio
    import time

    import asyncpg

    from app.persistence.db_auth import connect_kwargs

    async def run():
        con = await asyncpg.connect(**connect_kwargs(database_url))
        try:
            from live.fade_learning import ensure_schema
            await ensure_schema(con)
            for line in await grade_pending(con, time.time()):
                print("Fade model grade:", line)
        finally:
            await con.close()
    asyncio.run(run())
    return 0
