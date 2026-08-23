"""H-Scalp-1: 15-minute extreme-return mean reversion.

PRE-REGISTERED HYPOTHESIS (fixed before any result was seen)
------------------------------------------------------------
After an extreme 15-minute return, price has a measurable tendency to partially
mean-revert, because the move was driven by temporary order-flow imbalance --
forced or impatient flow -- rather than by information.

SIGNAL
    r_t   = log(close_t / close_{t-1})            on 15m bars
    sig_t = stdev(r) over the 96 bars ENDING AT t-1   (24h, strictly causal)
    z_t   = r_t / sig_t
    long  when z_t <= -k ;  short when z_t >= +k ;  k in {2.5, 3.0, 3.5} only

EXIT
    target : 50% retracement of the event move (open_t -> close_t)
    stop   : 1.5 x event range (high_t - low_t), triggered on MARK price
    time   : 8 bars (2 hours)
    same-bar stop-and-target resolves to the STOP (conservative)

R (initial risk in price terms) = 1.5 x event range.

Note on the reward/risk this specification implies: the target is 0.5x the
event *move* while the stop is 1.5x the event *range*, and range >= |move|.
So the target is at most 1/3 of the stop distance, and break-even therefore
requires a win rate above ~75%. That is a property of the pre-registered rules,
not a modelling choice, and it is reported rather than tuned away.

MAKER FILL MODEL -- read this before trusting any maker result
---------------------------------------------------------------
Delta serves no order-book or trade history, so queue position CANNOT be
modelled. This is a hard limitation of the data, not an omission. Two bracketing
assumptions are therefore reported for every maker result:

  OPTIMISTIC   filled if the bar's range merely TOUCHES the limit price
               (low <= limit for a buy). Assumes perfect queue priority.
  CONSERVATIVE filled only if the bar trades strictly THROUGH the limit by at
               least one tick (low < limit - tick). Proxies "the book cleared
               past me", i.e. no queue priority.

The truth lies between them. Neither can be validated without book data.

A stop can never be a maker order -- it converts to a market order on trigger.
So even under "maker/maker", stop exits and time-stop exits pay taker plus
slippage. Only the take-profit leg earns the maker rate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from deltabt.costs import SymbolCosts, funding_timestamps
from deltabt.strategy import resample_ohlcv

BARS_PER_DAY = 96          # 15m bars
VOL_LOOKBACK = 96          # 24h, pre-registered
MAX_HOLD_BARS = 8          # pre-registered
STOP_MULT = 1.5            # x event range, pre-registered
TARGET_FRAC = 0.5          # 50% retracement, pre-registered
K_VALUES = (2.5, 3.0, 3.5)  # pre-registered; DO NOT EXPAND

EXEC_MODELS = ("maker/maker", "maker/taker", "taker/taker", "maker/maker+delay")
FILL_MODELS = ("optimistic", "conservative")


@dataclass
class ScalpTrade:
    symbol: str
    side: int                 # +1 long, -1 short
    signal_time: int
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    r_price: float            # initial risk in price units
    z: float
    event_range: float
    bars_held: int
    exit_reason: str
    r_gross: float
    cost_r: float
    funding_r: float
    r_net: float
    mfe_r: float              # max favourable excursion, in R
    mae_r: float              # max adverse excursion, in R
    ambiguous: bool
    cluster: str


@dataclass
class ScalpResult:
    symbol: str
    k: float
    exec_model: str
    fill_model: str
    trades: list[ScalpTrade] = field(default_factory=list)
    signals: int = 0
    unfilled: int = 0
    bars: int = 0

    def to_frame(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame(columns=[f for f in ScalpTrade.__dataclass_fields__])
        return pd.DataFrame([t.__dict__ for t in self.trades])


def build_bars(ltp_1m: pd.DataFrame, mark_1m: pd.DataFrame, start: int,
               minutes: int = 15) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Resample to ``minutes`` and align mark to the LTP index.

    THE DEFAULT IS 15 AND MUST STAY 15. H-Scalp-1 and H-Scalp-2 are both
    recorded in the registry against 15m bars, and their numbers have to remain
    reproducible from this code. The parameter exists so H-Scalp-3 can ask the
    same question at a longer horizon WITHOUT a second copy of the simulator
    drifting away from this one; callers that do not pass it get exactly the
    behaviour the recorded experiments were run under.
    """
    ltp = resample_ohlcv(ltp_1m[ltp_1m.time >= start], minutes).reset_index(drop=True)
    if mark_1m is None or mark_1m.empty:
        return ltp, ltp.copy()
    mk = resample_ohlcv(mark_1m[mark_1m.time >= start], minutes)
    mk = mk.set_index("time").reindex(ltp["time"].to_numpy()).reset_index()
    for c in ("high", "low", "close", "open"):
        mk[c] = mk[c].fillna(ltp[c])
    return ltp, mk


def compute_signal(bars: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Return (z, sigma). Strictly causal: sigma uses bars up to t-1 only."""
    close = bars["close"].to_numpy(dtype="float64")
    r = np.concatenate(([np.nan], np.diff(np.log(close))))
    s = pd.Series(r)
    # shift(1) so the window ends at t-1 and never includes r_t itself
    sigma = s.shift(1).rolling(VOL_LOOKBACK, min_periods=VOL_LOOKBACK).std().to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        z = np.where(np.isfinite(sigma) & (sigma > 0), r / sigma, np.nan)
    return z, sigma


def _maker_filled(side: int, limit: float, bar_low: float, bar_high: float,
                  tick: float, conservative: bool) -> bool:
    """Would a resting limit at `limit` have filled inside this bar?"""
    if side > 0:  # buying: need price to come down to us
        return (bar_low < limit - tick) if conservative else (bar_low <= limit)
    return (bar_high > limit + tick) if conservative else (bar_high >= limit)


def run(
    ltp_1m: pd.DataFrame,
    mark_1m: pd.DataFrame,
    funding: pd.DataFrame,
    costs: SymbolCosts,
    *,
    k: float,
    exec_model: str,
    fill_model: str,
    start: int,
    cost_multiplier: float = 1.0,
    slippage_multiplier: float = 1.0,
) -> ScalpResult:
    """Run H-Scalp-1 for one symbol / k / execution model / fill model."""
    if exec_model not in EXEC_MODELS:
        raise ValueError(f"exec_model must be one of {EXEC_MODELS}")
    if fill_model not in FILL_MODELS:
        raise ValueError(f"fill_model must be one of {FILL_MODELS}")

    bars, mark = build_bars(ltp_1m, mark_1m, start)
    n = len(bars)
    res = ScalpResult(symbol=costs.symbol, k=k, exec_model=exec_model,
                      fill_model=fill_model, bars=n)
    if n < VOL_LOOKBACK + MAX_HOLD_BARS + 5:
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

    stamps = set(funding_timestamps(int(t_[0]), int(t_[-1]),
                                    costs.funding_interval_seconds).tolist())
    frate = {}
    if funding is not None and not funding.empty:
        ft = funding["time"].to_numpy("int64"); fv = funding["close"].to_numpy("float64")
        for s in stamps:
            i = np.searchsorted(ft, s, side="right") - 1
            if i >= 0 and np.isfinite(fv[i]):
                frate[s] = float(fv[i])

    busy_until = -1
    for t in range(VOL_LOOKBACK + 1, n - MAX_HOLD_BARS - 2):
        if not np.isfinite(z[t]) or t <= busy_until:
            continue
        side = 1 if z[t] <= -k else (-1 if z[t] >= k else 0)
        if side == 0:
            continue
        res.signals += 1

        event_range = h[t] - lo[t]
        event_move = abs(c[t] - o[t])
        if event_range <= 0 or event_move <= 0:
            continue

        # --- entry -------------------------------------------------------
        # The one-bar-delay model acts on the signal a full bar late: the limit
        # is re-posted at the NEXT bar's close and can only fill after that.
        post_bar = t + 1 if delayed else t
        fill_bar = post_bar + 1
        if fill_bar >= n - MAX_HOLD_BARS - 1:
            continue
        limit = c[post_bar]

        if maker_in:
            if not _maker_filled(side, limit, lo[fill_bar], h[fill_bar],
                                 costs.tick_size, conservative):
                res.unfilled += 1
                continue
            entry = limit
            entry_rate = maker_rate
        else:
            entry = o[fill_bar]
            entry_rate = taker_rate

        r_price = STOP_MULT * event_range
        if r_price <= 0:
            continue
        stop = entry - side * r_price
        # Target is 50% retracement of the EVENT move, measured from the event
        # close -- it is a property of the dislocation, not of our fill.
        target = c[t] + side * TARGET_FRAC * event_move

        # A fill worse than the target already (delay model) is unplayable.
        if side * (target - entry) <= 0:
            continue

        # --- manage ------------------------------------------------------
        exit_price = np.nan; exit_reason = ""; ambiguous = False
        exit_bar = fill_bar
        mfe = mae = 0.0
        for j in range(fill_bar, min(fill_bar + MAX_HOLD_BARS, n)):
            # See hscalp2: a maker entry fills at the bar's adverse extreme, so
            # that bar's favourable extreme generally preceded the fill and
            # must not be counted as a target hit.
            entry_bar_maker = (j == fill_bar) and maker_in

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

        # Only a resting take-profit earns the maker rate. A stop converts to a
        # market order on trigger, and a time exit is a market order too.
        exit_rate = maker_rate if (maker_out and exit_reason == "target") else taker_rate

        r_gross = side * (exit_price - entry) / r_price
        cost_r = (entry * entry_rate + exit_price * exit_rate) / r_price

        funding_r = 0.0
        for s in stamps:
            if t_[fill_bar] <= s <= t_[exit_bar]:
                funding_r += side * (frate.get(s, 0.0) / 100.0) * entry / r_price

        res.trades.append(ScalpTrade(
            symbol=costs.symbol, side=side,
            signal_time=int(t_[t]), entry_time=int(t_[fill_bar]),
            exit_time=int(t_[exit_bar]), entry_price=float(entry),
            exit_price=float(exit_price), stop_price=float(stop),
            target_price=float(target), r_price=float(r_price), z=float(z[t]),
            event_range=float(event_range), bars_held=exit_bar - fill_bar,
            exit_reason=exit_reason, r_gross=float(r_gross), cost_r=float(cost_r),
            funding_r=float(funding_r),
            r_net=float(r_gross - cost_r - funding_r),
            mfe_r=float(mfe), mae_r=float(mae), ambiguous=ambiguous,
            cluster=pd.Timestamp(int(t_[fill_bar]), unit="s").strftime("%Y-%m-%d"),
        ))
        busy_until = exit_bar  # one position at a time per symbol

    return res
