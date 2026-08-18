"""H-COST-1 -- economic feasibility of the frozen H-EMA-3 k=0.5 signal.

See out/hcost1/hcost1_preregistration.md (sha256 in candidates.json).

The signal is FROZEN and is never modified. What varies is the economics:
stop width, timeframe, volatility regime, exit horizon and cost scenario.

SYNTHETIC STOP LAYER
    The structural Supertrend stop is replaced by an explicit percentage
    distance, `stop = entry * (1 -/+ width)`, so stop width becomes an
    experimental variable rather than a property of the instrument. Two things
    follow. Cost per unit of risk becomes EXACT rather than distributional --
    `cost/R = 2(taker + slippage) / width` for every trade in the cell. And the
    mirror control remains valid, because P(hit +kR before -1R) = 1/(1+k) under
    a martingale for ANY width, which is the whole reason this estimator and not
    a resampled one is used for an experiment whose primary axis is stop width.

WHY WIDER IS NOT AUTOMATICALLY BETTER
    Widening the stop divides cost/R down, but it multiplies the distance the
    price must travel to reach +kR. H-EMA-3 measured the edge decaying to zero
    by ~0.53% of price and reversing by ~2.1%. The two effects oppose each
    other, and which wins is what this module measures.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from deltabt.research import hema3

GST = 1.18
ROUND_TRIP_COMPONENTS = dict(taker=0.0005, maker=0.0002)


def cost_per_r(width: float, *, slippage_bps: float, taker: float,
               maker_exit: bool = False) -> float:
    """Round-trip cost expressed in R. Exact, because width is fixed.

    Entry is always taker plus slippage. A maker exit models a resting limit
    target that earns the maker rate and pays no slippage.
    """
    slip = slippage_bps / 10_000.0
    entry_leg = taker * GST + slip
    exit_leg = (ROUND_TRIP_COMPONENTS["maker"] * GST) if maker_exit else (taker * GST + slip)
    return (entry_leg + exit_leg) / width


def executable_signal(bets: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Frozen conflicting-bar rule: BOTH -> DROP. Nothing is invented.

    The H-EMA-3 population is a measurement keyed on (symbol, tf, bar, side), so
    a bar can appear with both directions when different arms disagree. That is
    fine for a statistic and undefined for a trading rule.
    """
    key = ["symbol", "exec_tf", "bar"]
    n_side = bets.groupby(key).side.transform("nunique")
    keep = n_side == 1
    meta = dict(
        rows_in=int(len(bets)),
        bars_in=int(bets.groupby(key).ngroups),
        conflicting_bars=int(bets.loc[~keep].groupby(key).ngroups),
        dropped_rows=int((~keep).sum()),
    )
    meta["conflicting_pct"] = round(100 * meta["conflicting_bars"] / max(meta["bars_in"], 1), 3)
    return bets.loc[keep].copy(), meta


def volatility_thresholds(atr_over_close: np.ndarray) -> tuple[float, float]:
    """P33 / P67 -- computed on TRAIN only and frozen thereafter."""
    v = atr_over_close[np.isfinite(atr_over_close)]
    return float(np.quantile(v, 1 / 3)), float(np.quantile(v, 2 / 3))


def regime_of(v, p33: float, p67: float) -> np.ndarray:
    out = np.full(len(v), "NORMAL", dtype=object)
    out[v <= p33] = "LOW"
    out[v > p67] = "HIGH"
    out[~np.isfinite(v)] = "NA"
    return out


def synthetic_barriers(sym: dict, entry_idx: np.ndarray, width: float,
                       k_max: float, n_keep: int):
    """Barrier outcomes for BOTH directions under a fixed percentage stop."""
    ent = sym["o"][entry_idx]
    res = {}
    for tag, side in (("L", 1), ("S", -1)):
        stop = ent * (1.0 - side * width)
        b, s = hema3._barrier_walk(
            entry_idx.astype("int64"), ent, stop,
            np.full(entry_idx.size, side, "int64"),
            sym["mh"], sym["ml"], sym["h"], sym["l"], float(k_max), int(n_keep))
        res[f"best_{tag}"] = b
        res[f"stopped_{tag}"] = s
    return ent, res


def cell_result(df: pd.DataFrame, k: float, width: float, *, slippage_bps: float,
                taker: float, maker_exit: bool = False, **extra) -> dict:
    """Signal viability and economic viability, reported separately (S 3)."""
    r = hema3.paired_statistic(df, k)
    if not r.get("n"):
        return dict(k=k, stop_width=width, n=0, **extra)
    c = cost_per_r(width, slippage_bps=slippage_bps, taker=taker, maker_exit=maker_exit)
    # gross for a k-target against a 1R stop is (1+k)*p - 1
    sig_gross = (1.0 + k) * r["p_arm"] - 1.0
    ctl_gross = (1.0 + k) * r["p_mirror"] - 1.0
    out = dict(
        k=k, stop_width=width, n=r["n"], clusters=r["clusters"],
        unresolved=r["unresolved"],
        # A -- signal viability
        excess_gross_R=r["excess_gross_R"], se_gross_R=r["se_gross_R"], t=r["t"],
        ci_low_R=r["ci_low_R"], ci_high_R=r["ci_high_R"],
        p_signal=r["p_arm"], p_control=r["p_mirror"],
        signal_gross_R=sig_gross, control_gross_R=ctl_gross,
        # B -- economic viability
        cost_R=c,
        signal_net_R=sig_gross - c, control_net_R=ctl_gross - c,
        excess_net_R=r["excess_gross_R"],   # cost is identical for both legs
        break_even_cost_R=r["excess_gross_R"],
        edge_to_cost=r["excess_gross_R"] / c if c else None,
        required_multiple=(c / r["excess_gross_R"]) if r["excess_gross_R"] > 0 else None,
        **extra)
    out["signal_real"] = bool(r["ci_low_R"] > 0)
    out["economics_viable"] = bool(out["signal_net_R"] > 0 and out["signal_real"])
    return out


def gate(row: dict, net_low: float, net_high: float) -> str:
    """GREEN / YELLOW / RED per S 12. Never GREEN from a cheap cost scenario."""
    if not row.get("n") or not row.get("signal_real"):
        return "RED"
    if row.get("stop_width", 0) > 0.05:
        return "OUT-OF-MODEL"
    if row["signal_net_R"] <= 0:
        return "RED"
    return "GREEN" if (net_low > 0 and net_high > 0) else "YELLOW"
