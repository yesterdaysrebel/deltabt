"""Owner 2026-11-15: "Leave 2000 aside, how about 1000". From each of the 615 pick days, start with $3,184 and run a
rule until bank + wallet is up +$1,000 (stop for good) or the run ends: (a) the bot's $1,000 floor, or (b) a symmetric
quit when down $1,000 (total <= $2,184). Month off after 2 full stops in every rule. Uses the two-way-checked trade
functions: replay_bot.ref_trade (exit C at 10%, both picks, first fill), careful_big_one.t1 (75% / 50% margin, +$1,000
target, top pick, 7 days off after a win), careful_one_pct.p1 (1% fall target, top pick).

    PYTHONPATH=. python scripts/careful_goal_1000.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_one_pct as O  # noqa: E402  (loads Delta's live terms into D.PRODUCTS)
import careful_big_one as BO  # noqa: E402
import replay_bot as R  # noqa: E402

GOAL = 1000.0


def trade(rule, picks, bal, le):
    kind = rule[0]
    if kind == "C":
        best = None
        for rank, sym, H, L, Cl in picks:
            r = R.ref_trade(sym, H, L, Cl, bal, le)
            if r and (best is None or r[2] < best[2]):
                best = r
        return best
    q = picks[0]
    if kind == "big":
        BO.MARGIN, BO.EXTRA, BO.TARGET = rule[1], rule[2], 1000.0
        r = BO.t1(q[1], q[2], q[3], q[4], bal, le)
        return None if r is None else r[:4]
    return O.p1(q[1], q[2], q[3], q[4], bal, le, rule[1], rule[2], rule[3])


def run(days, i0, le, rule, quit_at, bank_monthly=False):
    wal, bank, busy, month, stops, until = D.START, 0.0, -1, None, 0, None
    for day, picks in days[i0:]:
        t0 = int(day.timestamp()) // 60
        m = day.strftime("%Y-%m")
        if m != month:
            month, stops = m, 0
            if bank_monthly and wal > D.START:
                bank, wal = bank + wal - D.START, D.START
        if wal < 1000.0 or wal + bank <= quit_at:
            return "LOST", (day - days[i0][0]).days, wal + bank
        if stops >= 2 or busy > t0 + 2 or (until is not None and day < until):
            continue
        r = trade(rule, picks, wal, le)
        if r is None:
            continue
        wal = max(wal + r[1], 0.0)
        busy = t0 + r[3]
        stops += r[0] in ("STOP", "LIQ")
        if rule[0] == "big" and r[0] == "TARGET":
            until = day + pd.Timedelta(days=8)
        if wal + bank >= D.START + GOAL:
            return "GOAL", (day - days[i0][0]).days, wal + bank
        if wal < 1000.0 or wal + bank <= quit_at:
            return "LOST", (day - days[i0][0]).days, wal + bank
    return "OPEN", None, wal + bank


RULES = [("exit C at 10% margin (approved)", ("C",)),
         ("75% margin, +$1,000 target", ("big", 0.75, 0.20)),
         ("50% margin, +$1,000 target", ("big", 0.50, 0.25)),
         ("1% fall target, 75% margin, stop 2.25%", ("pct", 0.75, 0.20, 0.0225)),
         ("1% fall target, 50% margin, stop 2.25%", ("pct", 0.50, 0.25, 0.0225)),
         ("1% fall target, 10% margin, stop 2.25%", ("pct", 0.10, 0.051, 0.0225))]


def main():
    days = D.load()
    n = len(days)
    print(f"One-time +$1,000 from $3,184, started on each of {n} pick days; STRICT .. LENIENT.\n")
    for stop_label, quit_at in (("the bot's $1,000 floor (risk ~$2,200 to win $1,000)", 0.0),
                                ("quit when down $1,000 (risk $1,000 to win $1,000)", D.START - GOAL)):
        print(f"ENDS AT {stop_label}:")
        print(f"   {'rule':42s} | {'+$1,000 first':>15s} | {'lost first':>12s} | {'not decided':>11s} | {'median days to +$1,000':>22s} | avg result per start (unfinished at Nov 14 total)")
        for name, rule in RULES:
            cells = []
            for le in (False, True):
                res = [run(days, i, le, rule, quit_at) for i in range(n)]
                g = [r for r in res if r[0] == "GOAL"]
                lo = [r for r in res if r[0] == "LOST"]
                o = sum(r[0] == "OPEN" for r in res)
                dec = g + lo
                ev = np.mean([r[2] - D.START for r in res])     # every start: unfinished ones at their Nov 14 total
                cells.append((len(g), len(lo), o, np.median([r[1] for r in g]) if g else float("nan"), ev))
            s, l_ = cells
            print(f"   {name:42s} | {s[0] / n:5.0%} .. {l_[0] / n:4.0%}   | {s[1] / n:4.0%} .. {l_[1] / n:4.0%} | {s[2] / n:4.0%} .. {l_[2] / n:3.0%} | "
                  f"{s[3]:8.0f} .. {l_[3]:5.0f}         | ${s[4]:+6,.0f} .. ${l_[4]:+6,.0f}")
        print()


if __name__ == "__main__":
    main()
