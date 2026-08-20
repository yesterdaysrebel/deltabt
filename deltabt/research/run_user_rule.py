"""THE TRADED RULE, MEASURED AS TRADED. TRAIN + VALIDATION. TEST LOCKED.

    PYTHONPATH=. python3 -u -m deltabt.research.run_user_rule

The rule, as stated, entirely on the 1m chart:

    LONG   Williams %R(140) rising from -80   and Supertrend(10,2) up
    SHORT  Williams %R(140) falling from -20  and Supertrend(10,2) down

No 5m anything. No ADX. No DI. Fixed stop and target. 25% of capital per
trade.

"RISING FROM -80" HAS TWO HONEST READINGS AND BOTH ARE RUN:

  cross     the bar where %R crosses up through -80 (prev <= -80 < now).
            One bar, the moment of the crossing.
  from_zone %R was below -80 on the previous bar and is now rising, whether
            or not it has yet cleared -80. Fires earlier and more often.

They are different rules with different firing rates, and reporting one as
"the" rule would be picking an interpretation to suit a result.

SIZING IS REPORTED THE WAY IT IS TRADED. R-multiples are normalised by risk,
so 25% of capital changes none of them -- but it is not how anyone experiences
a strategy. With 25% of capital at a stop of x%, the risk per trade is
0.25 * x of equity, so the equity path is compounded directly from the net R
of each trade at that fraction. Terminal equity from 10,000 is reported beside
the R statistics for exactly that reason.

THE STOP IS SWEPT BECAUSE IT IS THE DOMINANT TERM. cost_r x stop_pct = 0.159
across a twentyfold range (run_fixed_sl), so quoting this rule at one stop
width would be quoting an arbitrary point on a curve that moves by 0.6R across
the range. A rule is not separable from the stop it is traded with.

TEST IS NOT TOUCHED.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from deltabt.config import OUT_DIR
from deltabt.costs import SymbolCosts
from deltabt.data.quality import tradable_mask
from deltabt.data.store import CandleStore, ProductCatalog
from deltabt.research import hwpr
from deltabt.research.stats import trade_design_effect

OUT = OUT_DIR / "user_rule"
STUDY = int(pd.Timestamp("2025-01-01", tz="UTC").timestamp())

CAPITAL_FRACTION = 0.25          # 25% of capital as notional, per the rule
START_EQUITY = 10_000.0
SL_GRID = (0.0025, 0.005, 0.0075, 0.010, 0.015, 0.020, 0.030)
TP_GRID = (1.0, 1.5, 2.0, 3.0)
MAX_STOP_PCT = 0.10


def rule_masks(C: dict) -> dict:
    """The two readings of the stated rule, both gated on 1m Supertrend."""
    wpr = C["wpr"]
    prev = np.concatenate(([np.nan], wpr[:-1]))
    with np.errstate(invalid="ignore"):
        rising, falling = wpr > prev, wpr < prev
        cross_up = (wpr > -80.0) & (prev <= -80.0)
        cross_dn = (wpr < -20.0) & (prev >= -20.0)
        zone_up = (prev < -80.0) & rising
        zone_dn = (prev > -20.0) & falling
        return {
            "cross": (C["st1_long"] & cross_up, C["st1_short"] & cross_dn),
            "from_zone": (C["st1_long"] & zone_up, C["st1_short"] & zone_dn),
        }


def _conditions(C: dict, masks, sl_pct: float) -> dict:
    """Custom masks through the frozen path -- arm E is `f5 & wprA`."""
    lo, sh = masks
    n = len(C["close"])
    c = C["close"]
    D = dict(C)
    D["f5_long"], D["f5_short"] = lo, sh
    D["wprA_long"] = np.ones(n, dtype=bool)
    D["wprA_short"] = np.ones(n, dtype=bool)
    D["st1"] = c
    D["leg_lo"] = c * (1.0 - sl_pct)
    D["leg_hi"] = c * (1.0 + sl_pct)
    return D


def stats(df: pd.DataFrame, sl_pct: float) -> dict:
    if df.empty:
        return dict(trades=0)
    net = df.r_net.to_numpy("float64")
    de = trade_design_effect(df)
    n_eff = max(de["n_eff"], 1.0)
    se = float(net.std(ddof=1)) / np.sqrt(n_eff) if len(net) > 1 else float("nan")
    # 25% of capital at an x% stop risks 0.25x of equity per trade.
    frac = CAPITAL_FRACTION * sl_pct
    eq = START_EQUITY * np.prod(1.0 + net * frac)
    return dict(
        trades=int(len(df)), n_eff=round(n_eff, 1),
        win_rate=round(float((net > 0).mean()), 4),
        avg_win_r=round(float(net[net > 0].mean()), 3) if (net > 0).any() else None,
        avg_loss_r=round(float(net[net <= 0].mean()), 3) if (net <= 0).any() else None,
        gross_r=round(float(df.r_gross.mean()), 4),
        cost_r=round(float(df.cost_r.mean()), 4),
        net_r=round(float(net.mean()), 4), se=round(se, 4),
        t=round(float(net.mean() / se), 2) if se and np.isfinite(se) else None,
        risk_per_trade_pct=round(100 * frac, 3),
        end_equity=round(float(eq), 2),
        trades_per_day=round(len(df) / max(
            (df.entry_time.max() - df.entry_time.min()) / 86400, 1), 2),
        dur_median=float(df.bars_held.median()))


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
    print(f"train {pd.Timestamp(TR[0],unit='s').date()} -> {pd.Timestamp(TR[1],unit='s').date()}  |  "
          f"valid {pd.Timestamp(VA[0],unit='s').date()} -> {pd.Timestamp(VA[1],unit='s').date()}  |  "
          f"test {pd.Timestamp(VA[1],unit='s').date()} -> {pd.Timestamp(last,unit='s').date()} [LOCKED]")
    print("RULE: 1m only. long = %R(140) rising from -80 + Supertrend(10,2) up;")
    print("      short = %R(140) falling from -20 + Supertrend(10,2) down.")
    print(f"      {CAPITAL_FRACTION:.0%} of capital per trade, fixed SL/TP, from "
          f"${START_EQUITY:,.0f}\n")

    for s, d in data.items():
        d["C"] = hwpr.build_conditions(d["df"])
        d["masks"] = rule_masks(d["C"])

    def measure(reading, sl_pct, target_r, window):
        frames = []
        for s, d in data.items():
            C = _conditions(d["C"], d["masks"][reading], sl_pct)
            r = hwpr.run(d["df"], d["mark"], d["funding"], d["costs"], C,
                         arm="E", wpr_variant="A", target_r=target_r,
                         start=window[0], end=window[1],
                         tradable=d["tradable"], max_stop_pct=MAX_STOP_PCT)
            f = r.to_frame()
            if len(f):
                f = f[(f.entry_time >= window[0]) & (f.entry_time < window[1])]
            if len(f):
                frames.append(f)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    rows = []

    def line(reading, sl, tp):
        t = stats(measure(reading, sl, tp, TR), sl)
        v = stats(measure(reading, sl, tp, VA), sl)
        rows.append(dict(reading=reading, sl_pct=sl, target_r=tp,
                         **{f"tr_{k}": x for k, x in t.items()},
                         **{f"va_{k}": x for k, x in v.items()}))
        lbl = f"SL={sl:.2%} TP={tp}R"
        if not t.get("trades") or not v.get("trades"):
            print(f"  {lbl:22} -- too few trades --")
            return
        print(f"  {lbl:22} risk/trade {t['risk_per_trade_pct']:.3f}%  |  "
              f"train n={t['trades']:>6,} win={t['win_rate']:.3f} "
              f"net={t['net_r']:+.4f} (t={t['t']:>5.1f}) ${t['end_equity']:>9,.0f}  |  "
              f"valid n={v['trades']:>5,} win={v['win_rate']:.3f} "
              f"net={v['net_r']:+.4f} (t={v['t']:>5.1f}) ${v['end_equity']:>9,.0f}")

    for reading in ("cross", "from_zone"):
        print("=" * 150)
        print(f"READING: {reading}   "
              + ("(%R crosses the level on this bar)" if reading == "cross"
                 else "(%R was beyond the level and has turned)"))
        print("=" * 150)
        print("  -- stop sweep at TP=2R --")
        for sl in SL_GRID:
            line(reading, sl, 2.0)
        print("  -- target sweep at SL=1.00% --")
        for tp in TP_GRID:
            line(reading, 0.010, tp)
        print()

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "results.csv", index=False)
    json.dump(rows, open(OUT / "results.json", "w"), indent=2, default=str)

    ok = df[(df.tr_trades.fillna(0) >= 100) & (df.va_trades.fillna(0) >= 100)]
    print("=" * 150)
    print(f"SUMMARY over {len(ok)} settings with >=100 trades in both windows")
    print("=" * 150)
    if len(ok):
        print(f"  validation net_r: min {ok.va_net_r.min():+.4f}  "
              f"median {ok.va_net_r.median():+.4f}  max {ok.va_net_r.max():+.4f}")
        print(f"  positive on validation: {(ok.va_net_r > 0).sum()} / {len(ok)}")
        print(f"  positive on BOTH windows: "
              f"{((ok.va_net_r > 0) & (ok.tr_net_r > 0)).sum()} / {len(ok)}")
        print(f"  gross positive on both:   "
              f"{((ok.va_gross_r > 0) & (ok.tr_gross_r > 0)).sum()} / {len(ok)}")
    print(f"\nwrote {OUT/'results.csv'}\nTEST WAS NOT TOUCHED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
