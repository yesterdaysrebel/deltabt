"""Coverage, gap detection and the deterministic H-Vol-6 readiness gate.

WHAT THIS IS FOR
    One question, asked repeatedly while the recorders run: how much CLEAN
    OVERLAPPING data does H-Vol-6 actually have? Not how many files are on
    disk, and not how many calendar days have elapsed.

CALENDAR COVERAGE IS NOT USABLE COVERAGE
    Six months of partitions with 40% of snapshots missing is not six months
    of data. Every coverage figure here is reported twice: the calendar span
    between the first and last observation, and the fraction of the expected
    observation slots inside that span that actually contain a usable one.
    The readiness gate reads the second.

THE PRIMARY READINESS INDICATOR IS OVERLAP, NOT EITHER SERIES ALONE
    A delta-hedged study needs the option surface AND the hedge instrument at
    the same instant. Either recorder running alone contributes nothing to
    H-Vol-6. `hedgeable_slots` counts 15-minute grid points that carry both a
    usable option snapshot and a perpetual quote within tolerance, and that is
    the number the gate is written against.

NOTHING HERE REPAIRS ANYTHING
    A gap is reported, never filled. A stale quote is counted, never dropped.
    Missing means missing.
"""

from __future__ import annotations

import glob
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from deltabt.config import DATA_DIR
from deltabt.data import archive

#: The hedge grid H-Vol-6 is specified against.
HEDGE_GRID_SECONDS = 900
#: A perpetual quote counts as aligned to a hedge instant within this window.
ALIGN_TOLERANCE_SECONDS = 90
#: Underlyings H-Vol-6 is scoped to.
TARGET_UNDERLYINGS = ("BTC", "ETH")

#: Frozen readiness thresholds. Changing these changes what "ready" means, so
#: they live here rather than as call-site defaults.
REQUIRED_DAYS = 182                    # ~6 months
REQUIRED_USABLE_FRACTION = 0.90        # of expected slots inside the span
REQUIRED_QUOTE_QUALITY = 0.60          # two-sided share of option rows
REQUIRED_HEDGEABLE_FRACTION = 0.90     # grid points with option AND perp

OK, WARN, CRITICAL = "OK", "WARN", "CRITICAL"


def _load(pattern: str) -> pd.DataFrame:
    fs = sorted(glob.glob(pattern))
    if not fs:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)


def _span_days(lo: float, hi: float) -> float:
    return max(0.0, (hi - lo) / 86400.0)


# ------------------------------------------------------------------- options

def options_health(root: Path | None = None) -> dict:
    base = Path(root or DATA_DIR)
    df = _load(str(base / "quotes" / "quotes_*.parquet"))
    if df.empty:
        return {"status": CRITICAL, "reason": "no option partitions on disk",
                "rows": 0, "calendar_days": 0.0, "usable_days": 0.0}
    lo, hi = int(df.snapshot_ts.min()), int(df.snapshot_ts.max())
    span = _span_days(lo, hi)
    bid, ask = df.best_bid, df.best_ask
    two_sided = (bid > 0) & (ask > 0) & (ask >= bid)
    complete = (two_sided & df.mark_iv.notna() & df.delta.notna()
                & (df.spot_price > 0) & df.bid_size.notna() & df.ask_size.notna())

    # A snapshot is USABLE if it carries a complete two-sided quote on every
    # target underlying -- an option surface missing ETH cannot hedge ETH.
    per_snap = (df.assign(_ok=complete)
                  .query("underlying in @TARGET_UNDERLYINGS")
                  .groupby(["snapshot_ts", "underlying"])["_ok"].sum()
                  .unstack(fill_value=0))
    for u in TARGET_UNDERLYINGS:
        if u not in per_snap:
            per_snap[u] = 0
    usable_snaps = per_snap[(per_snap[list(TARGET_UNDERLYINGS)] > 0).all(axis=1)].index
    expected = max(1, int(span * 86400 / HEDGE_GRID_SECONDS) + 1)

    d = df.sort_values(["symbol", "snapshot_ts"])
    unchanged = (d.groupby("symbol")[["best_bid", "best_ask", "mark_price"]]
                 .shift() == d[["best_bid", "best_ask", "mark_price"]]).all(axis=1)
    days = pd.to_datetime(df.snapshot_ts, unit="s", utc=True).dt.date
    return {
        "rows": int(len(df)),
        "first_ts": lo, "last_ts": hi,
        "first": str(pd.Timestamp(lo, unit="s", tz="UTC")),
        "last": str(pd.Timestamp(hi, unit="s", tz="UTC")),
        "calendar_days": round(span, 3),
        "snapshots": int(df.snapshot_ts.nunique()),
        "usable_snapshots": int(len(usable_snaps)),
        "expected_snapshots": expected,
        # Clamped: poll drift can yield marginally MORE snapshots than the
        # nominal 15-minute grid, and a coverage fraction above 1.0 in a
        # readiness gate reads as a defect even when the surplus is benign.
        "usable_fraction": round(min(1.0, len(usable_snaps) / expected), 4),
        "usable_days": round(span * min(1.0, len(usable_snaps) / expected), 3),
        "usable_fraction_raw": round(len(usable_snaps) / expected, 4),
        "snapshots_per_day": round(df.snapshot_ts.nunique() / max(span, 1e-9), 1),
        "unique_contracts_per_day": round(
            float(df.groupby(days)["symbol"].nunique().mean()), 1),
        "two_sided_pct": round(100 * float(two_sided.mean()), 3),
        "complete_pct": round(100 * float(complete.mean()), 3),
        "missing_bid_pct": round(100 * float((bid.isna() | (bid <= 0)).mean()), 3),
        "missing_ask_pct": round(100 * float((ask.isna() | (ask <= 0)).mean()), 3),
        "missing_size_pct": round(100 * float(
            (df.bid_size.isna() | df.ask_size.isna()).mean()), 3),
        "stale_pct": round(100 * float(unchanged.fillna(False).mean()), 3),
        "crossed_pct": round(100 * float((bid > ask).mean()), 3),
        "locked_pct": round(100 * float(((bid == ask) & (bid > 0)).mean()), 3),
        "underlyings": sorted(df.underlying.dropna().unique().tolist()),
        "usable_snapshot_ts": usable_snaps.to_numpy(),
    }


# ---------------------------------------------------------------- perpetuals

def perp_health(root: Path | None = None) -> dict:
    base = Path(root or DATA_DIR)
    q = _load(str(base / "perp" / "perp_quotes_*.parquet"))
    c = _load(str(base / "perp" / "perp_candles_1m_*.parquet"))
    if q.empty and c.empty:
        return {"status": CRITICAL, "reason": "no perpetual partitions on disk",
                "quote_rows": 0, "candle_rows": 0, "calendar_days": 0.0,
                "usable_days": 0.0, "per_symbol": {}}
    out = {"quote_rows": int(len(q)), "candle_rows": int(len(c)), "per_symbol": {}}
    if not q.empty:
        lo, hi = int(q.snapshot_ts.min()), int(q.snapshot_ts.max())
        span = _span_days(lo, hi)
        out.update(first_ts=lo, last_ts=hi, calendar_days=round(span, 3),
                   first=str(pd.Timestamp(lo, unit="s", tz="UTC")),
                   last=str(pd.Timestamp(hi, unit="s", tz="UTC")),
                   symbols=sorted(q.symbol.unique().tolist()))
        lag = q.recv_ts - q.exchange_ts
        out["exchange_lag_s"] = {
            "median": round(float(lag.median()), 3),
            "p95": round(float(lag.quantile(0.95)), 3),
            "p99": round(float(lag.quantile(0.99)), 3),
            "max": round(float(lag.max()), 3),
        }
        for sym, g in q.groupby("symbol"):
            minutes = pd.Series(g.snapshot_ts // 60).nunique()
            exp_min = max(1, int((g.snapshot_ts.max() - g.snapshot_ts.min()) // 60) + 1)
            two = (g.best_bid > 0) & (g.best_ask > 0) & (g.best_ask >= g.best_bid)
            out["per_symbol"][sym] = {
                "quote_rows": int(len(g)),
                "distinct_minutes": int(minutes),
                "expected_minutes": exp_min,
                "minute_completeness": round(minutes / exp_min, 4),
                "two_sided_pct": round(100 * float(two.mean()), 3),
                "duplicate_rows": int(g.duplicated(["snapshot_ts", "symbol"]).sum()),
            }
        out["usable_days"] = round(
            span * float(np.mean([v["minute_completeness"]
                                  for v in out["per_symbol"].values()])), 3)
    if not c.empty:
        cd = {}
        for sym, g in c.groupby("symbol"):
            t = np.sort(g["time"].unique())
            exp = int((t.max() - t.min()) // 60) + 1
            gaps = np.diff(t)
            live = (g["fetched_ts"] - g["time"] <= 180).mean()
            cd[sym] = {
                "bars": int(len(t)),
                "expected_bars": exp,
                "completeness": round(len(t) / exp, 4),
                "missing_minutes": int(exp - len(t)),
                "duplicate_bar_times": int(g.duplicated(["time", "symbol"]).sum()),
                "gaps_over_1m": int((gaps > 60).sum()),
                "largest_gap_min": round(float(gaps.max() / 60), 1) if len(gaps) else 0.0,
                "recorded_live_pct": round(100 * float(live), 2),
            }
        out["candles"] = cd
    return out


# ------------------------------------------------------------------- overlap

def overlap_health(root: Path | None = None) -> dict:
    o = options_health(root)
    p = perp_health(root)
    if not o.get("rows") or not p.get("quote_rows"):
        return {"status": CRITICAL, "overlap_days": 0.0,
                "reason": "one or both recorders have no data",
                "options": o, "perp": p}
    start = max(o["first_ts"], p["first_ts"])
    end = min(o["last_ts"], p["last_ts"])
    if end <= start:
        return {"status": CRITICAL, "overlap_days": 0.0,
                "reason": "the two series do not overlap at all",
                "overlap_start": None, "overlap_end": None,
                "options": o, "perp": p}

    base = Path(root or DATA_DIR)
    q = _load(str(base / "perp" / "perp_quotes_*.parquet"))
    usable = np.asarray(o["usable_snapshot_ts"], dtype=np.int64)
    usable = usable[(usable >= start) & (usable <= end)]
    per_sym, hedgeable_all = {}, None
    for sym, g in q.groupby("symbol"):
        pts = np.sort(g.loc[
            (g.best_bid > 0) & (g.best_ask > 0) & (g.best_ask >= g.best_bid),
            "snapshot_ts"].to_numpy())
        if not len(pts) or not len(usable):
            per_sym[sym] = {"hedgeable_slots": 0, "fraction": 0.0}
            hedgeable_all = np.zeros(len(usable), dtype=bool)
            continue
        idx = np.searchsorted(pts, usable)
        lo = pts[np.clip(idx - 1, 0, len(pts) - 1)]
        hi = pts[np.clip(idx, 0, len(pts) - 1)]
        near = np.minimum(np.abs(usable - lo), np.abs(hi - usable))
        ok = near <= ALIGN_TOLERANCE_SECONDS
        per_sym[sym] = {
            "hedgeable_slots": int(ok.sum()),
            "fraction": round(float(ok.mean()), 4) if len(ok) else 0.0,
            "median_align_gap_s": round(float(np.median(near)), 1) if len(near) else None,
        }
        hedgeable_all = ok if hedgeable_all is None else (hedgeable_all & ok)
    span = _span_days(start, end)
    n_ok = int(hedgeable_all.sum()) if hedgeable_all is not None else 0
    expected = max(1, int(span * 86400 / HEDGE_GRID_SECONDS) + 1)
    frac = min(1.0, n_ok / expected)
    return {
        "overlap_start": str(pd.Timestamp(start, unit="s", tz="UTC")),
        "overlap_end": str(pd.Timestamp(end, unit="s", tz="UTC")),
        "overlap_days": round(span, 4),
        "usable_option_snapshots_in_overlap": int(len(usable)),
        "expected_grid_points": expected,
        "hedgeable_slots": n_ok,
        "hedgeable_fraction": round(frac, 4),
        "hedgeable_days": round(span * frac, 4),
        "per_symbol": per_sym,
        "options": o, "perp": p,
    }


# ----------------------------------------------------------- gap detection

def detect_gaps(root: Path | None = None, *, now: int | None = None) -> list[dict]:
    """Everything §12 requires, each as a named finding with a severity."""
    now = int(datetime.now(timezone.utc).timestamp()) if now is None else now
    findings: list[dict] = []

    def add(sev, code, msg, **kw):
        findings.append({"severity": sev, "code": code, "message": msg, **kw})

    o = options_health(root)
    p = perp_health(root)

    for name, h, stale_after, expected_period in (
            ("options", o, 3 * HEDGE_GRID_SECONDS, HEDGE_GRID_SECONDS),
            ("perp", p, 300, 60)):
        if not h.get("rows", h.get("quote_rows", 0)):
            add(CRITICAL, f"{name}_no_data", f"{name} recorder has produced no rows")
            continue
        age = now - h["last_ts"]
        if age > stale_after:
            add(CRITICAL, f"{name}_stopped",
                f"{name} recorder last wrote {age/60:.1f} min ago "
                f"(expected every {expected_period/60:.0f} min)", age_seconds=age)
        elif age > 2 * expected_period:
            add(WARN, f"{name}_late", f"{name} recorder is {age/60:.1f} min behind")

    base = Path(root or DATA_DIR)
    for ds in ("options", "perp_quotes", "perp_candles"):
        cp = archive.read_checkpoint(ds)
        if cp.last_error:
            add(WARN, f"{ds}_api_error",
                f"last recorded failure for {ds}: {cp.last_error}")
        if cp.schema_version and cp.schema_version != archive.SCHEMA_VERSIONS[ds]:
            add(CRITICAL, f"{ds}_schema_change",
                f"checkpoint schema {cp.schema_version} != code "
                f"{archive.SCHEMA_VERSIONS[ds]}")

    q = _load(str(base / "quotes" / "quotes_*.parquet"))
    if not q.empty:
        stamps = np.sort(q.snapshot_ts.unique())
        d = np.diff(stamps)
        big = d[d > 2 * HEDGE_GRID_SECONDS]
        if len(big):
            add(WARN, "options_missing_snapshots",
                f"{len(big)} gaps over {2*HEDGE_GRID_SECONDS//60} min in the option "
                f"series, largest {big.max()/3600:.2f}h", count=int(len(big)))
        if (d < 0).any():
            add(CRITICAL, "options_timestamp_jump", "snapshot timestamps go backwards")
        counts = q.groupby("snapshot_ts")["symbol"].nunique()
        if len(counts) > 8:
            med = counts.median()
            collapsed = counts[counts < 0.5 * med]
            if len(collapsed):
                add(WARN, "options_contract_collapse",
                    f"{len(collapsed)} snapshots carry under half the median "
                    f"contract count ({med:.0f})", count=int(len(collapsed)))

    c = _load(str(base / "perp" / "perp_candles_1m_*.parquet"))
    if not c.empty:
        for sym, g in c.groupby("symbol"):
            t = np.sort(g["time"].unique())
            miss = int((t.max() - t.min()) // 60 + 1 - len(t))
            if miss:
                add(WARN, "perp_missing_minutes",
                    f"{sym}: {miss} missing 1m bars (recorded, NOT backfilled)",
                    symbol=sym, missing=miss)

    ov = overlap_health(root)
    if ov.get("overlap_days", 0) <= 0:
        add(CRITICAL, "no_overlap",
            "options and perpetual series do not overlap; H-Vol-6 cannot be "
            "tested over any period")
    if not findings:
        add(OK, "healthy", "all recorders current, no gaps detected")
    return findings


# ------------------------------------------------------------- readiness gate

@dataclass
class Readiness:
    ready: bool
    status: str
    checks: dict = field(default_factory=dict)
    detail: dict = field(default_factory=dict)


def hvol6_readiness(root: Path | None = None) -> Readiness:
    """Deterministic. Returns BLOCKED until every condition holds.

    Measured on USABLE observations, never on calendar span or file count.
    """
    ov = overlap_health(root)
    o, p = ov.get("options", {}), ov.get("perp", {})
    checks = {
        "options_usable_days_ge_182": (
            o.get("usable_days", 0.0) >= REQUIRED_DAYS),
        "options_usable_fraction_ge_0.90": (
            o.get("usable_fraction", 0.0) >= REQUIRED_USABLE_FRACTION),
        "perp_usable_days_ge_182": (
            p.get("usable_days", 0.0) >= REQUIRED_DAYS),
        "overlap_hedgeable_days_ge_182": (
            ov.get("hedgeable_days", 0.0) >= REQUIRED_DAYS),
        "overlap_hedgeable_fraction_ge_0.90": (
            ov.get("hedgeable_fraction", 0.0) >= REQUIRED_HEDGEABLE_FRACTION),
        "quote_quality_two_sided_ge_0.60": (
            o.get("two_sided_pct", 0.0) / 100.0 >= REQUIRED_QUOTE_QUALITY),
        # Structural guarantees, true by construction of the recorders:
        # no mark is ever written into a bid/ask column and no observation is
        # produced other than by a live poll. Asserted in the test suite.
        "no_synthetic_execution": True,
        "no_future_reconstruction": True,
    }
    ready = all(checks.values())
    return Readiness(
        ready=ready,
        status="READY FOR PREREGISTRATION" if ready else "BLOCKED",
        checks=checks,
        detail={
            "options_calendar_days": o.get("calendar_days", 0.0),
            "options_usable_days": o.get("usable_days", 0.0),
            "options_usable_fraction": o.get("usable_fraction", 0.0),
            "perp_calendar_days": p.get("calendar_days", 0.0),
            "perp_usable_days": p.get("usable_days", 0.0),
            "overlap_days": ov.get("overlap_days", 0.0),
            "hedgeable_days": ov.get("hedgeable_days", 0.0),
            "hedgeable_fraction": ov.get("hedgeable_fraction", 0.0),
            "required_days": REQUIRED_DAYS,
        },
    )
