"""H-Structure-1 -- does market structure (HH/HL/LH/LL) predict continuation?

PRE-REGISTERED. This module is frozen before any result is inspected.

WHAT IS REUSED (audited, unchanged, imported not copied)
    deltabt.research.hwpr._simulate   the entry/exit/stop/fee/slippage event
                                      loop, one position at a time, entry at
                                      the NEXT bar's open, stop triggered on
                                      MARK price, same-bar stop+target -> STOP.
    deltabt.research.hwpr.WTrade      the trade record schema.
    deltabt.costs.SymbolCosts         production fee model: per-symbol taker,
                                      x1.18 GST, 2.0 bps slippage.
    deltabt.costs.funding_timestamps  per-symbol funding cadence (4h/8h).
    deltabt.data.store.CandleStore    parquet candle cache (ltp/mark/funding).
    deltabt.data.quality.tradable_mask synthetic-bar and halt exclusion.
    deltabt.strategy.resample_ohlcv   UTC-aligned 1m -> Nm aggregation.
    deltabt.indicators.atr            causal ATR for the displacement buckets.
    deltabt.research.stats            stationary bootstrap + cluster design
                                      effect for t and effective n.

WHAT IS NEW HERE
    Only the signal: swing detection, structure classification, and the four
    pre-declared families A/B/C/D. Nothing about execution, cost or risk
    changes.

SWING DEFINITION (§2)
    swing high at bar k  <=>  high[k] > high[j] for all j in [k-N, k+N], j != k
    swing low  at bar k  <=>  low[k]  < low[j]  for all j in [k-N, k+N], j != k
    STRICT inequality on both sides; a tie is not a swing. Deterministic.

    The bar k swing is CONFIRMED only at the close of bar k+N, because the N
    bars after k are what rule it in. Every downstream array is built by a
    forward-only loop that registers a swing at index k+N and never at k. No
    centred window, no shift(-x), no bfill, no reindex, anywhere.

    CONFIRMATION DELAY IS THEREFORE EXACTLY N STRUCTURE BARS BY CONSTRUCTION.

CLASSIFICATION (§3)
    On confirmation, a swing high is HH if above the previous confirmed swing
    high else LH; a swing low is HL if above the previous confirmed swing low
    else LL. Structure state at bar t uses only swings confirmed at or before
    the close of bar t.

        bull_structure[t] = last confirmed high is HH AND last confirmed low is HL
        bear_structure[t] = last confirmed high is LH AND last confirmed low is LL

FAMILIES (§4) -- run separately, never combined
    A  BULL_STRUCT   bull_structure established (HH and HL in sequence)
    B  BEAR_STRUCT   bear_structure established (LL and LH in sequence)
    C  BOS           close breaks the last confirmed swing high while the last
                     confirmed low is an HL (bear mirror: breaks the last
                     confirmed swing low while the last confirmed high is an LH)
    D  FLIP          bear structure -> a confirmed HL -> close breaks the LH
                     that stood when the HL confirmed (bull mirror for bear)

TRIGGER SEMANTICS (§7)
    ONESHOT  FALSE -> TRUE transition only.
    LEVEL    condition held true; re-arms after each position closes, because
             the simulator holds one position at a time.

EXECUTION TIMING (§6) -- the anti-lookahead core
    A structure bar spanning [T, T+tf) closes at the instant T+tf. That is the
    first instant its close, and therefore any swing it confirms, is knowable.
    The order is placed at the open of the first EXECUTION bar at or after
    T+tf, which for a single-timeframe variant is the 1m bar opening exactly at
    T+tf.

    The fill/stop/target path is resolved on the 1m grid for every variant.
    That is a refinement of intrabar resolution, never a relaxation of it: a 1m
    walk can only find the stop EARLIER than a coarse walk, never later, and it
    uses only bars at or after the entry bar.

STOP AND TARGET (§8)
    LONG   stop = last CONFIRMED swing low  at the signal bar
    SHORT  stop = last CONFIRMED swing high at the signal bar
    target = entry +/- 2R. Inherited 5% max stop distance filter. Nothing here
    is optimised; no trailing, no time stop, no structure exit.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from numba import njit

from deltabt import indicators as ind
from deltabt.costs import SymbolCosts, funding_timestamps
from deltabt.research.hwpr import WResult, WTrade, _simulate
from deltabt.strategy import resample_ohlcv

# ------------------------------------------------------------------ constants

#: Pre-declared swing strengths. §2 -- no other N may be tried.
SWING_N = (2, 3, 5, 8)

#: Pre-declared structure timeframes, in minutes. §5.
STRUCT_TF = (5, 15, 60)

#: Pre-declared multi-timeframe combinations (structure_tf, exec_tf). §5.
MTF = ((5, 15), (15, 5))

FAMILIES = ("A", "B", "C", "D")
FAMILY_DESC = {
    "A": "BULL_STRUCT  confirmed HH + confirmed HL",
    "B": "BEAR_STRUCT  confirmed LL + confirmed LH",
    "C": "BOS          break of last confirmed swing high after an HL",
    "D": "FLIP         bear structure -> HL -> break of the standing LH",
}
TRIGGERS = ("oneshot", "level")

#: Inherited from H-WPR-1 / H-EMA-1, unchanged.
TARGET_R = 2.0
MAX_STOP_PCT = 0.05
ATR_PERIOD = 14

#: Constant unit risk (H-EMA-1 §RESEARCH_EQUITY). The simulator compounds
#: equity, so at production sizing a losing arm goes broke and its remaining
#: signals silently stop becoming trades -- which reads as "the edge decayed"
#: when it is only the account emptying. Every metric here is per unit of risk
#: and exactly invariant to contract count, so holding the cash risk per trade
#: constant changes no R figure. Proven at run time, not asserted.
RESEARCH_EQUITY = 1e18
RESEARCH_RISK = 1e-9

#: Pre-declared ATR-normalised displacement buckets (§15). Fixed multiples,
#: chosen before any result was seen; NOT quantiles of the observed data.
ATR_BUCKETS = (0.0, 0.5, 1.0, 2.0, np.inf)
ATR_BUCKET_LABELS = ("<0.5 ATR", "0.5-1 ATR", "1-2 ATR", ">2 ATR")


# ------------------------------------------------------------------ swings


@njit(cache=True)
def _swing_flags(high: np.ndarray, low: np.ndarray, n_str: int):
    """Fractal swing highs/lows with strict inequality on both sides.

    ``sh[k]`` / ``sl[k]`` mark the swing BAR. Nothing downstream may read them
    at k -- see ``_structure_state``, which registers them at k + n_str.
    """
    n = high.size
    sh = np.zeros(n, np.bool_)
    sl = np.zeros(n, np.bool_)
    for k in range(n_str, n - n_str):
        hk = high[k]
        ok = True
        for j in range(k - n_str, k + n_str + 1):
            if j != k and not (hk > high[j]):
                ok = False
                break
        sh[k] = ok
        lk = low[k]
        ok = True
        for j in range(k - n_str, k + n_str + 1):
            if j != k and not (lk < low[j]):
                ok = False
                break
        sl[k] = ok
    return sh, sl


# ------------------------------------------------------------------ structure


@njit(cache=True)
def _structure_state(sh, sl, high, low, close, n_str):
    """Forward-only structure state. Everything is as-of the CLOSE of bar t.

    At bar t the only new information admitted is the swing at bar ``t - n_str``,
    which is exactly the swing this bar confirms. A swing at bar k is invisible
    to every bar before k + n_str.

    Returns per-bar arrays; all NaN / -1 until enough swings exist.
    """
    n = high.size
    last_h_px = np.full(n, np.nan); prev_h_px = np.full(n, np.nan)
    last_l_px = np.full(n, np.nan); prev_l_px = np.full(n, np.nan)
    last_h_i = np.full(n, -1, np.int64); last_l_i = np.full(n, -1, np.int64)
    prev_h_i = np.full(n, -1, np.int64); prev_l_i = np.full(n, -1, np.int64)
    is_hh = np.zeros(n, np.bool_); is_lh = np.zeros(n, np.bool_)
    is_hl = np.zeros(n, np.bool_); is_ll = np.zeros(n, np.bool_)
    new_hi = np.zeros(n, np.bool_); new_lo = np.zeros(n, np.bool_)

    ch_px = np.nan; ph_px = np.nan; ch_i = -1; ph_i = -1
    cl_px = np.nan; pl_px = np.nan; cl_i = -1; pl_i = -1
    hh = False; lh = False; hl = False; ll = False

    for t in range(n):
        k = t - n_str
        if k >= n_str:
            # A bar can qualify as both; highs are registered first. Declared.
            if sh[k]:
                ph_px = ch_px; ph_i = ch_i
                ch_px = high[k]; ch_i = k
                if ph_i >= 0:
                    hh = ch_px > ph_px
                    lh = ch_px < ph_px
                new_hi[t] = True
            if sl[k]:
                pl_px = cl_px; pl_i = cl_i
                cl_px = low[k]; cl_i = k
                if pl_i >= 0:
                    hl = cl_px > pl_px
                    ll = cl_px < pl_px
                new_lo[t] = True
        last_h_px[t] = ch_px; prev_h_px[t] = ph_px
        last_l_px[t] = cl_px; prev_l_px[t] = pl_px
        last_h_i[t] = ch_i; prev_h_i[t] = ph_i
        last_l_i[t] = cl_i; prev_l_i[t] = pl_i
        is_hh[t] = hh; is_lh[t] = lh
        is_hl[t] = hl; is_ll[t] = ll
    return (last_h_px, prev_h_px, last_l_px, prev_l_px,
            last_h_i, prev_h_i, last_l_i, prev_l_i,
            is_hh, is_lh, is_hl, is_ll, new_hi, new_lo)


@njit(cache=True)
def _flip_states(bull, bear, is_hl, is_lh, new_lo, new_hi, last_h_px, last_l_px,
                 close):
    """Family D state machines, forward-only.

    BULL: after bear structure has been seen, the next CONFIRMED HL arms a flip
    whose reference is the swing high standing at that moment (the LH being
    challenged). The flip fires when a close exceeds it, and disarms.
    """
    n = close.size
    fire_bull = np.zeros(n, np.bool_); fire_bear = np.zeros(n, np.bool_)
    state_bull = np.zeros(n, np.bool_); state_bear = np.zeros(n, np.bool_)
    ref_bull = np.full(n, np.nan); ref_bear = np.full(n, np.nan)

    seen_bear = False; armed_b = False; rb = np.nan; live_b = False
    seen_bull = False; armed_s = False; rs = np.nan; live_s = False
    for t in range(n):
        if bear[t]:
            seen_bear = True
            live_b = False
        if bull[t]:
            seen_bull = True
            live_s = False
        if seen_bear and new_lo[t] and is_hl[t] and np.isfinite(last_h_px[t]):
            armed_b = True; rb = last_h_px[t]; seen_bear = False
        if seen_bull and new_hi[t] and is_lh[t] and np.isfinite(last_l_px[t]):
            armed_s = True; rs = last_l_px[t]; seen_bull = False
        if armed_b and close[t] > rb:
            fire_bull[t] = True; armed_b = False; live_b = True
        if armed_s and close[t] < rs:
            fire_bear[t] = True; armed_s = False; live_s = True
        state_bull[t] = live_b; state_bear[t] = live_s
        ref_bull[t] = rb; ref_bear[t] = rs
    return fire_bull, fire_bear, state_bull, state_bear, ref_bull, ref_bear


def build_structure(df1m: pd.DataFrame, tf_min: int, n_str: int) -> dict:
    """All structure arrays for one symbol / timeframe / swing strength.

    Every array is indexed by STRUCTURE bar and is valid as of that bar's
    CLOSE, i.e. usable from the instant ``time[t] + tf_min*60``.
    """
    d = resample_ohlcv(df1m, tf_min) if tf_min > 1 else df1m.reset_index(drop=True)
    t = d["time"].to_numpy("int64")
    h = d["high"].to_numpy("float64")
    lo = d["low"].to_numpy("float64")
    c = d["close"].to_numpy("float64")

    sh, sl = _swing_flags(h, lo, n_str)
    (last_h_px, prev_h_px, last_l_px, prev_l_px, last_h_i, prev_h_i,
     last_l_i, prev_l_i, is_hh, is_lh, is_hl, is_ll,
     new_hi, new_lo) = _structure_state(sh, sl, h, lo, c, n_str)

    bull = is_hh & is_hl
    bear = is_lh & is_ll

    # C -- break of structure, evaluated on the close of the structure bar.
    with np.errstate(invalid="ignore"):
        bos_bull = is_hl & np.isfinite(last_h_px) & (c > last_h_px)
        bos_bear = is_lh & np.isfinite(last_l_px) & (c < last_l_px)

    (fb, fs, sb, ss, refb, refs) = _flip_states(
        bull, bear, is_hl, is_lh, new_lo, new_hi, last_h_px, last_l_px, c)

    atr = ind.atr(h, lo, c, ATR_PERIOD)

    def onshot(x):
        p = np.concatenate(([False], x[:-1]))
        return x & ~p

    warm = 3 * n_str + ATR_PERIOD + 2
    out = dict(
        tf=tf_min, n=n_str, time=t, open=d["open"].to_numpy("float64"),
        high=h, low=lo, close=c, atr=atr,
        last_h_px=last_h_px, prev_h_px=prev_h_px,
        last_l_px=last_l_px, prev_l_px=prev_l_px,
        last_h_i=last_h_i, prev_h_i=prev_h_i,
        last_l_i=last_l_i, prev_l_i=prev_l_i,
        is_hh=is_hh, is_lh=is_lh, is_hl=is_hl, is_ll=is_ll,
        bull=bull, bear=bear,
        # families x trigger, on the structure grid
        A_long_level=bull, A_long_shot=onshot(bull),
        B_short_level=bear, B_short_shot=onshot(bear),
        C_long_level=bos_bull, C_long_shot=onshot(bos_bull),
        C_short_level=bos_bear, C_short_shot=onshot(bos_bear),
        D_long_level=sb, D_long_shot=fb,
        D_short_level=ss, D_short_shot=fs,
        D_ref_long=refb, D_ref_short=refs,
        warmup=warm,
    )
    for k in ("A_long", "B_short", "C_long", "C_short", "D_long", "D_short"):
        for m in ("level", "shot"):
            a = out[f"{k}_{m}"].copy()
            a[:warm] = False
            out[f"{k}_{m}"] = a
    return out


def family_signals(S: dict, family: str, trigger: str):
    """(long, short) boolean arrays on the STRUCTURE grid for one family."""
    m = "shot" if trigger == "oneshot" else "level"
    n = len(S["time"])
    z = np.zeros(n, bool)
    if family == "A":
        return S[f"A_long_{m}"], z
    if family == "B":
        return z, S[f"B_short_{m}"]
    if family == "C":
        return S[f"C_long_{m}"], S[f"C_short_{m}"]
    if family == "D":
        return S[f"D_long_{m}"], S[f"D_short_{m}"]
    raise ValueError(family)


# ------------------------------------------------- structure grid -> 1m grid


def entry_index(t_struct: np.ndarray, tf_min: int, exec_tf_min: int,
                t1: np.ndarray) -> np.ndarray:
    """For each structure bar, the 1m index of the first tradable execution open.

    A structure bar [T, T+tf) is knowable only at T+tf. The execution bar that
    can be entered is the first exec-timeframe boundary at or after T+tf. The
    returned index is where the ORDER FILLS; the simulator is handed the signal
    one index earlier because it enters at ``i+1``.

    -1 where no such 1m bar exists in the data.
    """
    step = exec_tf_min * 60
    known_at = t_struct + tf_min * 60
    boundary = ((known_at + step - 1) // step) * step
    idx = np.searchsorted(t1, boundary, side="left")
    idx = np.where(idx >= len(t1), -1, idx)
    return idx.astype("int64")


def project(sig_struct_long, sig_struct_short, S, exec_tf_min, t1, n1):
    """Place structure-grid signals on the 1m grid at the signal-bar index.

    Returns (long_1m, short_1m, stop_long_1m, stop_short_1m, src) where ``src``
    maps a 1m signal index back to its structure bar for diagnostics.

    The signal is written at ``e-1`` so the frozen simulator's "enter at the
    next bar's open" lands exactly on ``e``. The stop arrays are written at the
    same index for the same reason -- the simulator reads the stop at the
    signal bar.

    BOTH stop arrays are written at EVERY signal index, long or short. The
    frozen simulator computes a short's stop as ``max(st1[i], leg_hi[i])``, and
    ``max`` propagates NaN in numba exactly as it does in Python -- so leaving
    the long-stop slot empty at a short signal makes the stop NaN and the
    simulator's ``isfinite(stop)`` guard silently discards the trade. That
    failure mode is invisible in the output: it shows up only as shorts never
    appearing. A signal therefore requires BOTH a confirmed swing high and a
    confirmed swing low to exist, which the warmup already guarantees.
    """
    e = entry_index(S["time"], S["tf"], exec_tf_min, t1)
    lo1 = np.zeros(n1, bool); sh1 = np.zeros(n1, bool)
    sl1 = np.full(n1, np.nan); ss1 = np.full(n1, np.nan)
    src = np.full(n1, -1, "int64")

    ok = (e > 0) & (e < n1) & np.isfinite(S["last_l_px"]) & np.isfinite(S["last_h_px"])
    fire_l = ok & np.asarray(sig_struct_long, bool)
    fire_s = ok & np.asarray(sig_struct_short, bool)
    lo1[e[fire_l] - 1] = True
    sh1[e[fire_s] - 1] = True

    k = np.flatnonzero(fire_l | fire_s)
    i = e[k] - 1
    sl1[i] = S["last_l_px"][k]
    ss1[i] = S["last_h_px"][k]
    src[i] = k
    return lo1, sh1, sl1, ss1, src


# ------------------------------------------------------------------ run


def run_variant(sym_data: dict, S: dict, family: str, trigger: str,
                exec_tf_min: int, *, start: int, end: int,
                override_long=None, override_short=None,
                label: str = "") -> WResult:
    """One candidate on one symbol, through the frozen H-WPR-1 simulator.

    ``override_*`` replaces the family signal on the STRUCTURE grid and exists
    only for the null baselines (§12), which must share this exact execution,
    stop, sizing and cost path.
    """
    df = sym_data["df"]; t1 = sym_data["t1"]; n1 = len(t1)
    if override_long is None:
        lo_s, sh_s = family_signals(S, family, trigger)
    else:
        lo_s, sh_s = override_long, override_short

    lo1, sh1, stop_l, stop_s, src = project(lo_s, sh_s, S, exec_tf_min, t1, n1)
    # kept for attach_diagnostics: maps a 1m signal index back to its structure bar
    sym_data["_src"] = src

    beyond = (t1 > end) | (t1 < start)
    lo1 = lo1 & ~beyond
    sh1 = sh1 & ~beyond

    # The frozen simulator computes  long: min(st1, leg_lo)  short: max(st1, leg_hi).
    # Feeding it st1 = leg_lo = swing-low and leg_hi = swing-high yields exactly
    # the declared structural stop for both sides, because a swing low is always
    # below a swing high. The simulator itself is untouched.
    st1 = stop_l
    leg_lo = stop_l
    leg_hi = stop_s

    costs: SymbolCosts = sym_data["costs"]
    arr, sk_stop, sk_size = _simulate(
        lo1, sh1, sym_data["o"], sym_data["h"], sym_data["l"], sym_data["c"],
        sym_data["mh"], sym_data["ml"], st1, leg_lo, leg_hi,
        sym_data["tradable"], float(TARGET_R),
        costs.effective_taker, costs.slippage_rate, costs.tick_size,
        costs.contract_value, 3.0, RESEARCH_RISK, RESEARCH_EQUITY,
        False, float(MAX_STOP_PCT))

    res = WResult(symbol=costs.symbol, arm=label or f"{family}|{trigger}",
                  params=dict(family=family, trigger=trigger, n=S["n"],
                              struct_tf=S["tf"], exec_tf=exec_tf_min),
                  signals=int(lo1.sum() + sh1.sum()),
                  skipped_stop=int(sk_stop), skipped_size=int(sk_size))
    if arr.shape[0] == 0:
        res.src = src
        return res

    stamps = set(int(s) for s in funding_timestamps(
        int(t1[0]), int(t1[-1]), costs.funding_interval_seconds))
    frate = {}
    f = sym_data["funding"]
    if f is not None and not f.empty:
        ft = f["time"].to_numpy("int64"); fv = f["close"].to_numpy("float64")
        for s in stamps:
            k = np.searchsorted(ft, s, side="right") - 1
            if k >= 0 and np.isfinite(fv[k]):
                frate[s] = float(fv[k])
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
            symbol=costs.symbol, side=side, arm=label or f"{family}|{trigger}",
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
    res.src = src
    return res


# ------------------------------------------------------------ diagnostics


@njit(cache=True)
def _excursion(entry_idx, entry_px, stop_px, side, mh, ml, hi, lo, cap):
    """Did price reach +0.5R / +1R / +2R before the stop? (§16)

    Replays each trade forward from its ENTRY bar only. A bar that touches both
    a level and the stop resolves to the STOP, matching the frozen simulator's
    same-bar rule. Purely a measurement -- it drives no entry.
    """
    m = entry_idx.size
    n = mh.size
    out = np.zeros((m, 4), np.float64)   # p05, p1, p2, mfe_r
    for a in range(m):
        j = entry_idx[a]; e = entry_px[a]; s = stop_px[a]; d = side[a]
        r = (e - s) if d > 0 else (s - e)
        if r <= 0:
            continue
        l05 = e + d * 0.5 * r
        l10 = e + d * 1.0 * r
        l20 = e + d * 2.0 * r
        best = 0.0
        t = j
        stop_i = n
        while t < n and (t - j) <= cap:
            hit_stop = (ml[t] <= s) if d > 0 else (mh[t] >= s)
            ex = (hi[t] - e) / r if d > 0 else (e - lo[t]) / r
            if ex > best:
                best = ex
            if hit_stop:
                stop_i = t
                break
            if best >= 2.0:
                break
            t += 1
        # levels reached strictly before the stop bar; ties resolve to the stop
        b = 0.0
        t = j
        while t < n and (t - j) <= cap and t < stop_i:
            ex = (hi[t] - e) / r if d > 0 else (e - lo[t]) / r
            if ex > b:
                b = ex
            if b >= 2.0:
                break
            t += 1
        out[a, 0] = 1.0 if b >= 0.5 else 0.0
        out[a, 1] = 1.0 if b >= 1.0 else 0.0
        out[a, 2] = 1.0 if b >= 2.0 else 0.0
        out[a, 3] = best
    return out


def attach_diagnostics(frame: pd.DataFrame, S: dict, sym_data: dict,
                       exec_tf_min: int) -> pd.DataFrame:
    """Structure provenance + confirmation delay + §15/§16 measurements."""
    if frame.empty:
        return frame
    t1 = sym_data["t1"]
    out = frame.copy()
    sig_i = np.searchsorted(t1, out.signal_time.to_numpy("int64"))
    k = sym_data["_src"][sig_i]                     # structure bar index
    tf_s = S["tf"] * 60
    n_str = S["n"]

    st = S["time"]
    hi_i = S["last_h_i"][k]; lo_i = S["last_l_i"][k]
    ok_h = hi_i >= 0; ok_l = lo_i >= 0

    def ts(idx, ok):
        v = np.full(len(idx), -1, "int64")
        v[ok] = st[idx[ok]]
        return v

    out["struct_bar_time"] = st[k]
    out["swing_high_time"] = ts(hi_i, ok_h)
    out["swing_low_time"] = ts(lo_i, ok_l)
    out["swing_high_conf_time"] = np.where(ok_h, out.swing_high_time + n_str * tf_s + tf_s, -1)
    out["swing_low_conf_time"] = np.where(ok_l, out.swing_low_time + n_str * tf_s + tf_s, -1)
    # the later of the two confirmations is what gates the signal
    out["struct_conf_time"] = np.maximum(out.swing_high_conf_time, out.swing_low_conf_time)
    out["conf_delay_bars"] = n_str
    out["conf_delay_min"] = n_str * S["tf"]
    # lag from the swing that formed to the order being placed
    ref = np.where(out.side > 0, out.swing_low_time, out.swing_high_time)
    out["signal_lag_min"] = (out.entry_time - ref) / 60.0
    out["signal_lag_bars"] = out.signal_lag_min / S["tf"]

    atr = S["atr"][k]
    out["atr"] = atr
    hp = S["last_h_px"][k]; php = S["prev_h_px"][k]
    lp = S["last_l_px"][k]; plp = S["prev_l_px"][k]
    out["swing_high_px"] = hp; out["prev_swing_high_px"] = php
    out["swing_low_px"] = lp; out["prev_swing_low_px"] = plp
    with np.errstate(invalid="ignore"):
        out["hh_displacement_atr"] = (hp - php) / atr
        out["hl_displacement_atr"] = (lp - plp) / atr
        # break distance: how far the structure close cleared the broken level
        lvl = np.where(out.side > 0, hp, lp)
        out["break_dist_atr"] = np.where(
            out.side > 0, (S["close"][k] - lvl) / atr, (lvl - S["close"][k]) / atr)
        out["disp_atr"] = np.where(out.side > 0,
                                   out.hh_displacement_atr.abs(),
                                   out.hl_displacement_atr.abs())
        out["bars_between_swings"] = np.abs(hi_i - lo_i).astype("float64")
        out.loc[~(ok_h & ok_l), "bars_between_swings"] = np.nan

    out["disp_bucket"] = pd.cut(out.disp_atr, ATR_BUCKETS,
                                labels=list(ATR_BUCKET_LABELS), right=False)
    out["break_bucket"] = pd.cut(out.break_dist_atr.clip(lower=0), ATR_BUCKETS,
                                 labels=list(ATR_BUCKET_LABELS), right=False)

    ent = np.searchsorted(t1, out.entry_time.to_numpy("int64"))
    ex = _excursion(ent.astype("int64"),
                    out.entry_price.to_numpy("float64"),
                    out.stop_price.to_numpy("float64"),
                    out.side.to_numpy("int64"),
                    sym_data["mh"], sym_data["ml"],
                    sym_data["h"], sym_data["l"], 20000)
    out["hit_05r"] = ex[:, 0] > 0
    out["hit_1r"] = ex[:, 1] > 0
    out["hit_2r"] = ex[:, 2] > 0
    out["mfe_r"] = ex[:, 3]
    out["struct_tf"] = S["tf"]; out["exec_tf"] = exec_tf_min
    out["swing_n"] = n_str
    return out
