"""Reported-only addendum to docs/daily_trend_prereg.md (owner's question, 2026-10-13): the B-1D cell
(ATR momentum, 1D bars) with ONE or THREE positions at a time instead of one slot per coin.
Ties on the same day go to the alphabetically first coin (engine event order) -- stated, not fixed.
Random control: same signal counts per coin at random bars, same exits, same slot limit, 30 runs.

    PYTHONPATH=. .venv/bin/python scripts/daily_trend_slots.py
"""
import dataclasses, sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
import numpy as np, pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "scripts")
import daily_trend_lab as L, labkit as K
from deltabt.portfolio import Book, RiskGates, run_portfolio


def run_slots(key, slots, seed=None):
    coins, minutes = L.BOOKS[key]; bks = {}; rng = np.random.default_rng(seed) if seed is not None else None
    for s, b in coins.items():
        sig = L.signals(b["P"], "atrmom")
        if rng is not None:
            n = len(b["P"]); idx = np.arange(sig.warmup, n); le = np.zeros(n, bool); se = np.zeros(n, bool)
            if len(idx):
                le[rng.choice(idx, min(int(sig.long_entry.sum()), len(idx)), replace=False)] = True
                se[rng.choice(idx, min(int(sig.short_entry.sum()), len(idx)), replace=False)] = True
            sig = L.signals(b["P"], "atrmom", entries=(le, se))
        bks[s] = Book(symbol=s, bars=b["P"], signals=sig, costs=b["costs"], mark=b["mark"], tradable=None, fill_ltp=b["ltp"], fill_mark=b["mark1m"])
    res = run_portfolio(bks, L.params(minutes), replace(RiskGates.off(), max_open_positions=slots), initial_capital=10_000.0,
                        funding={s: coins[s]["funding"] for s in bks})
    t = pd.DataFrame([dataclasses.asdict(x) for x in res.trades])
    if len(t): t["day"] = t.entry_time // 86400
    return t


def _rand(a):
    t = run_slots(*a); return t.r_multiple.mean() if len(t) else np.nan


if __name__ == "__main__":
    for win, root in K.WINDOWS.items():
        L.BOOKS[(win, 1440)] = (L.build(root, 1440, K.SCREEN), 1440); months = (9.4 if win.startswith("DEV") else 18.5)
        for slots in (1, 3, 99):
            t = run_slots((win, 1440), slots)
            with ProcessPoolExecutor(10) as pool:
                null = np.array(list(pool.map(_rand, [((win, 1440), slots, 700 + j) for j in range(30)])))
            r = t.r_multiple
            print(f"{win} | {'all coins' if slots == 99 else f'{slots} slot(s)':9s}: {len(t):3d} trades ({len(t)/months:.1f}/month)  net {r.mean():+.3f}R  win {100*(r>0).mean():.0f}%  "
                  f"total {r.sum():+.1f}R  day-t {K.day_t(t):+.2f}  | random x30 mean {np.nanmean(null):+.3f}R, p(random >= real) {np.nanmean(null >= r.mean()):.2f}", flush=True)
        del L.BOOKS[(win, 1440)]
