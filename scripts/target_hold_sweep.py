"""Sweep target_r and hold time -- the two exit parameters never varied.

Everything measured so far held target_r = 2.0 and max_hold = 48h fixed and
varied the ENTRY. gross_r = p*T - (1-p)*1 has two free terms and only p has
been attacked. This varies T.
"""
from dataclasses import replace

import pandas as pd

from deltabt import rulecore
from deltabt.catalog import build_spec
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.engine import StrategyParams, run_backtest
from deltabt.harness import CONFIRM_RATIO, EXIT_ON_TREND_FLIP, _resampled, load_symbol

SYMBOLS = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")
TF, CF = 15, 5

pc = ProductCatalog()
rows = []
for sym in SYMBOLS:
    data = load_symbol(sym)
    costs = SymbolCosts.from_spec(pc.get(sym))
    cache: dict = {}
    primary, mark, tradable = _resampled(data, TF, cache)
    confirm, _, _ = _resampled(data, CF, cache)
    for mult in (2.0, 3.0, 4.0):
        base = build_spec("atr_arm", TF, CF, mult)
        sig = rulecore.compute(primary, confirm, base)
        eng = rulecore.to_engine_signals(sig)
        for target in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
            for hold_h in (48, 168):
                params = StrategyParams(
                    base_minutes=TF,
                    confirm_minutes=max(TF * CONFIRM_RATIO, TF + 1),
                    max_hold_bars=max(20, (hold_h * 60) // TF),
                    exit_on_trend_flip=EXIT_ON_TREND_FLIP,
                    reward_risk=target,
                )
                res = run_backtest(primary, mark, data["funding"], eng,
                                   params, costs, tradable=tradable)
                df = res.to_frame()
                if not len(df):
                    continue
                gr = df["r_multiple"] + df["cost_per_r"]
                rows.append(dict(
                    symbol=sym, stop_mult=mult, target_r=target, hold_h=hold_h,
                    trades=len(df), gross_r=gr.mean(),
                    cost_r=df["cost_per_r"].mean(),
                    net_r=df["r_multiple"].mean(),
                    hit=100 * (df["exit_reason"] == "target").mean(),
                    timeout=100 * (df["exit_reason"] == "max_hold").mean(),
                ))

d = pd.DataFrame(rows)
d.to_csv("out/sweep/target_hold.csv", index=False)


def wt(g, k):
    return (g[k] * g["trades"]).sum() / g["trades"].sum()


print("TRADE-WEIGHTED over 4 majors, 15m primary / 5m confirm, atr_arm\n")
for hold_h, gh in d.groupby("hold_h"):
    print(f"=== max hold {hold_h}h ===")
    print(f"  {'stop':>5s} {'target':>7s} {'trades':>7s} {'target hit%':>12s} "
          f"{'timeout%':>9s} {'gross_r':>9s} {'cost_r':>8s} {'net_r':>9s}")
    for (m, t), g in gh.groupby(["stop_mult", "target_r"]):
        print(f"  {m:5.1f} {t:7.1f} {g['trades'].sum():7.0f} {wt(g,'hit'):12.1f} "
              f"{wt(g,'timeout'):9.1f} {wt(g,'gross_r'):+9.4f} "
              f"{wt(g,'cost_r'):8.4f} {wt(g,'net_r'):+9.4f}")
    print()

best = d.assign(w=d["trades"]).sort_values("net_r", ascending=False)
best = best[best["trades"] >= 100]
print("top cells by net_r (>=100 trades):")
print(best.head(8).to_string(index=False, float_format=lambda x: f"{x:.4f}"))
