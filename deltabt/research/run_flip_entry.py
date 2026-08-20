"""ENTER AT THE SUPERTREND FLIP, AND AT EVERY LAG AFTER IT. TRAIN + VALIDATION.

    PYTHONPATH=. python3 -u -m deltabt.research.run_flip_entry

Everything measured so far used Supertrend STATE -- `dir1 < 0`, true for dozens
of consecutive bars -- so a signal could fire at any point in a leg, including
its last bar. The live BTCUSD long that started this thread entered 74% of the
way through the only trend leg in thirteen hours and needed the leg to extend
another 43% to reach target. That is not a filter problem; it is a timing one,
and no filter tested can express "early in the move".

The flip can. `dir1` changing sign is one bar, and it is the earliest bar at
which the trend is knowable at all.

WHY A LAG SWEEP RATHER THAN JUST THE FLIP BAR. Testing the flip alone answers
"is entering at the flip good", which is worth little on its own -- it could be
better than state-entry while both are worthless. Entering exactly k bars after
each flip, for k across two orders of magnitude, maps net expectancy as a
FUNCTION of how late the entry is. That shape is the actual hypothesis:

  falling in k   -> entering early matters, and the late entries the state
                    rule was producing were the problem
  flat in k      -> the timing thesis is wrong; position within the leg
                    carries no information and the story about the 74% entry
                    was a coincidence worth abandoning
  rising in k    -> the opposite of the thesis, and worth knowing

A single number cannot distinguish those three. A curve can.

THE HELD-OUT SET IS ALREADY SPENT (run_user_adx, 2026-08-20). This runs on
train and validation only, both of which have now been read many times. So
NOTHING HERE CAN CONFIRM ANYTHING. A flat or falling curve is evidence against
the thesis and can be trusted, because it is a negative on data that has been
mined for positives. A rising curve is a hypothesis with no clean data left to
test it on, and would need new market history -- realistically months -- before
it meant anything. That asymmetry is the whole reason to state it in advance.

Stop is fixed at 1% so cost is held at 0.159R and only the entry timing moves.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from deltabt import indicators as ind
from deltabt.config import OUT_DIR
from deltabt.costs import SymbolCosts
from deltabt.data.quality import tradable_mask
from deltabt.data.store import CandleStore, ProductCatalog
from deltabt.research import hwpr
from deltabt.research.stats import trade_design_effect

OUT = OUT_DIR / "flip_entry"
STUDY = int(pd.Timestamp("2025-01-01", tz="UTC").timestamp())
SL_PCT = 0.010
LAGS = (0, 1, 2, 3, 5, 10, 20, 40)
RR_GRID = (1.0, 1.5, 2.0)


def flip_lag(dir1: np.ndarray) -> np.ndarray:
    """Bars elapsed since the Supertrend last changed sign. 0 on the flip bar."""
    n = dir1.size
    lag = np.zeros(n, dtype=np.int64)
    last = 0
    for i in range(1, n):
        if dir1[i] != dir1[i - 1]:
            last = i
        lag[i] = i - last
    return lag


def conditions(C: dict, lo, sh):
    n = len(C["close"])
    c = C["close"]
    D = dict(C)
    D["f5_long"], D["f5_short"] = lo, sh
    D["wprA_long"] = np.ones(n, dtype=bool)
    D["wprA_short"] = np.ones(n, dtype=bool)
    D["st1"] = c
    D["leg_lo"] = c * (1.0 - SL_PCT)
    D["leg_hi"] = c * (1.0 + SL_PCT)
    return D


def stats(df: pd.DataFrame) -> dict:
    if df.empty:
        return dict(trades=0)
    net = df.r_net.to_numpy("float64")
    de = trade_design_effect(df)
    n_eff = max(de["n_eff"], 1.0)
    se = float(net.std(ddof=1)) / np.sqrt(n_eff) if len(net) > 1 else float("nan")
    return dict(trades=int(len(df)),
                win_rate=round(float((net > 0).mean()), 4),
                gross_r=round(float(df.r_gross.mean()), 4),
                cost_r=round(float(df.cost_r.mean()), 4),
                net_r=round(float(net.mean()), 4),
                t=round(float(net.mean() / se), 2) if se and np.isfinite(se) else None,
                trades_per_day=round(len(df) / max(
                    (df.entry_time.max() - df.entry_time.min()) / 86400, 1), 2))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    universe = pd.read_csv(OUT_DIR / "hwpr_universe.csv").symbol.tolist()
    store, cat = CandleStore(), ProductCatalog()
    data = {}
    for s in universe:
        ltp = store.read(s, "ltp", "1m")
        ltp = ltp[ltp.time >= STUDY].reset_index(drop=True)
        data[s] = dict(df=ltp, mark=store.read(s, "mark", "1m"),
                       funding=store.read(s, "funding", "1h"),
                       costs=SymbolCosts.from_spec(cat.get(s), slippage_bps=2.0),
                       tradable=tradable_mask(ltp))
        C = hwpr.build_conditions(ltp)
        data[s]["C"] = C
        h = ltp["high"].to_numpy("float64")
        l = ltp["low"].to_numpy("float64")
        c = ltp["close"].to_numpy("float64")
        _st, d1 = ind.supertrend(h, l, c, hwpr.ST_MULT, hwpr.ST_PERIOD)
        data[s]["lag"] = flip_lag(d1)
        _p, _m, adx = ind.dmi(h, l, c, hwpr.DI_PERIOD, hwpr.ADX_PERIOD)
        with np.errstate(invalid="ignore"):
            data[s]["adx_ok"] = adx >= hwpr.ADX_MIN

    last = min(int(d["df"].time.iloc[-1]) for d in data.values())
    span = last - STUDY
    TR = (STUDY, STUDY + int(span * 0.6))
    VA = (STUDY + int(span * 0.6), STUDY + int(span * 0.8))
    print("ENTRY AT THE 1m SUPERTREND(10,2) FLIP, AND AT LAGS AFTER IT")
    print(f"fixed stop {SL_PCT:.2%}, no 5m filter, TEST SET ALREADY SPENT -- "
          "train and validation only\n")

    def measure(build, target_r, window):
        frames = []
        for s, d in data.items():
            lo, sh = build(d)
            r = hwpr.run(d["df"], d["mark"], d["funding"], d["costs"],
                         conditions(d["C"], lo, sh), arm="E", wpr_variant="A",
                         target_r=target_r, start=window[0], end=window[1],
                         tradable=d["tradable"], max_stop_pct=0.10)
            f = r.to_frame()
            if len(f):
                f = f[(f.entry_time >= window[0]) & (f.entry_time < window[1])]
            if len(f):
                frames.append(f)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    rows = []

    def emit(label, build, rr):
        t = stats(measure(build, rr, TR))
        v = stats(measure(build, rr, VA))
        rows.append(dict(config=label, rr=rr,
                         **{f"tr_{k}": x for k, x in t.items()},
                         **{f"va_{k}": x for k, x in v.items()}))
        if not t.get("trades") or not v.get("trades"):
            print(f"  {label:34} RR={rr:<4} -- too few trades --")
            return
        print(f"  {label:34} RR={rr:<4} "
              f"train n={t['trades']:>6,} win={t['win_rate']:.3f} "
              f"gross={t['gross_r']:+.4f} net={t['net_r']:+.4f} (t={t['t']:>5.1f})  |  "
              f"valid n={v['trades']:>5,} win={v['win_rate']:.3f} "
              f"gross={v['gross_r']:+.4f} net={v['net_r']:+.4f} (t={v['t']:>5.1f}) "
              f"{v['trades_per_day']:>5.1f}/day")

    def lag_builder(k):
        def build(d):
            at = d["lag"] == k
            return d["C"]["st1_long"] & at, d["C"]["st1_short"] & at
        return build

    def state_builder(d):
        return d["C"]["st1_long"], d["C"]["st1_short"]

    def flip_adx_builder(d):
        at = d["lag"] == 0
        return (d["C"]["st1_long"] & at & d["adx_ok"],
                d["C"]["st1_short"] & at & d["adx_ok"])

    for rr in RR_GRID:
        print("=" * 150)
        print(f"REWARD RATIO 1:{rr}")
        print("=" * 150)
        for k in LAGS:
            emit(f"entry {k} bar(s) after flip", lag_builder(k), rr)
        emit("flip + ADX(28)>=25", flip_adx_builder, rr)
        emit("REFERENCE: any bar in direction", state_builder, rr)
        print()

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "lags.csv", index=False)
    json.dump(rows, open(OUT / "lags.json", "w"), indent=2, default=str)

    print("=" * 150)
    print("THE SHAPE, which is the point of the run")
    print("=" * 150)
    for rr in RR_GRID:
        sub = df[(df.rr == rr) & df.config.str.startswith("entry ")]
        if sub.empty:
            continue
        tr = "  ".join(f"k={c.split()[1]}:{n:+.3f}"
                       for c, n in zip(sub.config, sub.tr_net_r))
        va = "  ".join(f"k={c.split()[1]}:{n:+.3f}"
                       for c, n in zip(sub.config, sub.va_net_r))
        print(f"  RR 1:{rr}  train  {tr}")
        print(f"  RR 1:{rr}  valid  {va}")
    print("\nFalling in k supports the timing thesis; flat kills it. A negative")
    print("here is trustworthy; a positive has no unseen data left to confirm it.")
    print(f"\nwrote {OUT/'lags.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
