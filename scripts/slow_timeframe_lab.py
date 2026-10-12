"""Idea 7: the same entry on 1h and 4h bars, with and without the after-a-win rule (2026-10-12).

    PYTHONPATH=. .venv/bin/python scripts/slow_timeframe_lab.py

Pre-registered in docs/ideas_1_to_4_prereg.md ("Idea 7"). manual_scalp_both_t3
built at a 60m primary (12m confirmation) and a 240m primary (48m), 4xATR stop,
3R target, hold caps 10 and 30 days; per-coin spread slippage; the 0.15 cost
gate. Both windows, every screen coin whose bars survive the completeness rule
(thin coins mostly do not at these sizes -- stated). Arms: every signal, and
money only after a win on the coin (idea 6's labelling, no re-run needed).
"""
import gc, sys
from dataclasses import replace
import numpy as np, pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "scripts")
import labkit as K
from win_follow_lab import label
from deltabt import rulecore
from deltabt.catalog import build_spec
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.harness import _resampled, params_for
from deltabt.portfolio import Book, RiskGates, run_portfolio
import dataclasses

TF = {60: 240, 240: 720}                     # primary minutes -> hold cap in hours


def books_tf(data, spec, mins):
    cat = ProductCatalog(); cache = {}; out = {}
    for s, d in data.items():
        P, mk, tr = _resampled(d, mins, cache)
        if len(P) < 200:
            continue
        C = _resampled(d, spec.confirm_minutes, cache)[0]
        c = replace(SymbolCosts.from_spec(cat.get(s)), slippage_bps=K.slip(s))
        out[s] = Book(symbol=s, bars=P, signals=rulecore.to_engine_signals(rulecore.compute(P, C, spec)),
                      costs=c, mark=mk, tradable=tr, fill_ltp=d["ltp"], fill_mark=d["mark"])
    return out


def run_tf(bks, data, spec, mins, exit_name):
    params = replace(params_for(spec, mins, TF[mins]), stop_fill="ltp_close", **K.EXITS[exit_name])
    res = run_portfolio(bks, params, replace(RiskGates.off(), max_open_positions=len(bks)), initial_capital=10_000.0,
                        funding={s: data[s]["funding"] for s in bks})
    t = pd.DataFrame([dataclasses.asdict(x) for x in res.trades]); t["day"] = t.entry_time // 86400
    return t


if __name__ == "__main__":
    cells = {}
    for win, root in K.WINDOWS.items():
        data = K.load_window(root, K.SCREEN)
        months = (max(d["ltp"].time.max() for d in data.values()) - min(d["ltp"].time.min() for d in data.values())) / (30 * 86400)
        for mins in TF:
            spec = build_spec("manual_scalp_both_t3", mins); bks = books_tf(data, spec, mins)
            print(f"\n==== {win} @ {mins}m: {len(bks)} of {len(data)} coins have usable bars ====")
            for ex in ("hold-to-3R", "trail"):
                t = label(run_tf(bks, data, spec, mins, ex)); R = t[t.real]
                coins = t.groupby("symbol").size(); n30 = int((coins >= 30).sum())
                tag = "(judged)" if ex == "hold-to-3R" else "(reported)"
                print(f"  {ex} {tag}: {len(t)} trades, {len(t) / months:.0f}/month, {n30} coins with >= 30 trades, "
                      f"median cost/R {t.cost_per_r.median():.3f}, median hold {(t.exit_time - t.entry_time).median() / 3600:.0f}h")
                print(K.fmt("every signal", t, f"  day-t {K.day_t(t):+.2f}"))
                print(K.fmt("after a win on the coin only", R, f"  day-t {K.day_t(R):+.2f}  share {100 * len(R) / max(len(t), 1):.0f}%"))
                if ex == "hold-to-3R":
                    cells[(mins, "all", win)] = t; cells[(mins, "after-win", win)] = R
            del bks; gc.collect()
        del data; gc.collect()
    print("\n#### VERDICT (hold-to-3R): a cell passes if net > 0 in BOTH windows and the pooled day-clustered t >= 2.5; "
          "fewer than 100 trades in a window = cannot answer")
    any_pass = False
    for mins in TF:
        for arm in ("all", "after-win"):
            a, b = cells[(mins, arm, list(K.WINDOWS)[0])], cells[(mins, arm, list(K.WINDOWS)[1])]
            if len(a) < 100 or len(b) < 100:
                print(f"  {mins}m {arm:10s}: CANNOT ANSWER (trades {len(a)} / {len(b)})"); continue
            pooled = pd.concat([a, b]); tt = K.day_t(pooled)
            ok = a.r_multiple.mean() > 0 and b.r_multiple.mean() > 0 and tt >= 2.5; any_pass |= ok
            print(f"  {mins}m {arm:10s}: DEV {a.r_multiple.mean():+.3f}R (n {len(a)})  OLD {b.r_multiple.mean():+.3f}R (n {len(b)})  "
                  f"pooled day-t {tt:+.2f} -> {'PASS' if ok else 'fail'}")
    print(f"\nIDEA 7: {'PASS' if any_pass else 'FAIL'}")
