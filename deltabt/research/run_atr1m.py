"""1m-ONLY DECISION ARMS WITH AN ATR STOP, ON TRAIN + VALIDATION. TEST LOCKED.

    PYTHONPATH=. python3 -u -m deltabt.research.run_atr1m

The question, in one line: the live ATR arm decides on 5m and confirms on 1m,
which is the INVERSE of what deltabt.research.hwpr does. What happens if the
decision moves back to 1m, the 5m regime filter is dropped entirely, and the
structural stop is replaced by 2 x ATR(10)?

WHY THE 5m FILTER CANNOT SIMPLY BE DELETED FROM THE LIVE ARM TO FIND OUT: it
is part of the strategy hash, so changing it ends the running experiment. This
measures it on history instead, at no cost to the run.

NOTHING IN deltabt/research/hwpr.py IS MODIFIED OR RE-IMPLEMENTED. Both changes
are made by rewriting entries in the conditions dict that hwpr.run already
reads, so the frozen simulator, cost model and funding accrual are the same
code that produced the pre-registered numbers:

  * dropping the 5m filter  -> f5_long / f5_short set to all-True, after which
    the EXISTING pre-declared arms compose exactly the 1m subsets wanted:
        arm A -> 1m Supertrend + 1m ADX/DI + WPR
        arm C -> 1m Supertrend + WPR              (no ADX -- the live arm)
        arm B -> 1m Supertrend + 1m ADX/DI        (no WPR)
        arm T_C -> 1m Supertrend only

  * the ATR stop -> the simulator computes
        long  stop = min(st1[i], leg_lo[i])
        short stop = max(st1[i], leg_hi[i])
    so feeding st1 = close, leg_lo = close - k*ATR, leg_hi = close + k*ATR
    yields exactly the ATR stop with no change to the event loop. Entry is
    still the NEXT bar's open, so r_price absorbs the open/close gap the same
    way the live arm's fill does.

Every arm here is a DIAGNOSTIC. The best row is not a strategy: choosing it
because it won would be selecting on the same data that measured it. Validation
is reported beside train for exactly that reason, and TEST IS NOT TOUCHED.
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
from deltabt.research.run_hwpr import summarise

OUT = OUT_DIR / "atr1m"
STUDY = int(pd.Timestamp("2025-01-01", tz="UTC").timestamp())

#: The live ATR arm's geometry, so this is comparable to what is running.
ATR_PERIOD, ATR_MULT, TARGET_R, MAX_STOP_PCT = 10, 2.0, 2.0, 0.10

#: (label, arm key, what the arm means once f5 is neutralised)
ARMS = [
    ("1m ST + ADX/DI + WPR", "A",   "full 1m stack"),
    ("1m ST + WPR",          "C",   "no ADX -- matches the live arm's filters"),
    ("1m ST + ADX/DI",       "B",   "no WPR"),
    ("1m ST only",           "T_C", "trend alignment alone"),
]


def _with_atr_stop(C: dict, mult: float = ATR_MULT) -> dict:
    """A copy of C whose stop is `mult` x ATR. The 5m filter is left alone."""
    c = C["close"]
    a = ind.atr(C["high"], C["low"], c, ATR_PERIOD)
    D = dict(C)
    D["st1"] = c                      # neutral: min/max picks the ATR leg
    D["leg_lo"] = c - mult * a
    D["leg_hi"] = c + mult * a
    return D


def _without_5m(C: dict) -> dict:
    """A copy of C with the 5m regime filter neutralised to all-True."""
    D = dict(C)
    D["f5_long"] = np.ones_like(C["f5_long"], dtype=bool)
    D["f5_short"] = np.ones_like(C["f5_short"], dtype=bool)
    return D


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    universe = pd.read_csv(OUT_DIR / "hwpr_universe.csv").symbol.tolist()
    store, cat = CandleStore(), ProductCatalog()
    print(f"FROZEN UNIVERSE: {universe}")

    data = {}
    for s in universe:
        ltp = store.read(s, "ltp", "1m")
        ltp = ltp[ltp.time >= STUDY].reset_index(drop=True)
        data[s] = dict(df=ltp, mark=store.read(s, "mark", "1m"),
                       funding=store.read(s, "funding", "1h"),
                       costs=SymbolCosts.from_spec(cat.get(s), slippage_bps=2.0),
                       tradable=tradable_mask(ltp))
        print(f"  {s}: {len(ltp):,} 1m bars")

    last = min(int(d["df"].time.iloc[-1]) for d in data.values())
    span = last - STUDY
    TR = (STUDY, STUDY + int(span * 0.6))
    VA = (STUDY + int(span * 0.6), STUDY + int(span * 0.8))
    print(f"\n  train {pd.Timestamp(TR[0],unit='s').date()} -> {pd.Timestamp(TR[1],unit='s').date()}")
    print(f"  valid {pd.Timestamp(VA[0],unit='s').date()} -> {pd.Timestamp(VA[1],unit='s').date()}")
    print(f"  test  {pd.Timestamp(VA[1],unit='s').date()} -> {pd.Timestamp(last,unit='s').date()}  [LOCKED]\n")

    print("building conditions + ATR stop arrays...")
    for s, d in data.items():
        d["C"] = hwpr.build_conditions(d["df"])
        d["C_atr5"] = _with_atr_stop(d["C"])              # ATR stop, 5m KEPT
        d["C_atr"] = _without_5m(d["C_atr5"])             # ATR stop, 5m DROPPED
        d["C_1m"] = _without_5m(d["C"])                   # structural stop, 5m dropped
    print("  done\n")

    def run_all(arm, key, window, *, target_r=TARGET_R, max_stop_pct=MAX_STOP_PCT):
        """EACH WINDOW STARTS WITH FRESH EQUITY, which run_hwpr does not do.

        run_hwpr passes start=STUDY and filters trades to the window
        afterwards, so the account carried into validation is whatever train
        left behind. The simulator compounds -- `equity += gross - fee - slip`
        with no floor -- and sizes from it, so a configuration that loses
        heavily in train arrives at validation with an account too small to
        buy one contract. Its validation then reports a handful of trades that
        are not a sample of the strategy but the residue of a bankruptcy.

        Measured here first-hand: the 1m ATR arms returned 8,892 train trades
        and 179 validation trades, a rate collapse of 90% that had nothing to
        do with signal frequency. Starting each window at its own boundary
        removes the coupling and makes the two windows comparable.
        """
        frames, sk_size, sk_stop, sig = [], 0, 0, 0
        for s, d in data.items():
            r = hwpr.run(d["df"], d["mark"], d["funding"], d["costs"], d[key],
                         arm=arm, wpr_variant="A", target_r=target_r,
                         start=window[0], end=window[1], tradable=d["tradable"],
                         max_stop_pct=max_stop_pct)
            sk_size += r.skipped_size; sk_stop += r.skipped_stop; sig += r.signals
            f = r.to_frame()
            if len(f):
                f = f[(f.entry_time >= window[0]) & (f.entry_time < window[1])]
            if len(f):
                frames.append(f)
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        return df, dict(signals=sig, skipped_size=sk_size, skipped_stop=sk_stop)

    def show(label, arm, key, note="", **kw):
        row = {}
        for nm, win in (("train", TR), ("valid", VA)):
            df, meta = run_all(arm, key, win, **kw)
            m = summarise(df, nm)
            m.update(meta)
            row[nm] = m
            if not m["trades"]:
                print(f"  {label:26} {nm}: NO TRADES")
                continue
            print(f"  {label:26} {nm}: n={m['trades']:>6,}  win={m['win_rate']:.3f}  "
                  f"GROSS={m['gross_r']:+.4f}  cost={m['cost_r']:.4f}  "
                  f"NET={m['net_r']:+.4f}  t_net={m['t_net']}  "
                  f"[{m['ci_low']:+.3f},{m['ci_high']:+.3f}]  "
                  f"{m['trades_per_day']:.1f}/day  stop={m['stop_pct_median']:.3f}%"
                  f"  skip(size)={m['skipped_size']:,}")
        if note:
            print(f"  {'':26} -> {note}")
        return row

    out = {}

    print("=" * 118)
    print("REFERENCE — the pre-registered baseline, unchanged (5m regime + 1m stack, structural stop)")
    print("=" * 118)
    out["ref_armA_structural"] = show("Arm A frozen", "A", "C", max_stop_pct=0.05)

    print("\n" + "=" * 118)
    print("ONE CHANGE AT A TIME (5m filter KEPT in the first, stop KEPT in the second)")
    print("=" * 118)
    out["armA_5m_atrstop"] = show("Arm A, ATR stop only", "A", "C_atr5",
                                  "the stop change alone")
    out["armA_1m_structural"] = show("Arm A, no 5m only", "A", "C_1m",
                                     "dropping the 5m filter alone")

    print("\n" + "=" * 118)
    print(f"1m-ONLY DECISION + {ATR_MULT}x ATR({ATR_PERIOD}) STOP + {TARGET_R}R TARGET  (5m filter removed)")
    print("=" * 118)
    for label, arm, note in ARMS:
        out[f"1m_{arm}"] = show(label, arm, "C_atr", note)

    # ---- separate the timeframe from the stop WIDTH -----------------------
    #
    # 2 x ATR(10) on the 1m grid is a 0.23% stop; the frozen structural stop is
    # 0.67%. Cost is a fixed fraction of notional against a variable R, so
    # cost/R scales as 1/stop_pct -- a stop a third as wide costs three times
    # as much per R before anything about the signal is considered. Comparing
    # the two directly would therefore attribute to "deciding on 1m" an effect
    # that is entirely the stop width. This sweep holds the arm fixed and moves
    # only the multiplier, so the two can be told apart.
    print("\n" + "=" * 118)
    print("STOP-WIDTH SWEEP — same 1m arm, only the ATR multiple moves")
    print("=" * 118)
    for mult in (2.0, 3.5, 5.0, 7.0):
        for _s, d in data.items():
            d["C_sweep"] = _without_5m(_with_atr_stop(d["C"], mult))
        out[f"1m_A_atr{mult}"] = show(f"1m stack, {mult}x ATR", "A", "C_sweep")

    json.dump(out, open(OUT / "arms.json", "w"), indent=2, default=str)
    rows = []
    for k, v in out.items():
        for split, m in v.items():
            rows.append(dict(config=k, split=split, **m))
    pd.DataFrame(rows).to_csv(OUT / "arms.csv", index=False)
    print(f"\nwrote {OUT/'arms.csv'}")
    print("\nTEST WAS NOT TOUCHED. Every row above is a diagnostic; picking the")
    print("winner here would be selecting on the data that measured it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
