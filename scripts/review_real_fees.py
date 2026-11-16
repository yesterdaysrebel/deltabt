"""REVIEW 2026-11-16 (owner: "run a review on all the studies ... is it really not possible to make money here").

Finding under review: every careful_* script charges 0.12% of the position per round trip (0.02% + 0.05% in, 0.05%
out). Delta's REAL fills on this account (SOPH 2026-11-14, GST included): maker 0.0212%, taker 0.0531%. A resting
sell that fills is a MAKER fill; a resting take-profit is a MAKER fill; a stop or a timed exit is a TAKER fill. So the
real round trip is 0.042% (target) / 0.074% (stop, time) -- the sims over-charged 0.05-0.08% of the position, which at
20x is 1-1.6% of the margin on every trade.

This is a NEW implementation (not p1/p2/t1/t2): Spike Sell entry (top pick, resting sell 5% above the 00:02 close),
then a grid of take-profit x stop, out after 24 h. Per trade as % of the POSITION (no leverage, no liquidation cap, no
account rules), under the OLD and the REAL fees, STRICT and LENIENT fills (as docs/careful_sim_spec.md), and PERFECT
fills (exact prices, target on touch) as the upper bound. Validated against careful_one_pct.p1 at the old fees.

    PYTHONPATH=. python scripts/review_real_fees.py        # -> out/review/real_fees.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402

OLD = dict(fin=0.0007, f_tp=0.0005, f_mkt=0.0005)
REAL = dict(fin=0.000212, f_tp=0.000212, f_mkt=0.000531)
TARGETS = (0.005, 0.01, 0.015, 0.02, 0.03, 0.05, None)
STOPS = (0.015, 0.0225, 0.03, 0.05, 0.10)


def trade(H, L, Cl, t, s, fill, fees):
    """-> (how, return as a fraction of the position, entry minute) or None. fill: STRICT | LENIENT | PERFECT."""
    e = Cl[2] * 1.05
    f = np.flatnonzero(H[3:1440] >= e * 1.0005)
    if not f.size:
        return None
    k = 3 + int(f[0])
    last = min(2880, k + 1440)
    h, l, c = H[k + 1:last + 1], L[k + 1:last + 1], Cl[k + 1:last + 1]
    sp = e * (1 + s)
    si = np.flatnonzero(h >= sp)
    a = int(si[0]) if si.size else 10 ** 9
    b = 10 ** 9
    if t is not None:
        tp = e * (1 - t)
        ti = np.flatnonzero(l <= (tp if fill == "PERFECT" else tp * 0.999))
        b = int(ti[0]) if ti.size else 10 ** 9
    if a <= b and a < 10 ** 9:                               # same minute: the stop counts first
        x = sp if fill == "PERFECT" else (sp * 1.001 if fill == "LENIENT" else max(sp, c[a]) * 1.001)
        return "STOP", -(x / e - 1) - fees["fin"] - fees["f_mkt"] * x / e, k
    if b < 10 ** 9:
        return "TARGET", t - fees["fin"] - fees["f_tp"] * (1 - t), k
    x = Cl[last] * (1.0 if fill == "PERFECT" else 1.001)
    return "24h", -(x / e - 1) - fees["fin"] - fees["f_mkt"] * x / e, k


def validate(days):
    """Old fees, STRICT and LENIENT, must equal careful_one_pct.p1 with liquidation pushed out of reach."""
    import careful_one_pct as O
    import careful_sim_b as B
    n = 0
    for le, fill in ((False, "STRICT"), (True, "LENIENT")):
        for day, picks in days:
            _, sym, H, L, Cl = picks[0]
            for s in (0.0225, 0.01):
                mine = trade(H, L, Cl, 0.01, s, fill, OLD)
                ref = O.p1(sym, H, L, Cl, D.START, le, 0.01, 0.9, s)     # 1% margin + 90% extra: no liquidation
                assert (mine is None) == (ref is None), (day, sym)
                if mine is None:
                    continue
                notional, _ = B.sizing(sym, 0.01 * D.START, Cl[2] * 1.05, 0.9 * D.START)
                assert mine[0] == ("TARGET" if ref[0] == "TARGET" else "STOP" if ref[0] in ("STOP", "LIQ") else "24h"), (day, sym, mine, ref)
                if ref[0] != "LIQ":
                    assert abs(mine[1] - ref[1] / notional) < 1e-9, (day, sym, mine, ref[1] / notional)
                n += 1
    return n


def main():
    days = D.load()
    out = [f"VALIDATION PASSED: {validate(days)} trades equal careful_one_pct.p1 at the old fees (outcome and return).", ""]
    y = np.array([d.year for d, _ in days])
    top = [(d, p[0]) for d, p in days]
    out.append("Spike Sell entry, top pick, take-profit x stop, out after 24 h. Mean per trade, % of the POSITION. 2025 / 2026+real.")
    out.append("OLD fees = 0.12% round trip (what every careful_* study charged). REAL = 0.042% target / 0.074% stop (Delta's fills).")
    for fill in ("STRICT", "LENIENT", "PERFECT"):
        out.append(f"\n=== {fill} fills ===")
        out.append(f"{'target':>7} {'stop':>6} | {'n':>4} {'win':>4} | {'OLD 2025':>9} {'OLD 2026':>9} | {'REAL 2025':>9} {'REAL 2026':>9} | {'REAL all':>9} {'t':>6} | verdict (REAL)")
        for t in TARGETS:
            for s in STOPS:
                res = {}
                for nm, fees in (("OLD", OLD), ("REAL", REAL)):
                    rows = [(d, trade(p[2], p[3], p[4], t, s, fill, fees)) for d, p in top]
                    res[nm] = pd.DataFrame([(d, r[0], r[1]) for d, r in rows if r], columns=["day", "how", "ret"])
                r = res["REAL"]
                yr = r.day.dt.year
                m = lambda df, yy: 100 * df.ret[df.day.dt.year.eq(2025) if yy == 2025 else df.day.dt.year.ge(2026)].mean()  # noqa: E731
                tt = r.ret.mean() / (r.ret.std(ddof=1) / np.sqrt(len(r)))
                both = m(r, 2025) > 0 and m(r, 2026) > 0
                out.append(f"{'none' if t is None else f'{t:.1%}':>7} {s:6.2%} | {len(r):4d} {100 * (r.ret > 0).mean():3.0f}% | "
                           f"{m(res['OLD'], 2025):+8.3f}% {m(res['OLD'], 2026):+8.3f}% | {m(r, 2025):+8.3f}% {m(r, 2026):+8.3f}% | "
                           f"{100 * r.ret.mean():+8.3f}% {tt:+6.2f} | {'positive both years' if both else '-'}")
    txt = "\n".join(out)
    print(txt)
    Path("out/review").mkdir(parents=True, exist_ok=True)
    Path("out/review/real_fees.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
