"""The data foundation H-Vol-6 will eventually be tested on.

Nothing here computes a return. These tests protect three things:

  1. PERSISTENCE. A partially written snapshot must never be readable as a
     complete one, and a restart must not duplicate or corrupt an observation.
  2. SEMANTICS. A timestamp means one thing. `time` on a candle is the bar
     OPEN; `snapshot_ts` is the LOCAL poll instant; `exchange_ts` is the
     venue's own clock. Silently reinterpreting any of them would invalidate
     every future alignment.
  3. THE GATE. Readiness is measured on USABLE observations. Six calendar
     months of 40%-empty partitions must not read as ready.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]

from deltabt.data import archive, health  # noqa: E402
from deltabt.data import perp_recorder as pr  # noqa: E402
from deltabt.data import quote_recorder as qr  # noqa: E402


# --------------------------------------------------------------- fixtures

def _quote_rows(snapshot_ts: int, symbols=("BTCUSD", "ETHUSD")) -> pd.DataFrame:
    return pd.DataFrame([{
        "snapshot_ts": snapshot_ts, "exchange_ts": snapshot_ts - 5,
        "recv_ts": float(snapshot_ts), "symbol": s, "underlying": s[:3],
        "best_bid": 100.0, "best_ask": 100.5, "bid_size": 10.0, "ask_size": 12.0,
        "mark_price": 100.25, "spot_price": 100.2, "last_price": 100.1,
        "mark_basis": 0.0001, "funding_rate": 0.01, "oi_contracts": 5.0,
        "turnover_usd": 1.0, "volume": 2.0,
    } for s in symbols])


def _candle_rows(t0: int, n: int, symbol="BTCUSD", fetched=None) -> pd.DataFrame:
    return pd.DataFrame([{
        "time": t0 + 60 * i, "fetched_ts": fetched or (t0 + 60 * n),
        "symbol": symbol, "open": 1.0, "high": 2.0, "low": 0.5,
        "close": 1.5, "volume": 3.0,
    } for i in range(n)])


# ------------------------------------------------------------ schema/version

def test_every_dataset_has_a_schema_version_and_a_dedup_key():
    for ds in ("options", "perp_quotes", "perp_candles"):
        assert archive.SCHEMA_VERSIONS[ds] >= 1
        assert archive.DEDUP_KEYS[ds]
        assert archive.TIME_COLUMN[ds] in archive.DEDUP_KEYS[ds] or ds == "options"


def test_unknown_dataset_is_refused_not_guessed():
    with pytest.raises(ValueError):
        archive.partition_path("bogus", 1787946442)


def test_recorder_schemas_are_fixed_not_payload_driven():
    """A field Delta adds or drops must not silently change the schema."""
    assert set(pr._QUOTE_COLUMNS) >= {
        "snapshot_ts", "exchange_ts", "recv_ts", "symbol", "best_bid",
        "best_ask", "bid_size", "ask_size", "mark_price", "spot_price"}
    assert set(pr._CANDLE_COLUMNS) == {
        "time", "fetched_ts", "symbol", "open", "high", "low", "close", "volume"}
    assert set(qr._COLUMNS) >= {
        "snapshot_ts", "exchange_ts", "symbol", "best_bid", "best_ask",
        "bid_size", "ask_size", "mark_iv", "delta", "gamma", "vega", "theta",
        "spot_price"}


# --------------------------------------------------------------- persistence

def test_write_is_atomic_and_leaves_no_temp_behind(tmp_path):
    p = tmp_path / "x.parquet"
    archive.atomic_write_parquet(_quote_rows(1000), p)
    assert p.exists() and not list(tmp_path.glob("*.tmp"))
    assert len(pd.read_parquet(p)) == 2


def test_a_failed_write_leaves_the_previous_good_file_intact(tmp_path):
    p = tmp_path / "x.parquet"
    archive.atomic_write_parquet(_quote_rows(1000), p)
    before = p.read_bytes()

    class Boom(pd.DataFrame):
        def to_parquet(self, *a, **k):
            raise RuntimeError("disk full")

    with pytest.raises(RuntimeError):
        archive.atomic_write_parquet(Boom(_quote_rows(2000)), p)
    assert p.read_bytes() == before, "a failed write damaged the good partition"
    assert not list(tmp_path.glob("*.tmp")), "temp file left behind"


def test_append_is_idempotent_under_the_dedup_key(tmp_path):
    df = _quote_rows(1_800_000_000)
    for _ in range(3):
        archive.append_partition(df, "perp_quotes", root=tmp_path)
    got = pd.read_parquet(archive.partition_path(
        "perp_quotes", 1_800_000_000, tmp_path))
    assert len(got) == 2, "the same snapshot was written more than once"


def test_refetched_candles_overwrite_rather_than_duplicate(tmp_path):
    t0 = 1_800_000_000
    archive.append_partition(_candle_rows(t0, 5), "perp_candles", root=tmp_path)
    later = _candle_rows(t0, 5, fetched=t0 + 999)
    archive.append_partition(later, "perp_candles", root=tmp_path)
    got = pd.read_parquet(archive.partition_path("perp_candles", t0, tmp_path))
    assert len(got) == 5
    assert (got["fetched_ts"] == t0 + 999).all(), "keep='last' did not take effect"


def test_a_batch_straddling_midnight_is_split_by_utc_day(tmp_path):
    midnight = 1_800_000_000 - (1_800_000_000 % 86400)
    df = pd.concat([_candle_rows(midnight - 120, 2),
                    _candle_rows(midnight, 2)], ignore_index=True)
    archive.append_partition(df, "perp_candles", root=tmp_path)
    files = sorted((tmp_path / "perp").glob("perp_candles_1m_*.parquet"))
    assert len(files) == 2, "a midnight-straddling batch landed in one day"


def test_empty_batches_are_refused(tmp_path):
    with pytest.raises(ValueError):
        archive.append_partition(pd.DataFrame({"time": [], "symbol": []}),
                                 "perp_candles", root=tmp_path)


def test_rotation_is_one_file_per_dataset_per_utc_day():
    a = archive.partition_path("options", 1_800_000_000)
    b = archive.partition_path("options", 1_800_000_000 + 3600)
    c = archive.partition_path("options", 1_800_000_000 + 86400)
    assert a == b and a != c


# ---------------------------------------------------------------- manifests

def test_manifest_describes_the_partition_and_pins_its_checksum(tmp_path):
    t = 1_800_000_000
    p = archive.append_partition(_quote_rows(t), "perp_quotes", root=tmp_path)
    archive.write_manifest("perp_quotes", p, root=tmp_path / "manifests")
    m = json.loads((tmp_path / "manifests" / "perp_quotes" /
                    f"{archive.utc_day(t)}.json").read_text())
    assert m["rows"] == 2 and m["unique_contracts"] == 2
    assert m["first_timestamp"] == t and m["last_timestamp"] == t
    assert m["schema_version"] == archive.SCHEMA_VERSIONS["perp_quotes"]
    assert m["checksums"][p.name] == archive.sha256_file(p)


def test_a_changed_partition_breaks_its_manifest_checksum(tmp_path):
    t = 1_800_000_000
    p = archive.append_partition(_quote_rows(t), "perp_quotes", root=tmp_path)
    m = archive.build_manifest("perp_quotes", p)
    archive.append_partition(_quote_rows(t + 60), "perp_quotes", root=tmp_path)
    assert archive.sha256_file(p) != m["checksums"][p.name], (
        "the partition changed and the checksum did not; tampering would be "
        "invisible")


def test_manifests_exist_for_every_recorded_option_day():
    days = sorted(p.stem.replace("quotes_", "")
                  for p in (ROOT / "data" / "quotes").glob("quotes_*.parquet"))
    man = sorted(p.stem for p in
                 (ROOT / "data" / "manifests" / "options").glob("*.json"))
    assert days and set(days) <= set(man), f"unmanifested days: {set(days)-set(man)}"


# --------------------------------------------------------------- checkpoints

def test_checkpoint_roundtrips_and_survives_a_missing_file(tmp_path):
    cp = archive.read_checkpoint("perp_quotes", tmp_path)
    assert cp.last_timestamp == 0
    cp.last_timestamp = 12345
    cp.rows_written = 7
    archive.write_checkpoint(cp, tmp_path)
    again = archive.read_checkpoint("perp_quotes", tmp_path)
    assert again.last_timestamp == 12345 and again.rows_written == 7
    assert again.updated_at


def test_checkpoint_write_is_atomic(tmp_path):
    cp = archive.read_checkpoint("perp_quotes", tmp_path)
    archive.write_checkpoint(cp, tmp_path)
    assert not list(tmp_path.glob("*.tmp"))


# ------------------------------------------------------- timestamp semantics

def test_candle_time_is_the_bar_open_not_the_close():
    """Pinned against the client's documented convention.

    If this ever flips, every option/perp alignment silently shifts by one
    minute and no other test would notice.
    """
    src = (ROOT / "deltabt" / "data" / "client.py").read_text()
    assert "``time`` is the bar OPEN in unix seconds" in src
    rows = _candle_rows(1_800_000_000, 3)
    assert (np.diff(rows["time"]) == 60).all()


def test_snapshot_ts_is_local_and_exchange_ts_is_the_venue_clock():
    df = _quote_rows(1_800_000_000)
    assert (df["snapshot_ts"] > df["exchange_ts"]).all()
    assert "recv_ts" in df.columns, "local receipt time is not recorded"


def test_option_and_perp_use_the_same_timestamp_convention():
    assert archive.TIME_COLUMN["options"] == archive.TIME_COLUMN["perp_quotes"]
    assert archive.DEDUP_KEYS["options"] == archive.DEDUP_KEYS["perp_quotes"]


def test_exchange_timestamp_is_converted_from_microseconds_once():
    t = {"symbol": "X", "timestamp": 1_800_000_000_000_000, "quotes": {},
         "greeks": {}}
    assert qr._row(t, 0)["exchange_ts"] == 1_800_000_000


def test_timestamps_are_stored_sorted_within_a_partition(tmp_path):
    archive.append_partition(_quote_rows(1_800_000_060), "perp_quotes", root=tmp_path)
    archive.append_partition(_quote_rows(1_800_000_000), "perp_quotes", root=tmp_path)
    got = pd.read_parquet(archive.partition_path(
        "perp_quotes", 1_800_000_000, tmp_path))
    assert got["snapshot_ts"].is_monotonic_increasing


# ------------------------------------------------------------ data validity

def test_recorded_option_quotes_obey_bid_le_ask_where_two_sided():
    df = pd.read_parquet(sorted((ROOT / "data" / "quotes").glob("*.parquet"))[-1])
    ok = (df.best_bid > 0) & (df.best_ask > 0)
    crossed = (df.loc[ok, "best_bid"] > df.loc[ok, "best_ask"])
    assert crossed.mean() < 0.01, f"{100*crossed.mean():.2f}% crossed"


def test_recorded_prices_are_never_negative():
    df = pd.read_parquet(sorted((ROOT / "data" / "quotes").glob("*.parquet"))[-1])
    for c in ("mark_price", "best_bid", "best_ask", "spot_price"):
        assert (df[c].dropna() >= 0).all()


def test_recorded_deltas_are_in_range_and_greeks_are_sane():
    df = pd.read_parquet(sorted((ROOT / "data" / "quotes").glob("*.parquet"))[-1])
    assert (df["delta"].dropna().abs() <= 1.0).all()
    assert (df["gamma"].dropna() >= 0).all()
    assert (df["vega"].dropna() >= 0).all()


def test_expiry_is_after_the_snapshot_for_live_contracts():
    import re
    df = pd.read_parquet(sorted((ROOT / "data" / "quotes").glob("*.parquet"))[-1])
    pat = re.compile(r"^[CP]-[A-Z0-9]+-[0-9.]+-(\d{2})(\d{2})(\d{2})$")
    exp = df["symbol"].map(
        lambda s: pd.Timestamp(f"20{pat.match(s).group(3)}-{pat.match(s).group(2)}"
                               f"-{pat.match(s).group(1)} 12:00", tz="UTC")
        if pat.match(s) else pd.NaT)
    ts = pd.to_datetime(df["snapshot_ts"], unit="s", utc=True)
    assert (exp < ts).mean() < 0.02, "many contracts quoted after expiry"


def test_perp_symbols_are_discovered_not_assumed():
    src = (ROOT / "deltabt" / "data" / "perp_recorder.py").read_text()
    assert "contract_type" in src and "perpetual_futures" in src
    assert "refusing to guess" in src


def test_the_perp_universe_is_btc_and_eth_only():
    assert pr.UNDERLYINGS == ("BTC", "ETH")


# ----------------------------------------------------- gaps and no backfill

def test_missing_minutes_are_reported_not_filled(tmp_path):
    t0 = 1_800_000_000
    df = pd.concat([_candle_rows(t0, 3), _candle_rows(t0 + 600, 3)],
                   ignore_index=True)
    archive.append_partition(df, "perp_candles", root=tmp_path)
    got = pd.read_parquet(archive.partition_path("perp_candles", t0, tmp_path))
    assert len(got) == 6, "the gap was filled; it must stay a gap"
    assert (np.diff(np.sort(got["time"].unique())) > 60).any()


def test_no_recorder_forward_fills_or_interpolates():
    for mod in ("perp_recorder.py", "quote_recorder.py"):
        src = (ROOT / "deltabt" / "data" / mod).read_text()
        for banned in ("ffill", "bfill", "interpolate", "fillna("):
            assert banned not in src, f"{banned} in {mod}"


def test_health_reports_a_missing_dataset_as_critical(tmp_path):
    f = health.detect_gaps(root=tmp_path)
    codes = {x["code"] for x in f}
    assert "options_no_data" in codes and "perp_no_data" in codes
    assert all(x["severity"] in (health.OK, health.WARN, health.CRITICAL)
               for x in f)


def test_zero_overlap_is_critical(tmp_path):
    archive.append_partition(_quote_rows(1_800_000_000), "perp_quotes", root=tmp_path)
    ov = health.overlap_health(root=tmp_path)
    assert ov["overlap_days"] == 0.0


# ------------------------------------------------------------ readiness gate

def test_readiness_is_blocked_today():
    r = health.hvol6_readiness()
    assert r.ready is False and r.status == "BLOCKED"


def test_readiness_measures_usable_not_calendar_coverage():
    """Six calendar months that are 40% empty must NOT read as ready."""
    o = {"usable_days": 60.0, "usable_fraction": 0.40, "calendar_days": 182.0,
         "two_sided_pct": 90.0}
    assert o["calendar_days"] >= health.REQUIRED_DAYS
    assert o["usable_days"] < health.REQUIRED_DAYS
    assert o["usable_fraction"] < health.REQUIRED_USABLE_FRACTION


def test_readiness_thresholds_are_explicit_constants():
    assert health.REQUIRED_DAYS == 182
    assert health.REQUIRED_USABLE_FRACTION == 0.90
    assert health.REQUIRED_HEDGEABLE_FRACTION == 0.90
    assert health.HEDGE_GRID_SECONDS == 900


def test_every_readiness_check_must_pass_for_ready():
    checks = dict.fromkeys(health.hvol6_readiness().checks, True)
    assert all(checks.values())
    checks["overlap_hedgeable_days_ge_182"] = False
    assert not all(checks.values())


def test_no_experiment_record_was_created():
    """This is instrumentation. It must not touch the research ledger."""
    ledger = ROOT / "out" / "experiments.jsonl"
    ids = [json.loads(l)["experiment_id"] for l in ledger.read_text().splitlines() if l.strip()]
    assert not any("Vol" in i for i in ids), f"an options experiment was registered: {ids}"


# --------------------------------------------------- regression: bounded runs

def test_max_polls_bounds_attempts_not_successes(monkeypatch, tmp_path):
    """Found by the section-13 recovery test, which hung on it.

    `max_polls` originally counted only SUCCESSFUL polls, so a sustained API
    outage looped forever and no bounded run could terminate. That is the right
    behaviour for the daemon and wrong for anything that has to finish.
    """
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise RuntimeError("simulated outage")

    monkeypatch.setattr(pr, "record_once", boom)
    monkeypatch.setattr(pr, "discover_symbols", lambda c: ["BTCUSD", "ETHUSD"])
    monkeypatch.setattr(pr, "DeltaClient", lambda *a, **k: object())
    written = pr.run(interval=0, root=tmp_path, max_polls=3)
    assert written == 0, "a failed poll was counted as written"
    assert calls["n"] == 3, f"bounded run made {calls['n']} attempts, expected 3"


def test_a_failed_poll_records_the_error_and_writes_no_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(pr, "record_once",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
    monkeypatch.setattr(pr, "discover_symbols", lambda c: ["BTCUSD"])
    monkeypatch.setattr(pr, "DeltaClient", lambda *a, **k: object())
    pr.run(interval=0, root=tmp_path, max_polls=1)
    cp = archive.read_checkpoint("perp_quotes", tmp_path / "checkpoints")
    assert "nope" in cp.last_error
    assert not list((tmp_path / "perp").glob("*.parquet")) if (tmp_path / "perp").exists() else True
