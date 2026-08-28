"""How much clean overlapping H-Vol-6 data do we have? Run this weekly.

Reports coverage, gaps and the deterministic readiness gate, and writes the
infrastructure-state record to out/data_readiness/options_vol.json.

NOT research evidence. No P&L, no strategy, no hypothesis. If this script ever
prints a return, something has gone badly wrong.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from deltabt.config import OUT_DIR
from deltabt.data import archive, health

RECORD = OUT_DIR / "data_readiness" / "options_vol.json"


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def main() -> None:
    # Manifests track the data, so refresh them before describing coverage.
    # A manifest that lags its partition would misreport what existed when.
    for ds in ("options", "perp_quotes", "perp_candles"):
        archive.refresh_manifests(ds)

    ov = health.overlap_health()
    o, p = ov.get("options", {}), ov.get("perp", {})
    gaps = health.detect_gaps()
    r = health.hvol6_readiness()

    W = 34
    print("=" * 72)
    print("H-VOL-6 DATA FOUNDATION AUDIT   " +
          datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    print("=" * 72)

    print("\nOPTIONS (BTC/ETH surface)")
    print(f"  {'rows':<{W}} {o.get('rows', 0):,}")
    print(f"  {'span':<{W}} {o.get('first','-')}  ->  {o.get('last','-')}")
    print(f"  {'calendar coverage':<{W}} {o.get('calendar_days',0):.2f} days")
    print(f"  {'USABLE coverage':<{W}} {o.get('usable_days',0):.2f} days "
          f"({100*o.get('usable_fraction',0):.1f}% of expected slots)")
    print(f"  {'snapshots / usable':<{W}} {o.get('snapshots',0)} / {o.get('usable_snapshots',0)}")
    print(f"  {'snapshots per day':<{W}} {o.get('snapshots_per_day',0)}")
    print(f"  {'unique contracts per day':<{W}} {o.get('unique_contracts_per_day',0)}")
    for k, label in (("two_sided_pct", "two-sided quotes"),
                     ("complete_pct", "complete (quote+IV+greeks+spot)"),
                     ("missing_bid_pct", "missing bid"),
                     ("missing_ask_pct", "missing ask"),
                     ("missing_size_pct", "missing size"),
                     ("stale_pct", "stale"), ("crossed_pct", "crossed"),
                     ("locked_pct", "locked")):
        print(f"  {label:<{W}} {o.get(k,0):.3f}%")

    print("\nPERPETUALS (hedge instrument)")
    print(f"  {'quote rows / candle rows':<{W}} {p.get('quote_rows',0):,} / {p.get('candle_rows',0):,}")
    print(f"  {'span':<{W}} {p.get('first','-')}  ->  {p.get('last','-')}")
    print(f"  {'calendar coverage':<{W}} {p.get('calendar_days',0):.2f} days")
    print(f"  {'USABLE coverage':<{W}} {p.get('usable_days',0):.2f} days")
    lag = p.get("exchange_lag_s", {})
    if lag:
        print(f"  {'exchange lag (s)':<{W}} median {lag['median']}  p95 {lag['p95']}  "
              f"p99 {lag['p99']}  max {lag['max']}")
    for sym, v in (p.get("per_symbol") or {}).items():
        print(f"    {sym:<10} minutes {v['distinct_minutes']}/{v['expected_minutes']} "
              f"({100*v['minute_completeness']:.1f}%)  two-sided {v['two_sided_pct']:.1f}%  "
              f"dups {v['duplicate_rows']}")
    for sym, v in (p.get("candles") or {}).items():
        print(f"    {sym:<10} bars {v['bars']}/{v['expected_bars']} "
              f"({100*v['completeness']:.1f}%)  missing {v['missing_minutes']}  "
              f"gaps>1m {v['gaps_over_1m']}  live {v['recorded_live_pct']}%")

    print("\nOVERLAP  <- the primary readiness indicator")
    print(f"  {'overlap window':<{W}} {ov.get('overlap_start','-')}  ->  {ov.get('overlap_end','-')}")
    print(f"  {'overlap duration':<{W}} {ov.get('overlap_days',0):.4f} days")
    print(f"  {'hedgeable 15m slots':<{W}} {ov.get('hedgeable_slots',0)} / "
          f"{ov.get('expected_grid_points',0)} ({100*ov.get('hedgeable_fraction',0):.1f}%)")
    print(f"  {'HEDGEABLE coverage':<{W}} {ov.get('hedgeable_days',0):.4f} days")
    for sym, v in (ov.get("per_symbol") or {}).items():
        print(f"    {sym:<10} slots {v['hedgeable_slots']} ({100*v['fraction']:.1f}%)"
              + (f"  median align gap {v['median_align_gap_s']}s"
                 if v.get("median_align_gap_s") is not None else ""))

    print("\nGAPS / ALERTS")
    for g in gaps:
        print(f"  [{g['severity']:<8}] {g['code']:<28} {g['message']}")

    print("\nREADINESS GATE")
    for k, v in r.checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print(f"\n  overall H-Vol-6 readiness:  {r.status}")
    print("=" * 72)

    RECORD.parent.mkdir(parents=True, exist_ok=True)
    o_clean = {k: v for k, v in o.items() if k != "usable_snapshot_ts"}
    ov_clean = {k: v for k, v in ov.items() if k not in ("options", "perp")}
    RECORD.write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "options_coverage": o_clean,
        "perp_coverage": p,
        "overlap": ov_clean,
        "schema_versions": archive.SCHEMA_VERSIONS,
        "dedup_keys": {k: list(v) for k, v in archive.DEDUP_KEYS.items()},
        "collector_version": archive.COLLECTOR_VERSION,
        "health_findings": gaps,
        "readiness": {"status": r.status, "ready": r.ready,
                      "checks": r.checks, "detail": r.detail},
        "note": "Infrastructure state, not research evidence. No experiment has been run.",
    }, indent=2, default=str))
    print(f"wrote {RECORD}")


if __name__ == "__main__":
    main()
