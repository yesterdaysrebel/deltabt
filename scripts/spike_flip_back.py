"""Owner 2026-11-16: "I don't like 24 h hold, it generally flips back and we don't have a mechanism in place to cut the
trade when that happens."  Spike Sell with the WIDE stop (short 5% above the 00:02 close, hard stop 10%), both daily
picks, careful dataset 2025-01 .. 2026-11.  Two ways to deal with the flip back, every cell printed:

  A  a shorter clock: out at the close 1 / 2 / 4 / 8 / 12 / 24 h after the fill
  B  a rule that cuts the trade once it has been in profit and comes back (clock 24 h and 8 h):
       BE X        once the price has been X below the entry, the stop moves to the entry price
       LOCK X      once it has been X below, the stop moves to X/2 below the entry (keeps half)
       TRAIL X/T   once it has been X below, the stop follows T above the lowest price so far

ZERO fees (owner: "don't worry about fees"). Fills as docs/careful_sim_spec.md:
  STRICT  a stop fills at the worse of the stop and that minute's close, 0.1% worse; a moved stop counts from the same
          minute that moved it (inside one minute the low is assumed to come first); the clock exit is 0.1% worse.
  LENIENT a stop fills 0.1% worse than its price; a moved stop counts from the next minute.
The plain case (no rule, 24 h) must equal scripts/review_real_fees.py::trade -- asserted on every trade.

    PYTHONPATH=. python scripts/spike_flip_back.py         # -> out/careful/spike_flip_back.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import review_real_fees as V  # noqa: E402

STOP = 0.10
ZERO = dict(fin=0.0, f_tp=0.0, f_mkt=0.0)


def variants():
    out = [(f"clock {h} h", None, h) for h in (1, 2, 4, 8, 12, 24)]
    rules = [("BE", 0.02, None), ("BE", 0.03, None), ("BE", 0.05, None), ("LOCK", 0.03, None), ("LOCK", 0.05, None), ("LOCK", 0.08, None),
             ("TRAIL", 0.02, 0.03), ("TRAIL", 0.03, 0.03), ("TRAIL", 0.03, 0.05), ("TRAIL", 0.05, 0.03), ("TRAIL", 0.05, 0.05), ("TRAIL", 0.08, 0.05)]
    for h in (24, 8):
        for kind, x, t in rules:
            nm = f"{kind} {x:.0%}" + (f" / {t:.0%}" if t else "")
            out.append((f"{nm}, clock {h} h", (kind, x, t), h))
    return out


def trade(H, L, Cl, rule, hours, fill):
    """-> (return as a fraction of the position, how, minutes held) or None if the resting sell never fills."""
    e = Cl[2] * 1.05
    f = np.flatnonzero(H[3:1440] >= e * 1.0005)
    if not f.size:
        return None
    k = 3 + int(f[0])
    last = min(2880, k + hours * 60)
    h, l, c = H[k + 1:last + 1], L[k + 1:last + 1], Cl[k + 1:last + 1]
    lo = np.minimum.accumulate(l)
    if fill == "LENIENT":
        lo = np.r_[np.inf, lo[:-1]]                                   # a moved stop counts from the next minute
    stop = np.full(len(h), e * (1 + STOP))
    if rule:
        kind, x, t = rule
        on = lo <= e * (1 - x)
        if kind == "BE":
            stop[on] = e
        elif kind == "LOCK":
            stop[on] = e * (1 - x / 2)
        else:
            stop[on] = np.minimum(stop[on], lo[on] * (1 + t))
    hit = np.flatnonzero(h >= stop)
    if hit.size:
        j = int(hit[0])
        px = (max(stop[j], c[j]) if fill == "STRICT" else stop[j]) * 1.001
        return -(px / e - 1), ("STOP" if stop[j] >= e * (1 + STOP) else "CUT"), j + 1
    return -(Cl[last] * 1.001 / e - 1), "CLOCK", last - k


def main():
    days = D.load()
    picks = [(d, p) for d, ps in days for p in ps]
    for fill in ("STRICT", "LENIENT"):                                 # the plain case against the validated simulator
        for d, p in picks:
            a, b = trade(p[2], p[3], p[4], None, 24, fill), V.trade(p[2], p[3], p[4], None, STOP, fill, ZERO)
            assert (a is None) == (b is None) and (a is None or abs(a[0] - b[1]) < 1e-12), (d, p[1], a, b)
    out = ["SPIKE SELL, WIDE STOP 10%: WAYS TO DEAL WITH THE FLIP BACK. Before fees, % of the position per trade. Plain 24 h case = review_real_fees.trade on every trade.",
           f"{'':26} | {'STRICT':^74} | {'LENIENT':^52}",
           f"{'':26} | {'win':>4} {'avg win':>8} {'avg loss':>8} {'mean':>8} {'t':>5} {'2025':>7} {'2026':>7} {'cut':>4} {'stop':>4} {'hold':>7} | {'win':>4} {'avg win':>8} {'avg loss':>8} {'mean':>8} {'t':>5} {'2025':>7} {'2026':>7}"]
    keep = {}
    for nm, rule, hours in variants():
        row = f"{nm:26} |"
        for fill in ("STRICT", "LENIENT"):
            r = [(d, trade(p[2], p[3], p[4], rule, hours, fill)) for d, p in picks]
            x = pd.DataFrame([(d, a[0], a[1], a[2]) for d, a in r if a], columns=["day", "ret", "how", "hold"])
            keep[(nm, fill)] = x
            day = x.groupby("day").ret.sum()
            t = day.mean() / (day.std(ddof=1) / np.sqrt(len(day)))
            w, lo = x.ret[x.ret > 0], x.ret[x.ret <= 0]
            row += (f" {100 * (x.ret > 0).mean():3.0f}% {100 * w.mean():+7.2f}% {100 * lo.mean():+7.2f}% {100 * x.ret.mean():+7.3f}% {t:+5.2f} "
                    f"{100 * x.ret[x.day.dt.year == 2025].mean():+6.2f}% {100 * x.ret[x.day.dt.year >= 2026].mean():+6.2f}%")
            if fill == "STRICT":
                row += f" {100 * (x.how == 'CUT').mean():3.0f}% {100 * (x.how == 'STOP').mean():3.0f}% {x.hold.median() / 60:5.1f} h |"
            else:
                row += " |"
        out.append(row)
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/spike_flip_back.txt").write_text(txt + "\n")
    pd.to_pickle(keep, "out/careful/spike_flip_back.pkl")


if __name__ == "__main__":
    main()
