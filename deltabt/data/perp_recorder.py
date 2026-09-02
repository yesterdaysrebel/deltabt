"""Forward recorder for the perpetual hedge instrument.

WHY THIS EXISTS
    The options gate found the gap that blocks H-Vol-6: the perpetual 1-minute
    series ends 2026-08-12 10:52 and the option quote recorder starts
    2026-08-24 16:03. **Zero overlap.** A delta-hedged option study needs the
    underlying at the hedge instants, and nothing was recording it. Every hour
    this is not running is an hour H-Vol-6 can never be tested over.

TWO SERIES, DELIBERATELY, BECAUSE THEY ARE DIFFERENT KINDS OF FACT

    perp_quotes   `/v2/tickers` snapshots at a 60s cadence, carrying
                  best_bid / best_ask / bid_size / ask_size / mark_price /
                  spot_price / funding_rate / mark_basis. Recorded in real
                  time; each row is an observation that existed at a local
                  instant. **This is the executable series.** A delta hedge
                  crosses this spread.

    perp_candles  1-minute OHLCV from `/v2/history/candles`. A derived
                  exchange product, not a live observation, and `close` is a
                  LAST TRADED PRICE -- not a fill anyone was offered.
                  Recorded because H-Vol-6 must be able to build its own
                  15-minute hedge grid from a raw minute series rather than
                  inherit one, and because it is the continuity check on the
                  quote series.

WHY THE CANDLE WINDOW IS SMALL AND WHY THAT MATTERS
    Each poll requests only the trailing `CANDLE_LOOKBACK_SECONDS` (15 min).
    A missed poll or two therefore self-heals within the recorder's own
    operating life, while a genuine outage leaves a genuine gap that
    `health.py` reports. Requesting a wide window on restart would silently
    reconstruct history the recorder was not running for, which is exactly the
    backfill the research gate forbids. `fetched_ts` is stored on every bar so
    a bar recorded live is distinguishable from one recovered minutes later.

WHAT THIS DOES NOT DO
    No forward fill. No interpolation. No substituting mark for a missing
    quote. No widening the window to cover an outage. A gap is data.

UNIVERSE
    BTCUSD and ETHUSD only, discovered from the products endpoint rather than
    assumed -- the gate's instruction is to keep collection focused on the
    eventual H-Vol-6 experiment, and every extra symbol is bytes that make the
    partitions slower to read for no hypothesis.

READ-ONLY. Public endpoints, no keys, no signing, no order path.
"""

from __future__ import annotations

import argparse
import logging
import signal
import time
from pathlib import Path

import pandas as pd

from deltabt.data import archive
from deltabt.data.client import DeltaClient

log = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 60
CANDLE_LOOKBACK_SECONDS = 900
UNDERLYINGS = ("BTC", "ETH")

_QUOTE_COLUMNS = {
    "snapshot_ts": "int64",       # local poll instant, seconds
    "exchange_ts": "int64",       # ticker `timestamp`, microseconds -> seconds
    "recv_ts": "float64",         # local receipt, sub-second
    "symbol": "object",
    "underlying": "object",
    "best_bid": "float64",
    "best_ask": "float64",
    "bid_size": "float64",
    "ask_size": "float64",
    "mark_price": "float64",
    "spot_price": "float64",
    "last_price": "float64",
    "mark_basis": "float64",
    "funding_rate": "float64",
    "oi_contracts": "float64",
    "turnover_usd": "float64",
    "volume": "float64",
}

_CANDLE_COLUMNS = {
    "time": "int64",              # BAR OPEN, unix seconds UTC
    "fetched_ts": "int64",        # local instant this bar was retrieved
    "symbol": "object",
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "volume": "float64",
}


def _f(value, default: float = float("nan")) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def discover_symbols(client: DeltaClient,
                     underlyings: tuple[str, ...] = UNDERLYINGS) -> list[str]:
    """Resolve perpetual symbols from the products endpoint. Never guessed.

    Naming on this venue is not uniformly derivable -- the spot indices are
    `.DEXBTUSD` for BTC but `.DEETHUSD` for ETH -- so the perpetual symbol is
    read off `contract_type == "perpetual_futures"` rather than assembled from
    the underlying.
    """
    out = []
    for p in client.products():
        if p.get("contract_type") != "perpetual_futures":
            continue
        under = (p.get("underlying_asset") or {}).get("symbol") \
            or p.get("underlying_asset_symbol")
        if under in underlyings and p.get("symbol"):
            out.append(p["symbol"])
    missing = set(underlyings) - {s[:3] for s in out}
    if len(out) < len(underlyings):
        raise RuntimeError(
            f"only resolved {out} for {underlyings}; refusing to guess "
            f"(unresolved: {missing})")
    return sorted(set(out))


def quote_snapshot(client: DeltaClient, symbols: list[str], *,
                   now: int | None = None) -> pd.DataFrame:
    """One pass over the perpetual tickers, filtered to the target symbols."""
    snapshot_ts = int(time.time()) if now is None else now
    tickers = client.tickers(contract_types="perpetual_futures")
    recv = time.time()
    if not tickers:
        raise RuntimeError("empty perpetual ticker response")
    wanted = set(symbols)
    rows = []
    for t in tickers:
        if t.get("symbol") not in wanted:
            continue
        q = t.get("quotes") or {}
        ex_us = t.get("timestamp")
        rows.append({
            "snapshot_ts": snapshot_ts,
            "exchange_ts": int(ex_us // 1_000_000) if isinstance(ex_us, int) else 0,
            "recv_ts": recv,
            "symbol": t.get("symbol"),
            "underlying": t.get("underlying_asset_symbol"),
            "best_bid": _f(q.get("best_bid")),
            "best_ask": _f(q.get("best_ask")),
            "bid_size": _f(q.get("bid_size")),
            "ask_size": _f(q.get("ask_size")),
            "mark_price": _f(t.get("mark_price")),
            "spot_price": _f(t.get("spot_price")),
            "last_price": _f(t.get("close")),
            "mark_basis": _f(t.get("mark_basis")),
            "funding_rate": _f(t.get("funding_rate")),
            "oi_contracts": _f(t.get("oi_contracts")),
            "turnover_usd": _f(t.get("turnover_usd"), 0.0),
            "volume": _f(t.get("volume"), 0.0),
        })
    if len(rows) != len(wanted):
        raise RuntimeError(
            f"expected {len(wanted)} perpetual tickers, got {len(rows)}; "
            "refusing a partial snapshot")
    df = pd.DataFrame(rows)
    for col, dtype in _QUOTE_COLUMNS.items():
        if col not in df.columns:
            df[col] = pd.Series(dtype=dtype)
        if dtype != "object":
            df[col] = df[col].astype(dtype)
    return df[list(_QUOTE_COLUMNS)]


def candle_batch(client: DeltaClient, symbols: list[str], *,
                 lookback: int = CANDLE_LOOKBACK_SECONDS,
                 now: int | None = None) -> pd.DataFrame:
    """The trailing `lookback` seconds of 1-minute bars for each symbol.

    The client drops the currently-forming bar, so every row here is a closed
    bar. `time` is the bar OPEN in unix seconds, which is the client's
    documented and separately tested convention.
    """
    end = int(time.time()) if now is None else now
    start = end - lookback
    rows = []
    for sym in symbols:
        bars = client.candles(sym, "1m", start, end)
        for b in bars:
            rows.append({
                "time": int(b["time"]), "fetched_ts": end, "symbol": sym,
                "open": _f(b.get("open")), "high": _f(b.get("high")),
                "low": _f(b.get("low")), "close": _f(b.get("close")),
                "volume": _f(b.get("volume"), 0.0),
            })
    if not rows:
        raise RuntimeError("no 1m candles returned for any symbol")
    df = pd.DataFrame(rows)
    for col, dtype in _CANDLE_COLUMNS.items():
        if dtype != "object":
            df[col] = df[col].astype(dtype)
    return df[list(_CANDLE_COLUMNS)]


def _sidecar_roots(root: Path | None) -> tuple[Path | None, Path | None]:
    """Manifests and checkpoints follow the data root.

    Without this a test pointed at a temp directory would still write its
    manifests and checkpoints into the live `data/` tree, so an isolated run
    would silently mutate production state.
    """
    if root is None:
        return None, None
    return Path(root) / "manifests", Path(root) / "checkpoints"


def _commit(df: pd.DataFrame, dataset: str, time_col: str,
            root: Path | None) -> Path:
    """Persist one batch, refresh its manifest, advance its checkpoint."""
    man_root, cp_root = _sidecar_roots(root)
    path = archive.append_partition(df, dataset, root=root)

    # MANIFEST EVERY DAY THE BATCH TOUCHED, NOT JUST THE ONE IT RETURNED.
    #
    # append_partition splits a batch that straddles midnight and returns only
    # the LAST path -- groupby yields days ascending, so that is the NEW day.
    # Manifesting only that left YESTERDAY's partition rewritten with a stale
    # manifest, and verification then refuses the entire backup:
    #
    #     source failed verification; refusing to propagate a partition that
    #     does not match its manifest
    #
    # It happened on 2026-09-01 (parquet rewritten 00:13:29, manifest written
    # 00:00:29) and again on 2026-09-02, both times on perp_candles, because
    # the 00:0x poll carries the previous day's final minutes.
    #
    # unmanifested_days() below does NOT catch this: it checks a manifest
    # EXISTS, not that it AGREES.
    # Grouped exactly as append_partition groups, so the set of days is the
    # same set it wrote. utc_day returns a DATE STRING, so a representative
    # timestamp from each group is what resolves the path.
    tcol = archive.TIME_COLUMN[dataset]
    for _day, part in df.groupby(df[tcol].map(archive.utc_day)):
        day_path = archive.partition_path(dataset, int(part[tcol].iloc[0]), root)
        if day_path.exists():
            archive.write_manifest(dataset, day_path, root=man_root)

    # See quote_recorder.record_once: the manifest write cannot complain, so
    # something has to ask. Six sealed partitions went undescribed for two days
    # in 2026-08 while every poll logged success.
    try:
        gaps = archive.unmanifested_days(dataset, root=root,
                                         manifest_root=man_root)
        if gaps:
            log.warning("SEALED DAYS WITH NO MANIFEST: %s %s -- rebuild with "
                        "archive.rebuild_manifest so they are labelled "
                        "'rebuilt', never 'recorder'", dataset, ", ".join(gaps))
    except Exception:                                    # noqa: BLE001
        log.exception("manifest coverage check failed for %s; batch is still "
                      "saved", dataset)
    cp = archive.read_checkpoint(dataset, cp_root)
    cp.last_timestamp = int(df[time_col].max())
    cp.last_partition = str(path)
    cp.rows_written += len(df)
    cp.batches_written += 1
    cp.schema_version = archive.SCHEMA_VERSIONS[dataset]
    cp.checksum = archive.sha256_file(path)
    cp.last_error = ""
    archive.write_checkpoint(cp, cp_root)
    return path


def record_once(client: DeltaClient | None = None, *,
                symbols: list[str] | None = None,
                root: Path | None = None) -> dict[str, int]:
    """One poll: a quote snapshot and a trailing candle batch, both persisted."""
    client = client or DeltaClient()
    symbols = symbols or discover_symbols(client)
    written = {}
    q = quote_snapshot(client, symbols)
    p = _commit(q, "perp_quotes", "snapshot_ts", root)
    written["perp_quotes"] = len(q)

    c = candle_batch(client, symbols)
    _commit(c, "perp_candles", "time", root)
    written["perp_candles"] = len(c)

    lag = float(q["recv_ts"].iloc[0] - q["exchange_ts"].iloc[0])
    log.info("perp: %d quotes, %d bars -> %s (exchange lag %.1fs)",
             len(q), len(c), p.name, lag)
    return written


def run(*, interval: int = DEFAULT_INTERVAL_SECONDS,
        root: Path | None = None, max_polls: int | None = None) -> int:
    """Poll until interrupted. Returns SUCCESSFUL polls; `max_polls` bounds ATTEMPTS.

    The failure is written to the checkpoint rather than only logged, so a
    later audit can see that the recorder was alive and the API was not.
    """
    client = DeltaClient()
    symbols = discover_symbols(client)
    log.info("recording perpetuals %s every %ds", symbols, interval)
    stopping = {"now": False}

    def _stop(signum, _frame):
        log.info("signal %s received, stopping after this poll", signum)
        stopping["now"] = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except ValueError:
            pass

    attempts = written = 0
    lock_root = Path(root) / "locks" if root is not None else None
    with archive.single_instance("perp_recorder", lock_root):
        while not stopping["now"]:
            started = time.monotonic()
            attempts += 1
            try:
                record_once(client, symbols=symbols, root=root)
                written += 1
            except Exception as exc:  # noqa: BLE001 -- a recorder that exits records nothing
                log.warning("perp poll failed, continuing: %s: %s",
                            type(exc).__name__, exc)
                _, cp_root = _sidecar_roots(root)
                for ds in ("perp_quotes", "perp_candles"):
                    cp = archive.read_checkpoint(ds, cp_root)
                    cp.last_error = f"{type(exc).__name__}: {exc}"
                    archive.write_checkpoint(cp, cp_root)
            # ATTEMPTS, not successes. Bounding on successes means a sustained
            # outage never terminates a bounded run -- which is right for the
            # daemon and wrong for anything that has to finish, including the
            # recovery test that found this.
            if max_polls is not None and attempts >= max_polls:
                break
            remaining = interval - (time.monotonic() - started)
            while remaining > 0 and not stopping["now"]:
                time.sleep(min(1.0, remaining))
                remaining -= 1.0
    return written


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--max-polls", type=int, default=None)
    args = ap.parse_args()
    if args.once:
        print(record_once())
        return
    n = run(interval=args.interval, max_polls=args.max_polls)
    log.info("perp recorder wrote %d polls", n)


if __name__ == "__main__":
    main()
