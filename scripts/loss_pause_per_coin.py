"""The exact loss pause, per coin, against random pauses on that coin (2026-10-09).

    PYTHONPATH=. .venv/bin/python scripts/loss_pause_per_coin.py

WHY. Owner: "did you try it per coin?" The pause in scripts/loss_pause_exact.py
is already per coin (a loss on AKEUSD pauses AKEUSD only); its results were
pooled. This splits the same exact engine runs (hold-to-3R, prod universe,
4h and 8h) by coin and compares each with 200 random pauses at the same rate.
POST-HOC: six looks after the pre-registered 8h cell failed.

RESULT 2026-10-09 (out/sweep/five_min_arm_lab/loss_pause/per_coin_2026-10-09.txt):
no coin holds up. BEAT 4h +0.133R vs +0.037R (p 0.06) but 8h +0.015R (p 0.43);
AKE -0.293R / -0.207R, no different from random pauses (ANY pause swings AKE by
about -0.3R: path dependence, not the loss); BANK has 29-41 trades.
"""
import dataclasses, importlib.util, sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
import numpy as np, pandas as pd
sys.path.insert(0, ".")
spec = importlib.util.spec_from_file_location("lpe", "scripts/loss_pause_exact.py"); L = importlib.util.module_from_spec(spec)
sys.modules["lpe"] = L; spec.loader.exec_module(L)
from deltabt.harness import params_for
from deltabt.portfolio import RiskGates, run_portfolio
BOOKS, FUND = L.load(["BEATUSD", "AKEUSD", "BANKUSD"])
def run(hours, mode, p=0.0, seed=0):
    if mode == "none": sel = None
    elif mode == "loss": sel = lambda t: t.r_multiple < 0
    else:
        rng = np.random.default_rng(seed); sel = lambda t: rng.random() < p
    gates = replace(RiskGates.off(), max_open_positions=3, pause_seconds=int(hours * 3600) if sel else 0, pause_after=sel)
    params = replace(params_for(L.SPEC, 5, 72), stop_fill="ltp_close")
    res = run_portfolio(BOOKS, params, gates, initial_capital=10_000.0, funding=FUND)
    t = pd.DataFrame([dataclasses.asdict(x) for x in res.trades])
    return t[["symbol", "entry_time", "r_multiple"]]
def coin_means(args):
    t = run(*args); return t.groupby("symbol").r_multiple.mean().to_dict()
if __name__ == "__main__":
    base = run(0, "none"); split = np.median(base.entry_time)
    def show(name, t):
        for s, g in t.groupby("symbol"):
            h1 = g[g.entry_time < split].r_multiple; h2 = g[g.entry_time >= split].r_multiple
            yield s, f"{name:16s} {s:8s} trades {len(g):4d}  net {g.r_multiple.mean():+.3f}R  win {100*(g.r_multiple>0).mean():5.1f}%  total {g.r_multiple.sum():+7.1f}R  halves {h1.mean():+.3f} ({len(h1)}) / {h2.mean():+.3f} ({len(h2)})"
    p = float((base.r_multiple < 0).mean())
    rows = dict(show("no pause", base))
    print("hold-to-3R, prod coins, exact engine; pause = that coin only, timed from the losing trade's exit bar\n")
    for s in sorted(rows): print(rows[s])
    for hours in (4, 8):
        t = run(hours, "loss")
        with ProcessPoolExecutor() as pool:
            null = list(pool.map(coin_means, [(hours, "random", p, 1000 + k) for k in range(200)]))
        print(f"\n--- pause {hours}h after a loss on the coin ---")
        for s, line in dict(show(f"loss pause {hours}h", t)).items():
            obs = t[t.symbol == s].r_multiple.mean(); nl = np.array([d.get(s, np.nan) for d in null])
            print(line + f"  | random {hours}h pauses on this coin: mid {np.nanmedian(nl):+.3f}R, p {np.nanmean(nl >= obs):.2f}")
