"""Minimal visualisations for H-Scalp-1 (the seven required figures).

    PYTHONPATH=. python3 -m deltabt.research.report_hscalp1
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from deltabt.config import OUT_DIR  # noqa: E402
from deltabt.costs import SymbolCosts  # noqa: E402
from deltabt.data.store import CandleStore, ProductCatalog  # noqa: E402
from deltabt.research import hscalp1, nulls  # noqa: E402
from deltabt.research.run_hscalp1 import START, SYMBOLS  # noqa: E402

OUT = OUT_DIR / "hscalp1"
plt.rcParams.update({"figure.dpi": 110, "font.size": 9, "axes.grid": True,
                     "grid.alpha": 0.25, "axes.spines.top": False,
                     "axes.spines.right": False})


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    store, cat = CandleStore(), ProductCatalog()

    per_symbol_res, frames, null_pool = {}, [], []
    for s in SYMBOLS:
        ltp = store.read(s, "ltp", "1m"); mark = store.read(s, "mark", "1m")
        fund = store.read(s, "funding", "1h")
        costs = SymbolCosts.from_spec(cat.get(s), slippage_bps=2.0)
        r = hscalp1.run(ltp, mark, fund, costs, k=3.0, exec_model="maker/maker",
                        fill_model="conservative", start=START)
        per_symbol_res[s] = (r, ltp, mark, fund, costs)
        df = r.to_frame()
        if not df.empty:
            frames.append(df)
            nn = nulls.random_entry_null(ltp, mark, fund, costs, df,
                                         start=START, n_sims=60, seed=7)
            if nn["per_trade"].size:
                null_pool.append(nn["per_trade"])

    trades = pd.concat(frames, ignore_index=True).sort_values("entry_time")
    trades.to_csv(OUT / "trades_primary.csv", index=False)
    r = trades["r_net"].to_numpy("float64")
    eq = np.cumsum(r)
    ts = pd.to_datetime(trades["entry_time"], unit="s")

    # 1 + 2: equity and drawdown
    fig, ax = plt.subplots(2, 1, figsize=(9, 6), sharex=True,
                           gridspec_kw={"height_ratios": [2, 1]})
    ax[0].plot(ts, eq, lw=1.2, color="#c0392b")
    ax[0].axhline(0, color="k", lw=0.8)
    ax[0].set_ylabel("cumulative R")
    ax[0].set_title("H-Scalp-1 — k=3.0, maker/maker, conservative fill (pooled 4 symbols)")
    dd = eq - np.maximum.accumulate(eq)
    ax[1].fill_between(ts, dd, 0, color="#c0392b", alpha=0.35)
    ax[1].set_ylabel("drawdown (R)")
    fig.tight_layout(); fig.savefig(OUT / "fig1_2_equity_drawdown.png"); plt.close(fig)

    # 3: distribution of R
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(np.clip(r, -3, 3), bins=80, color="#34495e", alpha=0.85)
    ax.axvline(0, color="k", lw=0.8)
    ax.axvline(r.mean(), color="#c0392b", lw=1.5,
               label=f"mean {r.mean():+.3f}R")
    ax.axvline(np.median(r), color="#16a085", lw=1.5,
               label=f"median {np.median(r):+.3f}R")
    ax.set_xlabel("net R per trade (clipped +/-3)"); ax.set_ylabel("count")
    ax.set_title("Distribution of net R — high win rate, negative mean")
    ax.legend(); fig.tight_layout()
    fig.savefig(OUT / "fig3_r_distribution.png"); plt.close(fig)

    # 4: strategy vs random control
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(np.arange(len(r)), eq, lw=1.4, color="#c0392b", label="H-Scalp-1")
    if null_pool:
        npool = np.concatenate(null_pool)
        rng = np.random.default_rng(3)
        for i in range(40):
            samp = rng.choice(npool, size=len(r), replace=False if len(npool) >= len(r) else True)
            ax.plot(np.arange(len(r)), np.cumsum(samp), lw=0.5,
                    color="#7f8c8d", alpha=0.25,
                    label="random control (40 paths)" if i == 0 else None)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xlabel("trade #"); ax.set_ylabel("cumulative R")
    ax.set_title("Strategy vs exposure-matched random entry")
    ax.legend(); fig.tight_layout()
    fig.savefig(OUT / "fig4_vs_random.png"); plt.close(fig)

    # 5 + 6: by symbol and by quarter
    trades["quarter"] = pd.to_datetime(trades["entry_time"], unit="s").dt.to_period("Q").astype(str)
    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    g = trades.groupby("symbol")["r_net"]
    m, se = g.mean(), g.std() / np.sqrt(g.size())
    ax[0].bar(m.index, m.values, yerr=1.96 * se.values, color="#c0392b", alpha=0.8, capsize=4)
    ax[0].axhline(0, color="k", lw=0.8); ax[0].set_ylabel("net R / trade")
    ax[0].set_title("By symbol (95% CI)")
    gq = trades.groupby("quarter")["r_net"]
    mq, seq = gq.mean(), gq.std() / np.sqrt(gq.size())
    ax[1].bar(mq.index, mq.values, yerr=1.96 * seq.values, color="#c0392b", alpha=0.8, capsize=4)
    ax[1].axhline(0, color="k", lw=0.8); ax[1].tick_params(axis="x", rotation=45)
    ax[1].set_title("By quarter (95% CI)")
    fig.tight_layout(); fig.savefig(OUT / "fig5_6_symbol_quarter.png"); plt.close(fig)

    # 7: example signals on price
    res, ltp, mark, fund, costs = per_symbol_res["BTCUSD"]
    bars, _ = hscalp1.build_bars(ltp, mark, START)
    df = res.to_frame()
    fig, axes = plt.subplots(2, 2, figsize=(11, 6))
    for ax_, (_, tr) in zip(axes.ravel(), df.iloc[::max(len(df) // 4, 1)].head(4).iterrows()):
        i = int(np.searchsorted(bars["time"].to_numpy(), tr["entry_time"]))
        w = bars.iloc[max(i - 12, 0): i + 14]
        t_ = pd.to_datetime(w["time"], unit="s")
        ax_.plot(t_, w["close"], lw=1.0, color="#34495e")
        ax_.axhline(tr["entry_price"], color="#2980b9", lw=1, ls="--", label="entry")
        ax_.axhline(tr["target_price"], color="#16a085", lw=1, ls=":", label="target")
        ax_.axhline(tr["stop_price"], color="#c0392b", lw=1, ls=":", label="stop")
        ax_.set_title(f"BTCUSD z={tr['z']:.1f} {tr['exit_reason']} {tr['r_net']:+.2f}R", fontsize=8)
        ax_.tick_params(axis="x", rotation=30, labelsize=6)
    axes.ravel()[0].legend(fontsize=6)
    fig.suptitle("Example H-Scalp-1 signals (BTCUSD)")
    fig.tight_layout(); fig.savefig(OUT / "fig7_examples.png"); plt.close(fig)

    print(f"7 figures written to {OUT}")
    for p in sorted(OUT.glob("fig*.png")):
        print(f"  {p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
