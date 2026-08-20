"""FULL FACTORIAL OVER 1m FILTERS. NO 5m ANYTHING. TRAIN + VALIDATION.

    PYTHONPATH=. python3 -u -m deltabt.research.run_1m_grid

The earlier passes tested four filter combinations and called it a search. It
was not one. This is the factorial: every on/off combination of the 1m filters
that can be built from the frozen indicators, with the 5m regime absent
entirely -- not neutralised as a special case, simply never referenced.

TWO THINGS THE EARLIER RUNS CONFLATED, SEPARATED HERE:

  * ADX and DI. `adx1_long` in hwpr is `(adx >= 25) & (+DI > -DI)` -- one key
    holding two independent ideas, a trend-STRENGTH gate and a trend-DIRECTION
    gate. No pre-declared arm can hold one without the other, so "remove ADX"
    has never actually been tested; it always removed DI too.

  * stop width. Cost is a law here: cost_r x stop_pct = 0.159, measured across
    a twentyfold range in run_fixed_sl. A filter set tested at a 0.25% stop is
    condemned by arithmetic before its signal is examined. Every cell below
    therefore runs at a fixed 1.00% stop, where cost is 0.159R -- real, but not
    disqualifying -- so the comparison is between FILTERS and nothing else.

WPR is treated as a six-level factor rather than on/off, because "which WPR
rule" is the question that started this and the variants disagree by orders of
magnitude in firing rate. `band` is new: it admits the middle of the range and
excludes BOTH extremes, which is the one shape the pre-declared variants never
tried -- A and B exclude only the counter-trend extreme, and C requires the
extreme itself.

HOW TO READ THE OUTPUT, WHICH MATTERS MORE THAN THE OUTPUT. 47 configurations
see train and validation here. The best of 47 will look good even if every one
of them is worthless -- that is what a maximum over noise does. So the summary
reports the whole DISTRIBUTION of validation results, not a winner. A single
cell standing out means nothing unless the distribution itself is displaced.
TEST IS NOT TOUCHED.
"""

from __future__ import annotations

import itertools
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

OUT = OUT_DIR / "grid1m"
STUDY = int(pd.Timestamp("2025-01-01", tz="UTC").timestamp())
SL_PCT, TARGET_R, MAX_STOP_PCT = 0.010, 2.0, 0.10


def components(df: pd.DataFrame, C: dict) -> dict:
    """Every 1m filter as an independent mask, ADX and DI finally separate."""
    h = df["high"].to_numpy("float64")
    l = df["low"].to_numpy("float64")
    c = df["close"].to_numpy("float64")
    p1, m1, adx1 = ind.dmi(h, l, c, hwpr.DI_PERIOD, hwpr.ADX_PERIOD)
    wpr = C["wpr"]
    prev = np.concatenate(([np.nan], wpr[:-1]))
    with np.errstate(invalid="ignore"):
        rising, falling = wpr > prev, wpr < prev
        return dict(
            st=(C["st1_long"], C["st1_short"]),
            adx=(adx1 >= hwpr.ADX_MIN, adx1 >= hwpr.ADX_MIN),
            di=(p1 > m1, m1 > p1),
            wpr_none=(np.ones_like(rising), np.ones_like(rising)),
            wpr_A=(C["wprA_long"], C["wprA_short"]),
            wpr_B=(C["wprB_long"], C["wprB_short"]),
            wpr_C=(C["wprC_long"], C["wprC_short"]),
            wpr_pullback=(C["pullback_long"], C["pullback_short"]),
            # Excludes BOTH extremes: enter while there is still range left to
            # travel in the trade's direction, which is the shape no
            # pre-declared variant tests.
            wpr_band=(((wpr > -80.0) & (wpr < -30.0) & rising),
                      ((wpr < -20.0) & (wpr > -70.0) & falling)),
        )


WPR_LEVELS = ["wpr_none", "wpr_A", "wpr_B", "wpr_C", "wpr_pullback", "wpr_band"]


def _conditions(C: dict, comp: dict, use_st, use_adx, use_di, wpr_key) -> dict:
    """Compose a custom filter set and smuggle it through the frozen path.

    hwpr.run composes signals itself via arm_signals, which cannot express an
    arbitrary combination. Arm E is `f5_long & wprA_long`, so putting the
    custom mask in f5_* and making wprA_* all-True makes arm E evaluate exactly
    the mask -- the frozen simulator, cost model and funding accrual are still
    the code that produced the pre-registered numbers.
    """
    n = len(C["close"])
    lo = np.ones(n, dtype=bool)
    sh = np.ones(n, dtype=bool)
    for on, key in ((use_st, "st"), (use_adx, "adx"), (use_di, "di")):
        if on:
            lo &= comp[key][0]
            sh &= comp[key][1]
    lo &= comp[wpr_key][0]
    sh &= comp[wpr_key][1]

    c = C["close"]
    D = dict(C)
    D["f5_long"], D["f5_short"] = lo, sh
    D["wprA_long"] = np.ones(n, dtype=bool)
    D["wprA_short"] = np.ones(n, dtype=bool)
    D["st1"] = c
    D["leg_lo"] = c * (1.0 - SL_PCT)
    D["leg_hi"] = c * (1.0 + SL_PCT)
    return D


def light(df: pd.DataFrame) -> dict:
    """Mean/SE only. The full bootstrap is far too slow for 94 cells."""
    if df.empty:
        return dict(trades=0)
    net = df.r_net.to_numpy("float64")
    de = trade_design_effect(df)
    n_eff = max(de["n_eff"], 1.0)
    se = float(net.std(ddof=1)) / np.sqrt(n_eff) if len(net) > 1 else float("nan")
    return dict(trades=int(len(df)), n_eff=round(n_eff, 1),
                win_rate=round(float((net > 0).mean()), 4),
                gross_r=round(float(df.r_gross.mean()), 4),
                cost_r=round(float(df.cost_r.mean()), 4),
                net_r=round(float(net.mean()), 4), se=round(se, 4),
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
    last = min(int(d["df"].time.iloc[-1]) for d in data.values())
    span = last - STUDY
    TR = (STUDY, STUDY + int(span * 0.6))
    VA = (STUDY + int(span * 0.6), STUDY + int(span * 0.8))
    print(f"universe {universe}")
    print(f"train {pd.Timestamp(TR[0],unit='s').date()} -> {pd.Timestamp(TR[1],unit='s').date()}  "
          f"valid {pd.Timestamp(VA[0],unit='s').date()} -> {pd.Timestamp(VA[1],unit='s').date()}  "
          f"test {pd.Timestamp(VA[1],unit='s').date()} -> {pd.Timestamp(last,unit='s').date()} [LOCKED]")
    print(f"fixed stop {SL_PCT:.2%}, target {TARGET_R}R, NO 5m FILTER ANYWHERE\n")

    for s, d in data.items():
        d["C"] = hwpr.build_conditions(d["df"])
        d["comp"] = components(d["df"], d["C"])

    def measure(use_st, use_adx, use_di, wpr_key, window):
        frames = []
        for s, d in data.items():
            C = _conditions(d["C"], d["comp"], use_st, use_adx, use_di, wpr_key)
            r = hwpr.run(d["df"], d["mark"], d["funding"], d["costs"], C,
                         arm="E", wpr_variant="A", target_r=TARGET_R,
                         start=window[0], end=window[1],
                         tradable=d["tradable"], max_stop_pct=MAX_STOP_PCT)
            f = r.to_frame()
            if len(f):
                f = f[(f.entry_time >= window[0]) & (f.entry_time < window[1])]
            if len(f):
                frames.append(f)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    rows = []
    combos = [(a, b, c, w) for a, b, c in itertools.product([0, 1], repeat=3)
              for w in WPR_LEVELS if (a or b or c or w != "wpr_none")]
    print(f"{len(combos)} configurations\n")
    hdr = f"{'ST':>3}{'ADX':>5}{'DI':>4}  {'WPR':<13}"
    print(hdr + f"{'n(tr)':>8}{'net(tr)':>9}{'t':>7}   {'n(va)':>8}{'net(va)':>9}{'t':>7}{'/day':>7}")
    print("-" * 100)
    for use_st, use_adx, use_di, wkey in combos:
        t = light(measure(use_st, use_adx, use_di, wkey, TR))
        v = light(measure(use_st, use_adx, use_di, wkey, VA))
        rows.append(dict(st=use_st, adx=use_adx, di=use_di, wpr=wkey,
                         **{f"tr_{k}": x for k, x in t.items()},
                         **{f"va_{k}": x for k, x in v.items()}))
        line = (f"{use_st:>3}{use_adx:>5}{use_di:>4}  {wkey.replace('wpr_',''):<13}")
        if not t.get("trades") or not v.get("trades"):
            print(line + "       -- insufficient --")
            continue
        print(line + f"{t['trades']:>8,}{t['net_r']:>+9.4f}{t['t']:>7.1f}   "
              f"{v['trades']:>8,}{v['net_r']:>+9.4f}{v['t']:>7.1f}{v['trades_per_day']:>7.1f}")

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "grid.csv", index=False)

    ok = df[(df.va_trades.fillna(0) >= 200) & (df.tr_trades.fillna(0) >= 200)]
    print("\n" + "=" * 100)
    print(f"DISTRIBUTION over {len(ok)} configurations with >=200 trades in both windows")
    print("=" * 100)
    if len(ok):
        v = ok.va_net_r
        print(f"  validation net_r:  min {v.min():+.4f}  median {v.median():+.4f}  "
              f"max {v.max():+.4f}")
        print(f"  configurations with POSITIVE validation net: {(v > 0).sum()} / {len(ok)}")
        print(f"  ... and also positive on train:              "
              f"{((v > 0) & (ok.tr_net_r > 0)).sum()} / {len(ok)}")
        best = ok.loc[v.idxmax()]
        print(f"\n  best by validation: ST={best.st} ADX={best.adx} DI={best.di} "
              f"{best.wpr}  train {best.tr_net_r:+.4f} (n={int(best.tr_trades):,})  "
              f"valid {best.va_net_r:+.4f} (n={int(best.va_trades):,})")
        print("  A maximum over many cells is not evidence. What would be evidence")
        print("  is the DISTRIBUTION sitting above zero, or train and validation")
        print("  agreeing in sign for the same cell rather than for different ones.")
    json.dump(rows, open(OUT / "grid.json", "w"), indent=2, default=str)
    print(f"\nwrote {OUT/'grid.csv'}\nTEST WAS NOT TOUCHED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
