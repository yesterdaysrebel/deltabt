"""Owner 2026-11-15: "run them on just real trades" -- the main versions on the 20 real picks 2026-10-29..11-14
(Delta 1m candles), trade by trade, STRICT and LENIENT stop fills (scripts/clean_compare.py rules; lenient = a stop
fills at its price +0.1%). Fixed $3,184 per trade, then as an account (compounding, 2-stops/month hard stop).

    PYTHONPATH=. python scripts/real_trades_only.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402

STRICT = C.short


def lenient(*a, **k):
    """clean_compare.short with lenient=True: a triggered stop fills at its price +0.1%. (Fixed 2026-11-15: an earlier
    version here also filled the IMMEDIATE lock exit at the lock price; that exit is a market fill at the close.)"""
    return STRICT(*a, lenient=True, **k)


VERSIONS = [
    ("your version: 50%, $500, stop 2.25%, no extra", dict(entry="spike", margin=0.5, target=500, lock=200, stop=0.0225, extra=0.0)),
    ("50%, $700, stop 2.25%, 25% extra", dict(entry="spike", margin=0.5, target=700, lock=200, stop=0.0225, extra=0.25)),
    ("50%, $700, stop 1%, 25% extra", dict(entry="spike", margin=0.5, target=700, lock=200, stop=0.01, extra=0.25)),
    ("75%, $700, stop 2.25%, no extra", dict(entry="spike", margin=0.75, target=700, lock=200, stop=0.0225, extra=0.0)),
    ("75%, $700, stop 2.25%, 20% extra", dict(entry="spike", margin=0.75, target=700, lock=200, stop=0.0225, extra=0.20)),
    ("20x at 00:03 (no spike entry), 50%, $500, 2.25%", dict(entry="0003", margin=0.5, target=500, lock=None, stop=0.0225, extra=0.0)),
    ("steady: filter, 3x, 10% margin, take 10%", dict(entry="0003", margin=0.10, target=0, lock=None, lev=3,
                                                       stop=0.9 * (1 / 3 - 0.025), take_frac=0.10, filter=C.funding_ok)),
]


def one(fn, p, cfg, bal):
    day, sym, hi, lo, cl, k0 = p
    if cfg.get("filter") and not cfg["filter"](sym, day):
        return "filtered"
    r = fn(sym, hi, lo, cl, k0, cfg["margin"] * bal, cfg["target"], cfg["entry"], cfg["lock"], cfg.get("stop", 0.0225),
           cfg.get("lev"), cfg.get("take_frac"), cfg.get("extra", 0.0) * bal)
    return r if r else "no fill"


def account(fn, real, cfg):
    C.short = fn
    try:
        return C.account(real, cfg, months_off_after=2)[0], C.account(real, cfg, months_off_after=None)[0]
    finally:
        C.short = STRICT


def main() -> None:
    C.checks()
    real = sorted(C.paths_real(), key=lambda x: x[0])
    print(f"THE 20 REAL PICKS 2026-10-29..11-14 ONLY. Start ${C.START:,.0f}. STRICT = worse of stop and the minute's close "
          "(+0.1%); LENIENT = stop price +0.1%.\n")
    summ = []
    for name, cfg in VERSIONS:
        print(f"== {name}")
        print(f"   {'day':6s} {'coin':9s} | {'STRICT':>22s} | {'LENIENT':>22s}")
        tot = {"s": 0.0, "l": 0.0}
        cnt = {"s": {}, "l": {}}
        for p in real:
            cells = []
            for key, fn in (("s", STRICT), ("l", lenient)):
                r = one(fn, p, cfg, C.START)
                if isinstance(r, str):
                    cells.append(f"{r:>22s}")
                    continue
                tot[key] += r[1]
                cnt[key][r[0]] = cnt[key].get(r[0], 0) + 1
                cells.append(f"{r[0]:>6s} {C.Q.hm(r[2])}->{C.Q.hm(r[3])} {r[1]:+7.0f}")
            print(f"   {p[0].strftime('%m-%d'):6s} {p[1][:-3]:9s} | {cells[0]} | {cells[1]}")
        a_s = account(STRICT, real, cfg)
        a_l = account(lenient, real, cfg)
        print(f"   each trade on a fixed $3,184: STRICT {cnt['s']} total ${tot['s']:+,.0f} | LENIENT {cnt['l']} total ${tot['l']:+,.0f}")
        print(f"   as an account (compounding): with 2-stops/month hard stop STRICT ${a_s[0]:,.0f} LENIENT ${a_l[0]:,.0f} | "
              f"no hard stop STRICT ${a_s[1]:,.0f} LENIENT ${a_l[1]:,.0f}\n")
        summ.append((name, tot["s"], tot["l"], a_s, a_l))
    print("SUMMARY (20 real picks, from $3,184)")
    print(f"  {'version':48s} | {'fixed-size total':>23s} | {'account, hard stop':>21s} | {'account, no hard stop':>21s}")
    for name, ts, tl, a_s, a_l in summ:
        print(f"  {name:48s} | {ts:+10,.0f} .. {tl:+10,.0f} | {a_s[0]:9,.0f} .. {a_l[0]:9,.0f} | {a_s[1]:9,.0f} .. {a_l[1]:9,.0f}")
    print("\n  The real account (rule A) on its share of these picks: $1,661.84 on Nov 9 -> $1,530.20 on Nov 14 "
          "(+ the manual SOPH trade on Nov 14 -$609.26).")


if __name__ == "__main__":
    main()
