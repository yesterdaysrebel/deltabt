"""EXPORT EVERY TRADE THE STATED RULE TOOK, SO THE COUNTS CAN BE CHECKED.

    PYTHONPATH=. python3 -u -m deltabt.research.dump_user_trades

Writes out/user_rule/trades_SL<x>_TP<y>.csv -- one row per trade, with entry
and exit timestamps in UTC and IST, both prices, the stop and target that were
set, why it closed, and the R decomposition into gross, fee, slippage and
funding. Nothing is aggregated away: the win rate quoted anywhere else is
recomputable from these rows.

Two settings are dumped rather than one, because a single stop width is a point
on a curve, not a result: the rule at a 1.00% stop, and at the 1.50% stop that
produced the least bad validation. Both at a 2R target, both the `cross`
reading of "rising from -80".

WHAT COUNTS AS A WIN HERE is r_net > 0, i.e. AFTER costs. A trade that reaches
its target and then hands the gain back in fees and slippage is a loss, because
it is. Counting gross wins would inflate the rate by several points and would
not be the rate anyone experiences.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from deltabt.config import OUT_DIR
from deltabt.costs import SymbolCosts
from deltabt.data.quality import tradable_mask
from deltabt.data.store import CandleStore, ProductCatalog
from deltabt.research import hwpr
from deltabt.research.run_user_rule import (STUDY, _conditions, rule_masks,
                                            CAPITAL_FRACTION, START_EQUITY)

OUT = OUT_DIR / "user_rule"
IST = 5.5 * 3600


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    universe = pd.read_csv(OUT_DIR / "hwpr_universe.csv").symbol.tolist()
    store, cat = CandleStore(), ProductCatalog()
    data = {}
    for s in universe:
        ltp = store.read(s, "ltp", "1m")
        ltp = ltp[ltp.time >= STUDY].reset_index(drop=True)
        data[s] = dict(df=ltp, mark=store.read(s, "mark", "1m"),
                       funding=store.read(s, "funding", "1h"),
                       costs=SymbolCosts.from_spec(cat.get(s), slippage_bps=2.0),
                       tradable=tradable_mask(ltp))
        data[s]["C"] = hwpr.build_conditions(ltp)
        data[s]["masks"] = rule_masks(data[s]["C"])

    last = min(int(d["df"].time.iloc[-1]) for d in data.values())
    span = last - STUDY
    TR_END = STUDY + int(span * 0.6)
    VA_END = STUDY + int(span * 0.8)

    for sl in (0.010, 0.015):
        frames = []
        for s, d in data.items():
            C = _conditions(d["C"], d["masks"]["cross"], sl)
            r = hwpr.run(d["df"], d["mark"], d["funding"], d["costs"], C,
                         arm="E", wpr_variant="A", target_r=2.0,
                         start=STUDY, end=VA_END, tradable=d["tradable"],
                         max_stop_pct=0.10)
            f = r.to_frame()
            if len(f):
                frames.append(f)
        t = pd.concat(frames, ignore_index=True).sort_values("entry_time")
        t = t[t.entry_time < VA_END].reset_index(drop=True)

        t["window"] = np.where(t.entry_time < TR_END, "train", "valid")
        for col in ("signal_time", "entry_time", "exit_time"):
            t[col + "_utc"] = pd.to_datetime(t[col], unit="s", utc=True)
            t[col + "_ist"] = pd.to_datetime(t[col] + IST, unit="s")
        t["side_txt"] = np.where(t.side > 0, "LONG", "SHORT")
        t["win"] = t.r_net > 0
        t["hold_min"] = t.bars_held
        # 25% of capital at an x% stop risks 0.25x of equity per trade.
        t["equity_pct"] = t.r_net * CAPITAL_FRACTION * sl * 100

        cols = ["window", "symbol", "side_txt", "entry_time_utc", "entry_time_ist",
                "exit_time_utc", "hold_min", "entry_price", "stop_price",
                "target_price", "exit_price", "exit_reason", "stop_pct",
                "r_gross", "fee_r", "slip_r", "funding_r", "cost_r", "r_net",
                "win", "equity_pct", "contracts", "notional", "ambiguous"]
        path = OUT / f"trades_SL{sl*100:.2f}pct_TP2R.csv"
        t[cols].to_csv(path, index=False)

        print("=" * 104)
        print(f"SL={sl:.2%}  TP=2R  ·  cross reading  ·  {len(t):,} trades  ->  {path.name}")
        print("=" * 104)
        for w in ("train", "valid"):
            x = t[t.window == w]
            if x.empty:
                continue
            win, loss = x[x.win], x[~x.win]
            eq = START_EQUITY * np.prod(1 + x.r_net * CAPITAL_FRACTION * sl)
            print(f"  {w:5}  n={len(x):>6,}  wins={len(win):>5,}  losses={len(loss):>5,}  "
                  f"win_rate={len(win)/len(x):.4f}")
            print(f"         avg win {win.r_net.mean():+.3f}R   "
                  f"avg loss {loss.r_net.mean():+.3f}R   "
                  f"expectancy {x.r_net.mean():+.4f}R   "
                  f"equity ${eq:,.0f}")
            print(f"         exits: " + ", ".join(
                f"{k}={v:,}" for k, v in x.exit_reason.value_counts().items()))
        print("  by symbol (whole span):")
        for sym, g in t.groupby("symbol"):
            print(f"    {sym:8} n={len(g):>6,}  win={g.win.mean():.4f}  "
                  f"net={g.r_net.mean():+.4f}R")
        print("  by side (whole span):")
        for sd, g in t.groupby("side_txt"):
            print(f"    {sd:8} n={len(g):>6,}  win={g.win.mean():.4f}  "
                  f"net={g.r_net.mean():+.4f}R")
        print(f"  gross wins (before costs): {(t.r_gross > 0).mean():.4f}   "
              f"net wins (after costs): {t.win.mean():.4f}")
        print(f"  ambiguous bars (stop and target both touched in one bar, "
              f"resolved AS A LOSS): {t.ambiguous.mean():.4%}\n")

        print("  first 12 trades:")
        show = t.head(12)[["symbol", "side_txt", "entry_time_ist", "hold_min",
                           "entry_price", "exit_price", "exit_reason", "r_net"]]
        print(show.to_string(index=False, justify="left"))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
