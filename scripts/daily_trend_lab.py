"""Multi-day trend on 12h / 1D bars, as pre-registered in docs/daily_trend_prereg.md (2026-10-13).

    PYTHONPATH=. .venv/bin/python scripts/daily_trend_lab.py preflight   # checklist + BTCUSD smoke log
    PYTHONPATH=. .venv/bin/python scripts/daily_trend_lab.py run         # the four cells, both windows
"""
import dataclasses, gc, sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
import numpy as np, pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "scripts")
import labkit as K
from deltabt.config import StrategyParams
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.portfolio import Book, RiskGates, run_portfolio
from deltabt.strategy import Signals

N, ATR_N, K_STOP, K_MOM, HOLD = 20, 14, 2.5, 1.5, 20
CELLS = {"A-12h": ("donchian", 720), "A-1D": ("donchian", 1440), "B-12h": ("atrmom", 720), "B-1D": ("atrmom", 1440)}
N_RANDOM = 50
BOOKS, DATA = {}, {}


def bars(ltp, minutes, min_share=0.30):
    step = minutes * 60; b = ltp.time.to_numpy("int64") // step * step
    g = ltp.groupby(b).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"), volume=("volume", "sum"), n=("time", "size"))
    g = g[g.n >= min_share * minutes].reset_index().rename(columns={"index": "time", "time": "time"})
    g.columns = ["time", "open", "high", "low", "close", "volume", "n"] if "time" not in g.columns[:1] else g.columns
    return g.reset_index(drop=True)


def wilder_atr(h, l, c, n):
    pc = np.concatenate([[np.nan], c[:-1]]); tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = np.full(len(c), np.nan)
    if len(c) > n:
        atr[n] = np.nanmean(tr[1:n + 1])
        for i in range(n + 1, len(c)):
            atr[i] = (atr[i - 1] * (n - 1) + tr[i]) / n
    return atr


def signals(P, arm, entries=None):
    h, l, c = P.high.to_numpy(float), P.low.to_numpy(float), P.close.to_numpy(float); n = len(c); atr = wilder_atr(h, l, c, ATR_N)
    if entries is None:
        if arm == "donchian":
            hh = pd.Series(h).rolling(N).max().shift(1).to_numpy(); ll = pd.Series(l).rolling(N).min().shift(1).to_numpy()
            le, se = c > hh, c < ll
        else:
            prev = np.concatenate([np.full(N, np.nan), c[:-N]]); cl = (c - prev) > K_MOM * atr; cs = (prev - c) > K_MOM * atr
            le = cl & ~np.concatenate([[False], cl[:-1]]); se = cs & ~np.concatenate([[False], cs[:-1]])
        le = le & np.isfinite(atr); se = se & np.isfinite(atr)
    else:
        le, se = entries
    z = np.zeros(n, bool); nan = np.full(n, np.nan)
    return Signals(long_entry=le, short_entry=se, stop_long=c - K_STOP * atr, stop_short=c + K_STOP * atr, supertrend=nan.copy(),
                   direction=np.zeros(n), atr=atr, wpr=nan.copy(), adx_1m=nan.copy(), adx_5m=nan.copy(), bull_1m=z.copy(), bear_1m=z.copy(),
                   long_base=z.copy(), short_base=z.copy(), warmup=N + ATR_N)


def build(root, minutes, syms):
    data = K.load_window(root, syms); cat = ProductCatalog(); out = {}
    for s, d in data.items():
        P = bars(d["ltp"], minutes)
        if len(P) < N + ATR_N + 10:
            continue
        mk = bars(d["mark"], minutes, 0.0) if d["mark"] is not None else P
        c = replace(SymbolCosts.from_spec(cat.get(s)), slippage_bps=K.slip(s))
        out[s] = dict(P=P, mark=mk[["time", "open", "high", "low", "close"]], costs=c, ltp=d["ltp"], mark1m=d["mark"], funding=d["funding"])
    return out


def params(minutes):
    return StrategyParams(base_minutes=minutes, confirm_minutes=minutes + 1, max_hold_bars=HOLD, cooldown_bars=0, exit_on_trend_flip=False,
                          reward_risk=100.0, stop_fill="ltp_close", trail_after_r=0.0, trail_r=1.0, risk_percent=1.0, max_cost_per_r=None)


def run(key, arm, rand_seed=None):
    coins, minutes = BOOKS[key]; bks = {}; rng = np.random.default_rng(rand_seed) if rand_seed is not None else None
    for s, b in coins.items():
        sig = signals(b["P"], arm)
        if rng is not None:                                   # random control: same count per side, random bars after warm-up
            n = len(b["P"]); idx = np.arange(sig.warmup, n); le = np.zeros(n, bool); se = np.zeros(n, bool)
            if len(idx):
                le[rng.choice(idx, min(int(sig.long_entry.sum()), len(idx)), replace=False)] = True
                se[rng.choice(idx, min(int(sig.short_entry.sum()), len(idx)), replace=False)] = True
            sig = signals(b["P"], arm, entries=(le, se))
        bks[s] = Book(symbol=s, bars=b["P"], signals=sig, costs=b["costs"], mark=b["mark"], tradable=None, fill_ltp=b["ltp"], fill_mark=b["mark1m"])
    res = run_portfolio(bks, params(minutes), replace(RiskGates.off(), max_open_positions=len(bks)), initial_capital=10_000.0,
                        funding={s: coins[s]["funding"] for s in bks})
    t = pd.DataFrame([dataclasses.asdict(x) for x in res.trades])
    if len(t):
        t["day"] = t.entry_time // 86400
    return t


def _rand(args):
    return run(*args).r_multiple.mean() if len(run(*args)) else np.nan


def preflight():
    print("PRE-FLIGHT (docs/daily_trend_prereg.md)")
    d = K.load_window("data", ["BTCUSD"])["BTCUSD"]
    for mins in (720, 1440):
        P = bars(d["ltp"], mins); off = (P.time % (mins * 60) != 0).sum()
        gap = np.abs(P.open.to_numpy()[1:] / P.close.to_numpy()[:-1] - 1)
        print(f"  1. {mins}m bars: {len(P)} bars, {off} off the UTC boundary; first {pd.to_datetime(P.time.iloc[0], unit='s')} | "
              f"2. next-open vs signal-close gap: median {100 * np.median(gap):.4f}%, p99 {100 * np.percentile(gap, 99):.3f}%")
    print("  2. stop/target checks start the bar AFTER entry (engine: entries are processed after exits each bar).")
    for win, root in K.WINDOWS.items():
        b = build(root, 1440, K.SCREEN)
        first = {s: pd.to_datetime(v['P'].time.iloc[0], unit='s').strftime('%Y-%m-%d') for s, v in b.items()}
        print(f"  4. {win}: {len(b)} coins with 1D bars; earliest first bars: " + ", ".join(f"{s} {t}" for s, t in sorted(first.items(), key=lambda x: x[1])[:3])
              + "; latest: " + ", ".join(f"{s} {t}" for s, t in sorted(first.items(), key=lambda x: x[1])[-3:]))
        del b; gc.collect()
    print(f"  5. cells: {CELLS}; N {N}, ATR {ATR_N}, stop {K_STOP}xATR trailing, momentum {K_MOM}xATR, hold {HOLD} bars. No grid.")
    BOOKS["smoke"] = (build("data", 1440, ["BTCUSD"]), 1440)
    t = run("smoke", "donchian")
    print(f"  6. BTCUSD 1D Donchian smoke: {len(t)} trades, funding charged on {int((t.funding != 0).sum())}; first three recomputed by hand:")
    cv = BOOKS["smoke"][0]["BTCUSD"]["costs"].contract_value
    for _, x in t.head(3).iterrows():
        risk = x.risk_per_unit * x.contracts * cv; gross = x.side * (x.exit_price - x.entry_price) * x.contracts * cv
        manual = (gross - x.fees - x.funding) / risk
        print(f"     {x.side:+d} {pd.to_datetime(x.entry_time, unit='s'):%Y-%m-%d} -> {pd.to_datetime(x.exit_time, unit='s'):%Y-%m-%d} {x.exit_reason:6s} entry {x.entry_price:.1f} exit {x.exit_price:.1f} "
              f"stop0 {x.stop_price:.1f} contracts {x.contracts} fees ${x.fees:.2f} funding ${x.funding:+.2f} | engine R {x.r_multiple:+.3f} manual R {manual:+.3f} {'OK' if abs(manual - x.r_multiple) < 1e-6 else 'MISMATCH'}")


def full():
    cells = {}
    for win, root in K.WINDOWS.items():
        for mins in (720, 1440):
            BOOKS[(win, mins)] = (build(root, mins, K.SCREEN), mins)
            for cell, (arm, m) in CELLS.items():
                if m != mins:
                    continue
                t = run((win, mins), arm); months = (max(v["P"].time.max() for v in BOOKS[(win, mins)][0].values()) - min(v["P"].time.min() for v in BOOKS[(win, mins)][0].values())) / (30 * 86400)
                with ProcessPoolExecutor(10) as pool:
                    null = np.array(list(pool.map(_rand, [((win, mins), arm, 900 + j) for j in range(N_RANDOM)])))
                cells[(cell, win)] = (t, null)
                r = t.r_multiple
                print(f"\n== {cell} | {win}: {len(t)} trades ({len(t) / months:.1f}/month, {len(BOOKS[(win, mins)][0])} coins)  net {r.mean():+.3f}R/trade  win {100 * (r > 0).mean():.0f}%  "
                      f"total {r.sum():+.1f}R  day-t {K.day_t(t):+.2f}  median hold {(t.exit_time - t.entry_time).median() / 86400:.1f}d  median cost/R {t.cost_per_r.median():.3f}  "
                      f"funding/trade ${t.funding.mean():+.2f}  long {t[t.side > 0].r_multiple.mean():+.3f} (n {int((t.side > 0).sum())}) short {t[t.side < 0].r_multiple.mean():+.3f}  "
                      f"exits {t.exit_reason.value_counts().to_dict()}\n   random x{N_RANDOM}: mean {np.nanmean(null):+.3f}R, 5-95% {np.nanpercentile(null, 5):+.3f}..{np.nanpercentile(null, 95):+.3f}; "
                      f"p(random >= real) {np.nanmean(null >= r.mean()):.2f}", flush=True)
            del BOOKS[(win, mins)]; gc.collect()
    print("\n#### VERDICT: a cell passes if net > 0 in BOTH windows, pooled day-t >= 2.5 and pooled p vs random <= 0.05; < 60 trades in a window = cannot answer")
    any_pass = False
    for cell in CELLS:
        (a, na), (b, nb) = cells[(cell, list(K.WINDOWS)[0])], cells[(cell, list(K.WINDOWS)[1])]
        if len(a) < 60 or len(b) < 60:
            print(f"  {cell}: CANNOT ANSWER (trades {len(a)} / {len(b)})"); continue
        pooled = pd.concat([a, b]); tt = K.day_t(pooled); pr = float(np.nanmean(np.concatenate([na, nb]) >= pooled.r_multiple.mean()))
        ok = a.r_multiple.mean() > 0 and b.r_multiple.mean() > 0 and tt >= 2.5 and pr <= 0.05; any_pass |= ok
        print(f"  {cell}: DEV {a.r_multiple.mean():+.3f}R (n {len(a)})  OLD {b.r_multiple.mean():+.3f}R (n {len(b)})  pooled day-t {tt:+.2f}  p vs random {pr:.2f} -> {'PASS' if ok else 'fail'}")
    print(f"\nDAILY TREND: {'PASS' if any_pass else 'FAIL'}")


if __name__ == "__main__":
    {"preflight": preflight, "run": full}[sys.argv[1]]()
