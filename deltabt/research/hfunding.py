"""H-Funding: does extreme perpetual funding predict returns after all cash flows?

Pre-registered. Two arms, deliberately NOT combined:

  ARM A  pure funding carry.
         funding >= upper percentile -> SHORT (a short receives when funding>0)
         funding <= lower percentile -> LONG  (a long receives when funding<0)
         No price condition at all.

  ARM B  funding extreme + evidence price has stopped continuing in the
         crowded direction.

SIGN CONVENTION (asserted in tests, not assumed):
    funding > 0  ->  LONGS PAY SHORTS
    funding < 0  ->  SHORTS PAY LONGS
    cash to a position of `side` at rate f is  -side * f  per unit notional.

AVAILABILITY (the rule that prevents timestamp leakage):
    A funding observation stamped T is treated as knowable only after its bar
    closes. Entry therefore occurs at the OPEN of the first 1H bar that starts
    strictly after T + one funding-bar interval. Nothing at or before T is used
    from the future, and the rate that settles after entry cannot influence the
    decision to enter.

DATA POLICY:
    Missing funding is never forward-filled or invented. An observation that is
    NaN is skipped, and a trade whose holding window contains a funding gap is
    flagged so it can be excluded from the carry accounting.

MEASURED DATA PATHOLOGY (documented before results were seen):
    21-44% of funding observations sit exactly at +/-0.01% -- the interest-rate
    baseline. For BTC and ETH the 95th percentile IS that pin, so the
    pre-registered `>= p95` condition selects the modal value rather than a
    tail. The pre-registered rule is implemented as the primary; a strict `>`
    variant is reported beside it as a documented correction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from deltabt import indicators as ind
from deltabt.costs import SymbolCosts
from deltabt.strategy import resample_ohlcv

MIN_PCTL_OBS = 500          # expanding percentile warm-up
HOLD_HOURS = (24, 48, 72)
RISK_PCT = 0.005
MAX_LEVERAGE = 3.0          # deliberately low: studying the signal, not liquidation
START_EQUITY = 10_000.0

GRID = dict(
    low_pctl=(0.05, 0.10),
    high_pctl=(0.90, 0.95),
    hold_h=HOLD_HOURS,
    price_lookback_h=(12, 24),   # Arm B only
)
PRIMARY = dict(low_pctl=0.05, high_pctl=0.95, hold_h=24, price_lookback_h=24)


@dataclass
class FTrade:
    symbol: str
    arm: str
    side: int
    signal_time: int        # funding observation timestamp
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    funding_at_signal: float
    funding_pctl_at_signal: float
    hold_hours: int
    settlements: int
    funding_gap: bool
    # decomposition, all in basis points of notional
    price_bps: float
    funding_bps: float
    fee_bps: float
    slippage_bps: float
    net_bps: float
    # R-normalised (R = ATR(24) on 1H at entry) for comparability with prior work
    atr_r: float
    price_r: float
    funding_r: float
    cost_r: float
    net_r: float
    contracts: int
    notional: float
    pnl_usd: float
    cluster: str


@dataclass
class FResult:
    symbol: str
    arm: str
    params: dict
    trades: list[FTrade] = field(default_factory=list)
    signals: int = 0
    skipped_nan: int = 0
    skipped_no_bar: int = 0
    skipped_confirm: int = 0

    def to_frame(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame(columns=[f for f in FTrade.__dataclass_fields__])
        return pd.DataFrame([t.__dict__ for t in self.trades])


def expanding_percentile(v: pd.Series, q: float, min_obs: int = MIN_PCTL_OBS) -> np.ndarray:
    """Expanding quantile using ONLY observations strictly before each point."""
    return v.expanding(min_obs).quantile(q).shift(1).to_numpy()


def funding_rank(v: pd.Series, min_obs: int = MIN_PCTL_OBS) -> np.ndarray:
    """Percentile rank of each observation within its own history (causal)."""
    return v.expanding(min_obs).apply(
        lambda w: float((w[:-1] <= w[-1]).mean()) if len(w) > 1 else np.nan, raw=True
    ).to_numpy()


def build(ltp_1m: pd.DataFrame, funding: pd.DataFrame, start: int):
    h1 = resample_ohlcv(ltp_1m[ltp_1m.time >= start], 60).reset_index(drop=True)
    f = funding[funding.time >= start].sort_values("time").reset_index(drop=True)
    return h1, f


def run(
    ltp_1m: pd.DataFrame, funding: pd.DataFrame, costs: SymbolCosts, *,
    start: int, end: int | None = None, arm: str = "A",
    low_pctl: float = 0.05, high_pctl: float = 0.95, hold_h: int = 24,
    price_lookback_h: int = 24, strict: bool = False,
    cost_multiplier: float = 1.0, slippage_multiplier: float = 1.0,
) -> FResult:
    """`strict=True` uses > / < instead of >= / <= (the pathology correction)."""
    if arm not in ("A", "B"):
        raise ValueError("arm must be 'A' or 'B'")
    h1, f = build(ltp_1m, funding, start)
    params = dict(low_pctl=low_pctl, high_pctl=high_pctl, hold_h=hold_h,
                  price_lookback_h=price_lookback_h, strict=strict)
    res = FResult(symbol=costs.symbol, arm=arm, params=params)
    if len(h1) < 200 or len(f) < MIN_PCTL_OBS + 10:
        return res

    ft = f["time"].to_numpy("int64")
    fv = f["close"].to_numpy("float64")
    fs = pd.Series(fv)
    lo_thr = expanding_percentile(fs, low_pctl)
    hi_thr = expanding_percentile(fs, high_pctl)
    rank = funding_rank(fs)

    ht = h1["time"].to_numpy("int64")
    ho = h1["open"].to_numpy("float64"); hh = h1["high"].to_numpy("float64")
    hl = h1["low"].to_numpy("float64"); hc = h1["close"].to_numpy("float64")
    atr24 = ind.atr(hh, hl, hc, 24)
    n = len(h1)
    hi_bound = ht[-1] if end is None else end

    interval = costs.funding_interval_seconds
    fmap = {int(t): v for t, v in zip(ft, fv) if np.isfinite(v)}

    taker = costs.effective_taker * cost_multiplier
    slip = costs.slippage_rate * slippage_multiplier
    equity = START_EQUITY
    busy_until = -1

    for i in range(len(f)):
        v = fv[i]
        if not np.isfinite(v) or not np.isfinite(lo_thr[i]) or not np.isfinite(hi_thr[i]):
            if not np.isfinite(v):
                res.skipped_nan += 1
            continue
        if strict:
            hi_hit, lo_hit = v > hi_thr[i], v < lo_thr[i]
        else:
            hi_hit, lo_hit = v >= hi_thr[i], v <= lo_thr[i]
        if not (hi_hit or lo_hit):
            continue
        # crowded longs (funding high) -> SHORT ; crowded shorts -> LONG
        side = -1 if hi_hit else 1
        res.signals += 1

        # availability: knowable only after the funding bar closes
        known_at = int(ft[i]) + 3600
        j = int(np.searchsorted(ht, known_at, side="left"))
        if j >= n or ht[j] > hi_bound:
            res.skipped_no_bar += 1
            continue
        if j <= busy_until:
            continue

        if arm == "B":
            lb = price_lookback_h
            if j - lb < 1 or j < 7:
                res.skipped_confirm += 1
                continue
            ret_lb = hc[j - 1] / hc[j - 1 - lb] - 1.0
            # "no new 6h extreme after the funding extreme", evaluated on bars
            # strictly before entry
            w_lo = hl[max(j - 6, 0):j].min(); w_hi = hh[max(j - 6, 0):j].max()
            no_new_low = hl[j - 1] > w_lo
            no_new_high = hh[j - 1] < w_hi
            turn_up = hc[j - 1] > hc[j - 2]
            turn_dn = hc[j - 1] < hc[j - 2]
            ok = ((side > 0 and ret_lb < 0 and no_new_low and turn_up)
                  or (side < 0 and ret_lb > 0 and no_new_high and turn_dn))
            if not ok:
                res.skipped_confirm += 1
                continue

        entry = ho[j]
        k = min(j + hold_h, n - 1)
        if ht[k] > hi_bound:
            k = int(np.searchsorted(ht, hi_bound, side="right")) - 1
            if k <= j:
                res.skipped_no_bar += 1
                continue
        exit_px = hc[k]

        # --- funding cash over the holding window -------------------------
        first = ((int(ht[j]) + interval - 1) // interval) * interval
        stamps = list(range(first, int(ht[k]) + 1, interval))
        gap = False
        f_units = 0.0
        for s_ in stamps:
            r_ = fmap.get(s_)
            if r_ is None:
                gap = True          # never invented; flagged instead
                continue
            f_units += -side * (r_ / 100.0)   # long pays when rate > 0

        price_units = side * (exit_px - entry) / entry
        fee_units = 2 * taker
        slip_units = 2 * slip
        net_units = price_units + f_units - fee_units - slip_units

        a = atr24[j] if np.isfinite(atr24[j]) and atr24[j] > 0 else np.nan
        if np.isfinite(a):
            price_r = side * (exit_px - entry) / a
            funding_r = f_units * entry / a
            cost_r = (fee_units + slip_units) * entry / a
        else:
            price_r = funding_r = cost_r = np.nan

        units = (equity * MAX_LEVERAGE) / entry
        contracts = costs.contracts_for(units)
        if contracts <= 0:
            continue
        notional = costs.notional(contracts, entry)
        pnl = notional * net_units
        equity += pnl

        res.trades.append(FTrade(
            symbol=costs.symbol, arm=arm, side=side, signal_time=int(ft[i]),
            entry_time=int(ht[j]), exit_time=int(ht[k]), entry_price=float(entry),
            exit_price=float(exit_px), funding_at_signal=float(v),
            funding_pctl_at_signal=float(rank[i]) if np.isfinite(rank[i]) else np.nan,
            hold_hours=int(k - j), settlements=len(stamps), funding_gap=gap,
            price_bps=float(price_units * 1e4), funding_bps=float(f_units * 1e4),
            fee_bps=float(fee_units * 1e4), slippage_bps=float(slip_units * 1e4),
            net_bps=float(net_units * 1e4), atr_r=float(a) if np.isfinite(a) else np.nan,
            price_r=float(price_r), funding_r=float(funding_r), cost_r=float(cost_r),
            net_r=float(price_r + funding_r - cost_r) if np.isfinite(price_r) else np.nan,
            contracts=int(contracts), notional=float(notional), pnl_usd=float(pnl),
            cluster=pd.Timestamp(int(ht[j]), unit="s").strftime("%Y-%m-%d"),
        ))
        busy_until = k

    return res


def funding_persistence(funding: pd.DataFrame, trades: pd.DataFrame,
                        start: int) -> pd.DataFrame:
    """Descriptive only: where does the funding percentile sit after entry?

    Explicitly NOT a strategy condition -- it is not pre-registered as one and
    must not become a filter.
    """
    if trades.empty:
        return pd.DataFrame()
    f = funding[funding.time >= start].sort_values("time").reset_index(drop=True)
    rank = funding_rank(pd.Series(f["close"].to_numpy("float64")))
    ft = f["time"].to_numpy("int64")
    rows = []
    for st in trades["signal_time"].to_numpy("int64"):
        i = int(np.searchsorted(ft, st))
        if i >= len(ft):
            continue
        row = {"entry": rank[i] if i < len(rank) else np.nan}
        for h in (6, 12, 24):
            k = int(np.searchsorted(ft, st + h * 3600))
            row[f"+{h}h"] = rank[k] if k < len(rank) else np.nan
        rows.append(row)
    return pd.DataFrame(rows)
