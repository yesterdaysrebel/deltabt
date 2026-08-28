"""H-Carry-2 -- does the funding carry survive a tradeable universe?

WHAT THIS IS
    Execution of the pre-registered experiment in the H-Carry-2 research plan.
    It is EXPLORATORY relative to the historical dataset: the 604-day window
    has already been seen by H-Funding-1, by scripts/funding_spread.py and by
    the 80-cell rebalance sweep, so no chronological split of it is genuinely
    out-of-sample. The train/validation/test tables exist because the prereg
    demanded them, not because they confirm anything.

WHY IT IS A NEW FILE AND NOT AN EDIT TO scripts/funding_spread.py
    That script is a DIFFERENT experiment -- a 4-major carry book with a
    7-day-mean liquidity screen and a fixed leg count -- and fourteen tests in
    tests/test_funding_spread.py pin its source text so its numbers stay
    reproducible. Editing it would silently restate a prior result. The repo's
    own precedent is ef5ff77, which ADDED a `production` gate set rather than
    correcting the existing one.

WHERE THIS DIFFERS FROM THAT SCRIPT, AND WHY
    The frozen spec governs; each of these is a deliberate divergence.

    1. LOOKBACK IS IN SETTLEMENTS, NOT DAYS. `L = 7` means the last seven
       funding payments, which is 28h on a 4h product and 56h on an 8h one.
       funding_spread.py rolls over L calendar days of daily-summed funding.
       (The two rank identically at a fixed interval -- daily sum is the rate
       times a per-symbol constant -- but they weight 4h and 8h products
       differently, and the spec is explicit.)
    2. LIQUIDITY IS A 30-DAY MEDIAN, not a 7-day mean. A median cannot be
       carried by one listing-day volume spike.
    3. LEG COUNT IS DERIVED: k = clip(floor(N/5), 2, 6) from the eligible
       count N, rather than a fixed --legs.
    4. ELIGIBILITY ADDS a 180-day daily-history minimum and a launch-date
       assertion, neither of which that script has.
    5. SETTLEMENT IS READ AT THE GRID INSTANT. funding_spread.py uses
       `resample(step).last()`, which labels a bin by its START and reads the
       rate from the middle of it -- the 07:00 rate stamped as the 00:00
       settlement. This reindexes onto the epoch-anchored grid and reads the
       rate AT the settlement instant, matching app/portfolio/funding.py's
       settlement_grid, which is what the live bot charges.

WHAT IS DELIBERATELY IDENTICAL
    LEG_COST. 0.05% taker x 1.18 GST + 2bps slippage = 0.00079 per unit of
    gross notional traded, charged on turnover so both legs pay at every
    rebalance. Same constant as funding_spread.py and deltabt.costs, so the
    two results are comparable.

THE CAUSAL CONVENTION, STATED ONCE
    A daily bar labelled day d closes at d+1 00:00 UTC. The book for day d is
    therefore decided and traded at d 00:00, using only settlements strictly
    before d 00:00 and only daily bars through d-1. It then earns the day-d
    price return and the day-d funding settlements. A settlement landing
    exactly on d 00:00 is simultaneous with the trade and is charged to the
    new book; it cannot leak, because the signal window ends strictly before
    that instant.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd

from deltabt.config import CACHE_DIR, META_DIR

#: Taker + slippage, one leg, one way. 0.05% x 1.18 GST + 2bps.
#: Identical to scripts/funding_spread.py and deltabt.costs.
LEG_COST = 0.00059 + 0.00020

#: Frozen universe rules.
MIN_DAILY_HISTORY = 180
TURNOVER_WINDOW = 30
DAYS_PER_YEAR = 365.25
SECONDS_PER_YEAR = 365 * 86400


# --------------------------------------------------------------------------
# pure functions -- everything the leakage tests pin lives here
# --------------------------------------------------------------------------

def settlement_instants(index: pd.DatetimeIndex, interval_s: int) -> pd.DatetimeIndex:
    """Epoch-anchored settlement grid covering ``index``.

    Anchored to the UTC epoch, not to when a series starts, so an 8h product
    settles at 00:00 / 08:00 / 16:00 exactly as app/portfolio/funding.py
    computes it for the live bot.
    """
    if interval_s <= 0:
        raise ValueError("funding interval must be positive")
    lo = int(index.min().timestamp())
    hi = int(index.max().timestamp())
    first = -(-lo // interval_s) * interval_s
    stamps = range(first, hi + 1, interval_s)
    return pd.DatetimeIndex(pd.to_datetime(list(stamps), unit="s", utc=True))


def settled_rates(hourly: pd.Series, interval_s: int) -> pd.Series:
    """Rate PAID at each settlement, percent per interval.

    Reads the rate at the settlement instant itself. Missing settlements are
    dropped, never forward-filled: an absent rate is absent information, and
    filling it would invent a payment that the exchange did not make.

    Summing the hourly series instead would count every payment 4 to 8 times
    and turn a true +1.5%/yr into roughly +9%/yr, which is close enough to a
    plausible answer to be dangerous.
    """
    grid = settlement_instants(hourly.index, interval_s)
    return hourly.reindex(grid).dropna()


def annualise(mean_rate_pct: float, interval_s: int) -> float:
    """Mean percent-per-interval -> annual fraction."""
    return mean_rate_pct / 100.0 * (SECONDS_PER_YEAR / interval_s)


def notional_usd(close: pd.Series, volume: pd.Series, contract_value: float) -> pd.Series:
    """Daily turnover in DOLLARS.

    `close * volume` is CONTRACTS. BTCUSD's contract_value is 0.001 and a
    micro-cap's is 1.0, so omitting it understates BTC turnover by 1000x while
    leaving the micro-cap untouched -- inverting the liquidity screen rather
    than loosening it. That bug reported a Sharpe near 4 once already.
    """
    return close * volume * contract_value


def leg_count(n_eligible: int) -> int:
    """k = clip(floor(N/5), 2, 6). Frozen; do not tune."""
    return int(np.clip(n_eligible // 5, 2, 6))


def max_drawdown(equity: pd.Series) -> float:
    return float((equity - equity.cummax()).min())


def newey_west_t(x: np.ndarray, lag: int) -> float:
    """t-statistic on the mean under serial correlation (Bartlett kernel)."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 3:
        return float("nan")
    mu = x.mean()
    e = x - mu
    gamma0 = float(e @ e) / n
    var = gamma0
    for k in range(1, min(lag, n - 1) + 1):
        cov = float(e[k:] @ e[:-k]) / n
        var += 2.0 * (1.0 - k / (lag + 1.0)) * cov
    if var <= 0:
        return float("nan")
    return float(mu / np.sqrt(var / n))


def two_sided_p(t: float) -> float:
    """Normal-approximation two-sided p-value. This venv has no scipy."""
    import math
    if not np.isfinite(t):
        return float("nan")
    return float(math.erfc(abs(t) / math.sqrt(2.0)))


def stationary_bootstrap_sharpe(x: np.ndarray, mean_block: int, n_boot: int,
                                seed: int) -> tuple[float, float]:
    """Percentile CI for the annualised Sharpe under a stationary bootstrap.

    Geometric block lengths with mean ``mean_block`` preserve the serial
    dependence that an IID resample would destroy, and the daily series here
    is autocorrelated by construction: weights are held fixed between
    rebalances, so H consecutive days share one book.
    """
    x = np.asarray(x, dtype=float)
    n = len(x)
    rng = np.random.default_rng(seed)
    p = 1.0 / mean_block
    out = np.empty(n_boot)
    starts = rng.integers(0, n, size=(n_boot, n))
    jumps = rng.random((n_boot, n)) < p
    for b in range(n_boot):
        idx = np.empty(n, dtype=np.int64)
        cur = starts[b, 0]
        for i in range(n):
            if i and not jumps[b, i]:
                cur = cur + 1 if cur + 1 < n else 0
            elif i:
                cur = starts[b, i]
            idx[i] = cur
        s = x[idx]
        sd = s.std(ddof=1)
        out[b] = (s.mean() / sd * np.sqrt(DAYS_PER_YEAR)) if sd > 0 else np.nan
    out = out[np.isfinite(out)]
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def benjamini_hochberg(pvals: list[float], alpha: float = 0.05) -> list[bool]:
    """Step-up BH. Applied to the whole family at once, never elementwise.

    An elementwise comparison of p_i to i*alpha/m lets an isolated small
    p-value through a procedure that is defined by its largest passing rank.
    """
    m = len(pvals)
    order = np.argsort(pvals)
    passed = np.zeros(m, dtype=bool)
    kmax = -1
    for rank, i in enumerate(order, start=1):
        if pvals[i] <= rank * alpha / m:
            kmax = rank
    if kmax > 0:
        passed[order[:kmax]] = True
    return passed.tolist()


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

@dataclass
class SymbolData:
    symbol: str
    interval_s: int
    contract_value: float
    launch: pd.Timestamp
    settle_ts: np.ndarray          # datetime64[ns] settlement instants
    settle_rate: np.ndarray        # percent per interval, at those instants
    close: pd.Series               # daily, UTC-midnight indexed
    turnover: pd.Series            # daily notional USD


def load() -> dict[str, SymbolData]:
    """Every symbol with a funding series, a daily series and a product row."""
    products = json.loads((META_DIR / "products.json").read_text())
    out: dict[str, SymbolData] = {}
    for sym in sorted(os.listdir(CACHE_DIR)):
        fpath = CACHE_DIR / sym / "funding_1h.parquet"
        dpath = CACHE_DIR / sym / "ltp_1d.parquet"
        if not (fpath.exists() and dpath.exists()) or sym not in products:
            continue
        p = products[sym]
        f = pd.read_parquet(fpath)
        f.index = pd.to_datetime(f["time"], unit="s", utc=True)
        settled = settled_rates(f["close"].sort_index(), p["funding_interval_seconds"])
        d = pd.read_parquet(dpath)
        d.index = pd.to_datetime(d["time"], unit="s", utc=True)
        d = d.sort_index()
        out[sym] = SymbolData(
            symbol=sym,
            interval_s=int(p["funding_interval_seconds"]),
            contract_value=float(p["contract_value"]),
            launch=pd.Timestamp(p["launch_time"]).tz_convert("UTC"),
            settle_ts=settled.index.values,
            settle_rate=settled.to_numpy(dtype=float),
            close=d["close"],
            turnover=notional_usd(d["close"], d["volume"], float(p["contract_value"])),
        )
    return out


# --------------------------------------------------------------------------
# the book
# --------------------------------------------------------------------------

@dataclass
class Panel:
    calendar: pd.DatetimeIndex
    symbols: list[str]
    ret: pd.DataFrame              # daily price return
    carry: pd.DataFrame            # daily funding fraction PAID by a long
    turnover_med: pd.DataFrame     # causal 30d median notional, known at day t
    history: pd.DataFrame          # causal count of daily bars before day t
    listed: pd.DataFrame           # bool, day t >= launch_time
    data: dict[str, SymbolData]


def build_panel(data: dict[str, SymbolData]) -> Panel:
    syms = sorted(data)
    calendar = pd.DatetimeIndex(sorted(set().union(*(d.close.index for d in data.values()))))
    close = pd.DataFrame({s: data[s].close for s in syms}).reindex(calendar)
    turn = pd.DataFrame({s: data[s].turnover for s in syms}).reindex(calendar)

    # Daily funding PAID BY A LONG, as a fraction: sum of the settlements that
    # land inside day d. Each settlement is counted exactly once.
    carry = pd.DataFrame(0.0, index=calendar, columns=syms)
    for s in syms:
        d = data[s]
        ser = pd.Series(d.settle_rate / 100.0,
                        index=pd.DatetimeIndex(d.settle_ts).tz_localize("UTC"))
        daily = ser.groupby(ser.index.floor("D")).sum()
        carry[s] = daily.reindex(calendar).fillna(0.0)

    return Panel(
        calendar=calendar,
        symbols=syms,
        ret=close.pct_change(),
        carry=carry,
        # .shift(1): the median known at day t uses bars through day t-1.
        turnover_med=turn.rolling(TURNOVER_WINDOW).median().shift(1),
        history=close.notna().cumsum().shift(1),
        listed=pd.DataFrame(
            {s: calendar >= data[s].launch for s in syms}, index=calendar),
        data=data,
    )


def signal_at(d: SymbolData, day: pd.Timestamp, lookback: int) -> float:
    """Annualised mean funding over the last ``lookback`` settlements before ``day``.

    ``side='left'`` makes the cut STRICT: a settlement stamped exactly at
    ``day`` 00:00 is simultaneous with the trade and is excluded. Without that
    the book would be ranked using a payment it is about to collect.
    """
    j = int(np.searchsorted(d.settle_ts, np.datetime64(day.tz_convert(None)), side="left"))
    if j < lookback:
        return float("nan")
    return annualise(float(d.settle_rate[j - lookback:j].mean()), d.interval_s)


def eligible_at(panel: Panel, day: pd.Timestamp, lookback: int,
                threshold: float) -> pd.Series:
    """Annualised funding signal for every symbol tradeable at ``day``."""
    out = {}
    for s in panel.symbols:
        if not bool(panel.listed.at[day, s]):
            continue
        hist = panel.history.at[day, s]
        if not np.isfinite(hist) or hist < MIN_DAILY_HISTORY:
            continue
        med = panel.turnover_med.at[day, s]
        if not np.isfinite(med) or med < threshold:
            continue
        f = signal_at(panel.data[s], day, lookback)
        if np.isfinite(f):
            out[s] = f
    return pd.Series(out, dtype=float)


def _pick(sig: pd.Series, k: int, mode: str, rng) -> tuple[list[str], list[str]]:
    """Short the richest funding, long the cheapest. Nulls scramble the input only."""
    if mode == "sign":
        sig = sig * rng.choice([-1.0, 1.0], size=len(sig))
    elif mode == "shuffle":
        sig = pd.Series(rng.permutation(sig.to_numpy()), index=sig.index)
    elif mode == "random":
        pick = rng.permutation(sig.index.to_numpy())
        return list(pick[:k]), list(pick[k:2 * k])
    elif mode != "real":
        raise ValueError(f"unknown mode {mode!r}")
    ranked = sig.sort_values(ascending=False)
    return list(ranked.index[:k]), list(ranked.index[-k:])


def run(panel: Panel, lookback: int, hold: int, threshold: float,
        mode: str = "real", seed: int = 0) -> dict:
    """Daily P&L of the dollar-neutral carry book, decomposed."""
    rng = np.random.default_rng(seed)
    rows, census, legs_turnover = [], [], []
    held: dict[str, float] = {}
    for i, day in enumerate(panel.calendar):
        cost = 0.0
        if i % hold == 0:
            sig = eligible_at(panel, day, lookback, threshold)
            n = len(sig)
            new: dict[str, float] = {}
            k = 0
            if n >= 4:
                k = leg_count(n)
                if n >= 2 * k:
                    shorts, longs = _pick(sig, k, mode, rng)
                    new = {**{s: -0.5 / k for s in shorts},
                           **{s: +0.5 / k for s in longs}}
            turnover = sum(abs(new.get(s, 0.0) - held.get(s, 0.0))
                           for s in set(new) | set(held))
            cost = turnover * LEG_COST
            held = new
            for s, w in held.items():
                census.append(dict(day=day, symbol=s, side="SHORT" if w < 0 else "LONG",
                                   turnover=panel.turnover_med.at[day, s]))
            legs_turnover.append(dict(day=day, n_eligible=n, k=k))
        if not held:
            continue
        r = panel.ret.loc[day]
        c = panel.carry.loc[day]
        # A long PAYS positive funding; a short RECEIVES it.
        carry = float(sum(-w * c.get(s, 0.0) for s, w in held.items()))
        price = float(sum(w * r.get(s, 0.0) for s, w in held.items()))
        if not np.isfinite(price):
            price = float(sum(w * (r.get(s, 0.0) if np.isfinite(r.get(s, np.nan)) else 0.0)
                              for s, w in held.items()))
        rows.append(dict(day=day, carry=carry, price=price, fees=cost,
                         total=carry + price - cost, n_legs=len(held)))
    daily = pd.DataFrame(rows).set_index("day") if rows else pd.DataFrame(
        columns=["carry", "price", "fees", "total", "n_legs"])
    return dict(daily=daily,
                census=pd.DataFrame(census),
                sizing=pd.DataFrame(legs_turnover))


def summarise(daily: pd.DataFrame, hold: int, *, boot: bool = True,
              seed: int = 7) -> dict:
    if not len(daily):
        return dict(days=0)
    yrs = len(daily) / DAYS_PER_YEAR
    tot = daily["total"]
    vol = float(tot.std(ddof=1) * np.sqrt(DAYS_PER_YEAR))
    ann = float(tot.sum() / yrs)
    res = dict(
        days=int(len(daily)),
        years=round(yrs, 3),
        rebalances=int((daily["fees"] > 0).sum()),
        carry_ann=float(daily["carry"].sum() / yrs),
        price_ann=float(daily["price"].sum() / yrs),
        fees_ann=float(daily["fees"].sum() / yrs),
        net_ann=ann,
        vol_ann=vol,
        price_vol_ann=float(daily["price"].std(ddof=1) * np.sqrt(DAYS_PER_YEAR)),
        sharpe=float(ann / vol) if vol > 0 else float("nan"),
        max_drawdown=max_drawdown(tot.cumsum()),
        win_rate=float((tot > 0).mean()),
        nw_t=newey_west_t(tot.to_numpy(), lag=2 * hold),
    )
    res["p_value"] = two_sided_p(res["nw_t"])
    if boot:
        lo, hi = stationary_bootstrap_sharpe(tot.to_numpy(), 10, 10_000, seed)
        res["sharpe_ci"] = [lo, hi]
    return res
