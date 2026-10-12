"""Liquidation-cascade reversion, as pre-registered in docs/liquidation_reversion_prereg.md (2026-10-13).

    PYTHONPATH=. .venv/bin/python scripts/liquidation_reversion_lab.py count   # step 0 only, no outcomes
    PYTHONPATH=. .venv/bin/python scripts/liquidation_reversion_lab.py run     # trades, controls, verdict

A small dedicated simulator (the engine has no per-trade target): minute-by-
minute after a taker entry, stop on mark -> LTP close less slippage (taker),
target as a resting limit (maker), 24h time stop (taker), funding at each 8h
settlement crossed. Same fill model the engine uses for stops.
"""
import gc, pathlib, sys
import numpy as np, pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "scripts")
import labkit as K
from deltabt.config import GST_MULTIPLIER
from deltabt.data.store import ProductCatalog

H, DAY = 3600, 86400; K_RET, K_VOL, OI_DROP, LIQ = 3.0, 3.5, -0.03, 1e6; ATR_N, VOL_N, VWAP_N, WAIT, HOLD_H = 14, 720, 20, 3, 24
MIN_EVENTS, MIN_DAYS, N_RANDOM = 50, 25, 50
OUT = pathlib.Path("out/sweep/five_min_arm_lab/liquidation_reversion"); OUT.mkdir(parents=True, exist_ok=True)
CAT = ProductCatalog()


def wilder_atr(h, l, c, n):
    pc = np.concatenate([[np.nan], c[:-1]]); tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc))); atr = np.full(len(c), np.nan)
    if len(c) > n:
        atr[n] = np.nanmean(tr[1:n + 1])
        for i in range(n + 1, len(c)):
            atr[i] = (atr[i - 1] * (n - 1) + tr[i]) / n
    return atr


def hourly(d, s):
    """1h bars from Delta 1m, with Binance volume / dollar volume and OI at bar start and end."""
    l = d["ltp"]; b = l.time.to_numpy("int64") // H * H
    P = l.groupby(b).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"), dvol=("volume", "sum"), n=("time", "size"))
    P = P[P.n >= 18].reset_index().rename(columns={"index": "time"}); P.columns = ["time", "open", "high", "low", "close", "dvol", "n"]
    k = pd.read_parquet(f"data/binance/{s}/klines_1m.parquet"); kb = k.open_time.to_numpy("int64") // H * H
    kv = k.groupby(kb).agg(bvol=("volume", "sum"), usd=("quote_volume", "sum")); P = P.join(kv, on="time")
    m = pd.read_parquet(f"data/binance/{s}/metrics_5m.parquet"); mt = m.time.to_numpy("int64"); mo = m.sum_open_interest.to_numpy(float)
    def oi_at(x):
        j = np.searchsorted(mt, x, side="right") - 1; ok = (j >= 0) & (mt[np.maximum(j, 0)] >= x - 2 * H)
        return np.where(ok, mo[np.maximum(j, 0)], np.nan)
    t = P.time.to_numpy("int64"); P["oi0"] = oi_at(t); P["oi1"] = oi_at(t + H - 1)
    P["atr"] = wilder_atr(P.high.to_numpy(float), P.low.to_numpy(float), P.close.to_numpy(float), ATR_N)
    P["vol_avg"] = P.bvol.rolling(VOL_N, min_periods=240).mean().shift(1)
    daily = P.groupby(P.time // DAY).usd.sum(); liq = daily.rolling(30, min_periods=10).median().shift(1)
    P["liq"] = (P.time // DAY).map(liq)
    pv = (P.close * P.bvol).rolling(VWAP_N).sum(); vv = P.bvol.rolling(VWAP_N).sum(); P["vwap"] = pv / vv
    return P


def events(P, s):
    c = P.close.to_numpy(float); ret = np.concatenate([[np.nan], np.diff(c)]); atr = P.atr.to_numpy(float)
    big_dn = ret <= -K_RET * atr; big_up = ret >= K_RET * atr
    vol_ok = P.bvol.to_numpy(float) >= K_VOL * P.vol_avg.to_numpy(float)
    oi_ok = (P.oi1.to_numpy(float) / P.oi0.to_numpy(float) - 1) <= OI_DROP
    liq_ok = P.liq.to_numpy(float) >= LIQ
    cand = np.flatnonzero((big_dn | big_up) & vol_ok & oi_ok & liq_ok & np.isfinite(atr))
    out, last = [], -10 ** 12
    for i in cand:
        if P.time.iat[i] - last >= DAY:
            out.append(dict(symbol=s, i=int(i), time=int(P.time.iat[i]), side=1 if big_dn[i] else -1)); last = P.time.iat[i]
    return out


def plan_trade(P, ev):
    """Exhaustion entry, stop, frozen VWAP target. None if no entry."""
    i, side = ev["i"], ev["side"]; ext = P.low.iat[i] if side > 0 else P.high.iat[i]; atr = P.atr.iat[i]
    for j in range(i + 1, min(i + 1 + WAIT, len(P))):
        if (side > 0 and P.low.iat[j] >= ext) or (side < 0 and P.high.iat[j] <= ext):
            entry = float(P.close.iat[j]); stop = ext - side * 0.5 * atr
            if side * (entry - stop) < 1.0 * atr:
                stop = entry - side * 1.0 * atr
            target = float(P.vwap.iat[j])
            if not np.isfinite(target) or side * (target - entry) <= 0:
                return None
            return dict(symbol=ev["symbol"], side=side, entry_time=int(P.time.iat[j]) + H, entry=entry, stop=float(stop), target=target,
                        stop_atr=float(side * (entry - stop) / atr), tgt_atr=float(side * (target - entry) / atr), event_time=ev["time"], atr=float(atr))
        if (side > 0 and P.low.iat[j] < ext) or (side < 0 and P.high.iat[j] > ext):
            ext = P.low.iat[j] if side > 0 else P.high.iat[j]        # cascade continues: track the new extreme
    return None


def simulate(tr, d, s, slip_mult=1.0):
    """Minute-by-minute resolution. Returns dict with gross R, net R, exit reason."""
    spec = CAT.get(s); taker = float(spec["taker_fee"]) * GST_MULTIPLIER; maker = float(spec["maker_fee"]) * GST_MULTIPLIER
    slip = slip_mult * K.slip(s) / 1e4; fi = float(spec.get("funding_interval_seconds") or 28800)
    l = d["ltp"]; mk = d["mark"]; t0 = tr["entry_time"]; side = tr["side"]
    lt = l.time.to_numpy("int64"); a = np.searchsorted(lt, t0); b = np.searchsorted(lt, t0 + HOLD_H * H)
    if a >= len(lt) or b - a < 10:
        return None
    lc, lh, ll = l.close.to_numpy(float)[a:b], l.high.to_numpy(float)[a:b], l.low.to_numpy(float)[a:b]; tt = lt[a:b]
    if mk is not None:
        mt = mk.time.to_numpy("int64"); ma = np.searchsorted(mt, t0); mb = np.searchsorted(mt, t0 + HOLD_H * H)
        mlo = pd.Series(mk.low.to_numpy(float)[ma:mb], index=mt[ma:mb]).reindex(tt).ffill().to_numpy(); mhi = pd.Series(mk.high.to_numpy(float)[ma:mb], index=mt[ma:mb]).reindex(tt).ffill().to_numpy()
    else:
        mlo, mhi = ll, lh
    entry = lc[0] * (1 + side * slip); stop, target = tr["stop"], tr["target"]; rpu = side * (entry - stop)
    if rpu <= 0:
        return None
    exit_px, reason, k_exit = None, "time", len(tt) - 1
    for k in range(1, len(tt)):
        hit_stop = (mlo[k] <= stop) if side > 0 else (mhi[k] >= stop)
        hit_tgt = (lh[k] >= target) if side > 0 else (ll[k] <= target)
        if hit_stop:
            exit_px, reason, k_exit = lc[k] * (1 - side * slip), "stop", k; break
        if hit_tgt:
            exit_px, reason, k_exit = target, "target", k; break
    if exit_px is None:
        exit_px = lc[-1] * (1 - side * slip)
    fee_rate = taker + (maker if reason == "target" else taker)
    fees = fee_rate * (entry + exit_px) / 2 / rpu                                      # in R, per unit
    fd = d["funding"]; fund_r = 0.0
    if len(fd):
        ft = fd.time.to_numpy("int64"); fr = fd.close.to_numpy(float)
        for st in range(int(np.ceil(t0 / fi)) * int(fi), int(tt[k_exit]) + 1, int(fi)):
            j = np.searchsorted(ft, st, side="right") - 1
            if j >= 0:
                fund_r += side * (fr[j] / 100.0) * entry / rpu
    gross = side * (exit_px - entry) / rpu
    return dict(**tr, exit_time=int(tt[k_exit]), exit=float(exit_px), reason=reason, gross_r=float(gross), net_r=float(gross - fees - fund_r), fees_r=float(fees), funding_r=float(fund_r))


def window_trades(root, slip_mult=1.0, random_seed=None, real=None):
    data = K.load_window(root, K.SCREEN); rows = []; evs = []
    for s, d in data.items():
        if not (pathlib.Path("data/binance") / s / "klines_1m.parquet").exists():
            continue
        P = hourly(d, s); ev = events(P, s); evs += ev
        if random_seed is None:
            for e in ev:
                tr = plan_trade(P, e)
                if tr:
                    r = simulate(tr, d, s, slip_mult)
                    if r: rows.append(r)
        else:                                                           # matched-geometry random control
            rng = np.random.default_rng(random_seed + hash(s) % 1000); mine = [r for r in real if r["symbol"] == s]
            ev_t = np.array([e["time"] for e in ev]); ok = np.isfinite(P.atr.to_numpy(float)) & np.isfinite(P.vwap.to_numpy(float))
            idx = np.flatnonzero(ok)
            idx = idx[np.array([np.all(np.abs(P.time.iat[i] - ev_t) > DAY) if len(ev_t) else True for i in idx])] if len(idx) else idx
            for r in mine:
                if not len(idx): break
                j = int(rng.choice(idx)); entry = float(P.close.iat[j]); atr = float(P.atr.iat[j]); side = r["side"]
                tr = dict(symbol=s, side=side, entry_time=int(P.time.iat[j]) + H, entry=entry, stop=entry - side * r["stop_atr"] * atr, target=entry + side * r["tgt_atr"] * atr,
                          stop_atr=r["stop_atr"], tgt_atr=r["tgt_atr"], event_time=int(P.time.iat[j]), atr=atr)
                x = simulate(tr, d, s, slip_mult)
                if x: rows.append(x)
    del data; gc.collect()
    return pd.DataFrame(rows), pd.DataFrame(evs)


def one_slot(T):
    T = T.sort_values("entry_time"); keep, busy = [], -1
    for _, r in T.iterrows():
        if r.entry_time >= busy:
            keep.append(r.name); busy = r.exit_time
    return T.loc[keep]


def day_t(x, days):
    g = pd.DataFrame(dict(x=x, d=days)).groupby("d").x.agg(["sum", "count"]); n = g["count"].sum(); mu = g["sum"].sum() / n
    se = np.sqrt(((g["sum"] - mu * g["count"]) ** 2).sum()) / n; return mu, (mu / se if se > 0 else np.nan)


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "count":
        print("STEP 0 -- event counts (no outcomes read)")
        for win, root in K.WINDOWS.items():
            data = K.load_window(root, K.SCREEN); tot = []
            for s, d in data.items():
                if (pathlib.Path("data/binance") / s / "klines_1m.parquet").exists():
                    tot += events(hourly(d, s), s)
            E = pd.DataFrame(tot); days = E.time.floordiv(DAY).nunique() if len(E) else 0
            print(f"  {win}: {len(E)} events on {days} distinct days, {E.symbol.nunique() if len(E) else 0} coins; longs {int((E.side > 0).sum()) if len(E) else 0}, "
                  f"shorts {int((E.side < 0).sum()) if len(E) else 0}; floors {MIN_EVENTS} events / {MIN_DAYS} days -> {'OK' if len(E) >= MIN_EVENTS and days >= MIN_DAYS else 'CANNOT ANSWER'}")
            if len(E): print("     by coin: " + ", ".join(f"{s} {n}" for s, n in E.symbol.value_counts().head(12).items()))
            del data; gc.collect()
        sys.exit(0)
    verdict = {}
    for win, root in K.WINDOWS.items():
        T, E = window_trades(root); days = E.time.floordiv(DAY).nunique() if len(E) else 0
        print(f"\n==== {win}: {len(E)} events / {days} days -> {len(T)} trades taken (exhaustion entry found and target beyond entry) ====")
        if len(E) < MIN_EVENTS or days < MIN_DAYS:
            print("  CANNOT ANSWER (below floors)"); verdict[win] = None; continue
        T.to_csv(OUT / f"trades_{win[:3]}.csv", index=False)
        null = []
        for j in range(N_RANDOM):
            R, _ = window_trades(root, random_seed=5000 + j, real=T.to_dict("records")); null.append(R.net_r.mean() if len(R) else np.nan)
        null = np.array(null); mu_g, t_g = day_t(T.gross_r, T.event_time // DAY); mu_n, t_n = day_t(T.net_r, T.event_time // DAY)
        p = float(np.nanmean(null >= mu_n)); S = one_slot(T); Tx, _ = window_trades(root, slip_mult=2.0)
        print(f"  per coin: gross {mu_g:+.3f}R (day-t {t_g:+.2f})  net {mu_n:+.3f}R (day-t {t_n:+.2f})  win {100 * (T.net_r > 0).mean():.0f}%  "
              f"exits {T.reason.value_counts().to_dict()}  median stop {T.stop_atr.median():.2f} ATR, target {T.tgt_atr.median():.2f} ATR  fees {T.fees_r.mean():.3f}R  funding {T.funding_r.mean():+.3f}R")
        print(f"  random x{N_RANDOM} (matched geometry): mean {np.nanmean(null):+.3f}R, 5-95% {np.nanpercentile(null, 5):+.3f}..{np.nanpercentile(null, 95):+.3f}; p(random >= real) {p:.2f}")
        print(f"  one position at a time: {len(S)} trades, net {S.net_r.mean():+.3f}R  |  2x spread: net {Tx.net_r.mean():+.3f}R ({len(Tx)} trades)")
        print(f"  longs {T[T.side > 0].net_r.mean():+.3f}R (n {int((T.side > 0).sum())})  shorts {T[T.side < 0].net_r.mean():+.3f}R (n {int((T.side < 0).sum())})")
        G1, G2 = mu_g > 0, mu_n > 0 and p <= 0.05
        print(f"  G1 gross > 0: {'PASS' if G1 else 'fail'}   G2 net > 0 and p <= 0.05: {'PASS' if G2 else 'fail'}"); verdict[win] = bool(G1 and G2)
    print("\nLIQUIDATION REVERSION:", "PASS" if all(v is True for v in verdict.values()) else "FAIL" if any(v is False for v in verdict.values()) else "CANNOT ANSWER")
