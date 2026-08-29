"""Walk-forward the wide-stop / long-hold cells before believing them.

WHY THIS EXISTS SEPARATELY
    scripts/backtest_sweep.py found net_r of +0.11 to +0.20 for 8xATR stops
    held 168-720 hours -- the first clearly positive numbers in this
    repository. Every previous positive here has dissolved on a second
    question: XRPUSD@45m failed a cross-symbol check, a 4R target held at 15m
    and no other timeframe, a funding book at Sharpe 3.7 was a broken
    liquidity screen. So this asks the second question first.

WHAT IS ASKED
    Anchored blocks, cells tracked BY NAME across every block regardless of
    how they rank in any of them. That is the `fixed` test in
    walkforward_specs.py and it is the one that matters: a configuration
    chosen because it ranked well in-sample and then measured out-of-sample is
    testing the act of choosing, while a named cell measured in every block is
    testing the cell.

WHAT WOULD MAKE IT REAL
    The same sign in every block, on a trade count large enough that the block
    means something, with the effect present at BOTH timeframes rather than
    one. The 4R target passed the first of those and failed the third.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from deltabt.config import OUT_DIR
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.harness import load_symbol
from scripts.walkforward_specs import cell_result, pooled  # noqa: E402

SYMBOLS = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "BEATUSD", "BANKUSD", "AKEUSD")
SPLITS = 4

#: (timeframe, confirm, stop multiple, hold hours). The 24h rows are the
#: control: if the wide stop only works once the hold cap is lifted, the cap
#: was the binding constraint and that is itself the finding.
CELLS = [
    (15, 5, 2.0, 24), (15, 5, 2.0, 720),
    (15, 5, 8.0, 24), (15, 5, 8.0, 168), (15, 5, 8.0, 720),
    (15, 5, 16.0, 720),
    (60, 5, 2.0, 24), (60, 5, 2.0, 720),
    (60, 5, 4.0, 720),
    (60, 5, 8.0, 24), (60, 5, 8.0, 168), (60, 5, 8.0, 720),
]


def main() -> None:
    catalog = ProductCatalog()
    loaded, costs = {}, {}
    for s in SYMBOLS:
        d = load_symbol(s)
        if d is None:
            continue
        loaded[s] = d
        costs[s] = SymbolCosts.from_spec(catalog.get(s))
    print(f"{len(loaded)} symbols loaded")

    times = np.concatenate([d["ltp"]["time"].to_numpy("int64") for d in loaded.values()])
    lo, hi = int(times.min()), int(times.max())
    edges = np.linspace(lo, hi, SPLITS + 2).astype(int)
    blocks = [(int(edges[i + 1]), int(edges[i + 2])) for i in range(SPLITS)]
    print(f"{pd.to_datetime(lo, unit='s')} -> {pd.to_datetime(hi, unit='s')}, "
          f"{SPLITS} blocks\n")

    rows = []
    for tf, cf, mult, hold in CELLS:
        cache: dict = {}
        per_block = []
        for bi, window in enumerate(blocks):
            res = [cell_result(loaded[s], "atr_arm", tf, costs[s], cache,
                               window, cf, mult, hold) for s in loaded]
            p = pooled([r for r in res if r])
            per_block.append(p)
        n = sum(b["trades"] for b in per_block)
        g = [b.get("gross_r", float("nan")) for b in per_block]
        v = [b["net_r"] for b in per_block]
        rows.append(dict(
            cell=f"{tf}m x{mult:g}ATR {hold}h", trades=n,
            blocks_net_pos=sum(1 for x in v if np.isfinite(x) and x > 0),
            blocks_gross_pos=sum(1 for x in g if np.isfinite(x) and x > 0),
            net_by_block=" ".join(f"{x:+.3f}" for x in v),
            trades_by_block=" ".join(f"{b['trades']:d}" for b in per_block),
            mean_net=float(np.nansum([b["net_r"] * b["trades"] for b in per_block])
                           / n) if n else float("nan"),
        ))
        print(f"  {rows[-1]['cell']:20s} n={n:5d}  net {rows[-1]['net_by_block']}"
              f"   ({rows[-1]['blocks_net_pos']}/{SPLITS} positive)")

    d = pd.DataFrame(rows)
    print("\n" + "=" * 78)
    print(d[["cell", "trades", "mean_net", "blocks_net_pos",
             "blocks_gross_pos", "trades_by_block"]].to_string(
        index=False, float_format=lambda x: f"{x:.4f}"))
    out = OUT_DIR / "sweep" / "wide_slow_walkforward.csv"
    d.to_csv(out, index=False)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
