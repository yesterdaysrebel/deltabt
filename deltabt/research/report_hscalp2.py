"""Minimal visualisations for H-Scalp-2 (the seven required figures).

    PYTHONPATH=. python3 -m deltabt.research.report_hscalp2
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
from deltabt.research.hscalp1 import build_bars  # noqa: E402
from deltabt.research.hscalp2 import ENTRY_WINDOW_BARS, MAX_HOLD_BARS, VOL_LOOKBACK  # noqa: E402
from deltabt.research.run_hscalp1 import START, SYMBOLS  # noqa: E402

OUT = OUT_DIR / "hscalp2"
plt.rcParams.update({"figure.dpi": 110, "font.size": 9, "axes.grid": True,
                     "grid.alpha": 0.25, "axes.spines.top": False,
                     "axes.spines.right": False})


def matched_null_paths(trades: pd.DataFrame, n_paths: int = 40) -> np.ndarray:
    """Resting-limit null at random times -- the entry-mechanism-matched one."""
    store, cat = CandleStore(), ProductCatalog()
    rng = np.random.default_rng(9)
    per_symbol = {}
    for s in SYMBOLS:
        tmpl = trades[trades.symbol == s]
        if tmpl.empty:
            continue
        c = SymbolCosts.from_spec(cat.get(s), slippage_bps=2.0)
        bars, mark = build_bars(store.read(s, "ltp", "1m"), store.read(s, "mark", "1m"), START)
        per_symbol[s] = (bars, mark, c, tmpl)

    out = []
    for _ in range(n_paths):
        path = []
        for s, (bars, mark, c, tmpl) in per_symbol.items():
            h = bars.high.to_numpy(float); lo = bars.low.to_numpy(float)
            cl = bars.close.to_numpy(float)
            mh = mark.high.to_numpy(float); ml = mark.low.to_numpy(float)
            n = len(bars); tick = c.tick_size
            mk = c.effective_maker; tk = c.effective_taker + c.slippage_rate
            sides = tmpl.side.to_numpy(); Rs = tmpl.r_price.to_numpy(float)
            offs = (tmpl.event_move.abs() * 0.33).to_numpy(float)
            picks = rng.integers(VOL_LOOKBACK + 2,
                                 n - MAX_HOLD_BARS - ENTRY_WINDOW_BARS - 2, size=len(tmpl))
            dr = rng.integers(0, len(tmpl), size=len(tmpl))
            for bi, ti in zip(picks, dr):
                side = int(sides[ti]); R = float(Rs[ti]); off = float(offs[ti])
                level = cl[bi] - side * off
                f = -1
                for j in range(bi + 1, min(bi + 1 + ENTRY_WINDOW_BARS, n)):
                    if (lo[j] < level - tick) if side > 0 else (h[j] > level + tick):
                        f = j; break
                if f < 0:
                    continue
                entry = level; stop = entry - side * R; tgt = entry + side * R
                px = np.nan; reason = ""
                for j in range(f, min(f + MAX_HOLD_BARS, n)):
                    hs = (ml[j] <= stop) if side > 0 else (mh[j] >= stop)
                    ht = ((h[j] >= tgt) if side > 0 else (lo[j] <= tgt)) and j > f
                    if hs: px, reason = stop, "stop"; break
                    if ht: px, reason = tgt, "tgt"; break
                    px = cl[j]
                if not reason:
                    reason = "time"
                rate_out = mk if reason == "tgt" else tk
                path.append(side * (px - entry) / R - (entry * mk + px * rate_out) / R)
        out.append(np.array(path))
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    trades = pd.read_csv(OUT / "trades_primary.csv").sort_values("entry_time")
    r = trades["r_net"].to_numpy("float64")
    eq = np.cumsum(r)
    ts = pd.to_datetime(trades["entry_time"], unit="s")

    fig, ax = plt.subplots(2, 1, figsize=(9, 6), sharex=True,
                           gridspec_kw={"height_ratios": [2, 1]})
    ax[0].plot(ts, eq, lw=1.2, color="#2c3e50")
    ax[0].axhline(0, color="k", lw=0.8); ax[0].set_ylabel("cumulative R")
    ax[0].set_title("H-Scalp-2 — k=3.0, retest 33%, maker/maker, conservative fill")
    dd = eq - np.maximum.accumulate(eq)
    ax[1].fill_between(ts, dd, 0, color="#2c3e50", alpha=0.35)
    ax[1].set_ylabel("drawdown (R)")
    fig.tight_layout(); fig.savefig(OUT / "fig1_2_equity_drawdown.png"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(np.clip(r, -2, 2), bins=70, color="#34495e", alpha=0.85)
    ax.axvline(0, color="k", lw=0.8)
    ax.axvline(r.mean(), color="#c0392b", lw=1.5, label=f"mean {r.mean():+.3f}R")
    ax.axvline(np.median(r), color="#16a085", lw=1.5, label=f"median {np.median(r):+.3f}R")
    ax.set_xlabel("net R per trade"); ax.set_ylabel("count")
    ax.set_title("Distribution of net R (1:1 payoff, 52% win rate)")
    ax.legend(); fig.tight_layout()
    fig.savefig(OUT / "fig3_r_distribution.png"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(np.arange(len(r)), eq, lw=1.5, color="#2c3e50", label="H-Scalp-2", zorder=3)
    for i, p in enumerate(matched_null_paths(trades, n_paths=25)):
        ax.plot(np.arange(len(p)), np.cumsum(p), lw=0.5, color="#7f8c8d", alpha=0.3,
                label="matched resting-limit null" if i == 0 else None)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xlabel("trade #"); ax.set_ylabel("cumulative R")
    ax.set_title("Strategy vs entry-mechanism-matched random null")
    ax.legend(); fig.tight_layout()
    fig.savefig(OUT / "fig4_vs_random.png"); plt.close(fig)

    trades["quarter"] = pd.to_datetime(trades["entry_time"], unit="s").dt.to_period("Q").astype(str)
    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    for a, key, title in ((ax[0], "symbol", "By symbol"), (ax[1], "quarter", "By quarter")):
        g = trades.groupby(key)["r_net"]
        m, se = g.mean(), g.std() / np.sqrt(g.size())
        a.bar(m.index, m.values, yerr=1.96 * se.values, color="#2c3e50", alpha=0.8, capsize=4)
        a.axhline(0, color="k", lw=0.8); a.set_title(f"{title} (95% CI)")
        a.tick_params(axis="x", rotation=45 if key == "quarter" else 0)
    ax[0].set_ylabel("net R / trade")
    fig.tight_layout(); fig.savefig(OUT / "fig5_6_symbol_quarter.png"); plt.close(fig)

    store, cat = CandleStore(), ProductCatalog()
    bars, _ = build_bars(store.read("BTCUSD", "ltp", "1m"),
                         store.read("BTCUSD", "mark", "1m"), START)
    bt = trades[trades.symbol == "BTCUSD"]
    fig, axes = plt.subplots(2, 2, figsize=(11, 6))
    for ax_, (_, tr) in zip(axes.ravel(), bt.iloc[::max(len(bt) // 4, 1)].head(4).iterrows()):
        i = int(np.searchsorted(bars["time"].to_numpy(), tr["signal_time"]))
        w = bars.iloc[max(i - 8, 0): i + 16]
        t_ = pd.to_datetime(w["time"], unit="s")
        ax_.plot(t_, w["close"], lw=1.0, color="#34495e")
        ax_.axvline(pd.Timestamp(int(tr["signal_time"]), unit="s"), color="#8e44ad",
                    lw=1, ls="-", alpha=0.6, label="event")
        ax_.axhline(tr["entry_price"], color="#2980b9", lw=1, ls="--", label="retest entry")
        ax_.axhline(tr["target_price"], color="#16a085", lw=1, ls=":", label="target")
        ax_.axhline(tr["stop_price"], color="#c0392b", lw=1, ls=":", label="stop = invalidation")
        ax_.set_title(f"z={tr['z']:.1f} {tr['exit_reason']} {tr['r_net']:+.2f}R", fontsize=8)
        ax_.tick_params(axis="x", rotation=30, labelsize=6)
    axes.ravel()[0].legend(fontsize=6)
    fig.suptitle("Example H-Scalp-2 signals (BTCUSD): event -> retest -> continuation")
    fig.tight_layout(); fig.savefig(OUT / "fig7_examples.png"); plt.close(fig)

    print(f"figures written to {OUT}")
    for p in sorted(OUT.glob("fig*.png")):
        print(f"  {p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
