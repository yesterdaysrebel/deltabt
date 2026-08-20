"""WAIT FOR CONFIRMATION AFTER THE FLIP, THEN ENTER. TRAIN + VALIDATION.

    PYTHONPATH=. python3 -u -m deltabt.research.run_flip_confirm

run_flip_entry swept a fixed DELAY -- enter exactly k bars after every flip --
and found entering at the flip is the worst timing available while waiting is
mildly better. But a fixed delay is not a confirmation. It waits the same
number of bars whether the trend strengthened or rolled straight back over,
and it takes every flip regardless.

A confirmation is conditional in both directions: enter at the first bar after
the flip where something specific happens, and if it never happens inside the
window, TAKE NO TRADE AT ALL. That second half is the part a delay cannot
express and the part that might matter, because the flips it discards are
exactly the false ones a 1m chart produces in chop.

Five confirmations, each fired at most ONCE per flip, at the first bar that
satisfies it:

  break_extreme  close beyond the most extreme price since the flip. The
                 trend did something, not merely persisted.
  n_closes       three consecutive closes on the correct side of the
                 Supertrend. Persistence, but demanded rather than assumed.
  adx_rise       ADX at or above 25 AND higher than it was at the flip: the
                 move is gaining strength, not inheriting it.
  di_widen       the DI spread wider than at the flip, same idea read
                 directionally.
  wpr_cross      %R leaving its extreme -- the confluence from
                 run_reversal_confluence, included as a like-for-like
                 reference rather than re-derived.

MAX WAIT IS SWEPT because it is the whole discard mechanism. A long window
confirms nearly every flip eventually and collapses back into the delay sweep;
a short one refuses most of them. Where that trade-off lands is the result.

THE HELD-OUT SET IS SPENT (run_user_adx). Train and validation only, both
mined many times over. A negative here is trustworthy; a positive has nothing
clean left to confirm it. Stop fixed at 1% so cost is held at 0.159R and only
the entry rule moves.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from numba import njit

from deltabt import indicators as ind
from deltabt.config import OUT_DIR
from deltabt.costs import SymbolCosts
from deltabt.data.quality import tradable_mask
from deltabt.data.store import CandleStore, ProductCatalog
from deltabt.research import hwpr
from deltabt.research.stats import trade_design_effect

OUT = OUT_DIR / "flip_confirm"
STUDY = int(pd.Timestamp("2025-01-01", tz="UTC").timestamp())
SL_PCT = 0.010
MAX_WAITS = (3, 5, 10, 30)
RR_GRID = (1.5, 2.0)


@njit(cache=True)
def _first_after_flip(flip, cond, max_wait):
    """First bar within max_wait of each flip where cond holds. One per flip."""
    n = flip.size
    out = np.zeros(n, dtype=np.bool_)
    armed = False
    since = 0
    for i in range(n):
        if flip[i]:
            armed = True
            since = 0
        elif armed:
            since += 1
            if since > max_wait:
                armed = False
        if armed and since > 0 and cond[i]:
            out[i] = True
            armed = False          # one entry per flip, never a run of them
    return out


@njit(cache=True)
def _extreme_since_flip(flip, high, low):
    """Running max high / min low since the last flip, EXCLUDING this bar."""
    n = flip.size
    hi = np.full(n, np.nan)
    lo = np.full(n, np.nan)
    ch = -np.inf
    cl = np.inf
    for i in range(n):
        hi[i] = ch
        lo[i] = cl
        if flip[i]:
            ch = high[i]
            cl = low[i]
        else:
            if high[i] > ch:
                ch = high[i]
            if low[i] < cl:
                cl = low[i]
    return hi, lo


@njit(cache=True)
def _value_at_flip(flip, values):
    """The value of `values` as of the most recent flip."""
    n = flip.size
    out = np.full(n, np.nan)
    cur = np.nan
    for i in range(n):
        if flip[i]:
            cur = values[i]
        out[i] = cur
    return out


@njit(cache=True)
def _run_length(flag):
    """How many consecutive bars `flag` has been true, inclusive."""
    n = flag.size
    out = np.zeros(n, dtype=np.int64)
    c = 0
    for i in range(n):
        c = c + 1 if flag[i] else 0
        out[i] = c
    return out


def components(df: pd.DataFrame, C: dict) -> dict:
    h = df["high"].to_numpy("float64")
    l = df["low"].to_numpy("float64")
    c = df["close"].to_numpy("float64")
    st, d1 = ind.supertrend(h, l, c, hwpr.ST_MULT, hwpr.ST_PERIOD)
    prev = np.concatenate(([d1[0]], d1[:-1]))
    fl = (d1 < 0) & (prev >= 0)        # Pine: dir < 0 is bullish
    fs = (d1 > 0) & (prev <= 0)
    p1, m1, adx = ind.dmi(h, l, c, hwpr.DI_PERIOD, hwpr.ADX_PERIOD)
    spread = p1 - m1

    hi_l, lo_l = _extreme_since_flip(fl, h, l)
    hi_s, lo_s = _extreme_since_flip(fs, h, l)
    adx_at_l, adx_at_s = _value_at_flip(fl, adx), _value_at_flip(fs, adx)
    sp_at_l, sp_at_s = _value_at_flip(fl, spread), _value_at_flip(fs, spread)
    run_up = _run_length(c > st)
    run_dn = _run_length(c < st)

    wpr = C["wpr"]
    pw = np.concatenate(([np.nan], wpr[:-1]))
    with np.errstate(invalid="ignore"):
        cross_up = (wpr > -80.0) & (pw <= -80.0)
        cross_dn = (wpr < -20.0) & (pw >= -20.0)
        return dict(
            flip_long=fl, flip_short=fs,
            conds={
                "break_extreme": (c > hi_l, c < lo_s),
                "n_closes": (run_up >= 3, run_dn >= 3),
                "adx_rise": ((adx >= hwpr.ADX_MIN) & (adx > adx_at_l),
                             (adx >= hwpr.ADX_MIN) & (adx > adx_at_s)),
                "di_widen": (spread > sp_at_l, -spread > -sp_at_s),
                "wpr_cross": (cross_up, cross_dn),
            })


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
        data[s]["K"] = components(ltp, data[s]["C"])

    last = min(int(d["df"].time.iloc[-1]) for d in data.values())
    span = last - STUDY
    TR = (STUDY, STUDY + int(span * 0.6))
    VA = (STUDY + int(span * 0.6), STUDY + int(span * 0.8))
    print("CONFIRMATION AFTER A 1m SUPERTREND(10,2) FLIP")
    print(f"  fixed stop {SL_PCT:.2%}; no trade at all if confirmation never "
          f"arrives inside the window")
    print("  TEST SET ALREADY SPENT -- train and validation only\n")

    # How many flips does each confirmation actually accept?
    print("flips accepted, whole span (both directions, all symbols):")
    total_flips = sum(int(d["K"]["flip_long"].sum() + d["K"]["flip_short"].sum())
                      for d in data.values())
    for name in ("break_extreme", "n_closes", "adx_rise", "di_widen", "wpr_cross"):
        for w in MAX_WAITS:
            got = 0
            for d in data.values():
                cl, cs = d["K"]["conds"][name]
                got += int(_first_after_flip(d["K"]["flip_long"], cl, w).sum())
                got += int(_first_after_flip(d["K"]["flip_short"], cs, w).sum())
            print(f"  {name:14} wait<={w:<3} {got:>8,} / {total_flips:,} flips "
                  f"({100*got/total_flips:5.1f}%)")
    print()

    def measure(name, w, rr, window):
        frames = []
        for s, d in data.items():
            cl, cs = d["K"]["conds"][name]
            lo = _first_after_flip(d["K"]["flip_long"], cl, w)
            sh = _first_after_flip(d["K"]["flip_short"], cs, w)
            r = hwpr.run(d["df"], d["mark"], d["funding"], d["costs"],
                         conditions(d["C"], lo, sh), arm="E", wpr_variant="A",
                         target_r=rr, start=window[0], end=window[1],
                         tradable=d["tradable"], max_stop_pct=0.10)
            f = r.to_frame()
            if len(f):
                f = f[(f.entry_time >= window[0]) & (f.entry_time < window[1])]
            if len(f):
                frames.append(f)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    rows = []
    for rr in RR_GRID:
        print("=" * 146)
        print(f"REWARD RATIO 1:{rr}")
        print("=" * 146)
        for name in ("break_extreme", "n_closes", "adx_rise", "di_widen", "wpr_cross"):
            for w in MAX_WAITS:
                t, v = stats(measure(name, w, rr, TR)), stats(measure(name, w, rr, VA))
                rows.append(dict(confirm=name, max_wait=w, rr=rr,
                                 **{f"tr_{k}": x for k, x in t.items()},
                                 **{f"va_{k}": x for k, x in v.items()}))
                if not t.get("trades") or not v.get("trades"):
                    print(f"  {name:14} wait<={w:<3} -- too few trades --")
                    continue
                print(f"  {name:14} wait<={w:<3} "
                      f"train n={t['trades']:>6,} win={t['win_rate']:.3f} "
                      f"gross={t['gross_r']:+.4f} net={t['net_r']:+.4f} "
                      f"(t={t['t']:>5.1f})  |  valid n={v['trades']:>5,} "
                      f"win={v['win_rate']:.3f} gross={v['gross_r']:+.4f} "
                      f"net={v['net_r']:+.4f} (t={v['t']:>5.1f}) "
                      f"{v['trades_per_day']:>5.1f}/day")
            print()

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "confirm.csv", index=False)
    json.dump(rows, open(OUT / "confirm.json", "w"), indent=2, default=str)

    ok = df[(df.tr_trades.fillna(0) >= 100) & (df.va_trades.fillna(0) >= 100)]
    print("=" * 146)
    print(f"VERDICT over {len(ok)} settings with >=100 trades in both windows")
    print("=" * 146)
    if len(ok):
        print(f"  validation net_r: min {ok.va_net_r.min():+.4f}  "
              f"median {ok.va_net_r.median():+.4f}  max {ok.va_net_r.max():+.4f}")
        print(f"  positive on validation:   {(ok.va_net_r > 0).sum()} / {len(ok)}")
        print(f"  positive on BOTH windows: "
              f"{((ok.va_net_r > 0) & (ok.tr_net_r > 0)).sum()} / {len(ok)}")
        print(f"  GROSS positive on both:   "
              f"{((ok.va_gross_r > 0) & (ok.tr_gross_r > 0)).sum()} / {len(ok)}")
        b = ok.loc[ok.va_net_r.idxmax()]
        print(f"\n  best by validation: {b.confirm} wait<={b.max_wait} RR 1:{b.rr}"
              f"  train {b.tr_net_r:+.4f}  valid {b.va_net_r:+.4f}")
    print(f"\nwrote {OUT/'confirm.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
