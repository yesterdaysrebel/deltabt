"""Owner 2026-11-15: "You are not considering banking and compounding here". The 1% fall target
(scripts/careful_one_pct.py: top pick only, compounding -- each trade sized on the wallet balance) with BANKING:
  none      everything compounds
  monthly   on the first pick day of each month, the wallet above $3,184 moves to the bank (never traded again)
  each win  after every winning trade, the wallet above $3,184 moves to the bank
Total = bank + wallet; month off after 2 full stops; no trades while the wallet is under the $1,000 floor.
Also the one-time +$2,000 goal (bank + wallet >= $5,184) from all 615 start days. Two account implementations.

    PYTHONPATH=. python scripts/careful_one_pct_bank.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_one_pct as O  # noqa: E402  (loads Delta's live terms)

START, FLOOR = D.START, 1000.0


def acct_a(days, le, cfg, bank_mode, i0=0, goal=None):
    wal, bank, busy, month, stops = START, 0.0, -1, None, 0
    for day, picks in days[i0:]:
        t0 = int(day.timestamp()) // 60
        m = day.strftime("%Y-%m")
        if m != month:
            month, stops = m, 0
            if bank_mode == "monthly" and wal > START:
                bank, wal = bank + wal - START, START
        if wal < FLOOR:
            return ("FLOOR", (day - days[i0][0]).days, wal, bank) if goal else (None, None, wal, bank)
        if stops >= 2 or busy > t0 + 2:
            continue
        r = O.p1(picks[0][1], picks[0][2], picks[0][3], picks[0][4], wal, le, *cfg)
        if r is None:
            continue
        wal = max(wal + r[1], 0.0)
        busy = t0 + r[3]
        stops += r[0] in ("STOP", "LIQ")
        if bank_mode == "each win" and r[1] > 0 and wal > START:
            bank, wal = bank + wal - START, START
        if goal and wal + bank >= START + goal:
            return "GOAL", (day - days[i0][0]).days, wal, bank
    if goal and wal < FLOOR:
        return "FLOOR", None, wal, bank
    return ("OPEN" if goal else None), None, wal, bank


def acct_b(days, le, cfg, bank_mode):
    """Independent: an explicit ledger of cash moves."""
    st = dict(w=START, b=0.0, free=-10 ** 9, ym=None, full=0)
    for day, picks in days:
        ym = (day.year, day.month)
        if ym != st["ym"]:
            st["ym"], st["full"] = ym, 0
            if bank_mode == "monthly":
                move = max(0.0, st["w"] - START)
                st["w"] -= move
                st["b"] += move
        if st["w"] < FLOOR:
            break
        base = int(day.timestamp()) // 60
        if st["full"] >= 2 or st["free"] > base + 2:
            continue
        q = picks[0]
        t = O.p2(q[1], q[2], q[3], q[4], st["w"], le, *cfg)
        if t is None:
            continue
        st["w"] = max(0.0, st["w"] + t[1])
        st["free"] = base + t[3]
        if t[0] in ("STOP", "LIQ"):
            st["full"] += 1
        if bank_mode == "each win" and t[1] > 0:
            move = max(0.0, st["w"] - START)
            st["w"] -= move
            st["b"] += move
    return st["w"], st["b"]


def main():
    days = D.load()
    real = [d for d in days if d[0] >= pd.Timestamp("2026-10-29")]
    y26 = [d for d in days if d[0] >= pd.Timestamp("2026-01-01")]
    modes = ("none", "monthly", "each win")
    for cfg in O.CFGS:
        for mode in modes:
            for le in (False, True):
                a, b = acct_a(days, le, cfg, mode), acct_b(days, le, cfg, mode)
                assert abs(a[2] - b[0]) < 0.01 and abs(a[3] - b[1]) < 0.01, (cfg, mode, le, a, b)
    print("CHECK PASSED: two account implementations agree (every version, every banking mode, both fills).\n")
    print("1% fall target, top pick, compounding. TOTAL = bank + wallet, from $3,184. STRICT .. LENIENT.\n")
    for cfg in O.CFGS:
        mg, ex, s = cfg
        print(f"=== {mg:.0%} margin, stop {s:.2%} ===")
        print(f"   {'banking':9s} | {'full history':>19s} | {'(of which banked)':>19s} | {'2026 from Jan 1':>19s} | {'20 real picks':>19s} | +$2,000 goal first / floor first (of 615)")
        for mode in modes:
            r = [acct_a(days, le, cfg, mode) for le in (False, True)]
            y = [acct_a(y26, le, cfg, mode) for le in (False, True)]
            q = [acct_a(real, le, cfg, mode) for le in (False, True)]
            g = []
            for le in (False, True):
                res = [acct_a(days, le, cfg, mode, i0=i, goal=2000.0) for i in range(len(days))]
                g.append((sum(x[0] == "GOAL" for x in res), sum(x[0] == "FLOOR" for x in res)))
            tot = lambda x: x[2] + x[3]  # noqa: E731
            print(f"   {mode:9s} | {tot(r[0]):8,.0f} .. {tot(r[1]):8,.0f} | {r[0][3]:8,.0f} .. {r[1][3]:8,.0f} | {tot(y[0]):8,.0f} .. {tot(y[1]):8,.0f} | "
                  f"{tot(q[0]):8,.0f} .. {tot(q[1]):8,.0f} | {g[0][0]:3d} .. {g[1][0]:3d} / {g[0][1]:3d} .. {g[1][1]:3d}")
        print()


if __name__ == "__main__":
    main()
