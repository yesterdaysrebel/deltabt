"""H-Compress-1: volatility compression -> confirmed expansion -> passive retest.

Pre-registration: see `hcompress_prereg.md`. Every parameter below is frozen
there; nothing is tuned in this file.

Mechanism under test: markets alternate between compression and expansion.
The strategy does not attempt to predict direction during compression -- it
waits for the expansion to declare a direction, then tries to enter passively
on a retest of the broken boundary rather than chasing.

Structure:
    15m  compression detection (ATR percentile, duration, range quality)
     5m  expansion confirmation (close beyond boundary + body + volume)
     5m  passive retest entry, management, exits

NO LOOK-AHEAD, enforced structurally:
  * the ATR percentile uses a trailing window ending at t-1;
  * compression zones are built only from CLOSED 15m bars, and a 5m bar may
    only see the 15m bar that closed at or before that 5m bar opened;
  * a resting order can only fill on bars strictly after the expansion bar;
  * on a passive fill bar the stop may trigger but the target may not, because
    a maker long fills at the bar's low and that bar's high generally preceded
    the fill (this error inflated an earlier experiment by +0.5R).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from numba import njit

from deltabt import indicators as ind
from deltabt.costs import SymbolCosts, funding_timestamps
from deltabt.strategy import resample_ohlcv

# ---- frozen parameters -----------------------------------------------------
PCT_LOOKBACK_15M = 960     # 10 days of 15m bars for the trailing percentile
RANGE_QUALITY_MAX = 1.5    # compression_range / ATR(15m)
ORDER_LIFETIME_5M = 3      # bars a resting retest order stays live
MAX_HOLD_5M = 24           # time exit
MAX_STOP_PCT = 0.02        # skip if the structural stop is wider than 2%
RISK_PCT = 0.005           # 0.5% of equity
MAX_LEVERAGE = 10.0
START_EQUITY = 10_000.0

GRID = dict(
    percentile=(0.10, 0.20, 0.30),
    min_duration=(4, 6),
    volume_mult=(1.0, 1.5, 2.0),
    body_mult=(0.25, 0.50, 0.75),
    target_r=(2.0, 3.0),
)
PRIMARY = dict(percentile=0.20, min_duration=4, volume_mult=1.5,
               body_mult=0.50, target_r=2.0)


@dataclass
class CTrade:
    symbol: str
    side: int
    arm: str
    zone_time: int
    expansion_time: int
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    r_price: float
    stop_pct: float
    zone_high: float
    zone_low: float
    zone_bars: int
    wait_bars: int
    bars_held: int
    exit_reason: str
    r_gross: float
    cost_r: float
    funding_r: float
    r_net: float
    contracts: int
    notional: float
    leverage: float
    pnl_usd: float
    cluster: str


@dataclass
class CResult:
    symbol: str
    arm: str
    params: dict
    trades: list[CTrade] = field(default_factory=list)
    compression_events: int = 0
    expansion_events: int = 0
    orders: int = 0
    fills: int = 0
    skipped_wide_stop: int = 0

    def to_frame(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame(columns=[f for f in CTrade.__dataclass_fields__])
        return pd.DataFrame([t.__dict__ for t in self.trades])


# ---- compression detection (15m) -------------------------------------------


@njit(cache=True)
def _rolling_quantile_causal(x: np.ndarray, window: int, q: float) -> np.ndarray:
    """Quantile of the trailing `window` values ENDING AT t-1 (excludes t).

    Written explicitly rather than via pandas so the exclusion of the current
    bar is visible and cannot be lost to an off-by-one.
    """
    n = x.size
    out = np.full(n, np.nan)
    buf = np.empty(window)
    for t in range(n):
        lo = t - window
        if lo < 0:
            continue
        m = 0
        for i in range(lo, t):          # strictly before t
            v = x[i]
            if not np.isnan(v):
                buf[m] = v
                m += 1
        if m < window // 2:
            continue
        s = np.sort(buf[:m])
        pos = q * (m - 1)
        lo_i = int(np.floor(pos))
        hi_i = int(np.ceil(pos))
        frac = pos - lo_i
        out[t] = s[lo_i] * (1.0 - frac) + s[hi_i] * frac
    return out


@njit(cache=True)
def _compression_zones(
    high: np.ndarray, low: np.ndarray, atr: np.ndarray,
    compressed: np.ndarray, min_dur: int, range_max: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per-15m-bar compression zone, using only bars up to and including t."""
    n = high.size
    zh = np.full(n, np.nan); zl = np.full(n, np.nan)
    nb = np.zeros(n, dtype=np.int64); ok = np.zeros(n, dtype=np.bool_)
    run = 0
    for t in range(n):
        if compressed[t]:
            run += 1
        else:
            run = 0
        nb[t] = run
        if run >= min_dur and np.isfinite(atr[t]) and atr[t] > 0:
            h = high[t - run + 1]
            l = low[t - run + 1]
            for i in range(t - run + 2, t + 1):
                if high[i] > h:
                    h = high[i]
                if low[i] < l:
                    l = low[i]
            zh[t] = h; zl[t] = l
            ok[t] = (h - l) / atr[t] <= range_max
    return zh, zl, nb, ok


def build_frames(ltp_1m: pd.DataFrame, mark_1m: pd.DataFrame, start: int):
    b5 = resample_ohlcv(ltp_1m[ltp_1m.time >= start], 5).reset_index(drop=True)
    b15 = resample_ohlcv(ltp_1m[ltp_1m.time >= start], 15).reset_index(drop=True)
    if mark_1m is None or mark_1m.empty:
        m5 = b5.copy()
    else:
        m5 = resample_ohlcv(mark_1m[mark_1m.time >= start], 5)
        m5 = m5.set_index("time").reindex(b5["time"].to_numpy()).reset_index()
        for c in ("high", "low", "close", "open"):
            m5[c] = m5[c].fillna(b5[c])
    return b5, b15, m5


def detect(b5: pd.DataFrame, b15: pd.DataFrame, *, percentile: float,
           min_duration: int, volume_mult: float, body_mult: float):
    """Vectorised compression + expansion detection. Returns per-5m-bar arrays."""
    h15 = b15["high"].to_numpy("float64"); l15 = b15["low"].to_numpy("float64")
    c15 = b15["close"].to_numpy("float64"); t15 = b15["time"].to_numpy("int64")
    atr15 = ind.atr(h15, l15, c15, 14)
    with np.errstate(invalid="ignore", divide="ignore"):
        atr_pct = np.where(c15 > 0, atr15 / c15, np.nan)
    thr = _rolling_quantile_causal(atr_pct, PCT_LOOKBACK_15M, percentile)
    compressed = np.isfinite(atr_pct) & np.isfinite(thr) & (atr_pct < thr)
    zh, zl, nb, ok = _compression_zones(h15, l15, atr15, compressed,
                                        int(min_duration), RANGE_QUALITY_MAX)

    # A 5m bar may only use the 15m bar that CLOSED at or before it opened.
    t5 = b5["time"].to_numpy("int64")
    idx15 = np.searchsorted(t15 + 900, t5, side="right") - 1

    h5 = b5["high"].to_numpy("float64"); l5 = b5["low"].to_numpy("float64")
    c5 = b5["close"].to_numpy("float64"); o5 = b5["open"].to_numpy("float64")
    v5 = b5["volume"].to_numpy("float64")
    atr5 = ind.atr(h5, l5, c5, 14)
    avgv = pd.Series(v5).rolling(20, min_periods=20).mean().shift(1).to_numpy()

    valid = idx15 >= 0
    zone_ok = np.zeros(len(b5), dtype=bool)
    zhi = np.full(len(b5), np.nan); zlo = np.full(len(b5), np.nan)
    zbars = np.zeros(len(b5), dtype="int64"); ztime = np.zeros(len(b5), dtype="int64")
    zone_ok[valid] = ok[idx15[valid]]
    zhi[valid] = zh[idx15[valid]]; zlo[valid] = zl[idx15[valid]]
    zbars[valid] = nb[idx15[valid]]; ztime[valid] = t15[idx15[valid]]

    body = np.abs(c5 - o5)
    with np.errstate(invalid="ignore"):
        body_ok = np.isfinite(atr5) & (body >= body_mult * atr5)
        vol_ok = np.isfinite(avgv) & (v5 >= volume_mult * avgv)
        up = zone_ok & (c5 > zhi) & body_ok & vol_ok
        dn = zone_ok & (c5 < zlo) & body_ok & vol_ok
    return dict(up=up, dn=dn, zhi=zhi, zlo=zlo, zbars=zbars, ztime=ztime,
                idx15=idx15, atr5=atr5,
                n_compression=int(np.sum(ok)))


def run(
    ltp_1m: pd.DataFrame, mark_1m: pd.DataFrame, funding: pd.DataFrame,
    costs: SymbolCosts, *, start: int, end: int | None = None,
    arm: str = "A", percentile: float = 0.20, min_duration: int = 4,
    volume_mult: float = 1.5, body_mult: float = 0.50, target_r: float = 2.0,
    fill_model: str = "touch", cost_multiplier: float = 1.0,
    slippage_multiplier: float = 1.0,
) -> CResult:
    """Arm A = passive retest (primary). Arm B = taker breakout (diagnostic)."""
    if arm not in ("A", "B"):
        raise ValueError("arm must be 'A' (passive retest) or 'B' (taker breakout)")

    b5, b15, m5 = build_frames(ltp_1m, mark_1m, start)
    params = dict(percentile=percentile, min_duration=min_duration,
                  volume_mult=volume_mult, body_mult=body_mult, target_r=target_r)
    res = CResult(symbol=costs.symbol, arm=arm, params=params)
    if len(b5) < 500 or len(b15) < PCT_LOOKBACK_15M + 50:
        return res

    d = detect(b5, b15, percentile=percentile, min_duration=min_duration,
               volume_mult=volume_mult, body_mult=body_mult)
    res.compression_events = d["n_compression"]

    t5 = b5["time"].to_numpy("int64")
    o5 = b5["open"].to_numpy("float64"); h5 = b5["high"].to_numpy("float64")
    l5 = b5["low"].to_numpy("float64"); c5 = b5["close"].to_numpy("float64")
    mh = m5["high"].to_numpy("float64"); ml = m5["low"].to_numpy("float64")
    n = len(b5)
    hi_lim = n if end is None else int(np.searchsorted(t5, end, side="right"))

    taker = costs.effective_taker * cost_multiplier + costs.slippage_rate * slippage_multiplier
    maker = costs.effective_maker * cost_multiplier
    conservative = fill_model == "through"

    stamps = set(int(s) for s in funding_timestamps(int(t5[0]), int(t5[-1]),
                                                    costs.funding_interval_seconds))
    frate = {}
    if funding is not None and not funding.empty:
        ft = funding["time"].to_numpy("int64"); fv = funding["close"].to_numpy("float64")
        for s in stamps:
            i = np.searchsorted(ft, s, side="right") - 1
            if i >= 0 and np.isfinite(fv[i]):
                frate[s] = float(fv[i])

    equity = START_EQUITY
    busy_until = -1
    consumed_zone = -1
    events = np.flatnonzero((d["up"] | d["dn"]) & (np.arange(n) < hi_lim - MAX_HOLD_5M - 5))

    for j in events:
        if j <= busy_until:
            continue
        zt = int(d["ztime"][j])
        if zt == consumed_zone:
            continue          # one entry attempt per compression zone
        side = 1 if d["up"][j] else -1
        res.expansion_events += 1
        consumed_zone = zt

        zone_hi = float(d["zhi"][j]); zone_lo = float(d["zlo"][j])
        if not (np.isfinite(zone_hi) and np.isfinite(zone_lo)) or zone_hi <= zone_lo:
            continue

        if arm == "A":
            limit = zone_hi if side > 0 else zone_lo
            res.orders += 1
            filled = -1
            for m in range(j + 1, min(j + 1 + ORDER_LIFETIME_5M, n)):
                if side > 0:
                    hit = (l5[m] < limit - costs.tick_size) if conservative else (l5[m] <= limit)
                else:
                    hit = (h5[m] > limit + costs.tick_size) if conservative else (h5[m] >= limit)
                if hit:
                    filled = m
                    break
            if filled < 0:
                continue      # order expires; never converted to taker
            res.fills += 1
            entry = limit
            entry_rate = maker
        else:
            if j + 1 >= n:
                continue
            res.orders += 1; res.fills += 1
            filled = j + 1
            entry = o5[filled]
            entry_rate = taker

        stop = zone_lo if side > 0 else zone_hi
        r_price = abs(entry - stop)
        if r_price <= 0:
            continue
        stop_pct = r_price / entry
        if stop_pct > MAX_STOP_PCT:
            res.skipped_wide_stop += 1
            continue
        target = entry + side * target_r * r_price

        # --- sizing -------------------------------------------------------
        units = (equity * RISK_PCT) / r_price
        units = min(units, (equity * MAX_LEVERAGE) / entry)
        contracts = costs.contracts_for(units)
        if contracts <= 0:
            continue

        exit_price = np.nan; reason = ""; exit_bar = filled
        for m in range(filled, min(filled + MAX_HOLD_5M, n)):
            # Passive fill bar: the stop may trigger, the target may not.
            entry_bar_passive = (m == filled) and (arm == "A")
            hit_stop = (ml[m] <= stop) if side > 0 else (mh[m] >= stop)
            hit_tgt = (h5[m] >= target) if side > 0 else (l5[m] <= target)
            if entry_bar_passive:
                hit_tgt = False
            if hit_stop and hit_tgt:
                exit_price, reason, exit_bar = stop, "stop", m   # conservative
                break
            if hit_stop:
                exit_price, reason, exit_bar = stop, "stop", m
                break
            if hit_tgt:
                exit_price, reason, exit_bar = target, "target", m
                break
            exit_bar = m
        if not reason:
            exit_price, reason = c5[exit_bar], "time"

        exit_rate = maker if (arm == "A" and reason == "target") else taker
        r_gross = side * (exit_price - entry) / r_price
        cost_r = (entry * entry_rate + exit_price * exit_rate) / r_price
        funding_r = 0.0
        for s in stamps:
            if t5[filled] <= s <= t5[exit_bar]:
                funding_r += side * (frate.get(s, 0.0) / 100.0) * entry / r_price
        r_net = r_gross - cost_r - funding_r

        notional = costs.notional(contracts, entry)
        pnl = (side * (exit_price - entry) * contracts * costs.contract_value
               - notional * entry_rate - costs.notional(contracts, exit_price) * exit_rate)
        equity += pnl

        res.trades.append(CTrade(
            symbol=costs.symbol, side=side, arm=arm, zone_time=zt,
            expansion_time=int(t5[j]), entry_time=int(t5[filled]),
            exit_time=int(t5[exit_bar]), entry_price=float(entry),
            exit_price=float(exit_price), stop_price=float(stop),
            target_price=float(target), r_price=float(r_price),
            stop_pct=float(stop_pct), zone_high=zone_hi, zone_low=zone_lo,
            zone_bars=int(d["zbars"][j]), wait_bars=filled - j,
            bars_held=exit_bar - filled, exit_reason=reason,
            r_gross=float(r_gross), cost_r=float(cost_r), funding_r=float(funding_r),
            r_net=float(r_net), contracts=int(contracts), notional=float(notional),
            leverage=float(notional / max(equity, 1e-9)), pnl_usd=float(pnl),
            cluster=pd.Timestamp(int(t5[filled]), unit="s").strftime("%Y-%m-%d"),
        ))
        busy_until = exit_bar

    return res
