"""Backtest a grid of strategy families, timeframes and symbols.

    PYTHONPATH=. python3 -u scripts/backtest_sweep.py

Every cell is one :class:`~deltabt.spec.StrategySpec` executed through
``deltabt.rulecore`` and ``deltabt.engine.run_backtest`` -- the same definition
the paper trader would run, so a promising cell is promotable without being
reimplemented.

WHAT IS HELD CONSTANT ACROSS CELLS, AND WHY
    Comparing families across timeframes only means something if the things
    that are not the strategy do not move with the timeframe:

    * ``max_hold_bars`` is scaled to a constant 48 HOURS. The default is 240
      bars, which is 4 hours at 1m and 40 days at 240m -- so an unscaled grid
      would be comparing a scalp against a swing trade and calling the
      difference "timeframe".
    * ``confirm_minutes`` is held at a constant 5:1 ratio to the primary, the
      ratio the whole H-WPR family was designed around, rather than pinned at
      1m where a 240m primary would be confirmed by noise.
    * The cost gate stays at its default ``max_cost_per_r = 0.15``. It rejects
      a lot -- that rejection IS the result on fine timeframes, and the count
      is recorded per cell rather than tuned away.

THIS IS A BACKTEST SWEEP, NOT AN EXPERIMENT
    Nothing here is pre-registered and nothing is written to
    ``out/experiments.jsonl``. It reports what each configuration did on the
    cached data. With this many cells some will look good by chance; the
    ``cells`` and ``symbols_agreeing`` columns exist so that can be seen rather
    than discovered later.
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import pandas as pd

from deltabt.config import CACHE_DIR, OUT_DIR
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.catalog import FAMILIES
from deltabt.harness import TIMEFRAMES, load_symbol, run_cell

log = logging.getLogger("sweep")

OUT = OUT_DIR / "sweep"

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--families", nargs="*", default=None)
    ap.add_argument("--timeframes", nargs="*", type=int, default=None)
    # Hold the CONFIRMATION timeframe fixed instead of letting it track the
    # primary at 5:1. Each value listed here is run against every primary it
    # divides; primaries it does not divide are skipped rather than silently
    # rounded, because a confirmation bar that does not tile the primary lands
    # on a different instant every bar. Omit for the ratio, which is default.
    ap.add_argument("--confirm", nargs="*", type=int, default=None)
    # Override the family's ATR stop multiplier. cost_r = round_trip_rate /
    # stop_pct, so this is the only knob that moves cost directly rather than
    # by filtering signals. Skipped for fixed-percentage-stop families.
    ap.add_argument("--stop-mult", nargs="*", type=float, default=None)
    # Max hold in hours. The default 24h is the binding constraint on any wide
    # stop: a 2R target on an 8xATR stop is 16xATR away and a day is not long
    # enough to get there, so without this the stop sweep measures the cap.
    ap.add_argument("--hold-hours", nargs="*", type=int, default=None)
    ap.add_argument("--out", default=str(OUT / "backtests.csv"))
    args = ap.parse_args()

    symbols = args.symbols or sorted(
        p.name for p in CACHE_DIR.iterdir()
        if p.is_dir() and (p / "ltp_1m.parquet").exists() and p.name.endswith("USD")
        and not p.name.startswith(("C-", "P-", "OI:", "MARK:", "."))
    )
    families = args.families or list(FAMILIES)
    timeframes = args.timeframes or list(TIMEFRAMES)

    catalog = ProductCatalog()
    OUT.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    total = len(symbols) * len(families) * len(timeframes)
    log.info("%d symbols x %d families x %d timeframes = %d cells",
             len(symbols), len(families), len(timeframes), total)

    t0 = time.time()
    done = 0
    for symbol in symbols:
        data = load_symbol(symbol)
        if data is None:
            log.warning("%s: no candles", symbol)
            continue
        try:
            costs = SymbolCosts.from_spec(catalog.get(symbol))
        except (KeyError, LookupError) as exc:
            # ONLY a genuinely absent product is skippable. A broad `except
            # Exception` here previously swallowed a NameError and reported it
            # as "no product spec", so the sweep wrote an empty CSV and exited
            # 0. A programming error must crash, not be logged as missing data.
            log.warning("%s: not in the product catalog (%s)", symbol, exc)
            continue
        cache: dict = {}
        for minutes in timeframes:
            for family in families:
                for confirm in (args.confirm or [None]):
                    if confirm is not None and (confirm > minutes
                                                or minutes % confirm):
                        continue
                    for mult in (args.stop_mult or [None]):
                      for hold in (args.hold_hours or [None]):
                        try:
                            row = run_cell(data, family, minutes, costs,
                                           cache, confirm, mult, hold)
                            row["stop_mult"] = mult
                            row["hold_hours"] = hold
                            rows.append(row)
                        except Exception as exc:          # noqa: BLE001
                            log.warning("%s %s @%dm/%sm x%s h%s failed: %s",
                                        symbol, family, minutes, confirm, mult,
                                        hold, exc)
                            rows.append(dict(symbol=symbol, family=family,
                                             timeframe_min=minutes,
                                             confirm_min=confirm,
                                             stop_mult=mult, hold_hours=hold,
                                             status=f"error: {exc}"))
                done += 1
            log.info("%s @%dm  (%d/%d, %.0fs)", symbol, minutes, done, total,
                     time.time() - t0)
        cache.clear()

    out = pd.DataFrame(rows)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    log.info("wrote %s (%d rows) in %.1f min", args.out, len(out), (time.time() - t0) / 60)


if __name__ == "__main__":
    main()
