"""%R(140) band crosses with the opposite band as the target (owner 2026-11-14: "wpr at 140 when it drops from -20
towards -80 (sell), when it rises from -80 to -20 (buy) mostly targets are hit"; timeframe and coins left to me).

    PYTHONPATH=. python scripts/wpr_band.py            # 10 large coins (out/wpr/, scripts/wpr_data.py)
    PYTHONPATH=. python scripts/wpr_band.py --holdout  # the 10 held-out coins, the chosen rows only

RULE. Williams %R over 140 bars on 5m / 15m / 1h (15m and 1h resampled from Binance 5m perp candles).
  SELL when %R closes at or below -20 having closed above it the bar before; BUY when it closes at or above -80
  having closed below it. Entry at that bar's close. One position per coin at a time (signals while in a trade are
  ignored).
  TARGET: %R closes at the other band (-80 for a sell, -20 for a buy): exit at that bar's close.
  STOP (variants): the 140-bar high (sell) / low (buy) at entry x/÷ 1.001 = the edge of the range; 2x ATR(14);
  4x ATR(14). Hit when a later bar's high / low reaches it; fills at the worse of the stop and that bar's close.
  A stop and a target on the same bar = the stop. TIME: out at the close 140 bars after entry.
  COSTS: 0.05% fee a side + 0.05% slippage = 0.15% round trip (Delta taker on large coins).
R = net return / stop distance.
PASS BAR (written before the run): over 2024-2026 mean R > 0 in each year AND t >= 2, on the 10 large coins; then
the passing rows must also be > 0 on the 10 held-out coins (never looked at before).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import trend_data as T  # noqa: E402

IN = Path("out/wpr")
N = 140
COST = 0.0015
YEARS = (2021, 2022, 2023, 2024, 2025, 2026)
TEST = (2024, 2025, 2026)


def load(sym: str, tf: str) -> pd.DataFrame | None:
    f = IN / f"{sym}_5m.parquet"
    if not f.exists():
        return None
    d = pd.read_parquet(f)
    if tf != "5m":
        sec = {"15m": 900, "1h": 3600}[tf]
        g = d.groupby(d.time // sec * sec)
        d = pd.DataFrame({"time": g.time.first() // sec * sec, "open": g.open.first(), "high": g.high.max(),
                          "low": g.low.min(), "close": g.close.last()}).reset_index(drop=True)
    return d


def supertrend(h, l, c, period=10, mult=2.0) -> np.ndarray:
    """+1 bullish / -1 bearish per bar: SuperTrend(10, 2), ATR as Wilder's RMA of the true range (TradingView)."""
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    atr = pd.Series(tr).ewm(alpha=1 / period, adjust=False).mean().to_numpy()
    mid = (h + l) / 2
    ub, lb = mid + mult * atr, mid - mult * atr
    fu, fl = ub.copy(), lb.copy()
    dirn = np.ones(len(c))
    for i in range(1, len(c)):
        fu[i] = ub[i] if (ub[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lb[i] if (lb[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
        if dirn[i - 1] < 0:
            dirn[i] = 1 if c[i] > fu[i - 1] else -1
        else:
            dirn[i] = -1 if c[i] < fl[i - 1] else 1
    dirn[:period] = 0
    return dirn


def trades(d: pd.DataFrame, stop_kind: str, st_mode: str = "off", take: float | None = None) -> list[tuple]:
    h, l, c = d.high.to_numpy(float), d.low.to_numpy(float), d.close.to_numpy(float)
    hh = pd.Series(h).rolling(N).max().to_numpy()
    ll = pd.Series(l).rolling(N).min().to_numpy()
    wr = -100 * (hh - c) / np.where(hh > ll, hh - ll, np.nan)
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    atr = pd.Series(tr).rolling(14).mean().to_numpy()
    yrs = pd.to_datetime(d.time, unit="s").dt.year.to_numpy()
    st = supertrend(h, l, c) if st_mode != "off" else None
    out, i, n = [], N + 1, len(c)
    while i < n - 1:
        side = 0
        if wr[i - 1] > -20 >= wr[i]:
            side = -1
        elif wr[i - 1] < -80 <= wr[i]:
            side = 1
        if st is not None and side != 0:
            if (st_mode == "aligned" and st[i] != side) or (st_mode == "against" and st[i] != -side):
                side = 0
        if side == 0 or not np.isfinite(atr[i]):
            i += 1
            continue
        e = c[i]
        if stop_kind == "range":
            sp = hh[i] * 1.001 if side < 0 else ll[i] / 1.001
        else:
            k = 2.0 if stop_kind == "2atr" else 4.0
            sp = e + k * atr[i] if side < 0 else e - k * atr[i]
        dist = abs(sp / e - 1)
        if not dist > 0:
            i += 1
            continue
        end = min(i + N, n - 1)
        j, how, xp = end, "time", c[end]
        for k_ in range(i + 1, end + 1):
            hit_s = h[k_] >= sp if side < 0 else l[k_] <= sp
            if take is None:
                hit_t = (wr[k_] <= -80) if side < 0 else (wr[k_] >= -20)
                tpx = c[k_]
            else:
                tpx = e * (1 - take) if side < 0 else e * (1 + take)
                hit_t = (l[k_] <= tpx * 0.9995) if side < 0 else (h[k_] >= tpx * 1.0005)   # must trade 0.05% through
            if hit_s:
                j, how, xp = k_, "stop", (max(sp, c[k_]) if side < 0 else min(sp, c[k_]))
                break
            if hit_t:
                j, how, xp = k_, "target", tpx
                break
        ret = side * (xp / e - 1) - COST
        out.append((yrs[i], side, ret / dist, how, ret, dist, j - i, int(d.time.iat[i]), i, j))
        i = j + 1
    return out


def summary(label: str, rows: list[tuple], ncoins: int) -> bool:
    if len(rows) < 30:
        print(f"  {label:34s} n {len(rows)}: too few")
        return False
    y = np.array([r[0] for r in rows])
    r = np.array([r[2] for r in rows])
    how = np.array([r[3] for r in rows])
    net = np.array([r[4] for r in rows])
    dist = np.array([r[5] for r in rows])
    bars = np.array([r[6] for r in rows])
    te = np.isin(y, TEST)
    rt = r[te]
    t = rt.mean() / (rt.std(ddof=1) / np.sqrt(len(rt))) if len(rt) > 2 else 0.0
    by = [r[y == yy].mean() if (y == yy).any() else np.nan for yy in YEARS]
    ok = all(np.isfinite(by[k]) and by[k] > 0 for k in (3, 4, 5)) and t >= 2
    yrs_span = max(1e-9, (len(np.unique(y)) or 1))
    print(f"  {label:34s} n {len(r):6d} ({len(r) / yrs_span / ncoins:4.0f}/coin/yr) target {100 * (how == 'target').mean():3.0f}% "
          f"stop {100 * (how == 'stop').mean():3.0f}% time {100 * (how == 'time').mean():3.0f}% win {100 * (net > 0).mean():3.0f}% | "
          f"stop {100 * np.median(dist):4.2f}% hold {np.median(bars):3.0f} bars | {r.mean():+.3f}R ({100 * net.mean():+.3f}%/trade) "
          f"| 2024-26 {rt.mean():+.3f}R t {t:+5.2f} | " + " ".join(f"{v:+.3f}" if np.isfinite(v) else "  n/a " for v in by)
          + f"  {'PASS' if ok else 'fail'}")
    return ok


def main() -> None:
    hold = "--holdout" in sys.argv
    if "--st" in sys.argv:
        return main_st(hold)
    if "--take" in sys.argv:
        return main_take(hold)
    coins = T.HOLDOUT if hold else T.COINS
    print(f"%R({N}) BAND CROSS, target = the other band. {'HELD-OUT' if hold else 'LARGE'} COINS: {', '.join(c[:-4] for c in coins)}")
    print(f"Binance perp candles 2021-01..2026-10, costs {100 * COST:.2f}% round trip. By year: " + " ".join(map(str, YEARS)) + "\n")
    for tf in ("5m", "15m", "1h"):
        data = {s: load(s, tf) for s in coins}
        data = {s: d for s, d in data.items() if d is not None}
        print(f"{tf} ({len(data)} coins)")
        for sk in ("range", "2atr", "4atr"):
            rows = [r for d in data.values() for r in trades(d, sk)]
            for side, nm in ((None, "both"), (-1, "sell only"), (1, "buy only")):
                sub = rows if side is None else [r for r in rows if r[1] == side]
                summary(f"stop {sk:5s} {nm}", sub, len(data))
        print()


def main_take(hold: bool) -> None:
    """Owner 2026-11-14: "exits are mostly capped at 500$" -- a fixed take-profit instead of the opposite band.
    $500 = a price move of 500 / position: 0.25% .. 2% for a $100k .. $25k position."""
    coins = T.HOLDOUT if hold else T.COINS
    print(f"%R({N}) BAND CROSS, FIXED TAKE-PROFIT (must trade 0.05% through). {'HELD-OUT' if hold else 'LARGE'} COINS: "
          f"{', '.join(c[:-4] for c in coins)}\nBinance perp candles 2021-01..2026-10, costs {100 * COST:.2f}% round trip.\n")
    for tf in ("5m", "15m"):
        data = {s: load(s, tf) for s in coins}
        data = {s: d for s, d in data.items() if d is not None}
        print(f"{tf} ({len(data)} coins)")
        for mode in ("off", "aligned"):
            for sk in ("range", "4atr"):
                for tk in (0.0025, 0.005, 0.01, 0.02):
                    rows = [r for d in data.values() for r in trades(d, sk, mode, tk)]
                    summary(f"ST {mode:7s} stop {sk:5s} take {100 * tk:4.2f}%", rows, len(data))
        print()


def main_st(hold: bool) -> None:
    """Owner 2026-11-14: "how about we add super trend 10,2 filter?" Same rule, entries only when SuperTrend(10, 2)
    on the same timeframe agrees (sell while bearish, buy while bullish); 'against' = the opposite, for comparison."""
    coins = T.HOLDOUT if hold else T.COINS
    print(f"%R({N}) BAND CROSS + SUPERTREND(10, 2) FILTER (same timeframe). {'HELD-OUT' if hold else 'LARGE'} COINS: "
          f"{', '.join(c[:-4] for c in coins)}\nBinance perp candles 2021-01..2026-10, costs {100 * COST:.2f}% round trip. "
          "By year: " + " ".join(map(str, YEARS)) + "\n")
    for tf in ("5m", "15m", "1h"):
        data = {s: load(s, tf) for s in coins}
        data = {s: d for s, d in data.items() if d is not None}
        print(f"{tf} ({len(data)} coins)")
        for mode in ("aligned", "against"):
            for sk in ("range", "2atr", "4atr"):
                rows = [r for d in data.values() for r in trades(d, sk, mode)]
                summary(f"ST {mode:7s} stop {sk:5s}", rows, len(data))
        print()


if __name__ == "__main__":
    main()
