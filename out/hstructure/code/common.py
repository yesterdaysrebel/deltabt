"""Shared loading, windowing and metrics for H-Structure-1.

OFFLINE. Reads the parquet candle cache, writes only into this experiment's
own output directory. Touches no production config, no paper/live bot, and
nothing under out/hwpr or out/ema_experiment.

THE SPLIT IS INHERITED, NOT RE-DERIVED
    run_hwpr.py computes TRAIN/VALID as fractions of ``last - STUDY`` where
    ``last`` is the newest cached bar, so refetching data silently moves the
    boundary. run_hema.py pinned that away with DATA_END = 2026-08-12 10:53Z.
    Reproducing its arithmetic:

        STUDY   = 2025-01-01 00:00Z
        span    = DATA_END - STUDY
        TRAIN   = STUDY .. STUDY + 0.60*span  ->  2025-12-20
        VALID   = ..     .. STUDY + 0.80*span  ->  2026-04-16
        TEST    = 2026-04-16 .. DATA_END       ->  LOCKED, NOT COMPUTED

    which is exactly the split named in the H-Structure-1 protocol §10. The
    boundaries are pinned as integers below so no data refresh can move them.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from deltabt.costs import SymbolCosts
from deltabt.data.quality import tradable_mask
from deltabt.data.store import CandleStore, ProductCatalog
from deltabt.research.stats import block_bootstrap_mean, trade_design_effect

#: Pre-declared universe. BTC/ETH/SOL/XRP is the frozen H-WPR-1 universe and
#: carries every headline number.
CORE = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
#: Declared supplementary symbol: listed 2026-01-05, so it has NO TRAIN data
#: and cannot influence candidate selection. Reported separately on VALID only.
SUPP = ["BEATUSD"]
#: AKEUSD and BANKUSD were listed 2026-07-22. Their entire history lies inside
#: the LOCKED TEST window, so including them would BE opening TEST. Excluded.
EXCLUDED = {"AKEUSD": "listed 2026-07-22, data is TEST-only",
            "BANKUSD": "listed 2026-07-22, data is TEST-only"}

STUDY = int(pd.Timestamp("2025-01-01", tz="UTC").timestamp())
DATA_END = 1786531980                       # 2026-08-12 10:53Z, from run_hema
_SPAN = DATA_END - STUDY
TRAIN = (STUDY, STUDY + int(_SPAN * 0.6))   # -> 2025-12-20
VALID = (TRAIN[1], STUDY + int(_SPAN * 0.8))  # -> 2026-04-16
SPLITS = (("train", TRAIN), ("valid", VALID))


def load(symbols) -> dict:
    store, cat = CandleStore(), ProductCatalog()
    data = {}
    for s in symbols:
        ltp = store.read(s, "ltp", "1m")
        if ltp.empty:
            raise SystemExit(f"no cached candles for {s}")
        ltp = ltp[(ltp.time >= STUDY) & (ltp.time <= DATA_END)].reset_index(drop=True)
        if ltp.empty:
            raise SystemExit(f"{s} has no bars inside the study window")
        mark = store.read(s, "mark", "1m")
        t1 = ltp["time"].to_numpy("int64")
        h = ltp["high"].to_numpy("float64"); l = ltp["low"].to_numpy("float64")
        if mark is not None and not mark.empty:
            mk = mark.set_index("time").reindex(t1)
            mh = mk["high"].to_numpy("float64"); ml = mk["low"].to_numpy("float64")
            bad = ~np.isfinite(mh) | ~np.isfinite(ml)
            mh = np.where(bad, h, mh); ml = np.where(bad, l, ml)
        else:
            mh, ml = h, l
        data[s] = dict(
            df=ltp, t1=t1,
            o=ltp["open"].to_numpy("float64"), h=h, l=l,
            c=ltp["close"].to_numpy("float64"), mh=mh, ml=ml,
            funding=store.read(s, "funding", "1h"),
            costs=SymbolCosts.from_spec(cat.get(s), slippage_bps=2.0),
            tradable=tradable_mask(ltp).astype(np.bool_),
        )
    return data


# ------------------------------------------------------------------ metrics


def summarise(df: pd.DataFrame, label: str, *, boot: bool = True, **extra) -> dict:
    """Metric set required by §14, built on run_hwpr.summarise's definitions."""
    out = dict(label=label, trades=0, **extra)
    if df is None or df.empty:
        return out
    g = df.r_gross.to_numpy("float64")
    n = df.r_net.to_numpy("float64")
    de = trade_design_effect(df)
    sq = np.sqrt(de["deff"])
    wins, losses = n[n > 0], n[n <= 0]
    gw, gl = g[g > 0], g[g <= 0]
    eq = np.cumsum(n)
    days = max((df.entry_time.max() - df.entry_time.min()) / 86400, 1)
    out.update(
        trades=int(len(df)), effective_n=round(de["n_eff"], 1),
        symbols=int(df.symbol.nunique()),
        win_rate=round(float((n > 0).mean()), 4),
        gross_r=round(float(g.mean()), 4),
        median_r=round(float(np.median(n)), 4),
        median_gross_r=round(float(np.median(g)), 4),
        fee_r=round(float(df.fee_r.mean()), 4),
        slip_r=round(float(df.slip_r.mean()), 4),
        funding_r=round(float(df.funding_r.mean()), 5),
        cost_r=round(float(df.cost_r.mean()), 4),
        net_r=round(float(n.mean()), 4),
        gross_total_r=round(float(g.sum()), 1),
        net_total_r=round(float(n.sum()), 1),
        profit_factor=(round(float(wins.sum() / -losses.sum()), 3)
                       if losses.sum() < 0 else None),
        pf_gross=(round(float(gw.sum() / -gl.sum()), 3) if gl.sum() < 0 else None),
        max_dd_r=round(float(np.max(np.maximum.accumulate(eq) - eq)), 1),
        stop_pct_median=round(float(df.stop_pct.median()) * 100, 4),
        cost_over_gross=(round(float(df.cost_r.mean() / g.mean()), 3)
                         if g.mean() > 0 else None),
        pct_long=round(100 * float((df.side > 0).mean()), 1),
        pct_target=round(100 * float((df.exit_reason == "target").mean()), 1),
        pct_stop=round(100 * float((df.exit_reason == "stop").mean()), 1),
        ambiguous_pct=round(100 * float(df.ambiguous.mean()), 1),
        hold_median_min=float(df.bars_held.median()),
        trades_per_day=round(len(df) / days, 2),
    )
    for c, k in (("hit_05r", "p_hit_05r"), ("hit_1r", "p_hit_1r"), ("hit_2r", "p_hit_2r")):
        if c in df:
            out[k] = round(float(df[c].mean()), 4)
    for c in ("conf_delay_bars", "conf_delay_min", "signal_lag_min",
              "disp_atr", "break_dist_atr", "bars_between_swings", "mfe_r"):
        if c in df:
            v = pd.to_numeric(df[c], errors="coerce")
            out[f"{c}_median"] = (round(float(v.median()), 3)
                                  if v.notna().any() else None)
    if boot:
        bg = block_bootstrap_mean(g, mean_block=6.0, n_boot=2000, seed=12)
        bn = block_bootstrap_mean(n, mean_block=6.0, n_boot=2000, seed=11)
        out.update(
            t_gross=round(float(bg["t"] / sq), 3) if np.isfinite(bg["t"]) else None,
            t_net=round(float(bn["t"] / sq), 3) if np.isfinite(bn["t"]) else None,
            gross_ci_low=round(bg["ci_low"], 4), gross_ci_high=round(bg["ci_high"], 4),
            net_ci_low=round(bn["ci_low"], 4), net_ci_high=round(bn["ci_high"], 4),
        )
    return out


def fmt_row(r: dict) -> str:
    if not r.get("trades"):
        return f"{r['label']:<44} {'no trades':>12}"
    return (f"{r['label']:<44} n={r['trades']:>6,} eff={r.get('effective_n', 0):>7.1f} "
            f"win={r['win_rate']:.3f} G={r['gross_r']:+.4f} "
            f"t={(r.get('t_gross') or 0):+6.2f} c={r['cost_r']:.4f} "
            f"N={r['net_r']:+.4f} PF={(r.get('pf_gross') or 0):.2f}")
