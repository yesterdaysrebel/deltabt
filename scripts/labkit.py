"""Shared pieces for the 2026-10-12 labs (docs/ideas_1_to_4_prereg.md).

Two windows loaded straight from their cache directories (the harness's
load_symbol reads one fixed directory), per-coin spread slippage, the engine
run, and the day-clustered bootstrap every unpaired comparison uses.
"""
import dataclasses, glob, json, pathlib
from dataclasses import replace
import numpy as np, pandas as pd
from deltabt import rulecore
from deltabt.catalog import build_spec
from deltabt.costs import SymbolCosts
from deltabt.data.quality import tradable_mask
from deltabt.data.store import ProductCatalog
from deltabt.harness import _resampled, params_for
from deltabt.portfolio import Book, RiskGates, run_portfolio

SCREEN = sorted(pathlib.Path(f).stem for f in glob.glob("out/sweep/five_min_arm_lab/symbol_screen/trades/*.csv"))
WINDOWS = {"DEV 2025-12..2026-09": "data", "OLD 2024-06..2025-12": "data_old"}
SPREAD = {r["symbol"]: float(r["spread_bps"]) for r in
          json.load(open("out/sweep/five_min_arm_lab/symbol_screen/tickers_2026-10-05.json"))}
EXITS = {"hold-to-3R": {}, "trail": dict(trail_after_r=0.5, trail_r=0.5)}
SPEC = build_spec("manual_scalp_both_t3", 5)


def slip(sym):
    return max(2.0, SPREAD.get(sym, 4.0) / 2)


def load_window(root, syms, min_rows=50_000):
    """{symbol: cleaned data dict} for the coins with enough 1m history under ``root``."""
    out = {}
    for s in syms:
        d = pathlib.Path(root) / "candles" / s
        if not (d / "ltp_1m.parquet").exists():
            continue
        l = pd.read_parquet(d / "ltp_1m.parquet").sort_values("time").reset_index(drop=True)
        if len(l) < min_rows:
            continue
        l = l[~((l.volume == 0) & (l.open == l.close) & (l.high == l.low))].reset_index(drop=True)
        mk = pd.read_parquet(d / "mark_1m.parquet").sort_values("time").reset_index(drop=True) if (d / "mark_1m.parquet").exists() else None
        fd = pd.read_parquet(d / "funding_1h.parquet") if (d / "funding_1h.parquet").exists() else pd.DataFrame()
        out[s] = dict(symbol=s, ltp=l, mark=mk, funding=fd, tradable=tradable_mask(l))
    return out


def books(data, spec=SPEC, maker_entry=False):
    cat = ProductCatalog(); cache = {}; out = {}
    for s, d in data.items():
        P, mk, tr = _resampled(d, 5, cache); C = _resampled(d, 1, cache)[0]
        c = replace(SymbolCosts.from_spec(cat.get(s)), slippage_bps=slip(s), maker_entry=maker_entry)
        out[s] = Book(symbol=s, bars=P, signals=rulecore.to_engine_signals(rulecore.compute(P, C, spec)),
                      costs=c, mark=mk, tradable=tr, fill_ltp=d["ltp"], fill_mark=d["mark"])
    return out


def run(bks, data, exit_name="hold-to-3R", spec=SPEC, gates=None, **extra):
    gates = gates or replace(RiskGates.off(), max_open_positions=len(bks))
    params = replace(params_for(spec, 5, 72), stop_fill="ltp_close", **EXITS[exit_name], **extra)
    res = run_portfolio(bks, params, gates, initial_capital=10_000.0, funding={s: data[s]["funding"] for s in bks})
    t = pd.DataFrame([dataclasses.asdict(x) for x in res.trades])
    if t.empty:
        t = pd.DataFrame(columns=["symbol", "side", "entry_time", "r_multiple", "fees", "cost_per_r", "stop_price", "entry_price", "exit_reason"])
    t["day"] = t.entry_time.astype("int64") // 86400
    return t, res.rejects


def summary(t):
    r = t.r_multiple.to_numpy(float)
    if len(r) == 0:
        return dict(n=0, mean=np.nan, win=np.nan, total=0.0, h1=np.nan, h2=np.nan)
    split = np.median(t.entry_time); h1 = r[t.entry_time < split]; h2 = r[t.entry_time >= split]
    return dict(n=len(r), mean=r.mean(), win=100 * (r > 0).mean(), total=r.sum(),
                h1=h1.mean() if len(h1) else np.nan, h2=h2.mean() if len(h2) else np.nan)


def fmt(name, t, extra=""):
    s = summary(t)
    return (f"    {name:34s} trades {s['n']:5d}  net {s['mean']:+.3f}R  win {s['win']:5.1f}%  total {s['total']:+8.1f}R"
            f"  halves {s['h1']:+.3f} / {s['h2']:+.3f}{extra}")


def day_boot_p(a, b, draws=2000, seed=11):
    """P(mean R of a <= mean R of b) under a day-clustered bootstrap: entry days are
    resampled with replacement and BOTH runs are re-read on the same drawn days."""
    days = np.array(sorted(set(a.day) | set(b.day)))
    if len(days) < 10 or len(a) == 0 or len(b) == 0:
        return float("nan")
    ga = a.groupby("day").r_multiple.agg(["sum", "count"]).reindex(days).fillna(0.0)
    gb = b.groupby("day").r_multiple.agg(["sum", "count"]).reindex(days).fillna(0.0)
    rng = np.random.default_rng(seed); diffs = np.empty(draws)
    sa, ca, sb, cb = ga["sum"].to_numpy(), ga["count"].to_numpy(), gb["sum"].to_numpy(), gb["count"].to_numpy()
    for k in range(draws):
        w = np.bincount(rng.integers(0, len(days), len(days)), minlength=len(days))
        na, nb = (w * ca).sum(), (w * cb).sum()
        diffs[k] = ((w * sa).sum() / na if na else 0.0) - ((w * sb).sum() / nb if nb else 0.0)
    return float((diffs <= 0).mean())


def day_t(t, col="r_multiple"):
    """t-statistic of the mean of ``col`` against zero, clustered by entry day."""
    g = t.groupby("day")[col].agg(["sum", "count"])
    n = g["count"].sum()
    if n < 2 or len(g) < 2:
        return float("nan")
    mean = g["sum"].sum() / n
    resid = g["sum"] - mean * g["count"]
    se = np.sqrt((resid ** 2).sum()) / n
    return float(mean / se) if se > 0 else float("nan")
