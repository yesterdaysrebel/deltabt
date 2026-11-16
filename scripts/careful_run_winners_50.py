"""Owner 2026-11-15: "How about we let the winners run" (at 50% margin). The no-take-profit exit (careful_margin
'Break-even + 5% trail' = exit C's rule: stop 2.25%, break-even once up, then a 5% trail, 24 h) at 49% margin,
compared with exit C at 10%. From each of the 615 pick days with $3,184 (both picks, first fill, month off after 2
stops, $1,000 floor): is the account ever up +$1,000, and where it ends. Both implementations (BT.t1, BT.t2) asserted
equal on every run.

    docker exec -w /src -e PYTHONPATH=/src:/src/scripts deltabt-py python scripts/careful_run_winners_50.py
"""
import sys
from pathlib import Path
from multiprocessing import Pool
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_margin as M  # noqa: E402

F1, F2 = M.EXITS["Break-even + 5% trail"]


def one(arg):
    mf, le, i = arg
    days = D.load()
    a, b = M.account(days[i:], F1, mf, le), M.account(days[i:], F2, mf, le)
    assert len(a) == len(b) and all(x[:5] == y[:5] and abs(x[5] - y[5]) < 0.01 for x, y in zip(a, b)), arg
    bal = [x[6] for x in a]
    hit = next((k for k, v in enumerate(bal) if v >= D.START + 1000), None)
    big = [x[5] for x in a if x[4] != "STOP" and x[4] != "LIQ" and x[5] > 0]
    return (hit is not None, (a[hit][0] - days[i][0].date()).days if hit is not None else None,
            bal[-1] if bal else D.START, max(big) if big else 0.0, len(a), sum(p >= 1000 for p in big))


def main():
    n = len(D.load())
    with Pool(8) as pool:
        for mf in (0.49, 0.10):
            print(f"=== {mf:.0%} margin, no take-profit (break-even then 5% trail), from each of {n} start days ===")
            for le in (False, True):
                r = pool.map(one, [(mf, le, i) for i in range(n)])
                hits = [x[1] for x in r if x[0]]
                ends = np.array([x[2] for x in r])
                print(f"  {'LENIENT' if le else 'STRICT '}: ever up +$1,000: {len(hits) / n:.0%} (median {np.median(hits) if hits else float('nan'):.0f} days); "
                      f"ends under $1,000: {(ends < 1000).mean():.0%}; ends above $3,184: {(ends > D.START).mean():.0%}; "
                      f"average end ${ends.mean():,.0f}; single trades of +$1,000 or more: {np.mean([x[5] for x in r]):.1f} per run, "
                      f"in {np.mean([x[4] for x in r]):.0f} trades")
            print()
    print("CHECK PASSED: BT.t1 and BT.t2 agree on every trade of every run.")


if __name__ == "__main__":
    main()
