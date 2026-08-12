"""H-WPR-1 — strict multi-timeframe trend continuation (no pullback).

PRE-REGISTERED. Frozen before any result was inspected.

HYPOTHESIS
    When the 5m and 1m trend regimes are aligned, ADX/DI confirm directional
    strength and Williams %R confirms directional momentum, price has enough
    short-term continuation edge to profit from entering WITH the trend.

    This is deliberately NOT the previously tested pullback strategy. There is
    no retracement, no proximity-to-Supertrend, and no oversold-recovery
    requirement. WPR here is a momentum confirmation, not a mean-reversion gate.

FROZEN INDICATORS (not optimised)
    Supertrend period 10, multiplier 2.0
    ADX period 28,  DI period 14,  Williams %R period 140

TIMEFRAME DISCIPLINE
    The 5m confirmation uses only the last FULLY CLOSED 5m bar, projected onto
    the 1m grid by shifting one 5m bar. A 1m bar inside the 09:00-09:05 period
    therefore sees the 08:55-09:00 value, never the bar it sits inside. Signals
    are evaluated on closed 1m bars; entry is at the NEXT 1m open.

STRUCTURAL STOP (defined before running, §9)
    LONG:  stop = min( lowest low since the 1m Supertrend last flipped bullish
                       (inclusive, closed bars only), supertrend_1m[t] )
    SHORT: mirror with the highest high and the Supertrend above.
    The Supertrend is the structural reference and the leg extreme is the most
    recent confirmed swing. No future bar is consulted.

    A LEGACY stop -- min(supertrend, current bar low), the implementation used
    by every earlier experiment in this program -- is retained as a declared
    diagnostic so the §20 comparison against the pullback strategy is exact
    under both definitions.

SAME-BAR AMBIGUITY
    A bar containing both stop and target resolves to the STOP.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from numba import njit

from deltabt import indicators as ind
from deltabt.costs import SymbolCosts, funding_timestamps
from deltabt.strategy import resample_ohlcv

ST_PERIOD, ST_MULT = 10, 2.0
ADX_PERIOD, DI_PERIOD, WPR_PERIOD = 28, 14, 140
ADX_MIN = 25.0
RISK_PCT = 0.005
START_EQUITY = 10_000.0
MAX_LEVERAGE = 3.0
MAX_HOLD_BARS = 0          # no time exit in the frozen spec

ARMS = ("A", "B", "C", "D", "E")
WPR_VARIANTS = ("A", "B", "C")


@dataclass
class WTrade:
    symbol: str
    side: int
    arm: str
    signal_time: int
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    r_price: float
    stop_pct: float
    bars_held: int
    exit_reason: str
    r_gross: float
    fee_r: float
    slip_r: float
    funding_r: float
    cost_r: float
    r_net: float
    contracts: int
    notional: float
    ambiguous: bool
    cluster: str


@dataclass
class WResult:
    symbol: str
    arm: str
    params: dict
    trades: list[WTrade] = field(default_factory=list)
    signals: int = 0
    skipped_stop: int = 0
    skipped_size: int = 0

    def to_frame(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame(columns=[f for f in WTrade.__dataclass_fields__])
        return pd.DataFrame([t.__dict__ for t in self.trades])


# ---------------------------------------------------------------- indicators


@njit(cache=True)
def _leg_extreme(high, low, direction):
    """Lowest low / highest high since the Supertrend last flipped.

    Causal: at bar t it only sees bars in the current leg up to and including
    t. Returns (leg_low, leg_high).
    """
    n = high.size
    lo = np.full(n, np.nan)
    hi = np.full(n, np.nan)
    cur_lo = np.inf
    cur_hi = -np.inf
    prev = np.nan
    for t in range(n):
        d = direction[t]
        if np.isnan(d):
            continue
        if np.isnan(prev) or d != prev:
            cur_lo = low[t]
            cur_hi = high[t]
        else:
            if low[t] < cur_lo:
                cur_lo = low[t]
            if high[t] > cur_hi:
                cur_hi = high[t]
        lo[t] = cur_lo
        hi[t] = cur_hi
        prev = d
    return lo, hi


def _confirmed_5m(values5: np.ndarray, t5: np.ndarray, t1: np.ndarray) -> np.ndarray:
    """Project a 5m series onto the 1m grid using the last CLOSED 5m bar."""
    shifted = np.concatenate(([np.nan], values5[:-1].astype("float64")))
    slot = np.searchsorted(t5, t1, side="right") - 1
    out = np.full(len(t1), np.nan)
    ok = slot >= 0
    out[ok] = shifted[slot[ok]]
    return out


def build_conditions(df: pd.DataFrame) -> dict:
    """All frozen indicator conditions, on the 1m grid, strictly causal."""
    h = df["high"].to_numpy("float64"); l = df["low"].to_numpy("float64")
    c = df["close"].to_numpy("float64"); t1 = df["time"].to_numpy("int64")

    st1, dir1 = ind.supertrend(h, l, c, ST_MULT, ST_PERIOD)
    p1, m1, adx1 = ind.dmi(h, l, c, DI_PERIOD, ADX_PERIOD)
    wpr = ind.wpr(h, l, c, WPR_PERIOD)
    leg_lo, leg_hi = _leg_extreme(h, l, dir1)

    d5 = resample_ohlcv(df, 5)
    t5 = d5["time"].to_numpy("int64")
    H5 = d5["high"].to_numpy("float64"); L5 = d5["low"].to_numpy("float64")
    C5 = d5["close"].to_numpy("float64")
    st5, dir5 = ind.supertrend(H5, L5, C5, ST_MULT, ST_PERIOD)
    p5, m5, adx5 = ind.dmi(H5, L5, C5, DI_PERIOD, ADX_PERIOD)

    dir5b = _confirmed_5m(dir5, t5, t1)
    adx5b = _confirmed_5m(adx5, t5, t1)
    p5b = _confirmed_5m(p5, t5, t1)
    m5b = _confirmed_5m(m5, t5, t1)

    prev_wpr = np.concatenate(([np.nan], wpr[:-1]))
    with np.errstate(invalid="ignore"):
        rising = wpr > prev_wpr
        falling = wpr < prev_wpr
        cross_up_80 = (wpr > -80.0) & (prev_wpr <= -80.0)
        cross_dn_20 = (wpr < -20.0) & (prev_wpr >= -20.0)

        return dict(
            t1=t1, close=c, high=h, low=l, st1=st1, dir1=dir1,
            leg_lo=leg_lo, leg_hi=leg_hi, wpr=wpr,
            # 5m regime (confirmed)
            f5_long=(dir5b < 0) & (adx5b >= ADX_MIN) & (p5b > m5b),
            f5_short=(dir5b > 0) & (adx5b >= ADX_MIN) & (m5b > p5b),
            # 1m components
            st1_long=dir1 < 0, st1_short=dir1 > 0,
            adx1_long=(adx1 >= ADX_MIN) & (p1 > m1),
            adx1_short=(adx1 >= ADX_MIN) & (m1 > p1),
            # WPR variants
            wprA_long=(wpr > -80.0) & rising, wprA_short=(wpr < -20.0) & falling,
            wprB_long=(wpr > -50.0) & rising, wprB_short=(wpr < -50.0) & falling,
            wprC_long=cross_up_80, wprC_short=cross_dn_20,
            # the PREVIOUS pullback rule, for the §20 comparison
            pullback_long=(prev_wpr < -80.0) & (wpr > prev_wpr),
            pullback_short=(prev_wpr > -20.0) & (wpr < prev_wpr),
            warmup=max(WPR_PERIOD, DI_PERIOD + 2 * ADX_PERIOD, ST_PERIOD) + 5,
        )


def arm_signals(C: dict, arm: str, wpr_variant: str = "A") -> tuple[np.ndarray, np.ndarray]:
    """Compose the pre-declared arms. Arm A is the frozen baseline."""
    wl = C[f"wpr{wpr_variant}_long"]; ws = C[f"wpr{wpr_variant}_short"]
    if arm == "A":       # full
        lo = C["f5_long"] & C["st1_long"] & C["adx1_long"] & wl
        sh = C["f5_short"] & C["st1_short"] & C["adx1_short"] & ws
    elif arm == "B":     # remove WPR
        lo = C["f5_long"] & C["st1_long"] & C["adx1_long"]
        sh = C["f5_short"] & C["st1_short"] & C["adx1_short"]
    elif arm == "C":     # remove 1m ADX/DI
        lo = C["f5_long"] & C["st1_long"] & wl
        sh = C["f5_short"] & C["st1_short"] & ws
    elif arm == "D":     # remove 1m Supertrend
        lo = C["f5_long"] & C["adx1_long"] & wl
        sh = C["f5_short"] & C["adx1_short"] & ws
    elif arm == "E":     # 5m regime + WPR only
        lo = C["f5_long"] & wl
        sh = C["f5_short"] & ws
    elif arm == "PULLBACK":   # the previous strategy, for comparison
        lo = (C["f5_long"] & C["st1_long"] & C["adx1_long"]
              & (C["close"] > C["st1"]) & C["pullback_long"])
        sh = (C["f5_short"] & C["st1_short"] & C["adx1_short"]
              & (C["close"] < C["st1"]) & C["pullback_short"])

    # ---- H-TREND-1 arms: pure trend alignment, no Williams %R anywhere ----
    # T_A is definitionally identical to H-WPR-1 Arm B. Kept under its own key
    # so the two experiments' records stay separable rather than aliased.
    elif arm == "T_A":        # 5m regime + 1m Supertrend + 1m ADX/DI
        lo = C["f5_long"] & C["st1_long"] & C["adx1_long"]
        sh = C["f5_short"] & C["st1_short"] & C["adx1_short"]
    elif arm == "T_B":        # 5m regime only -- no 1m filter at all
        lo = C["f5_long"]
        sh = C["f5_short"]
    elif arm == "T_C":        # 5m regime + 1m Supertrend only
        lo = C["f5_long"] & C["st1_long"]
        sh = C["f5_short"] & C["st1_short"]
    elif arm == "T_D":        # 5m regime + 1m ADX/DI only
        lo = C["f5_long"] & C["adx1_long"]
        sh = C["f5_short"] & C["adx1_short"]
    else:
        raise ValueError(f"unknown arm {arm}")
    w = C["warmup"]
    lo = lo.copy(); sh = sh.copy()
    lo[:w] = False; sh[:w] = False
    return lo, sh


# ---------------------------------------------------------------- simulation


@njit(cache=True)
def _simulate(long_sig, short_sig, o, h, l, c, mh, ml, st1, leg_lo, leg_hi,
              tradable, target_r, taker, slip, tick, contract_value,
              max_lev, risk_pct, start_equity, legacy_stop, max_stop_pct):
    """Event loop. One position at a time. Entry at the NEXT bar's open."""
    n = o.size
    max_tr = 200000
    out = np.zeros((max_tr, 16))
    k = 0
    equity = start_equity
    i = 0
    skipped_stop = 0
    skipped_size = 0
    while i < n - 2:
        side = 0
        if long_sig[i]:
            side = 1
        elif short_sig[i]:
            side = -1
        if side == 0:
            i += 1
            continue
        j = i + 1                       # entry at next bar open
        if not tradable[j]:
            i += 1
            continue
        entry = o[j]
        if not np.isfinite(entry) or entry <= 0:
            i += 1
            continue

        s = st1[i]
        if legacy_stop:
            stop = min(s, l[i]) if side > 0 else max(s, h[i])
        else:
            stop = min(s, leg_lo[i]) if side > 0 else max(s, leg_hi[i])
        if not np.isfinite(stop):
            i += 1
            continue
        r_price = (entry - stop) if side > 0 else (stop - entry)
        if r_price <= 0 or (r_price / entry) > max_stop_pct:
            skipped_stop += 1
            i += 1
            continue
        target = entry + side * target_r * r_price

        units = (equity * risk_pct) / r_price
        cap = (equity * max_lev) / entry
        if cap < units:
            units = cap
        contracts = int(units / contract_value)
        if contracts <= 0:
            skipped_size += 1
            i += 1
            continue

        exit_px = np.nan
        reason = 0                       # 0 stop, 1 target, 2 end
        amb = 0
        m = j
        while m < n:
            hit_stop = (ml[m] <= stop) if side > 0 else (mh[m] >= stop)
            hit_tgt = (h[m] >= target) if side > 0 else (l[m] <= target)
            if hit_stop and hit_tgt:
                amb = 1
                exit_px = stop
                reason = 0
                break
            if hit_stop:
                exit_px = stop
                reason = 0
                break
            if hit_tgt:
                exit_px = target
                reason = 1
                break
            m += 1
        if m >= n:
            m = n - 1
            exit_px = c[m]
            reason = 2

        notional_in = contracts * contract_value * entry
        notional_out = contracts * contract_value * exit_px
        fee = (notional_in + notional_out) * taker
        slp = (notional_in + notional_out) * slip
        gross_cash = side * (exit_px - entry) * contracts * contract_value
        unit_risk = r_price * contracts * contract_value
        equity += gross_cash - fee - slp

        out[k, 0] = side
        out[k, 1] = i
        out[k, 2] = j
        out[k, 3] = m
        out[k, 4] = entry
        out[k, 5] = exit_px
        out[k, 6] = stop
        out[k, 7] = target
        out[k, 8] = r_price
        out[k, 9] = reason
        out[k, 10] = gross_cash / unit_risk
        out[k, 11] = fee / unit_risk
        out[k, 12] = slp / unit_risk
        out[k, 13] = contracts
        out[k, 14] = amb
        out[k, 15] = notional_in
        k += 1
        if k >= max_tr:
            break
        i = m + 1                       # one position at a time
    return out[:k], skipped_stop, skipped_size


def run(df: pd.DataFrame, mark: pd.DataFrame, funding: pd.DataFrame,
        costs: SymbolCosts, C: dict, *, arm: str = "A", wpr_variant: str = "A",
        target_r: float = 2.0, start: int = 0, end: int | None = None,
        legacy_stop: bool = False, cost_multiplier: float = 1.0,
        slippage_multiplier: float = 1.0, tradable: np.ndarray | None = None,
        max_stop_pct: float = 0.05) -> WResult:
    lo_sig, sh_sig = arm_signals(C, arm, wpr_variant)
    t1 = C["t1"]
    n = len(t1)
    if end is not None:
        beyond = t1 > end
        lo_sig = lo_sig & ~beyond
        sh_sig = sh_sig & ~beyond
    if start:
        before = t1 < start
        lo_sig = lo_sig & ~before
        sh_sig = sh_sig & ~before

    o = df["open"].to_numpy("float64")
    h = C["high"]; l = C["low"]; c = C["close"]
    if mark is not None and not mark.empty:
        mk = mark.set_index("time").reindex(t1)
        mh = mk["high"].to_numpy("float64"); ml = mk["low"].to_numpy("float64")
        bad = ~np.isfinite(mh) | ~np.isfinite(ml)
        mh = np.where(bad, h, mh); ml = np.where(bad, l, ml)
    else:
        mh, ml = h, l
    if tradable is None:
        tradable = np.ones(n, dtype=np.bool_)

    arr, sk_stop, sk_size = _simulate(
        lo_sig, sh_sig, o, h, l, c, mh, ml, C["st1"], C["leg_lo"], C["leg_hi"],
        tradable.astype(np.bool_), float(target_r),
        costs.effective_taker * cost_multiplier,
        costs.slippage_rate * slippage_multiplier,
        costs.tick_size, costs.contract_value, MAX_LEVERAGE, RISK_PCT,
        START_EQUITY, bool(legacy_stop), float(max_stop_pct))

    res = WResult(symbol=costs.symbol, arm=arm,
                  params=dict(wpr_variant=wpr_variant, target_r=target_r,
                              legacy_stop=legacy_stop),
                  signals=int(lo_sig.sum() + sh_sig.sum()),
                  skipped_stop=int(sk_stop), skipped_size=int(sk_size))
    if arr.shape[0] == 0:
        return res

    stamps = set(int(s) for s in funding_timestamps(int(t1[0]), int(t1[-1]),
                                                    costs.funding_interval_seconds))
    frate = {}
    if funding is not None and not funding.empty:
        ft = funding["time"].to_numpy("int64"); fv = funding["close"].to_numpy("float64")
        for s in stamps:
            idx = np.searchsorted(ft, s, side="right") - 1
            if idx >= 0 and np.isfinite(fv[idx]):
                frate[s] = float(fv[idx])
    reasons = {0: "stop", 1: "target", 2: "end"}

    for row in arr:
        side = int(row[0]); i_ = int(row[1]); j_ = int(row[2]); m_ = int(row[3])
        entry = row[4]; r_price = row[8]
        f_r = 0.0
        for s in stamps:
            if t1[j_] <= s <= t1[m_]:
                f_r += side * (frate.get(s, 0.0) / 100.0) * entry / r_price
        cost_r = row[11] + row[12] + f_r
        res.trades.append(WTrade(
            symbol=costs.symbol, side=side, arm=arm,
            signal_time=int(t1[i_]), entry_time=int(t1[j_]), exit_time=int(t1[m_]),
            entry_price=float(entry), exit_price=float(row[5]),
            stop_price=float(row[6]), target_price=float(row[7]),
            r_price=float(r_price), stop_pct=float(r_price / entry),
            bars_held=int(m_ - j_), exit_reason=reasons[int(row[9])],
            r_gross=float(row[10]), fee_r=float(row[11]), slip_r=float(row[12]),
            funding_r=float(f_r), cost_r=float(cost_r),
            r_net=float(row[10] - cost_r), contracts=int(row[13]),
            notional=float(row[15]), ambiguous=bool(row[14]),
            cluster=pd.Timestamp(int(t1[j_]), unit="s").strftime("%Y-%m-%d"),
        ))
    return res
