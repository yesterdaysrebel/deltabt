"""Forward recorder for the option quote surface.

**This exists because the data it collects cannot be obtained any other way,
and every hour it is not running is an hour permanently absent from the
record.** Delta serves candle history for premium and mark going back to
2024-01, but it publishes *no history at all* for quotes, implied vol or
greeks -- `best_bid`, `best_ask`, `bid_iv`, `ask_iv`, `mark_iv` and the greek
set exist only in the live `/v2/tickers` snapshot and are gone the moment they
are replaced.

That gap is not cosmetic. `docs/options_feasibility.md` measures the round trip
on an ATM straddle at 6-11% of premium, and the **quoted spread is the larger
half of it**: 1.34% of mid at the median against a 1.18% fee term. Every
backtest conclusion on this venue is therefore conditional on a spread
assumption applied to a past date on which the spread was never observed. This
recorder is what eventually replaces that assumption with a measurement.

It is read-only against public endpoints, like everything else in this
repository -- no keys, no signing, no order path.

Design notes, all of them consequences of "a lost snapshot is unrecoverable":

* **Daily Parquet partitions, appended per snapshot.** A crash costs at most
  the snapshot in flight, never the day. A single growing file would risk the
  whole history to one corrupt write.
* **Writes are atomic.** Each partition is rewritten via a temp file and
  `replace()`, so an interrupted write leaves the previous good file in place
  rather than a truncated one.
* **A failed poll is logged and skipped, never fatal.** A recorder that exits
  on one HTTP error records nothing over a weekend.
* **The snapshot timestamp is the local poll instant**, recorded alongside the
  exchange's own `timestamp` field so clock skew is measurable after the fact
  rather than assumed away.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import signal
import time
from pathlib import Path

import pandas as pd

from deltabt.config import DATA_DIR
from deltabt.data.client import DeltaClient

log = logging.getLogger(__name__)

QUOTE_DIR = DATA_DIR / "quotes"

#: Default poll cadence. The purpose is to characterise the spread across
#: time-of-day, moneyness and expiry proximity -- not to reconstruct a tick
#: tape -- so 15 minutes is ample and keeps a year of history near 1 GB.
#: At 1,070 live contracts a snapshot is ~1,070 rows, so 15m is ~103k rows/day.
DEFAULT_INTERVAL_SECONDS = 900

CONTRACT_TYPES = ("call_options", "put_options")

#: Written for every contract on every snapshot. Fixed here rather than taken
#: from whatever the payload happens to contain, so a field Delta adds or drops
#: cannot silently change the schema mid-history.
_COLUMNS = {
    "snapshot_ts": "int64",
    "exchange_ts": "int64",
    "symbol": "object",
    "underlying": "object",
    "mark_price": "float64",
    "spot_price": "float64",
    "best_bid": "float64",
    "best_ask": "float64",
    "bid_size": "float64",
    "ask_size": "float64",
    "mark_iv": "float64",
    "bid_iv": "float64",
    "ask_iv": "float64",
    "delta": "float64",
    "gamma": "float64",
    "vega": "float64",
    "theta": "float64",
    "oi_contracts": "float64",
    "turnover_usd": "float64",
    "volume": "float64",
}


def _f(value, default: float = float("nan")) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _row(ticker: dict, snapshot_ts: int) -> dict:
    quotes = ticker.get("quotes") or {}
    greeks = ticker.get("greeks") or {}
    # `timestamp` is microseconds on this endpoint, unlike the candle
    # endpoint's seconds. Normalised to seconds so the two are comparable.
    exchange_us = ticker.get("timestamp")
    exchange_ts = int(exchange_us // 1_000_000) if isinstance(exchange_us, int) else 0
    return {
        "snapshot_ts": snapshot_ts,
        "exchange_ts": exchange_ts,
        "symbol": ticker.get("symbol"),
        "underlying": ticker.get("underlying_asset_symbol"),
        "mark_price": _f(ticker.get("mark_price")),
        "spot_price": _f(greeks.get("spot"), _f(ticker.get("spot_price"))),
        "best_bid": _f(quotes.get("best_bid")),
        "best_ask": _f(quotes.get("best_ask")),
        "bid_size": _f(quotes.get("bid_size")),
        "ask_size": _f(quotes.get("ask_size")),
        "mark_iv": _f(quotes.get("mark_iv")),
        "bid_iv": _f(quotes.get("bid_iv")),
        "ask_iv": _f(quotes.get("ask_iv")),
        "delta": _f(greeks.get("delta")),
        "gamma": _f(greeks.get("gamma")),
        "vega": _f(greeks.get("vega")),
        "theta": _f(greeks.get("theta")),
        "oi_contracts": _f(ticker.get("oi_contracts")),
        "turnover_usd": _f(ticker.get("turnover_usd"), 0.0),
        "volume": _f(ticker.get("volume"), 0.0),
    }


def snapshot(client: DeltaClient, *, now: int | None = None) -> pd.DataFrame:
    """One full pass over the live option surface.

    Both contract types are pulled before anything is returned, so a partial
    failure yields nothing rather than a snapshot silently missing every put.
    """
    snapshot_ts = int(time.time()) if now is None else now
    rows: list[dict] = []
    for ct in CONTRACT_TYPES:
        tickers = client.tickers(contract_types=ct)
        if not tickers:
            raise RuntimeError(f"empty ticker response for {ct}")
        rows.extend(_row(t, snapshot_ts) for t in tickers)

    df = pd.DataFrame(rows)
    for col, dtype in _COLUMNS.items():
        if col not in df.columns:
            df[col] = pd.Series(dtype=dtype)
        df[col] = df[col].astype(dtype) if dtype != "object" else df[col]
    return df[list(_COLUMNS)]


def partition_path(snapshot_ts: int, quote_dir: Path = QUOTE_DIR) -> Path:
    day = dt.datetime.fromtimestamp(snapshot_ts, dt.timezone.utc).strftime("%Y-%m-%d")
    return Path(quote_dir) / f"quotes_{day}.parquet"


def append(df: pd.DataFrame, *, quote_dir: Path = QUOTE_DIR) -> Path:
    """Append one snapshot to its UTC-day partition, atomically.

    Deduplicated on (snapshot_ts, symbol) so a retry or an overlapping run
    cannot double-count a contract into the spread statistics.
    """
    if df.empty:
        raise ValueError("refusing to append an empty snapshot")
    path = partition_path(int(df["snapshot_ts"].iloc[0]), quote_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        merged = pd.concat([pd.read_parquet(path), df], ignore_index=True)
        merged = merged.drop_duplicates(subset=["snapshot_ts", "symbol"], keep="last")
    else:
        merged = df

    tmp = path.with_suffix(".parquet.tmp")
    merged.to_parquet(tmp, index=False)
    tmp.replace(path)
    return path


def record_once(client: DeltaClient | None = None, *, quote_dir: Path = QUOTE_DIR) -> int:
    """Take one snapshot and persist it. Returns the row count written."""
    client = client or DeltaClient()
    df = snapshot(client)
    path = append(df, quote_dir=quote_dir)
    spread = _median_half_spread(df)
    log.info(
        "recorded %d contracts -> %s (median half-spread %.2f%% of mid)",
        len(df), path.name, spread * 100 if spread == spread else float("nan"),
    )
    return len(df)


def _median_half_spread(df: pd.DataFrame) -> float:
    """Median half-spread as a fraction of mid, over two-sided quotes only."""
    bid, ask = df["best_bid"], df["best_ask"]
    m = (bid > 0) & (ask > 0) & (ask >= bid)
    if not m.any():
        return float("nan")
    mid = (ask[m] + bid[m]) / 2.0
    return float((((ask[m] - bid[m]) / 2.0) / mid).median())


def run(
    *,
    interval: int = DEFAULT_INTERVAL_SECONDS,
    quote_dir: Path = QUOTE_DIR,
    max_snapshots: int | None = None,
) -> int:
    """Poll on a fixed cadence until interrupted. Returns snapshots written.

    Never raises on a failed poll. The whole value of this process is that it
    keeps running -- an unhandled HTTP error at 03:00 that kills the recorder
    costs every snapshot until somebody notices.
    """
    client = DeltaClient()
    stopping = {"now": False}

    def _stop(signum, _frame):
        log.info("signal %s received, finishing after this snapshot", signum)
        stopping["now"] = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except ValueError:
            pass  # not on the main thread; caller handles shutdown

    written = 0
    while not stopping["now"]:
        started = time.monotonic()
        try:
            record_once(client, quote_dir=quote_dir)
            written += 1
        except Exception as exc:  # noqa: BLE001 -- see docstring
            log.warning("snapshot failed, continuing: %s: %s", type(exc).__name__, exc)

        if max_snapshots is not None and written >= max_snapshots:
            break
        sleep_for = interval - (time.monotonic() - started)
        while sleep_for > 0 and not stopping["now"]:
            time.sleep(min(1.0, sleep_for))
            sleep_for -= 1.0
    return written


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    ap = argparse.ArgumentParser(description="Record the Delta India option quote surface.")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS,
                    help=f"seconds between snapshots (default {DEFAULT_INTERVAL_SECONDS})")
    ap.add_argument("--once", action="store_true", help="take a single snapshot and exit")
    ap.add_argument("--quote-dir", default=str(QUOTE_DIR))
    args = ap.parse_args()

    quote_dir = Path(args.quote_dir)
    if args.once:
        record_once(quote_dir=quote_dir)
        return
    log.info("recording every %ds into %s -- Ctrl-C to stop", args.interval, quote_dir)
    n = run(interval=args.interval, quote_dir=quote_dir)
    log.info("wrote %d snapshots", n)


if __name__ == "__main__":
    main()
