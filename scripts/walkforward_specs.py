"""Anchored walk-forward over the strategy catalog.

    PYTHONPATH=. python3 -u scripts/walkforward_specs.py

Everything in ``out/sweep/backtests.csv`` is an IN-SAMPLE fit: every cell saw
every bar. This asks the only question that matters before anything is put on
paper -- does a configuration chosen on the past do anything on the future it
did not see?

TWO SEPARATE TESTS, BECAUSE THEY ANSWER DIFFERENT QUESTIONS

1. ``selection``  Choose the best family/timeframe on the training window, then
   measure THAT choice on the next block. This tests the whole procedure,
   including the act of choosing. It is the honest analogue of what an operator
   would actually do.

2. ``fixed``      Track named cells across every block regardless of how they
   rank. This tests whether a specific result is stable in time rather than
   concentrated in one period -- the failure that killed H-Scalp-3, where a
   real-looking mechanism reversed between adjacent half-years.

WHY SIGNALS ARE COMPUTED ONCE ON FULL HISTORY AND SLICED AFTERWARDS
    ``rulecore`` is causal: bar t reads only bars <= t, asserted for every
    family by ``tests/test_rulecore_invariance.py``. So the signal at a bar is
    identical whether it was computed from full history or from history up to
    that bar, and slicing after the fact avoids re-warming the indicators for
    every window. This is NOT a shortcut around the split -- no training-window
    information reaches a test-window signal, because no FUTURE information
    reaches any signal at all.
"""

from __future__ import annotations

import argparse
import logging
import time

import numpy as np
import pandas as pd

from deltabt import rulecore
from deltabt.catalog import FAMILIES, build_spec
from deltabt.harness import (TIMEFRAMES, _resampled, load_symbol,
                             params_for, slice_signals)
from deltabt.config import OUT_DIR
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.engine import run_backtest
from deltabt.metrics import compute


log = logging.getLogger("walkforward")
OUT = OUT_DIR / "sweep"

#: A cell must clear this many trades in the TRAINING window to be selectable.
#: Without a floor the winner is whichever cell took three lucky trades.
MIN_TRAIN_TRADES = 40
#: ...and this many out of sample for the OOS number to mean anything.
MIN_OOS_TRADES = 10

#: Cells carried through every split by name -- the ones that survived the
#: in-sample grid, so their out-of-sample behaviour is the actual question.
#:
#: THIS LIST IS ITSELF A SELECTION, WHICH IS THE PROBLEM WITH IT. Every entry
#: is here because it ranked well in sample, so "2 of 7 held a positive gross
#: sign in all four blocks" was measured on a set already filtered by the
#: effect it was meant to detect. Chance alone predicts 0.88 of 7; against the
#: WHOLE grid the same rate predicts about 9 of 72, and whether the real count
#: is 9 or 25 is the difference between wpr_only@240m being a survivor of a
#: ranking and being a result.
#:
#: `--fixed all` tracks all 72 family x timeframe cells for that reason. The
#: seven stay the default so previously published numbers stay reproducible.
FIXED_CELLS = (("trend_wide_stop", 60), ("trend_wide_stop", 240),
               ("adx_only", 240), ("hwpr_no_confirm", 240), ("st_only", 240),
               # The two survivors of the gated PORTFOLIO run. Tracked by name
               # across every block because that is the question that decides
               # whether either is worth paper trading.
               ("wpr_only", 240), ("atr_arm", 240))


def cell_result(data: dict, family: str, minutes: int, costs: SymbolCosts,
                cache: dict, window: tuple[int, int] | None,
                confirm_minutes: int | None = None,
                stop_mult: float | None = None,
                hold_hours: int | None = None) -> dict | None:
    """One symbol, one cell, optionally restricted to a time window.

    NOTE the parameter is ``confirm_minutes``, not ``confirm``: the local
    ``confirm`` below is the confirmation DATAFRAME, and naming the argument
    the same thing silently rebound it into the signal cache key.
    """
    spec = build_spec(family, minutes, confirm_minutes, stop_mult)
    primary, mark, tradable = _resampled(data, minutes, cache)
    confirm, _, _ = (_resampled(data, spec.confirm_minutes, cache)
                     if spec.confirm.enabled else (None, None, None))
    if len(primary) < spec.warmup_bars * 3:
        return None

    key = (data["symbol"], family, minutes, confirm_minutes, stop_mult)
    if key not in cache:
        cache[key] = rulecore.to_engine_signals(
            rulecore.compute(primary, confirm, spec))
    sig = cache[key]

    t = primary["time"].to_numpy("int64")
    if window is None:
        mask = np.ones(len(t), dtype=bool)
    else:
        mask = (t >= window[0]) & (t <= window[1])
    if mask.sum() < spec.warmup_bars:
        return None

    params = params_for(spec, minutes, hold_hours)
    res = run_backtest(primary[mask].reset_index(drop=True), mark,
                       data["funding"], slice_signals(sig, mask), params,
                       costs, tradable=tradable[mask])
    m = compute(res)
    df = res.to_frame()
    # Gross = net + the modelled round trip. Reported alongside net so a cell
    # that fails can be attributed: negative GROSS is an absent signal, which
    # no cost saving can rescue; positive gross with negative net is friction.
    gross = float((df["r_multiple"] + df["cost_per_r"]).mean()) if len(df) else float("nan")
    return dict(symbol=data["symbol"], family=family, timeframe_min=minutes,
                confirm_min=confirm_minutes, stop_mult=stop_mult,
                hold_hours=hold_hours,
                trades=m.trades, net_r=m.expectancy_r, win_rate=m.win_rate,
                net_r_lo=m.expectancy_r_lo, gross_r=gross)


def pooled(rows: list[dict]) -> dict:
    """Trade-weighted pooling across symbols for one cell."""
    rows = [r for r in rows if r and r["trades"] > 0]
    if not rows:
        return dict(trades=0, net_r=float("nan"), symbols=0, positive_symbols=0)
    w = np.array([r["trades"] for r in rows], dtype=float)
    net = np.array([r["net_r"] for r in rows], dtype=float)
    gross = np.array([r.get("gross_r", np.nan) for r in rows], dtype=float)
    return dict(trades=int(w.sum()),
                net_r=float(np.average(net, weights=w)),
                gross_r=float(np.average(gross, weights=w)),
                symbols=len(rows),
                positive_symbols=int((net > 0).sum()))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--splits", type=int, default=4)
    ap.add_argument("--symbols", nargs="*",
                    default=["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"])
    ap.add_argument("--timeframes", nargs="*", type=int, default=list(TIMEFRAMES))
    ap.add_argument("--fixed", choices=("tracked", "all"), default="tracked",
                    help="which cells to carry through every block by name. "
                         "'all' walks the whole grid, which is what measures "
                         "whether a 4/4 gross sign is rare or ordinary.")
    args = ap.parse_args()

    catalog = ProductCatalog()
    loaded, costs = {}, {}
    for s in args.symbols:
        d = load_symbol(s)
        if d is None:
            log.warning("%s: no candles", s)
            continue
        loaded[s] = d
        costs[s] = SymbolCosts.from_spec(catalog.get(s))
    if not loaded:
        raise SystemExit("no symbols loaded")

    times = np.concatenate([d["ltp"]["time"].to_numpy() for d in loaded.values()])
    t0, t1 = int(times.min()), int(times.max())
    edges = np.linspace(t0, t1, args.splits + 2).astype(np.int64)
    log.info("%s -> %s, %d anchored splits",
             pd.Timestamp(t0, unit="s").date(), pd.Timestamp(t1, unit="s").date(),
             args.splits)

    cells = [(f, m) for m in args.timeframes for f in FAMILIES]
    fixed_cells = tuple(cells) if args.fixed == "all" else FIXED_CELLS
    log.info("tracking %d fixed cell(s) across every block", len(fixed_cells))
    cache: dict = {}
    sel_rows, fix_rows = [], []
    start = time.time()

    for k in range(args.splits):
        train = (t0, int(edges[k + 1]))
        test = (int(edges[k + 1]) + 1, int(edges[k + 2]))

        # --- 1. selection: best on train, measured on test ------------------
        scored = []
        for family, minutes in cells:
            p = pooled([cell_result(loaded[s], family, minutes, costs[s], cache, train)
                        for s in loaded])
            if p["trades"] >= MIN_TRAIN_TRADES:
                scored.append({"family": family, "timeframe_min": minutes, **p})
        row = {"split": k,
               "train_end": str(pd.Timestamp(train[1], unit="s").date()),
               "test_end": str(pd.Timestamp(test[1], unit="s").date())}
        if not scored:
            sel_rows.append({**row, "note": "nothing cleared the training floor"})
        else:
            best = max(scored, key=lambda r: r["net_r"])
            oos = pooled([cell_result(loaded[s], best["family"], best["timeframe_min"],
                                      costs[s], cache, test) for s in loaded])
            sel_rows.append({
                **row, "selected": f"{best['family']}@{best['timeframe_min']}m",
                "is_trades": best["trades"], "is_net_r": best["net_r"],
                "oos_trades": oos["trades"], "oos_gross_r": oos["gross_r"],
                "oos_net_r": oos["net_r"] if oos["trades"] >= MIN_OOS_TRADES else float("nan"),
                "candidates": len(scored),
            })

        # --- 2. fixed cells: same names, every block -------------------------
        for family, minutes in fixed_cells:
            p = pooled([cell_result(loaded[s], family, minutes, costs[s], cache, test)
                        for s in loaded])
            fix_rows.append({**row, "cell": f"{family}@{minutes}m",
                             "block_trades": p["trades"], "block_net_r": p["net_r"],
                             "block_gross_r": p["gross_r"],
                             "symbols_positive": f"{p['positive_symbols']}/{p['symbols']}"})
        log.info("split %d done (%.0fs)", k, time.time() - start)

    sel = pd.DataFrame(sel_rows)
    fix = pd.DataFrame(fix_rows)
    OUT.mkdir(parents=True, exist_ok=True)
    sel.to_csv(OUT / "walkforward_selection.csv", index=False)
    fix.to_csv(OUT / "walkforward_fixed.csv", index=False)

    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda v: f"{v:+.4f}")
    print("\n=== 1. SELECTION: best on train, measured out of sample ===")
    print(sel.to_string(index=False))
    if "oos_net_r" in sel:
        oos = sel["oos_net_r"].dropna()
        if len(oos):
            print(f"\nmean OOS net R across splits : {oos.mean():+.4f}")
            print(f"splits positive out of sample: {int((oos > 0).sum())}/{len(oos)}")
            insample = sel["is_net_r"].dropna()
            print(f"mean IN-sample net R of the same picks: {insample.mean():+.4f}")
            print(f"in-sample minus out-of-sample: {insample.mean() - oos.mean():+.4f}"
                  "   <- the selection premium, i.e. how much of the")
            print("     in-sample number was the act of choosing")

    label = ("the whole grid" if args.fixed == "all"
             else "the in-sample survivors")
    print(f"\n\n=== 2. FIXED CELLS: {label}, block by block ===")
    gross = fix.pivot(index="cell", columns="split", values="block_gross_r")
    net = fix.pivot(index="cell", columns="split", values="block_net_r")
    if args.fixed == "all":
        # 72 rows of per-block numbers is not readable and not the question.
        # The question is how OFTEN a cell holds its sign, against how often
        # chance would produce that.
        print("(72 cells; per-block tables suppressed -- see the CSV)")
    else:
        print(net.to_string())
        print("\nGROSS R per block -- before ANY cost is charged:")
        print(gross.to_string())
        print("\ntrades per block:")
        print(fix.pivot(index="cell", columns="split", values="block_trades").to_string())
        print("\nsymbols positive per block:")
        print(fix.pivot(index="cell", columns="split",
                        values="symbols_positive").to_string())

    # --- IS A 4/4 GROSS SIGN RARE, OR IS IT WHAT CHANCE DOES? ---------------
    # wpr_only@240m was selected partly because its gross stayed positive in
    # all four out-of-sample blocks. That is only evidence if most cells fail
    # to do it. Measured against the whole grid rather than against a set
    # already filtered by in-sample rank, which is what made the original
    # "2 of 7" uninterpretable.
    blocks = gross.shape[1]
    held = (gross > 0).sum(axis=1)
    n = len(held)
    perfect = int((held == blocks).sum())
    expected = n * 0.5 ** blocks
    print(f"\n\n=== 3. HOW RARE IS A {blocks}/{blocks} GROSS SIGN? ===")
    print(f"cells tracked                    : {n}")
    print(f"cells positive in all {blocks} blocks : {perfect}")
    print(f"expected by chance at p=0.5      : {expected:.2f}")
    if expected > 0:
        print(f"ratio observed/expected          : {perfect / expected:.2f}x")
    print("\ndistribution of blocks-held-positive:")
    for k in range(blocks + 1):
        c = int((held == k).sum())
        print(f"  {k}/{blocks}: {c:>3} cell(s)  {'#' * c}")
    if perfect:
        print(f"\nthe {perfect} cell(s) holding every block:")
        for cell in sorted(held[held == blocks].index):
            g = gross.loc[cell]
            nt = net.loc[cell]
            print(f"  {cell:28s} gross {' '.join(f'{v:+.3f}' for v in g)}"
                  f"   net {' '.join(f'{v:+.3f}' for v in nt)}")
    print("\nA count near the expected value means holding every block is what")
    print("chance does with this many cells, NOT evidence about any one of them.")
    print(f"\nwritten to {OUT}/walkforward_{{selection,fixed}}.csv")


if __name__ == "__main__":
    main()
