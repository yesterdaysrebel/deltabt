"""H-Pair: XAUT/PAXG relative-value convergence on Delta India.

Pre-registered. Tests whether the RELATIVE price of two perpetuals on the same
metal contains a tradable convergence edge -- not whether gold goes up or down.

MEASURED CONTRACT DIFFERENCES (documented, not assumed away):
  same : contract type, quote/settle asset, tick 0.01, contract value 0.001,
         initial margin 1%, funding method (mark), funding interval 4h, clamp
  differ: underlying token (XAUT vs PAXG), position limit (52k vs 200k),
         listing date (2026-04-17 vs 2026-02-19)
  NOTE : maker and taker fees are IDENTICAL for both metals (0.01% each, so
         1.18 bps after GST). The A/B/C execution comparison therefore differs
         only in slippage and fill probability, NOT in fee. That is a real
         property of the venue, not a modelling shortcut.

MEASURED SPREAD PROPERTIES (before any profitability was computed):
  half-life 24.6h; AR(1) coefficient -0.028; rolling OLS beta ~0.978;
  at |z|>=2 only 11.5% of excursions reach z=0 within 24h, 26.9% within 48h.

LEGGING: a pair signal is not a trade until BOTH legs fill. With no order-book
history queue position cannot be modelled, so maker fills are approximated by
range-touch with an explicit fill probability, and the sensitivity to that
assumption is reported. When one leg fills and the other does not, the position
is completed with a taker order on the following bar and the adverse move is
charged as LEGGING_COST.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from deltabt.costs import SymbolCosts, funding_timestamps
from deltabt.strategy import resample_ohlcv

HEDGE_LOOKBACK_D = 20        # primary; 10 and 40 are diagnostics
Z_LOOKBACK_D = 20
STOP_Z = 3.5
RISK_PCT = 0.005
MAX_LEVERAGE = 3.0
START_EQUITY = 10_000.0

GRID = dict(entry_z=(2.0, 2.5, 3.0), exit_z=(0.0, 0.5), max_hold_h=(24, 48))
PRIMARY = dict(entry_z=2.0, exit_z=0.0, max_hold_h=24)
EXEC_MODELS = ("maker/maker", "maker/taker", "taker/taker")


@dataclass
class PairTrade:
    signal_time: int
    entry_time: int
    exit_time: int
    side: int                 # +1 = long XAUT / short PAXG ; -1 = reverse
    beta: float
    z_entry: float
    z_exit: float
    spread_entry: float       # bps
    spread_exit: float        # bps
    hold_hours: int
    exit_reason: str
    exec_model: str
    legged: bool
    # decomposition, all in bps of pair notional
    xaut_ret_bps: float
    paxg_ret_bps: float
    gross_bps: float
    xaut_fee_bps: float
    paxg_fee_bps: float
    xaut_slip_bps: float
    paxg_slip_bps: float
    xaut_funding_bps: float
    paxg_funding_bps: float
    legging_bps: float
    total_cost_bps: float
    net_bps: float
    converged: bool
    cluster: str


@dataclass
class PairResult:
    exec_model: str
    params: dict
    trades: list[PairTrade] = field(default_factory=list)
    signals: int = 0
    unfilled: int = 0
    legged: int = 0

    def to_frame(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame(columns=[f for f in PairTrade.__dataclass_fields__])
        return pd.DataFrame([t.__dict__ for t in self.trades])


def rolling_beta(y: np.ndarray, x: np.ndarray, window: int) -> np.ndarray:
    """OLS slope of y on x over the trailing `window`, ENDING AT t-1.

    Causal by construction: the value at t is fitted on [t-window, t-1].
    """
    n = y.size
    out = np.full(n, np.nan)
    if n <= window:
        return out
    sx = pd.Series(x); sy = pd.Series(y)
    mx = sx.rolling(window).mean().shift(1)
    my = sy.rolling(window).mean().shift(1)
    mxy = (sx * sy).rolling(window).mean().shift(1)
    mxx = (sx * sx).rolling(window).mean().shift(1)
    cov = mxy - mx * my
    var = mxx - mx * mx
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(var > 0, cov / var, np.nan)
    return out


def build_panel(x_1m: pd.DataFrame, p_1m: pd.DataFrame, start: int, end: int | None):
    """Aligned 1H panel. No forward-filling across gaps: bars present in only
    one leg are dropped, and the count is reported by the caller."""
    lo = max(int(x_1m.time.iloc[0]), int(p_1m.time.iloc[0]), start)
    hi = min(int(x_1m.time.iloc[-1]), int(p_1m.time.iloc[-1]))
    if end is not None:
        hi = min(hi, end)
    xh = resample_ohlcv(x_1m[(x_1m.time >= lo) & (x_1m.time <= hi)], 60).set_index("time")
    ph = resample_ohlcv(p_1m[(p_1m.time >= lo) & (p_1m.time <= hi)], 60).set_index("time")
    idx = xh.index.intersection(ph.index).sort_values()
    return xh.loc[idx].reset_index(), ph.loc[idx].reset_index()


def signals(xh: pd.DataFrame, ph: pd.DataFrame, *, hedge_days=HEDGE_LOOKBACK_D,
            z_days=Z_LOOKBACK_D):
    lx = np.log(xh["close"].to_numpy("float64"))
    lp = np.log(ph["close"].to_numpy("float64"))
    beta = rolling_beta(lx, lp, hedge_days * 24)
    spread = (lx - beta * lp) * 1e4
    S = pd.Series(spread)
    w = z_days * 24
    mu = S.rolling(w, min_periods=w).mean().shift(1)
    sd = S.rolling(w, min_periods=w).std().shift(1)
    with np.errstate(invalid="ignore"):
        z = ((S - mu) / sd).to_numpy()
    return beta, spread, z


def _fee(costs: SymbolCosts) -> float:
    """Metals: maker == taker, so a single rate covers both."""
    return costs.effective_taker


def run(
    x_1m: pd.DataFrame, p_1m: pd.DataFrame,
    x_fund: pd.DataFrame, p_fund: pd.DataFrame,
    x_costs: SymbolCosts, p_costs: SymbolCosts, *,
    start: int, end: int | None = None,
    entry_z: float = 2.0, exit_z: float = 0.0, max_hold_h: int = 24,
    exec_model: str = "maker/maker", maker_fill_prob: float = 1.0,
    slippage_bps: float = 1.0, hedge_days: int = HEDGE_LOOKBACK_D,
    seed: int = 0,
) -> PairResult:
    if exec_model not in EXEC_MODELS:
        raise ValueError(f"exec_model must be one of {EXEC_MODELS}")
    xh, ph = build_panel(x_1m, p_1m, start, end)
    res = PairResult(exec_model=exec_model,
                     params=dict(entry_z=entry_z, exit_z=exit_z,
                                 max_hold_h=max_hold_h, hedge_days=hedge_days,
                                 maker_fill_prob=maker_fill_prob,
                                 slippage_bps=slippage_bps))
    n = len(xh)
    if n < (hedge_days + Z_LOOKBACK_D) * 24 + 50:
        return res

    beta, spread, z = signals(xh, ph, hedge_days=hedge_days)
    t = xh["time"].to_numpy("int64")
    xo = xh["open"].to_numpy("float64"); xc = xh["close"].to_numpy("float64")
    xhi = xh["high"].to_numpy("float64"); xlo = xh["low"].to_numpy("float64")
    po = ph["open"].to_numpy("float64"); pc = ph["close"].to_numpy("float64")
    phi = ph["high"].to_numpy("float64"); plo = ph["low"].to_numpy("float64")

    fx, fp = _fee(x_costs), _fee(p_costs)
    slip = slippage_bps / 1e4
    rng = np.random.default_rng(seed)

    def fund_map(f, interval):
        if f is None or f.empty:
            return {}
        return {int(a): b for a, b in zip(f["time"].to_numpy("int64"),
                                          f["close"].to_numpy("float64"))
                if np.isfinite(b)}
    fxm = fund_map(x_fund, x_costs.funding_interval_seconds)
    fpm = fund_map(p_fund, p_costs.funding_interval_seconds)

    busy_until = -1
    for i in range(1, n - 2):
        if i <= busy_until or not np.isfinite(z[i]):
            continue
        if abs(z[i]) < entry_z:
            continue
        # z >= +entry  -> spread rich -> SHORT XAUT / LONG PAXG  (side = -1)
        side = -1 if z[i] >= entry_z else 1
        res.signals += 1

        j = i + 1                       # entry on the NEXT bar, never same-bar
        if j >= n - 1:
            continue

        legged = False
        if exec_model == "taker/taker":
            x_entry, p_entry = xo[j], po[j]
            x_slip = p_slip = slip
        else:
            # maker legs rest at the signal bar's close and fill on a touch
            x_lim, p_lim = xc[i], pc[i]
            x_is_maker = True
            p_is_maker = exec_model == "maker/maker"
            x_fill = (xlo[j] <= x_lim <= xhi[j]) and (rng.random() < maker_fill_prob)
            p_fill = ((plo[j] <= p_lim <= phi[j]) and (rng.random() < maker_fill_prob)
                      if p_is_maker else True)
            if not x_fill and not p_fill:
                res.unfilled += 1
                continue
            # a single filled leg is completed with taker on the following bar
            if x_fill:
                x_entry, x_slip = x_lim, 0.0
            else:
                x_entry, x_slip, legged = xo[j + 1], slip, True
            if p_is_maker:
                if p_fill:
                    p_entry, p_slip = p_lim, 0.0
                else:
                    p_entry, p_slip, legged = po[j + 1], slip, True
            else:
                p_entry, p_slip = po[j], slip
        if legged:
            res.legged += 1

        b = beta[i] if np.isfinite(beta[i]) else 1.0
        k = min(j + max_hold_h, n - 1)
        exit_reason = "time"; converged = False
        for m in range(j + 1, k + 1):
            if not np.isfinite(z[m]):
                continue
            if abs(z[m]) >= STOP_Z and np.sign(z[m]) == np.sign(z[i]):
                k = m; exit_reason = "stop"; break
            if (z[i] > 0 and z[m] <= exit_z) or (z[i] < 0 and z[m] >= -exit_z):
                k = m; exit_reason = "target"; converged = True; break
        x_exit, p_exit = xc[k], pc[k]

        # legs: side=+1 long XAUT / short PAXG, weighted by beta
        x_ret = side * (x_exit - x_entry) / x_entry * 1e4
        p_ret = -side * b * (p_exit - p_entry) / p_entry * 1e4
        gross = x_ret + p_ret

        x_fee = 2 * fx * 1e4
        p_fee = 2 * fp * b * 1e4
        x_sl = (x_slip + slip) * 1e4          # entry slip + taker exit slip
        p_sl = (p_slip + slip) * b * 1e4
        legging = 0.0
        if legged:
            # the adverse move incurred completing the second leg
            legging = abs(xo[j + 1] - xc[i]) / xc[i] * 1e4

        def funding(fmap, interval, sgn, w):
            first = ((int(t[j]) + interval - 1) // interval) * interval
            tot = 0.0
            for s_ in range(first, int(t[k]) + 1, interval):
                r_ = fmap.get(s_)
                if r_ is not None:
                    tot += -sgn * (r_ / 100.0) * w
            return tot * 1e4
        x_fund_bps = funding(fxm, x_costs.funding_interval_seconds, side, 1.0)
        p_fund_bps = funding(fpm, p_costs.funding_interval_seconds, -side, b)

        cost = x_fee + p_fee + x_sl + p_sl + legging
        net = gross + x_fund_bps + p_fund_bps - cost

        res.trades.append(PairTrade(
            signal_time=int(t[i]), entry_time=int(t[j]), exit_time=int(t[k]),
            side=side, beta=float(b), z_entry=float(z[i]),
            z_exit=float(z[k]) if np.isfinite(z[k]) else np.nan,
            spread_entry=float(spread[i]), spread_exit=float(spread[k]),
            hold_hours=int(k - j), exit_reason=exit_reason, exec_model=exec_model,
            legged=legged, xaut_ret_bps=float(x_ret), paxg_ret_bps=float(p_ret),
            gross_bps=float(gross), xaut_fee_bps=float(x_fee), paxg_fee_bps=float(p_fee),
            xaut_slip_bps=float(x_sl), paxg_slip_bps=float(p_sl),
            xaut_funding_bps=float(x_fund_bps), paxg_funding_bps=float(p_fund_bps),
            legging_bps=float(legging), total_cost_bps=float(cost), net_bps=float(net),
            converged=converged,
            cluster=pd.Timestamp(int(t[j]), unit="s").strftime("%Y-%m-%d"),
        ))
        busy_until = k

    return res
