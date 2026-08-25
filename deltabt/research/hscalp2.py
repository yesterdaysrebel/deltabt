"""H-Scalp-2: extreme displacement -> retracement -> continuation.

PRE-REGISTERED HYPOTHESIS (fixed before any result was seen)
------------------------------------------------------------
An extreme 15-minute directional move carries information about persistent
directional order flow. The move is therefore NOT faded. Price temporarily
retraces, which offers a passive entry, and then continues in the direction of
the original displacement.

This is the opposite mechanism to H-Scalp-1 (permanently classified NO EDGE).
Only the direction and the entry mechanism change; the event definition is
identical so the two are directly comparable.

SIGNAL (identical to H-Scalp-1)
    r_t   = log(close_t / close_{t-1})              on 15m bars
    sig_t = stdev(r) over the 96 bars ENDING AT t-1    (24h, strictly causal)
    z_t   = r_t / sig_t
    z_t >= +k  ->  bias LONG      (continuation of an up move)
    z_t <= -k  ->  bias SHORT     (continuation of a down move)
    k = 3.0 primary; 2.5 and 3.5 as pre-registered robustness. No other values.

RETEST ENTRY
    M = close_t - open_t (signed event move)
    entry_level = close_t - retest_frac * M      (a pullback toward the origin)
    A passive limit rests at entry_level for at most ENTRY_WINDOW_BARS bars.
    retest_frac = 0.33 primary; 0.25 and 0.50 as robustness. No other values.

INVALIDATION (defined before running, no discretion)
    The continuation thesis is dead if the entire displacement is given back,
    i.e. price trades back through open_t (100% retracement) before the limit
    fills. The order is cancelled and no trade is taken.
    If a single bar spans both the entry level and the invalidation level, the
    OHLC cannot order the two events, so the trade is SKIPPED (conservative).
    The order is also cancelled if ENTRY_WINDOW_BARS elapse unfilled.

RISK AND EXIT
    R = entry_level - open_t  (for a long) = (1 - retest_frac) * |M|
    So the stop IS the structural invalidation level -- R is a property of the
    event, not a tuned parameter.
    stop   = entry -/+ 1.0R      (equals open_t)
    target = entry +/- 1.0R      (a genuine continuation beyond the event close)
    time   = 8 bars from entry
    Same-bar stop-and-target resolves to the STOP (conservative).

    This deliberately avoids H-Scalp-1's payoff structure, where a target of at
    most 1/3 of the stop required a ~75% win rate. Here 1:1 needs >50% plus
    costs.

NO LOOK-AHEAD
    entry_level, stop, target and the invalidation level are all functions of
    open_t and close_t only, both known at the close of bar t. Fill checking
    scans bars t+1 onward and never inspects a bar before deciding to rest an
    order in it.

MAKER FILL MODEL
    Delta serves no order-book history, so queue position cannot be modelled.
    Both bracketing assumptions are reported:
      OPTIMISTIC   filled if the bar merely TOUCHES the limit
      CONSERVATIVE filled only if the bar trades THROUGH it by one tick
    A stop is never a maker order; stop and time exits always pay taker.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from deltabt.costs import SymbolCosts, funding_timestamps
from deltabt.research.hscalp1 import VOL_LOOKBACK, build_bars, compute_signal

MAX_HOLD_BARS = 8          # from entry, pre-registered
ENTRY_WINDOW_BARS = 8      # how long the retest limit rests, pre-registered
K_VALUES = (2.5, 3.0, 3.5)         # pre-registered; DO NOT EXPAND
RETEST_FRACS = (0.25, 0.33, 0.50)  # pre-registered; DO NOT EXPAND
PRIMARY = dict(k=3.0, retest=0.33, exec_model="maker/maker", fill_model="conservative")

EXEC_MODELS = ("maker/maker", "maker/taker", "taker/taker", "maker/maker+delay")
FILL_MODELS = ("optimistic", "conservative")


@dataclass
class Trade2:
    symbol: str
    side: int
    signal_time: int
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    r_price: float
    z: float
    event_move: float
    wait_bars: int
    bars_held: int
    exit_reason: str
    r_gross: float
    cost_r: float
    funding_r: float
    r_net: float
    mfe_r: float
    mae_r: float
    ambiguous: bool
    cluster: str


@dataclass
class Result2:
    symbol: str
    k: float
    retest: float
    exec_model: str
    fill_model: str
    trades: list[Trade2] = field(default_factory=list)
    signals: int = 0
    invalidated: int = 0     # price gave back the whole move before filling
    expired: int = 0         # retest never reached inside the window
    skipped_ambiguous: int = 0

    def to_frame(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame(columns=[f for f in Trade2.__dataclass_fields__])
        return pd.DataFrame([t.__dict__ for t in self.trades])


def _touch(side: int, level: float, bar_low: float, bar_high: float,
           tick: float, conservative: bool) -> bool:
    """Would a resting limit at `level` fill inside this bar?

    A continuation LONG rests a BUY below the market, so it needs price to come
    down to it; a continuation SHORT rests a SELL above the market.
    """
    if side > 0:
        return (bar_low < level - tick) if conservative else (bar_low <= level)
    return (bar_high > level + tick) if conservative else (bar_high >= level)


def run(
    ltp_1m: pd.DataFrame,
    mark_1m: pd.DataFrame,
    funding: pd.DataFrame,
    costs: SymbolCosts,
    *,
    k: float,
    retest: float,
    exec_model: str,
    fill_model: str,
    start: int,
    cost_multiplier: float = 1.0,
    slippage_multiplier: float = 1.0,
    bar_minutes: int = 15,
) -> Result2:
    if exec_model not in EXEC_MODELS:
        raise ValueError(f"exec_model must be one of {EXEC_MODELS}")
    if fill_model not in FILL_MODELS:
        raise ValueError(f"fill_model must be one of {FILL_MODELS}")
    if not 0.0 < retest < 1.0:
        raise ValueError("retest fraction must lie strictly inside (0, 1)")

    # bar_minutes DEFAULTS TO 15, which is what the registry's H-Scalp-2
    # record was produced under. H-Scalp-3 passes a longer horizon to ask
    # whether the same mechanism survives where R is wide enough to pay for
    # itself; VOL_LOOKBACK stays at 96 BARS rather than 24 hours so the z-score
    # estimator keeps identical sampling properties across timeframes.
    bars, mark = build_bars(ltp_1m, mark_1m, start, minutes=bar_minutes)
    n = len(bars)
    res = Result2(symbol=costs.symbol, k=k, retest=retest,
                  exec_model=exec_model, fill_model=fill_model)
    if n < VOL_LOOKBACK + ENTRY_WINDOW_BARS + MAX_HOLD_BARS + 5:
        return res

    z, _ = compute_signal(bars)
    t_ = bars["time"].to_numpy("int64")
    o = bars["open"].to_numpy("float64"); h = bars["high"].to_numpy("float64")
    lo = bars["low"].to_numpy("float64"); c = bars["close"].to_numpy("float64")
    mh = mark["high"].to_numpy("float64"); ml = mark["low"].to_numpy("float64")

    maker_in = exec_model.startswith("maker")
    maker_out = exec_model in ("maker/maker", "maker/maker+delay")
    delayed = exec_model.endswith("+delay")
    conservative = fill_model == "conservative"

    taker_rate = (costs.effective_taker * cost_multiplier
                  + costs.slippage_rate * slippage_multiplier)
    maker_rate = costs.effective_maker * cost_multiplier

    stamps_arr = funding_timestamps(int(t_[0]), int(t_[-1]),
                                    costs.funding_interval_seconds)
    frate = {}
    if funding is not None and not funding.empty:
        ft = funding["time"].to_numpy("int64"); fv = funding["close"].to_numpy("float64")
        for s in stamps_arr:
            i = np.searchsorted(ft, s, side="right") - 1
            if i >= 0 and np.isfinite(fv[i]):
                frate[int(s)] = float(fv[i])
    stamps = set(int(s) for s in stamps_arr)

    last = n - MAX_HOLD_BARS - ENTRY_WINDOW_BARS - 2
    busy_until = -1
    for t in range(VOL_LOOKBACK + 1, last):
        if not np.isfinite(z[t]) or t <= busy_until:
            continue
        # Continuation: follow the sign of the displacement.
        side = 1 if z[t] >= k else (-1 if z[t] <= -k else 0)
        if side == 0:
            continue
        res.signals += 1

        move = c[t] - o[t]
        # The event's own direction must agree with the return sign; a bar that
        # closed against its own move is not a clean displacement.
        if side * move <= 0:
            continue

        entry_level = c[t] - retest * move
        invalidation = o[t]                      # 100% retracement
        r_price = abs(entry_level - invalidation)
        if r_price <= 0:
            continue

        first_scan = t + 2 if delayed else t + 1
        filled_at = -1
        for j in range(first_scan, min(first_scan + ENTRY_WINDOW_BARS, n)):
            hit_entry = _touch(side, entry_level, lo[j], h[j], costs.tick_size,
                               conservative)
            hit_inval = (lo[j] <= invalidation) if side > 0 else (h[j] >= invalidation)
            if hit_entry and hit_inval:
                # OHLC cannot order these two; skip rather than guess.
                res.skipped_ambiguous += 1
                break
            if hit_inval:
                res.invalidated += 1
                break
            if hit_entry:
                filled_at = j
                break
        else:
            # loop ran to completion: the retest was never reached in the window
            res.expired += 1
        if filled_at < 0:
            continue

        entry = entry_level if maker_in else o[min(filled_at + 1, n - 1)]
        entry_rate = maker_rate if maker_in else taker_rate
        if not maker_in:
            # A taker entry cannot be at the level; re-derive risk from it so
            # the 1:1 structure is preserved relative to the invalidation.
            r_price = abs(entry - invalidation)
            if r_price <= 0:
                continue

        stop = entry - side * r_price
        target = entry + side * r_price

        exit_price = np.nan; exit_reason = ""; ambiguous = False
        exit_bar = filled_at
        mfe = mae = 0.0
        for j in range(filled_at, min(filled_at + MAX_HOLD_BARS, n)):
            # A maker entry fills at the bar's adverse extreme (price had to
            # come to our resting limit), so that bar's FAVOURABLE extreme
            # almost always occurred BEFORE the fill. Counting it as a target
            # hit is look-ahead: measured at retest=0.50 it produced 356
            # same-bar target exits against 1 same-bar stop. On the fill bar we
            # therefore allow only the adverse outcome.
            entry_bar_maker = (j == filled_at) and maker_in

            fav = (h[j] - entry) if side > 0 else (entry - lo[j])
            adv = (entry - lo[j]) if side > 0 else (h[j] - entry)
            if not entry_bar_maker:
                mfe = max(mfe, fav / r_price)
            mae = max(mae, adv / r_price)

            hit_stop = (ml[j] <= stop) if side > 0 else (mh[j] >= stop)
            hit_tgt = (h[j] >= target) if side > 0 else (lo[j] <= target)
            if entry_bar_maker:
                hit_tgt = False
            if hit_stop and hit_tgt:
                ambiguous = True
                exit_price, exit_reason, exit_bar = stop, "stop", j
                break
            if hit_stop:
                exit_price, exit_reason, exit_bar = stop, "stop", j
                break
            if hit_tgt:
                exit_price, exit_reason, exit_bar = target, "target", j
                break
            exit_bar = j
        if not exit_reason:
            exit_price, exit_reason = c[exit_bar], "time"

        exit_rate = maker_rate if (maker_out and exit_reason == "target") else taker_rate
        r_gross = side * (exit_price - entry) / r_price
        cost_r = (entry * entry_rate + exit_price * exit_rate) / r_price
        funding_r = 0.0
        for s in stamps:
            if t_[filled_at] <= s <= t_[exit_bar]:
                funding_r += side * (frate.get(s, 0.0) / 100.0) * entry / r_price

        res.trades.append(Trade2(
            symbol=costs.symbol, side=side, signal_time=int(t_[t]),
            entry_time=int(t_[filled_at]), exit_time=int(t_[exit_bar]),
            entry_price=float(entry), exit_price=float(exit_price),
            stop_price=float(stop), target_price=float(target),
            r_price=float(r_price), z=float(z[t]), event_move=float(move),
            wait_bars=filled_at - t, bars_held=exit_bar - filled_at,
            exit_reason=exit_reason, r_gross=float(r_gross),
            cost_r=float(cost_r), funding_r=float(funding_r),
            r_net=float(r_gross - cost_r - funding_r),
            mfe_r=float(mfe), mae_r=float(mae), ambiguous=ambiguous,
            cluster=pd.Timestamp(int(t_[filled_at]), unit="s").strftime("%Y-%m-%d"),
        ))
        busy_until = exit_bar

    return res
