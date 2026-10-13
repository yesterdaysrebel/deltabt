"""Readings at signal time: ONE definition for the signal lab and the live bot.

WHY ONE MODULE (owner, 2026-10-13). The bot records every signal it sees, with
about forty readings, so a model can later be graded on them. The 5,273
historical signals in out/signal_lab/signals.parquet were built by
scripts/signal_features_lab.py; live rows are only poolable with them if both
are computed by the same code. The lab's `_coin` now calls this module, and
tests/test_signal_features_parity.py checks it against the lab's output from
before the move.

WHAT A FEATURE MAY READ. Only bars CLOSED at or before the signal bar's close
(`at`). Directional readings are multiplied by the trade side, so "+" always
means "with the trade"; %R is mirrored for shorts.

MISSING INPUTS GIVE NaN, NEVER AN EXCEPTION, in `signal_features` (the live
entry point). The lab calls the lower-level `prepare_coin` / `features_at`
with full histories.

NOTHING HERE DECIDES A TRADE. The model graded on these readings never filters
an entry (tests/live_exec/test_model_never_trades.py).
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from deltabt import rulecore

DAY = 86400
IST = 19800
TAGS = ("meme", "ai", "defi", "layer", "new")

#: The model's inputs, in the lab's column order.
FEATURES = (
    "wpr", "wpr_slope3", "st_dist_atr", "atr_pct", "stop_pct", "adx_5m", "vol_ratio",
    "range_atr", "ret_1h", "ret_4h", "ret_24h", "from_24h_high", "from_24h_low",
    "st_1h_agrees", "adx_1h", "st_4h_agrees", "day_gain_pct", "day_rank", "log_turnover",
    "days_listed", *(f"tag_{t}" for t in TAGS + ("other",)), "funding_side",
    "oi_chg_1h", "oi_chg_6h", "oi_chg_24h", "btc_ret_1h", "btc_ret_4h", "btc_ret_24h",
    "btc_st_1h_agrees", "hour_ist", "weekday", "run_4h_atr", "pullback_4h_atr",
    "with_trade_share_1h",
)


def tag_of(tags) -> str:
    """Delta's ticker `tags` -> one of TAGS or "other" (first match wins)."""
    tags = [str(t).lower() for t in (tags or [])]
    for name in TAGS:
        if any(name in t for t in tags):
            return name
    return "other"


def asof(close_time, values, at):
    """Value of the last bar CLOSED at or before `at`."""
    at = np.asarray(at)
    j = np.searchsorted(close_time, at, side="right") - 1
    out = np.full(len(at), np.nan)
    ok = j >= 0
    out[ok] = np.asarray(values, dtype="float64")[j[ok]]
    return out


@dataclass
class CoinContext:
    """One coin's closed bars with the indicators computed once."""
    minutes: int
    time: np.ndarray            # primary bar OPEN, unix s
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    vol: np.ndarray
    vol_prev: np.ndarray
    hi24: np.ndarray
    lo24: np.ndarray
    ind: object                 # rulecore.TimeframeIndicators on the primary bars
    htf: dict                   # "1h"/"4h" -> (close_time, TimeframeIndicators) or None


@dataclass
class BtcContext:
    m_time: np.ndarray          # 1m bar CLOSE times
    m_close: np.ndarray
    h_close_time: np.ndarray    # 60m bar CLOSE times
    h_dir: np.ndarray           # 60m Supertrend direction (-1 = up, as rulecore)


def _htf(frame, minutes, spec):
    if frame is None or len(frame) == 0:
        return None
    ind = rulecore.indicators(frame, spec)
    return frame["time"].to_numpy("int64") + minutes * 60, ind


def prepare_coin(p5: pd.DataFrame, h1: pd.DataFrame | None, h4: pd.DataFrame | None,
                 spec, minutes: int = 5) -> CoinContext:
    """Indicators on the primary (5m) bars and the 60m / 240m bars."""
    pi = rulecore.indicators(p5, spec)
    vol = p5["volume"].to_numpy("float64")
    return CoinContext(
        minutes=minutes, time=p5["time"].to_numpy("int64"),
        high=pi.high, low=pi.low, close=pi.close, vol=vol,
        vol_prev=pd.Series(vol).shift(1).rolling(20, min_periods=10).mean().to_numpy(),
        hi24=pd.Series(pi.high).rolling(288, min_periods=48).max().to_numpy(),
        lo24=pd.Series(pi.low).rolling(288, min_periods=48).min().to_numpy(),
        ind=pi, htf={"1h": _htf(h1, 60, spec), "4h": _htf(h4, 240, spec)})


def prepare_btc(m1: pd.DataFrame | None, h1: pd.DataFrame | None, spec) -> BtcContext | None:
    """BTC's 1m closes (returns) and 60m Supertrend (direction)."""
    if m1 is None or h1 is None or len(m1) == 0 or len(h1) == 0:
        return None
    ind = rulecore.indicators(h1, spec)
    return BtcContext(m_time=m1["time"].to_numpy("int64") + 60,
                      m_close=m1["close"].to_numpy("float64"),
                      h_close_time=h1["time"].to_numpy("int64") + 3600, h_dir=ind.direction)


def _ret(times, closes, at, back_s):
    now = asof(times, closes, at)
    then = asof(times, closes, at - back_s)
    return now / then - 1


def _agrees(dirn, side):
    return np.where(np.isfinite(dirn), ((dirn < 0) == (side > 0)).astype(float), np.nan)


def _pick_columns(picks, day, n):
    """(change %, rank, turnover USD) for each signal's UTC day of `at`.

    `picks` is a frame with pick_day/change/rank/turnover (the lab), or a
    mapping {pick_day: {"change", "rank", "turnover"}} (the live bot, which
    knows only today's pick)."""
    nan = np.full(n, np.nan)
    if picks is None:
        return nan, nan.copy(), nan.copy()
    if isinstance(picks, pd.DataFrame):
        pk = picks.set_index("pick_day")
        return (pk.change.reindex(day).to_numpy(), pk["rank"].reindex(day).to_numpy(),
                pk.turnover.reindex(day).to_numpy())
    cols = []
    for key in ("change", "rank", "turnover"):
        cols.append(np.array([_num((picks.get(int(d)) or {}).get(key)) for d in day], dtype=float))
    return tuple(cols)


def _num(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return v


def features_at(ctx: CoinContext, i, side, entry_price, stop_price, *,
                btc: BtcContext | None = None, funding: pd.DataFrame | None = None,
                oi: pd.DataFrame | None = None, picks=None, launch: int | None = None,
                tag: str | None = None) -> pd.DataFrame:
    """The readings for signals on primary bars `i` (arrays), one row each.

    `funding`: hourly FUNDING candles (time = open, close = rate %). `oi`:
    hourly open interest (time = open, column oi). Both optional.
    """
    i = np.asarray(i, dtype="int64")
    side = np.asarray(side, dtype=float)
    entry_price = np.asarray(entry_price, dtype=float)
    stop_price = np.asarray(stop_price, dtype=float)
    pi, c, h, lo_ = ctx.ind, ctx.close, ctx.high, ctx.low
    at = ctx.time[i] + ctx.minutes * 60       # the signal bar's close
    wpr = pi.wpr
    f = pd.DataFrame({
        "wpr": np.where(side > 0, wpr[i], -100 - wpr[i]),
        "wpr_slope3": (wpr[i] - wpr[np.maximum(i - 3, 0)]) * side,
        "st_dist_atr": (c[i] - pi.st[i]) / pi.atr[i] * side,
        "atr_pct": pi.atr[i] / c[i] * 100,
        "stop_pct": np.abs(entry_price - stop_price) / entry_price * 100,
        "adx_5m": pi.adx[i],
        "vol_ratio": ctx.vol[i] / ctx.vol_prev[i],
        "range_atr": (h[i] - lo_[i]) / pi.atr[i],
        "ret_1h": (c[i] / c[np.maximum(i - 12, 0)] - 1) * 100 * side,
        "ret_4h": (c[i] / c[np.maximum(i - 48, 0)] - 1) * 100 * side,
        "ret_24h": (c[i] / c[np.maximum(i - 288, 0)] - 1) * 100 * side,
        "from_24h_high": (c[i] / ctx.hi24[i] - 1) * 100,
        "from_24h_low": (c[i] / ctx.lo24[i] - 1) * 100,
    })
    nan = np.full(len(i), np.nan)
    for name in ("1h", "4h"):
        got = ctx.htf.get(name)
        if got is None:
            f[f"st_{name}_agrees"] = nan
            if name == "1h":
                f["adx_1h"] = nan
            continue
        ct, ind = got
        f[f"st_{name}_agrees"] = _agrees(asof(ct, ind.direction, at), side)
        if name == "1h":
            f["adx_1h"] = asof(ct, ind.adx, at)
    day = (at // DAY) * DAY
    chg, rank, turn = _pick_columns(picks, day, len(i))
    f["day_gain_pct"] = chg
    f["day_rank"] = rank
    with np.errstate(divide="ignore", invalid="ignore"):
        f["log_turnover"] = np.log10(turn)
    f["days_listed"] = (at - launch) / DAY if launch else np.nan
    for name in TAGS + ("other",):
        f[f"tag_{name}"] = float(tag == name)
    if funding is not None and not funding.empty:
        f["funding_side"] = asof(funding.time.to_numpy("int64") + 3600,
                                 funding.close.to_numpy(float), at) * side
    else:
        f["funding_side"] = nan
    if oi is not None and not oi.empty:
        oi = oi.sort_values("time")
        ot, ov = oi.time.to_numpy("int64") + 3600, oi.oi.to_numpy(float)
        for hrs in (1, 6, 24):
            f[f"oi_chg_{hrs}h"] = (asof(ot, ov, at) / asof(ot, ov, at - hrs * 3600) - 1) * 100
    else:
        for hrs in (1, 6, 24):
            f[f"oi_chg_{hrs}h"] = nan
    for hrs in (1, 4, 24):
        f[f"btc_ret_{hrs}h"] = (_ret(btc.m_time, btc.m_close, at, hrs * 3600) * 100 * side
                                if btc is not None else nan)
    f["btc_st_1h_agrees"] = (_agrees(asof(btc.h_close_time, btc.h_dir, at), side)
                             if btc is not None else nan)
    f["hour_ist"] = ((at + IST) % DAY) // 3600
    f["weekday"] = ((at + IST) // DAY + 3) % 7   # 0 = Monday

    # PRE-ENTRY SHAPE: the last 4h in ATR units, the trade's direction as "+":
    # the biggest move with the trade, the deepest pullback against it, and the
    # share of the last hour's bars that closed with the trade.
    atr_i = pi.atr[i]
    run4, pull4, green = [], [], []
    for k, ii in enumerate(i):
        w = slice(max(ii - 47, 0), ii + 1)
        sd = side[k]
        rel = (c[w] - c[max(ii - 48, 0)]) * sd / atr_i[k]
        run4.append(np.nanmax(rel)); pull4.append(np.nanmin(rel - np.maximum.accumulate(rel)))
        cc = c[max(ii - 12, 0):ii + 1]
        green.append(float(np.mean(np.diff(cc) * sd > 0)) if len(cc) > 1 else np.nan)
    f["run_4h_atr"], f["pullback_4h_atr"], f["with_trade_share_1h"] = run4, pull4, green
    return f[list(FEATURES)]


def pre_entry_path(ctx: CoinContext, ii: int, side: float, entry: float, stop: float) -> np.ndarray:
    """24h of primary closes up to the signal bar, in R from the entry (+ = with the trade)."""
    r = abs(entry - stop)
    return ((ctx.close[max(ii - 287, 0):ii + 1] - entry) * side / r).astype("float32")


def post_entry_path(time, high, low, ii: int, exit_time: int, side: float, entry: float,
                    rpu: float) -> dict:
    """Bar high/low after the signal bar until the exit bar, in R (NOT a model input)."""
    j = np.searchsorted(time, exit_time, side="right")
    hh, ll = high[ii + 1:j], low[ii + 1:j]
    fav = ((hh if side > 0 else ll) - entry) * side / rpu
    adv = ((ll if side > 0 else hh) - entry) * side / rpu
    return dict(fav=fav.astype("float32").tolist(), adv=adv.astype("float32").tolist(),
                mfe_R=float(np.max(fav)) if len(fav) else 0.0,
                mae_R=float(np.min(adv)) if len(adv) else 0.0,
                bars_to_mfe=int(np.argmax(fav)) + 1 if len(fav) else 0,
                bars_held=int(len(fav)))


# --- the live entry point ----------------------------------------------------

def _nan_row() -> dict:
    return {k: float("nan") for k in FEATURES}


def signal_features(*, p5: pd.DataFrame | None, h1: pd.DataFrame | None,
                    h4: pd.DataFrame | None, bar_open: int, side: int,
                    entry_price: float, stop_price: float, spec,
                    btc_1m: pd.DataFrame | None = None, btc_60m: pd.DataFrame | None = None,
                    funding: pd.DataFrame | None = None, oi: pd.DataFrame | None = None,
                    picks: Mapping | pd.DataFrame | None = None, launch: int | None = None,
                    tag: str | None = None, minutes: int = 5) -> tuple[dict, list | None]:
    """(features, pre_24h) for ONE signal on the 5m bar opening at `bar_open`.

    Frames are CLOSED bars only (time = bar open). Any missing or unusable
    input leaves its readings NaN; this never raises for bad data.
    """
    errors: list[str] = []
    try:
        if p5 is None or len(p5) == 0:
            raise ValueError("no primary bars")
        ctx = prepare_coin(p5, h1, h4, spec, minutes)
        ii = int(np.searchsorted(ctx.time, int(bar_open)))
        if ii >= len(ctx.time) or int(ctx.time[ii]) != int(bar_open):
            raise ValueError(f"signal bar {bar_open} not among the primary bars")
        try:
            btc = prepare_btc(btc_1m, btc_60m, spec)
        except Exception as exc:                    # noqa: BLE001
            btc, _ = None, errors.append(f"btc: {exc}")
        with np.errstate(all="ignore"):
            row = features_at(ctx, [ii], [float(side)], [float(entry_price)], [float(stop_price)],
                              btc=btc, funding=funding, oi=oi, picks=picks, launch=launch,
                              tag=tag).iloc[0].to_dict()
            pre = pre_entry_path(ctx, ii, float(side), float(entry_price), float(stop_price))
        out = {k: _clean(row.get(k)) for k in FEATURES}
        return ({**out, **({"feature_errors": errors} if errors else {})},
                [_clean(x) for x in pre.tolist()])
    except Exception as exc:                        # noqa: BLE001
        return {**_nan_row(), "feature_errors": errors + [f"{type(exc).__name__}: {exc}"]}, None


def _clean(x) -> float:
    """A finite float, or NaN (JSON writers turn NaN into null)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return v if math.isfinite(v) else float("nan")


# --- live-only readings (Delta public REST) -----------------------------------

def book_readings(ticker: Mapping | None, book: Mapping | None,
                  contract_value: float | None) -> dict:
    """Ticker and L2 order-book readings at signal time. NaN for anything missing.

    ticker: /v2/tickers/{symbol} `result`. book: /v2/l2orderbook/{symbol}
    `result` (levels of {price, size in contracts}).
    """
    out = {k: float("nan") for k in (
        "mark_price", "last_price", "mark_last_gap_bps", "funding_rate", "oi",
        "oi_change_usd_6h", "spread_bps", "bid_usd_0p5pct", "ask_usd_0p5pct",
        "bid_usd_1pct", "ask_usd_1pct")}
    t = ticker or {}
    q = t.get("quotes") or {}
    mark, last = _num(t.get("mark_price")), _num(t.get("close"))
    out.update(mark_price=mark, last_price=last, funding_rate=_num(t.get("funding_rate")),
               oi=_num(t.get("oi")), oi_change_usd_6h=_num(t.get("oi_change_usd_6h")))
    if math.isfinite(mark) and math.isfinite(last) and last > 0:
        out["mark_last_gap_bps"] = (mark / last - 1) * 1e4
    bid, ask = _num(q.get("best_bid")), _num(q.get("best_ask"))
    if math.isfinite(bid) and math.isfinite(ask) and bid > 0 and ask >= bid:
        out["spread_bps"] = (ask - bid) / ((ask + bid) / 2) * 1e4
    b = book or {}
    buys, sells = b.get("buy") or [], b.get("sell") or []
    cv = _num(contract_value)
    if buys and sells and math.isfinite(cv) and cv > 0:
        top_b, top_a = _num(buys[0].get("price")), _num(sells[0].get("price"))
        if math.isfinite(top_b) and math.isfinite(top_a) and top_b > 0 and top_a > 0:
            mid = (top_b + top_a) / 2
            for pct, tag in ((0.005, "0p5pct"), (0.01, "1pct")):
                out[f"bid_usd_{tag}"] = sum(
                    _num(x.get("size")) * cv * _num(x.get("price")) for x in buys
                    if _num(x.get("price")) >= mid * (1 - pct))
                out[f"ask_usd_{tag}"] = sum(
                    _num(x.get("size")) * cv * _num(x.get("price")) for x in sells
                    if _num(x.get("price")) <= mid * (1 + pct))
    return {k: _clean(v) for k, v in out.items()}
