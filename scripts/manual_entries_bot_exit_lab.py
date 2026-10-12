"""The owner's hand ENTRIES with the bot's EXIT, judged against random entries with the same exit.

    PYTHONPATH=. .venv/bin/python scripts/manual_entries_bot_exit_lab.py

Each hand entry on a coin with cached candles is re-played on 1m LTP: enter at the close of the 1m bar
holding the hand entry, stop = the spec's ATR stop as of the prior closed 5m bar, target 3R, 72h cap,
stop fills at that minute's close (the engine's ltp_close rule), taker fees x1.18 GST and per-coin
slippage both legs. Control: the same replay at a random traded minute of the same coin, side and UTC
day, 50 panels. This separates entry timing from the TP/leverage structure the record was traded with.
"""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "scripts")
import labkit
from deltabt import rulecore
from deltabt.config import GST_MULTIPLIER
from deltabt.harness import _resampled

SYMS = ["AKEUSD", "BANKUSD", "BEATUSD", "ETHUSD", "SOLUSD", "BLESSUSD", "XRPUSD", "DOGEUSD"]
TAKER, TARGET_R, CAP_S, N_PANELS = 0.0005 * GST_MULTIPLIER, 3.0, 72 * 3600, 50


def replay(l, lt, hi, lo, cl, t0, side, dist, slip):
    a = np.searchsorted(lt, t0)                                              # 1m bar at/after t0; enter at its close
    if a >= len(lt) - 2 or not np.isfinite(dist) or dist <= 0:
        return np.nan
    s = 1 if side == "long" else -1; entry = cl[a] * (1 + s * slip); stop = entry - s * dist; target = entry + s * TARGET_R * dist
    b = np.searchsorted(lt, lt[a] + CAP_S, side="right"); H, L, C = hi[a + 1:b], lo[a + 1:b], cl[a + 1:b]
    if len(C) == 0:
        return np.nan
    hit_stop = np.flatnonzero(L <= stop) if s > 0 else np.flatnonzero(H >= stop)
    hit_tgt = np.flatnonzero(H >= target) if s > 0 else np.flatnonzero(L <= target)
    ks = hit_stop[0] if len(hit_stop) else len(C); kt = hit_tgt[0] if len(hit_tgt) else len(C)
    if ks <= kt and ks < len(C): exit_px = C[ks] * (1 - s * slip)             # stop first on a tie: the conservative read
    elif kt < len(C): exit_px = target * (1 - s * slip)
    else: exit_px = C[-1] * (1 - s * slip)
    fees = TAKER * (entry + exit_px)
    return (s * (exit_px - entry) - fees) / dist


def main():
    T = pd.read_csv("data/manual/round_trips.csv", parse_dates=["t_in", "t_out"]); T = T[T.symbol.isin(SYMS)].copy()
    T["ein"] = T.t_in.map(lambda x: int(x.timestamp())); data = labkit.load_window("data", SYMS); cache = {}; rng = np.random.default_rng(7)
    hand, ctrl, days = [], [[] for _ in range(N_PANELS)], []
    for s in SYMS:
        d = data[s]; l = d["ltp"]; lt = l.time.to_numpy("int64"); hi, lo, cl = l.high.to_numpy(float), l.low.to_numpy(float), l.close.to_numpy(float)
        P, _, _ = _resampled(d, 5, cache); C = _resampled(d, 1, cache)[0]; sa = rulecore.compute(P, C, labkit.SPEC)
        bt = sa.time.astype("int64"); dist_long = sa.close - sa.stop_long; dist_short = sa.stop_short - sa.close; slip = labkit.slip(s) / 1e4
        if s == SYMS[0]:
            print("stop distance / ATR on the prior bar (should be the spec multiplier): median %.2f" % np.nanmedian(dist_long / sa.atr))
        for r in T[T.symbol == s].itertuples():
            kb = np.searchsorted(bt, r.ein, side="right") - 2                   # prior CLOSED 5m bar
            if kb < 0: continue
            dist = (dist_long if r.side == "long" else dist_short)[kb]
            hand.append(replay(l, lt, hi, lo, cl, r.ein, r.side, dist, slip)); days.append(r.ein // 86400)
            d0 = r.ein // 86400 * 86400; m = (lt >= d0) & (lt < d0 + 86400); cand = lt[m]
            for j in range(N_PANELS):
                t0 = int(rng.choice(cand)); kb2 = np.searchsorted(bt, t0, side="right") - 2
                dist2 = (dist_long if r.side == "long" else dist_short)[kb2] if kb2 >= 0 else np.nan
                ctrl[j].append(replay(l, lt, hi, lo, cl, t0, r.side, dist2, slip))
    H = pd.DataFrame(dict(r=hand, day=days, side=[r.side for r in T.itertuples()][:len(hand)])).dropna()
    g = H.groupby("day").r.agg(["sum", "count"]); n = g["count"].sum(); mu = g["sum"].sum() / n
    se = np.sqrt(((g["sum"] - mu * g["count"]) ** 2).sum()) / n; t = mu / se if se > 0 else np.nan
    ok = np.isfinite(np.array(hand)); null = np.array([np.nanmean(np.array(c)[ok]) for c in ctrl]); p = float(np.mean(null >= mu))
    print("\nhand entries with the bot's exit: n %d, mean %+.3fR, win %.0f%%, total %+.1fR, day-clustered t %+.2f" % (len(H), mu, 100 * (H.r > 0).mean(), H.r.sum(), t))
    print("random entries, same coin/side/day, same exit: mean of %d panels %+.3fR (5-95%%: %+.3f..%+.3f); p(random >= hand) = %.2f" % (len(null), null.mean(), *np.percentile(null, [5, 95]), p))
    print("by side:"); print(H.groupby("side").r.agg(n="size", mean="mean", win=lambda x: (x > 0).mean()).round(3).to_string())


if __name__ == "__main__":
    main()
