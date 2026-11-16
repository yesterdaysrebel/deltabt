"""A cushion at FULL trade size: bank the profits. Run 4 (final rules, 49% at 20x, 2-stops loss cap) trades the
trading wallet; at each month end anything above $3,184 is moved out (banked) and never traded again. No refills.
Total = bank + trading wallet. Month by month with run4_account.account_a (checked against account_b); a trade still
open at a month end is not carried over (trades last <= 24 h; noted).

    PYTHONPATH=. python scripts/run4_bank_profits.py
"""
import sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402
import run4_account as RA  # noqa: E402
import run4_margin75 as M  # noqa: E402


def run(paths, lenient, bank_on, log=None):
    months = {}
    for p in paths:
        months.setdefault(pd.Timestamp(p[0]).strftime("%Y-%m"), []).append(p)
    trade, bank = C.START, 0.0
    for m in sorted(months):
        start = trade
        trade = RA.account_a(months[m], 0.49, lenient, False, start=trade)[0] if trade >= 50 else trade
        if bank_on and trade > C.START:
            bank += trade - C.START
            trade = C.START
        if log is not None:
            log.append((m, start, trade, bank))
    return trade + bank, bank, trade


def main():
    C.checks()
    M.load_ranks()
    hist, *_ = C.paths_history()
    real = sorted(C.paths_real(), key=lambda x: x[0])
    y26 = [x for x in hist if x[0] >= pd.Timestamp("2026-01-01")] + real
    allp = hist + real
    print("Run 4 as built, from $3,184; month-by-month runs (no carry-over), STRICT .. LENIENT. Total = bank + wallet:\n")
    for nm, ps in (("20 real picks", real), ("2026 to Nov 14", y26), ("history 2025-01 .. now", allp)):
        a = [run(ps, le, False) for le in (False, True)]
        b = [run(ps, le, True) for le in (False, True)]
        print(f"  {nm:24s} no banking ${a[0][0]:7,.0f} .. ${a[1][0]:7,.0f} | BANK monthly ${b[0][0]:7,.0f} .. ${b[1][0]:7,.0f} "
              f"(banked ${b[0][1]:,.0f} .. ${b[1][1]:,.0f}, wallet ${b[0][2]:,.0f} .. ${b[1][2]:,.0f})")
    gone = {False: 0, True: 0}
    for le in (False, True):
        for mo in pd.period_range("2025-02", "2026-10", freq="M"):
            sub = [x for x in allp if x[0] >= mo.to_timestamp()]
            gone[le] += run(sub, le, True)[0] < 1000
    print(f"\n  21 monthly starts with banking: total under $1,000 at the end in {gone[False]} .. {gone[True]}")
    for le in (False, True):
        log = []
        run(y26, le, True, log)
        print(f"\n  2026 with banking, {'LENIENT' if le else 'STRICT'}:")
        for m, s, t, b in log:
            print(f"    {m} wallet {s:7,.0f} -> {t:7,.0f}   bank {b:7,.0f}   total {t + b:7,.0f}")


if __name__ == "__main__":
    main()
