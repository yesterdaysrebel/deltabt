"""H-EMA-2 / H-EMA-3 -- EMA mechanisms on higher timeframes, against controls.

WHAT IS REUSED, UNMODIFIED
    hwpr._simulate        entry/exit/stop/fee/slippage/funding event loop
    hwpr._leg_extreme     lowest low / highest high since the Supertrend flipped
    stops.injection_arrays  the stop-array contract, with its NaN/order traps
    costs.SymbolCosts     production fee model
    strategy.resample_ohlcv  UTC-aligned 1m -> Nm
    indicators.atr / .supertrend

WHAT IS NEW
    The EMA (none exists in indicators.py), the five mechanisms, the causal
    exec-TF -> 1m projection, and the controls. Nothing about execution, stop,
    sizing or cost changes.

WHY THE CONTROLS EXIST
    A higher timeframe widens the structural stop by construction, and cost/R
    falls mechanically with stop width. A positive net expectancy at 1h
    therefore says nothing on its own; it must be measured against a control
    that inherits the same stop geometry but no directional information.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numba import njit

from deltabt import indicators as ind
from deltabt.costs import SymbolCosts, funding_timestamps
from deltabt.research.hwpr import WResult, WTrade, _leg_extreme, _simulate
from deltabt.research.stops import injection_arrays
from deltabt.strategy import resample_ohlcv

ST_PERIOD, ST_MULT = 10, 2.0
ATR_PERIOD = 14
TARGET_R = 2.0
MAX_STOP_PCT = 0.05
MAX_LEVERAGE = 3.0
RESEARCH_EQUITY = 1e18
RESEARCH_RISK = 1e-9
SLOPE_LOOKBACK = 5
VOL_BASELINE = 20
PULLBACK_EXPIRY = 10
REGIME_PAIR = (20, 50)


# ------------------------------------------------------------------ EMA


@njit(cache=True)
def _ema(values: np.ndarray, length: int) -> np.ndarray:
    """SMA seed, then alpha = 2/(length+1). Strictly one-sided.

    ``out[t]`` depends only on ``values[:t+1]``. Everything before the seed
    index is NaN rather than a partially-converged value, so an arm can never
    trade on an EMA that has not had `length` observations.
    """
    n = values.size
    out = np.full(n, np.nan)
    if length <= 0 or n < length:
        return out
    alpha = 2.0 / (length + 1.0)
    s = 0.0
    cnt = 0
    seed_at = -1
    for t in range(n):
        v = values[t]
        if np.isnan(v):
            continue
        s += v
        cnt += 1
        if cnt == length:
            seed_at = t
            break
    if seed_at < 0:
        return out
    prev = s / length
    out[seed_at] = prev
    for t in range(seed_at + 1, n):
        v = values[t]
        if np.isnan(v):
            out[t] = prev
            continue
        prev = alpha * v + (1.0 - alpha) * prev
        out[t] = prev
    return out


def ema(values, length: int) -> np.ndarray:
    return _ema(np.ascontiguousarray(values, dtype="float64"), int(length))


def crossover_events(close, fast: int, slow: int) -> dict:
    """FALSE -> TRUE transitions only. A crossover is an event, not a state."""
    ef, es = ema(close, fast), ema(close, slow)
    above, below = ef > es, ef < es
    prev_above = np.concatenate(([False], above[:-1]))
    prev_below = np.concatenate(([False], below[:-1]))
    valid = np.isfinite(ef) & np.isfinite(es)
    ok = valid & np.concatenate(([False], valid[:-1]))
    return dict(ema_fast=ef, ema_slow=es,
                x_long=ok & above & ~prev_above,
                x_short=ok & below & ~prev_below)


# ------------------------------------------------------------------ mechanisms


@njit(cache=True)
def _pullback(x_long, x_short, close, ema_fast, atr, d, expiry):
    """M4. Arm on a crossover; fire on a later bar that retraces and resumes.

    Re-entry is evaluated BEFORE a new crossover is armed, so a setup can never
    fire on the bar that created it -- the pullback must actually be after the
    crossover. A new crossover replaces any armed setup; an unfired setup dies
    after `expiry` bars.
    """
    n = close.size
    fl = np.zeros(n, np.bool_)
    fs = np.zeros(n, np.bool_)
    armed = 0
    age = 0
    for t in range(n):
        if armed != 0:
            age += 1
            if age > expiry:
                armed = 0
            elif t > 0 and np.isfinite(atr[t]) and np.isfinite(ema_fast[t]):
                near = abs(close[t] - ema_fast[t]) <= d * atr[t]
                if near and armed > 0 and close[t] > close[t - 1]:
                    fl[t] = True
                    armed = 0
                elif near and armed < 0 and close[t] < close[t - 1]:
                    fs[t] = True
                    armed = 0
        if x_long[t]:
            armed = 1
            age = 0
        elif x_short[t]:
            armed = -1
            age = 0
    return fl, fs


def project_regime(t_reg, tf_reg_min, values, t_exec, tf_exec_min):
    """Last FULLY CLOSED regime bar as of each exec bar's close."""
    known_at = t_exec + tf_exec_min * 60
    slot = np.searchsorted(t_reg, known_at - tf_reg_min * 60, side="right") - 1
    out = np.full(len(t_exec), np.nan)
    ok = slot >= 0
    out[ok] = values[slot[ok]]
    return out


def build_tf(df1m: pd.DataFrame, tf_min: int) -> dict:
    """Execution-timeframe grid: Supertrend stop, ATR, and the OHLC it needs."""
    d = resample_ohlcv(df1m, tf_min) if tf_min > 1 else df1m.reset_index(drop=True)
    t = d["time"].to_numpy("int64")
    h = d["high"].to_numpy("float64")
    lo = d["low"].to_numpy("float64")
    c = d["close"].to_numpy("float64")
    st, dirn = ind.supertrend(h, lo, c, ST_MULT, ST_PERIOD)
    leg_lo, leg_hi = _leg_extreme(h, lo, dirn)
    # the frozen structural stop, evaluated on this grid
    stop_long = np.minimum(st, leg_lo)
    stop_short = np.maximum(st, leg_hi)
    atr = ind.atr(h, lo, c, ATR_PERIOD)
    return dict(tf=tf_min, time=t, high=h, low=lo, close=c, atr=atr,
                supertrend=st, direction=dirn,
                stop_long=stop_long, stop_short=stop_short)


def mech_signals(F: dict, X: dict, mech: str, params: dict, regime=None):
    """(long, short) on the execution-TF grid for one arm."""
    xl, xs = X["x_long"], X["x_short"]
    c, atr = F["close"], F["atr"]
    if mech == "M1":
        lo, sh = xl.copy(), xs.copy()
    elif mech == "M2":
        es = X["ema_slow"]
        k = SLOPE_LOOKBACK
        disp = np.full(len(es), np.nan)
        disp[k:] = es[k:] - es[:-k]
        with np.errstate(invalid="ignore", divide="ignore"):
            ns = disp / atr
        th = params["slope_threshold"]
        lo = xl & np.isfinite(ns) & (ns >= th)
        sh = xs & np.isfinite(ns) & (ns <= -th)
    elif mech == "M3":
        prev = np.concatenate(([np.nan], atr[:-1]))
        rising = np.isfinite(atr) & np.isfinite(prev) & (atr > prev)
        # bar t is excluded from its own baseline
        base = pd.Series(atr).rolling(VOL_BASELINE).mean().shift(1).to_numpy()
        with np.errstate(invalid="ignore", divide="ignore"):
            ratio = atr / base
        gate = rising & np.isfinite(ratio) & (ratio > params["atr_ratio_threshold"])
        lo, sh = xl & gate, xs & gate
    elif mech == "M4":
        lo, sh = _pullback(xl, xs, c, X["ema_fast"], atr,
                           float(params["pullback_atr"]), PULLBACK_EXPIRY)
    elif mech == "M5":
        bull, bear = regime
        lo, sh = xl & bull, xs & bear
    else:
        raise ValueError(f"unknown mechanism {mech}")
    return np.asarray(lo, bool), np.asarray(sh, bool)


# ------------------------------------------------- exec-TF grid -> 1m grid


def entry_index(t_tf, tf_min: int, t1) -> np.ndarray:
    """1m index whose OPEN is the first tradable instant after the TF close."""
    known_at = t_tf + tf_min * 60
    idx = np.searchsorted(t1, known_at, side="left")
    return np.where(idx >= len(t1), -1, idx).astype("int64")


def valid_stop_mask(F: dict) -> np.ndarray:
    """Bars whose frozen structural stop is usable at all.

    `injection_arrays` raises on a non-finite or mis-ordered stop, which is the
    right contract for a bug but the wrong behaviour for a degenerate bar, so
    those bars are excluded here and counted rather than aborting the arm.
    """
    sl, ss = F["stop_long"], F["stop_short"]
    return np.isfinite(sl) & np.isfinite(ss) & (sl < ss)


def project(sig_long, sig_short, F, t1, n1, e=None):
    """Signals + stops onto the 1m grid, at the index the simulator reads."""
    if e is None:
        e = entry_index(F["time"], F["tf"], t1)
    lo1 = np.zeros(n1, bool)
    sh1 = np.zeros(n1, bool)
    sl1 = np.full(n1, np.nan)
    ss1 = np.full(n1, np.nan)
    ok = (e > 0) & (e < n1) & valid_stop_mask(F)
    fl = ok & np.asarray(sig_long, bool)
    fs = ok & np.asarray(sig_short, bool)
    lo1[e[fl] - 1] = True
    sh1[e[fs] - 1] = True
    k = np.flatnonzero(fl | fs)
    i = e[k] - 1
    sl1[i] = F["stop_long"][k]
    ss1[i] = F["stop_short"][k]
    return lo1, sh1, sl1, ss1


def simulate(sym: dict, lo1, sh1, sl1, ss1, *, window, label="",
             truncate_at_window: bool = False) -> WResult:
    """Frozen simulator, reached only through the frozen stop contract.

    ``truncate_at_window`` bounds the EXIT walk at the split boundary. Without
    it the walk runs the full loaded array and resolves trades on data belonging
    to the next segment, which is why unresolved-at-boundary counts are
    otherwise always zero.
    """
    t1 = sym["t1"]
    beyond = (t1 > window[1]) | (t1 < window[0])
    lo1 = np.asarray(lo1, bool) & ~beyond
    sh1 = np.asarray(sh1, bool) & ~beyond
    st1, leg_lo, leg_hi = injection_arrays(lo1, sh1, sl1, ss1)
    n_keep = (int(np.searchsorted(t1, window[1], side="right"))
              if truncate_at_window else len(t1))
    s = slice(0, n_keep)
    costs: SymbolCosts = sym["costs"]
    arr, sk_stop, sk_size = _simulate(
        lo1[s], sh1[s], sym["o"][s], sym["h"][s], sym["l"][s], sym["c"][s],
        sym["mh"][s], sym["ml"][s], st1[s], leg_lo[s], leg_hi[s],
        sym["tradable"][s], float(TARGET_R),
        costs.effective_taker, costs.slippage_rate, costs.tick_size,
        costs.contract_value, MAX_LEVERAGE, RESEARCH_RISK, RESEARCH_EQUITY,
        False, float(MAX_STOP_PCT))
    res = WResult(symbol=costs.symbol, arm=label, params={},
                  signals=int(lo1.sum() + sh1.sum()),
                  skipped_stop=int(sk_stop), skipped_size=int(sk_size))
    if arr.shape[0] == 0:
        return res
    stamps = set(int(x) for x in funding_timestamps(
        int(t1[0]), int(t1[-1]), costs.funding_interval_seconds))
    frate = {}
    f = sym["funding"]
    if f is not None and not f.empty:
        ft = f["time"].to_numpy("int64")
        fv = f["close"].to_numpy("float64")
        for x in stamps:
            j = np.searchsorted(ft, x, side="right") - 1
            if j >= 0 and np.isfinite(fv[j]):
                frate[x] = float(fv[j])
    reasons = {0: "stop", 1: "target", 2: "end"}
    for row in arr:
        side = int(row[0]); i_ = int(row[1]); j_ = int(row[2]); m_ = int(row[3])
        entry = row[4]; r_price = row[8]
        f_r = 0.0
        for x in stamps:
            if t1[j_] <= x <= t1[m_]:
                f_r += side * (frate.get(x, 0.0) / 100.0) * entry / r_price
        cost_r = row[11] + row[12] + f_r
        res.trades.append(WTrade(
            symbol=costs.symbol, side=side, arm=label,
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


# ------------------------------------------------------------------ controls


def eligible_population(F, sym, window, warmup: int):
    """Every (exec-TF bar, direction) pair a control could legitimately enter.

    The prospective stop width uses the SAME projected 1m entry open the arm
    would have used, so it is knowable at signal time -- no future bar is read
    to decide eligibility. Bars failing the frozen 5% cap are excluded for the
    same reason the simulator would have skipped them.
    """
    t1 = sym["t1"]
    n1 = len(t1)
    e = entry_index(F["time"], F["tf"], t1)
    ok = (e > 0) & (e < n1) & valid_stop_mask(F)
    ok[:warmup] = False
    k = np.flatnonzero(ok)
    if k.size == 0:
        empty = np.zeros(0, "int64")
        return empty, empty, np.zeros(0)
    ent = sym["o"][e[k]]
    inwin = ((t1[e[k]] >= window[0]) & (t1[e[k]] < window[1])
             & np.isfinite(ent) & (ent > 0))
    k = k[inwin]
    ent = ent[inwin]
    bars, dirs, pcts = [], [], []
    for side, stop in ((1, F["stop_long"][k]), (-1, F["stop_short"][k])):
        r = (ent - stop) if side > 0 else (stop - ent)
        with np.errstate(invalid="ignore", divide="ignore"):
            pct = r / ent
        good = (r > 0) & np.isfinite(pct) & (pct <= MAX_STOP_PCT)
        bars.append(k[good])
        dirs.append(np.full(int(good.sum()), side, "int64"))
        pcts.append(pct[good])
    return np.concatenate(bars), np.concatenate(dirs), np.concatenate(pcts)


def control_ca(arm_lo1, arm_sh1, sl1, ss1, seed: int):
    """C-a: the arm's own entry bars, direction by fair coin. Diagnostic only.

    Note this necessarily breaks stop-width matching: flipping direction at a
    bar hands the trade the OTHER side's stop, which in a trending leg is much
    tighter. C-a's cost/R therefore runs far above the arm's, and it must not be
    read as a cost-comparable benchmark.
    """
    fired = arm_lo1 | arm_sh1
    rng = np.random.default_rng(seed)
    coin = rng.random(fired.size) < 0.5
    return fired & coin, fired & ~coin, sl1, ss1


def control_cb(arm_stop_pct, F, sym, window, warmup: int, seed: int):
    """C-b: same opportunity set, same stop-WIDTH distribution, no direction edge.

    Decile edges come from the ARM's realised stop_pct, never from the control,
    and BOTH end bins are CLOSED at the arm's realised minimum and maximum. An
    open bottom bin admits eligible bars with stops far tighter than anything
    the arm traded; because cost/R ~ 2(taker+slip)/stop_pct, a handful of them
    dominate the control's MEAN cost while leaving its median untouched, which
    inflated the control's cost ~70% and inverted the primary metric's sign.

    Direction is stratified rather than coin-flipped after selection. Matching
    stop width and drawing direction from an independent coin cannot both hold
    at a bar whose long and short stops differ -- selecting by width couples
    direction to width, while flipping afterwards hands the trade the other
    direction's width and destroys the match. Drawing half of each decile's
    quota from long-eligible bars and half from short-eligible bars gives a
    50/50 direction marginal AND an exact width match, with direction carrying
    no information about the future.

    Sampling is WITHOUT replacement: a signal is a boolean array position, so a
    repeated index cannot be represented and would silently collapse.
    """
    n1 = len(sym["t1"])
    lo1 = np.zeros(n1, bool); sh1 = np.zeros(n1, bool)
    sl1 = np.full(n1, np.nan); ss1 = np.full(n1, np.nan)
    meta = dict(requested=0, drawn=0, shortfall=0, collisions=0, deciles=0,
                pool_out_of_range=0)
    arm_stop_pct = np.asarray(arm_stop_pct, dtype="float64")
    if arm_stop_pct.size == 0:
        return lo1, sh1, sl1, ss1, meta

    bars, dirs, pcts = eligible_population(F, sym, window, warmup)
    if bars.size == 0:
        meta["shortfall"] = int(arm_stop_pct.size)
        return lo1, sh1, sl1, ss1, meta

    edges = np.quantile(arm_stop_pct, np.linspace(0.0, 1.0, 11))
    want = np.histogram(arm_stop_pct, bins=edges)[0]
    inrange = (pcts >= edges[0]) & (pcts <= edges[-1])
    slot = np.where(inrange, np.digitize(pcts, edges[1:-1], right=False), -1)
    meta["pool_out_of_range"] = int((~inrange).sum())
    meta["deciles"] = int((want > 0).sum())

    rng = np.random.default_rng(seed)
    e = entry_index(F["time"], F["tf"], sym["t1"])
    is_long = dirs > 0
    used = set()
    for d in range(len(want)):
        need = int(want[d])
        if need == 0:
            continue
        meta["requested"] += need
        n_long = need // 2 + (1 if (need % 2 and rng.random() < 0.5) else 0)
        for side_long, n_side in ((True, n_long), (False, need - n_long)):
            if n_side <= 0:
                continue
            pool = np.flatnonzero((slot == d) & (is_long == side_long))
            if pool.size == 0:
                meta["shortfall"] += n_side
                continue
            take = min(n_side, pool.size)
            if take < n_side:
                meta["shortfall"] += n_side - take
            for q in rng.choice(pool, size=take, replace=False):
                k = int(bars[q])
                if k in used:
                    meta["collisions"] += 1
                    continue
                used.add(k)
                i = int(e[k]) - 1
                if i < 0:
                    continue
                if side_long:
                    lo1[i] = True
                else:
                    sh1[i] = True
                sl1[i] = F["stop_long"][k]
                ss1[i] = F["stop_short"][k]
                meta["drawn"] += 1
    return lo1, sh1, sl1, ss1, meta
