"""THE REVERSAL CONFLUENCE: %R LEAVING ITS EXTREME *AND* THE SUPERTREND FLIPPING.

    PYTHONPATH=. python3 -u -m deltabt.research.run_reversal_confluence

This is the setup as actually described -- a REVERSAL trade -- and it is the
one combination the earlier runs managed to miss between them:

  run_user_rule       %R cross + Supertrend STATE. State is true for dozens of
                      consecutive bars, so it fires deep inside an existing
                      move, which is a continuation entry wearing a reversal's
                      clothes.
  run_flip_entry      Supertrend FLIP alone, %R dropped entirely. Every false
                      flip taken, and on a 1m chart in chop most flips are
                      false.
  THIS                Both, close together. The %R leaving an extreme says the
                      old move is exhausted; the flip says the trend has
                      actually turned. Neither component carries information on
                      its own -- measured, twice -- but a conjunction can carry
                      what its parts do not, and that has never been tested.

WHY A WINDOW RATHER THAN THE SAME BAR. Requiring both events on one bar is a
coincidence, not a setup: %R can lead the flip or lag it by a few minutes and
the trade is the same trade to a human watching it happen. So the window is
swept from 0 (strictly simultaneous) to 10 bars, and entry is at the bar where
the SECOND of the two completes -- never earlier, which would need the future,
and never repeatedly while both remain true, which would turn one setup into a
run of entries.

  LONG   %R(140) crosses up through -80   and Supertrend(10,2) flips bullish
  SHORT  %R(140) crosses down through -20 and Supertrend(10,2) flips bearish

THE HELD-OUT SET IS SPENT (run_user_adx). Train and validation only, both
heavily mined. So a negative here is trustworthy -- mining for positives does
not manufacture negatives -- and a positive is a hypothesis with no clean data
left to confirm it. Stop fixed at 1% so cost is held at 0.159R.
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

OUT = OUT_DIR / "reversal"
STUDY = int(pd.Timestamp("2025-01-01", tz="UTC").timestamp())
SL_PCT = 0.010
WINDOWS = (0, 1, 2, 3, 5, 10)
RR_GRID = (1.0, 1.5, 2.0)
IST = 5.5 * 3600


def _bars_since(flag: np.ndarray) -> np.ndarray:
    """Bars since `flag` was last true; a large number before the first one."""
    n = flag.size
    out = np.full(n, 10**9, dtype=np.int64)
    last = -10**9
    for i in range(n):
        if flag[i]:
            last = i
        out[i] = i - last
    return out


def build(df: pd.DataFrame, C: dict):
    """Component events and how long since each last fired."""
    h = df["high"].to_numpy("float64")
    l = df["low"].to_numpy("float64")
    c = df["close"].to_numpy("float64")
    _st, d1 = ind.supertrend(h, l, c, hwpr.ST_MULT, hwpr.ST_PERIOD)
    prev_d1 = np.concatenate(([d1[0]], d1[:-1]))
    flip_long = (d1 < 0) & (prev_d1 >= 0)     # Pine: dir < 0 is bullish
    flip_short = (d1 > 0) & (prev_d1 <= 0)

    wpr = C["wpr"]
    prev = np.concatenate(([np.nan], wpr[:-1]))
    with np.errstate(invalid="ignore"):
        cross_up = (wpr > -80.0) & (prev <= -80.0)
        cross_dn = (wpr < -20.0) & (prev >= -20.0)
    return dict(
        flip_long=flip_long, flip_short=flip_short,
        cross_up=cross_up, cross_dn=cross_dn,
        since_flip_long=_bars_since(flip_long),
        since_flip_short=_bars_since(flip_short),
        since_cross_up=_bars_since(cross_up),
        since_cross_dn=_bars_since(cross_dn))


def confluence(E: dict, w: int):
    """Fire on the bar where the SECOND of the two events lands."""
    lo = ((E["cross_up"] & (E["since_flip_long"] <= w))
          | (E["flip_long"] & (E["since_cross_up"] <= w)))
    sh = ((E["cross_dn"] & (E["since_flip_short"] <= w))
          | (E["flip_short"] & (E["since_cross_dn"] <= w)))
    return lo, sh


def conditions(C: dict, lo, sh, sl_pct: float = SL_PCT):
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


def stats(df: pd.DataFrame) -> dict:
    if df.empty:
        return dict(trades=0)
    net = df.r_net.to_numpy("float64")
    de = trade_design_effect(df)
    n_eff = max(de["n_eff"], 1.0)
    se = float(net.std(ddof=1)) / np.sqrt(n_eff) if len(net) > 1 else float("nan")
    return dict(trades=int(len(df)), n_eff=round(n_eff, 1),
                wins=int((net > 0).sum()), losses=int((net <= 0).sum()),
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
        data[s]["C"] = hwpr.build_conditions(ltp)
        data[s]["E"] = build(ltp, data[s]["C"])

    last = min(int(d["df"].time.iloc[-1]) for d in data.values())
    span = last - STUDY
    TR = (STUDY, STUDY + int(span * 0.6))
    VA = (STUDY + int(span * 0.6), STUDY + int(span * 0.8))
    print("REVERSAL CONFLUENCE -- %R leaves its extreme AND the Supertrend flips")
    print("  LONG  %R(140) crosses up through -80  + Supertrend(10,2) flips bullish")
    print("  SHORT %R(140) crosses down thru -20   + Supertrend(10,2) flips bearish")
    print(f"  fixed stop {SL_PCT:.2%}, entry at the bar the second event lands")
    print("  TEST SET ALREADY SPENT -- train and validation only\n")

    def measure(w, rr, window, sl_pct=SL_PCT):
        frames = []
        for s, d in data.items():
            lo, sh = confluence(d["E"], w)
            r = hwpr.run(d["df"], d["mark"], d["funding"], d["costs"],
                         conditions(d["C"], lo, sh, sl_pct), arm="E", wpr_variant="A",
                         target_r=rr, start=window[0], end=window[1],
                         tradable=d["tradable"], max_stop_pct=0.10)
            f = r.to_frame()
            if len(f):
                f = f[(f.entry_time >= window[0]) & (f.entry_time < window[1])]
            if len(f):
                frames.append(f)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    rows, keep = [], {}
    for rr in RR_GRID:
        print("=" * 150)
        print(f"REWARD RATIO 1:{rr}")
        print("=" * 150)
        for w in WINDOWS:
            dt, dv = measure(w, rr, TR), measure(w, rr, VA)
            t, v = stats(dt), stats(dv)
            rows.append(dict(window_bars=w, rr=rr,
                             **{f"tr_{k}": x for k, x in t.items()},
                             **{f"va_{k}": x for k, x in v.items()}))
            lbl = f"both within {w} bar(s)"
            if not t.get("trades") or not v.get("trades"):
                print(f"  {lbl:24} -- too few trades --")
                continue
            print(f"  {lbl:24} train n={t['trades']:>5,} W/L {t['wins']:>4,}/{t['losses']:<5,} "
                  f"win={t['win_rate']:.3f} gross={t['gross_r']:+.4f} "
                  f"net={t['net_r']:+.4f} (t={t['t']:>5.1f})  |  "
                  f"valid n={v['trades']:>4,} W/L {v['wins']:>3,}/{v['losses']:<4,} "
                  f"win={v['win_rate']:.3f} gross={v['gross_r']:+.4f} "
                  f"net={v['net_r']:+.4f} (t={v['t']:>5.1f}) {v['trades_per_day']:>4.1f}/day")
            if w == 3:
                keep[rr] = pd.concat([dt.assign(window="train"),
                                      dv.assign(window="valid")])
        print()

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "confluence.csv", index=False)
    json.dump(rows, open(OUT / "confluence.json", "w"), indent=2, default=str)

    for rr, t in keep.items():
        t = t.sort_values("entry_time").reset_index(drop=True)
        for col in ("entry_time", "exit_time"):
            t[col + "_utc"] = pd.to_datetime(t[col], unit="s", utc=True)
            t[col + "_ist"] = pd.to_datetime(t[col] + IST, unit="s")
        t["side_txt"] = np.where(t.side > 0, "LONG", "SHORT")
        t["win"] = t.r_net > 0
        t[["window", "symbol", "side_txt", "entry_time_utc", "entry_time_ist",
           "exit_time_utc", "bars_held", "entry_price", "stop_price",
           "target_price", "exit_price", "exit_reason", "r_gross", "cost_r",
           "r_net", "win"]].to_csv(OUT / f"trades_w3_rr{rr}.csv", index=False)

    # ---- stop sweep on the tightest confluence ---------------------------
    #
    # Cost is 0.159R at a 1% stop and scales as 0.159/stop_pct, so a setup
    # whose GROSS is positive but smaller than 0.159 is not refuted by a 1%
    # stop -- it is only priced out by one. The same-bar confluence is the only
    # cell of the eighteen with positive gross in both windows, so the stop is
    # swept on it specifically to find whether the gross survives being given
    # room, or was an artefact of the tight stop's trade population.
    #
    # THIS IS HYPOTHESIS-GENERATING ONLY. The held-out set was spent before this
    # ran, so a positive cell here has nothing left to confirm it and must not
    # be treated as a result.
    print("=" * 150)
    print("STOP SWEEP on the SAME-BAR confluence (the only cell with gross > 0 "
          "in both windows)")
    print("=" * 150)
    for sl in (0.010, 0.015, 0.020, 0.030, 0.040):
        for rr in (1.5, 2.0):
            t = stats(measure(0, rr, TR, sl))
            v = stats(measure(0, rr, VA, sl))
            if not t.get("trades") or not v.get("trades"):
                print(f"  SL={sl:.2%} RR=1:{rr}  -- too few trades --")
                continue
            rows.append(dict(window_bars=0, rr=rr, sl_pct=sl,
                             **{f"tr_{k}": x for k, x in t.items()},
                             **{f"va_{k}": x for k, x in v.items()}))
            print(f"  SL={sl:.2%} RR=1:{rr}  train n={t['trades']:>5,} "
                  f"win={t['win_rate']:.3f} gross={t['gross_r']:+.4f} "
                  f"cost={t['cost_r']:.3f} net={t['net_r']:+.4f}  |  "
                  f"valid n={v['trades']:>4,} win={v['win_rate']:.3f} "
                  f"gross={v['gross_r']:+.4f} cost={v['cost_r']:.3f} "
                  f"net={v['net_r']:+.4f} (t={v['t']:>5.1f})")
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "confluence.csv", index=False)
    print()

    ok = df[(df.tr_trades.fillna(0) >= 100) & (df.va_trades.fillna(0) >= 100)]
    print("=" * 150)
    print(f"VERDICT over {len(ok)} settings with >=100 trades in both windows")
    print("=" * 150)
    if len(ok):
        print(f"  validation net_r: min {ok.va_net_r.min():+.4f}  "
              f"median {ok.va_net_r.median():+.4f}  max {ok.va_net_r.max():+.4f}")
        print(f"  positive on validation:   {(ok.va_net_r > 0).sum()} / {len(ok)}")
        print(f"  positive on BOTH windows: "
              f"{((ok.va_net_r > 0) & (ok.tr_net_r > 0)).sum()} / {len(ok)}")
        print(f"  GROSS positive on both:   "
              f"{((ok.va_gross_r > 0) & (ok.tr_gross_r > 0)).sum()} / {len(ok)}")
    print(f"\nwrote {OUT/'confluence.csv'} and trade files for the 3-bar window")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
