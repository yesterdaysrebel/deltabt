"""H-MAKER-1 analysis: simulate the frozen order schedule over a recorded feed.

    PYTHONPATH=. python3 -m deltabt.research.run_hmaker1_analysis

Reads only. No exchange connection, no orders. Writes the six required reports
and one JSON of everything measured.
"""

from __future__ import annotations

import glob
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from deltabt.research import hmaker1 as h

PREREG = h.OUT / "preregistration.md"
PREREG_SHA = "c079c7a5244ba5a2665fcd6ad4d35f7f0fbcac06e1e6fb32ebaee78c4a5358c8"
TARGET_ORDERS = 600
TARGET_FILLS = 400


def check_prereg() -> None:
    got = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    if got != PREREG_SHA:
        raise SystemExit(f"pre-registration has changed.\n  frozen {PREREG_SHA}\n"
                         f"  now    {got}\nThe rules do not move after collection.")


def stats(rows: list[dict]) -> dict:
    n = len(rows)
    fills = [r for r in rows if r["filled"]]
    ttf = [r["time_to_fill"] for r in fills if r["time_to_fill"] is not None]
    return dict(
        orders=n, touched=sum(1 for r in rows if r["touched"]),
        touch_rate=sum(1 for r in rows if r["touched"]) / n if n else np.nan,
        fills=len(fills), fill_rate=len(fills) / n if n else np.nan,
        partial=sum(1 for r in fills if r["partial"]),
        partial_rate=(sum(1 for r in fills if r["partial"]) / len(fills)
                      if fills else np.nan),
        median_time_to_fill_s=float(np.median(ttf)) if ttf else np.nan,
        p90_time_to_fill_s=float(np.quantile(ttf, 0.9)) if ttf else np.nan,
        median_queue_ahead=float(np.median([r["queue_ahead"] for r in rows])) if n else np.nan,
        median_spread_bps=float(np.median([r["spread_bps"] for r in rows])) if n else np.nan,
    )


def adverse(rows: list[dict]) -> dict:
    fills = [r for r in rows if r["filled"]]
    out = {}
    for hz in h.MARKOUT_MIN:
        key = f"adverse_{hz}m"
        vals = [r.get(key, np.nan) for r in fills]
        out[f"+{hz}m"] = h.estimate(vals, [r["symbol"] for r in fills],
                                    [r["submit_ts"] for r in fills])
    return out


def by_symbol(rows: list[dict]) -> dict:
    out = {}
    for s in h.SYMBOLS:
        sub = [r for r in rows if r["symbol"] == s]
        if not sub:
            continue
        st = stats(sub)
        fills = [r for r in sub if r["filled"]]
        key = f"adverse_{h.PRIMARY_MARKOUT_MIN}m"
        st["adverse_primary"] = h.estimate(
            [r.get(key, np.nan) for r in fills],
            [r["symbol"] for r in fills], [r["submit_ts"] for r in fills])
        out[s] = st
    return out


def main() -> int:
    check_prereg()
    files = sorted(glob.glob(str(h.OUT / "feed" / "*.jsonl.gz")))
    if not files:
        raise SystemExit("no recorded feed found in out/hmaker1/feed/")
    print(f"loading {len(files)} feed file(s)...")
    feed = h.load_feed(files)
    for s in h.SYMBOLS:
        print(f"  {s:8} {len(feed.books[s].ts):>7,} snapshots  "
              f"{len(feed.trades[s]):>7,} trades")
    print(f"  feed gaps > {h.MAX_GAP_S}s: {len(feed.gaps)}")

    orders = h.generate_orders(feed)
    print(f"\ngenerated {len(orders):,} paper orders "
          f"(every {h.SUBMIT_EVERY_S:.0f}s per symbol, {h.ORDER_LIFETIME_S:.0f}s life)")

    rows = h.run_all(feed, orders)
    res = {}
    for mode in h.MODES:
        st = stats(rows[mode])
        res[mode] = dict(stats=st, adverse=adverse(rows[mode]),
                         per_symbol=by_symbol(rows[mode]))
        a = res[mode]["adverse"][f"+{h.PRIMARY_MARKOUT_MIN}m"]
        print(f"\n{mode.upper():14} orders {st['orders']:,}  touch "
              f"{st['touch_rate']:.1%}  FILL {st['fill_rate']:.1%} "
              f"({st['fills']:,})  partial {st['partial']}")
        print(f"{'':14} adverse@+{h.PRIMARY_MARKOUT_MIN}m "
              f"{a['mean']:+.3f} bps  95% CI [{a['ci_low']:+.3f}, {a['ci_high']:+.3f}]"
              f"  n={a['n']:,}  clusters={a['n_clusters']}  MDE {a['mde']:.3f}")

    c = res["conservative"]
    a = c["adverse"][f"+{h.PRIMARY_MARKOUT_MIN}m"]
    sample_ok = (c["stats"]["orders"] >= TARGET_ORDERS
                 and c["stats"]["fills"] >= TARGET_FILLS)
    vc = h.verdict(a["ci_low"], a["ci_high"])
    o = res["optimistic"]["adverse"][f"+{h.PRIMARY_MARKOUT_MIN}m"]
    vo = h.verdict(o["ci_low"], o["ci_high"])
    final = h.verdict(a["ci_low"], a["ci_high"], sample_ok=sample_ok,
                      bounds_agree=(vc == vo))

    print("\n" + "=" * 84)
    print(f"  sample targets: orders {c['stats']['orders']:,}/{TARGET_ORDERS} "
          f"fills {c['stats']['fills']:,}/{TARGET_FILLS} -> "
          f"{'MET' if sample_ok else 'NOT MET'}")
    print(f"  conservative verdict {vc} | optimistic verdict {vo} -> "
          f"bounds {'agree' if vc == vo else 'DISAGREE'}")
    print(f"  H-MAKER-1 VERDICT: {final}")
    print("=" * 84)

    out = dict(preregistration_sha256=PREREG_SHA,
               kill_threshold_bps=h.KILL_THRESHOLD_BPS,
               primary_markout_min=h.PRIMARY_MARKOUT_MIN,
               targets=dict(orders=TARGET_ORDERS, fills=TARGET_FILLS,
                            met=bool(sample_ok)),
               feed=dict(files=[Path(f).name for f in files],
                         snapshots={s: len(feed.books[s].ts) for s in h.SYMBOLS},
                         trades={s: len(feed.trades[s]) for s in h.SYMBOLS},
                         gaps=len(feed.gaps),
                         session=feed.session),
               orders_generated=len(orders),
               results=res,
               verdicts=dict(conservative=vc, optimistic=vo, final=final))
    (h.OUT / "results.json").write_text(json.dumps(out, indent=2, default=float) + "\n")
    print(f"written -> {h.OUT / 'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
