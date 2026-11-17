"""Owner 2026-11-17: "with current setup check how often it reaches target of 40-45$ per trade".

Current setup = rule C, 10% trail FROM THE FILL (live since #172): resting sell 5% above the 00:02 close, stop 10%
above the entry, 10% trail from the fill, out after 24 h; position ~1x the balance (20% margin at 5x): $2,878 notional
at today's $2,895.60. So $40 = a 1.39% fall from the entry, $45 = 1.56%.

Two questions, both before fees:
  1  REACHED: while the trade is open, does price ever go $40 / $45 in profit? (STRICT: a 1m low passes the level by
     0.1%, before the exit minute -- the stop wins a shared minute; LENIENT: touch, including the exit minute.)
     And where do those trades END with the rule as it runs (do they give it back)?
  2  IF BOOKED: the same rule plus a resting take-profit at $40 / $45 (both implementations of spike_verify, which
     must agree). Cross-check: its TARGET count must equal question 1's REACHED count.
History: every fill of ranks 1-2 in the careful dataset. Real: the 13 trades the bot would have taken on its real
picks 10-29..11-17 (scripts/real_picks_rule_c.py, today's CROSS at its actual fill).

    PYTHONPATH=. python scripts/spike_usd_target.py      # -> out/careful/spike_usd_target.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import real_picks_rule_c as R  # noqa: E402
from spike_verify import CELLS, trade1, trade2  # noqa: E402

NOTIONAL = 2878.0
C = CELLS["C from the fill"]
USD = (40.0, 45.0)


def reached(H, L, a, e, lvl, strict):
    """first minute the trade was `lvl` in profit before it closed, or None"""
    k, j, ret, how, _ = a
    if j == k:
        return None
    hi = j if (how == "CLOCK" or not strict) else j - 1
    tgt = e * (1 - lvl) * (0.999 if strict else 1.0)
    lo = L[k + 1:hi + 1]
    f = np.flatnonzero(np.isfinite(lo) & (lo <= tgt))
    return k + 1 + int(f[0]) if f.size else None


def history(strict):
    rows = []
    for day, ps in D.load():
        for rank, sym, H, L, Cl in ps:
            a = trade1(H, L, Cl, C, strict)
            if not a:
                continue
            e = Cl[2] * 1.05
            row = dict(day=day, sym=sym, ret=a[2], how=a[3], k=a[0])
            for u in USD:
                lvl = u / NOTIONAL
                row[f"r{u:.0f}"] = reached(H, L, a, e, lvl, strict)
                c = dict(C, tp=lvl)
                t1, t2 = trade1(H, L, Cl, c, strict), trade2(H, L, Cl, c, strict)
                assert t1[:2] == t2[:2] and abs(t1[2] - t2[2]) < 1e-9 and t1[3] == t2[3], (day, sym, t1, t2)
                assert (t1[3] == "TARGET") == (row[f"r{u:.0f}"] is not None), (day, sym, t1, row)
                row[f"tp{u:.0f}"] = t1[2]
            rows.append(row)
    return pd.DataFrame(rows)


def real(strict):
    """the 13 trades the bot takes on its real picks (same selection as real_picks_rule_c)"""
    cand = []
    for day, rank, sym, H, L, Cl in PICKS:
        ent = None
        if (day, sym) in R.ACTUAL:
            k, e = R.ACTUAL[(day, sym)]
            ent = (k, e, e, True)
        a = trade1(H, L, Cl, C, strict, ent)
        cand.append((day, a[0] if a else 9999, rank, sym, H, L, Cl, ent, a))
    cand.sort(key=lambda x: (x[0], x[1], x[2]))
    rows, traded, busy = [], set(), None
    for day, _, rank, sym, H, L, Cl, ent, a in cand:
        if day in traded or (busy is not None and busy > pd.Timestamp(day)) or not a:
            continue
        t_in = pd.Timestamp(day) + pd.Timedelta(minutes=a[0])
        if busy is not None and t_in <= busy:
            continue
        busy = pd.Timestamp(day) + pd.Timedelta(minutes=a[1])
        traded.add(day)
        e = Cl[2] * 1.05 if ent is None else ent[1]
        row = dict(day=day, sym=sym, ret=a[2], how=a[3], k=a[0])
        for u in USD:
            lvl = u / NOTIONAL
            row[f"r{u:.0f}"] = reached(H, L, a, e, lvl, strict)
            t1 = trade1(H, L, Cl, dict(C, tp=lvl), strict, ent)
            assert (t1[3] == "TARGET") == (row[f"r{u:.0f}"] is not None), (day, sym, t1, row)
            row[f"tp{u:.0f}"] = t1[2]
        rows.append(row)
    return pd.DataFrame(rows)


def stats(r):
    usd = r * NOTIONAL
    w = usd > 0
    return (f"win {100 * w.mean():3.0f}%  avg win ${usd[w].mean():+5.0f}  avg loss ${usd[~w].mean():+5.0f}  "
            f"per trade ${usd.mean():+5.1f}")


def report(d, lab, w):
    usd = d.ret * NOTIONAL
    w(f"  {lab}: {len(d)} trades.  As it runs now: {stats(d.ret)}")
    w(f"    final result: >= $45 {100 * (usd >= 45).mean():.0f}%,  $40-45 {100 * ((usd >= 40) & (usd < 45)).mean():.0f}%,  "
      f"$0-40 {100 * ((usd > 0) & (usd < 40)).mean():.0f}%,  loss {100 * (usd <= 0).mean():.0f}%")
    for u in USD:
        hit = d[f"r{u:.0f}"].notna()
        mins = (d[f"r{u:.0f}"][hit] - d.k[hit]).astype(float)
        g = usd[hit]
        w(f"    REACHED ${u:.0f} at some point: {int(hit.sum())} of {len(d)} ({100 * hit.mean():.0f}%), median {mins.median() / 60:.1f} h after the fill;"
          f" of those, ended >= ${u:.0f} {100 * (g >= u).mean():.0f}%, ended $0-{u:.0f} {100 * ((g > 0) & (g < u)).mean():.0f}%, "
          f"ended a LOSS {100 * (g <= 0).mean():.0f}%")
        w(f"      never reached ${u:.0f}: {int((~hit).sum())} trades, {stats(d.ret[~hit])}")
        w(f"      IF BOOKED at ${u:.0f} (take-profit added): {stats(d[f'tp{u:.0f}'])}")


def main():
    global PICKS
    out = [__doc__.split("\n\n")[0], f"Notional ${NOTIONAL:,.0f}; $40 = {100 * 40 / NOTIONAL:.2f}%, $45 = {100 * 45 / NOTIONAL:.2f}% below the entry."]
    w = out.append
    for fill in ("STRICT", "LENIENT"):
        w(f"\n===== {fill}")
        h = history(fill == "STRICT")
        report(h, f"HISTORY {h.day.min():%Y-%m-%d}..{h.day.max():%Y-%m-%d}, every fill", w)
        for a, b in (("2025-01-01", "2025-07-01"), ("2025-07-01", "2026-01-01"), ("2026-01-01", "2026-07-01"), ("2026-07-01", "2027-01-01")):
            m = (h.day >= a) & (h.day < b)
            w(f"      {a[:7]}..: {int(m.sum())} trades, reached $40 {100 * h.r40[m].notna().mean():.0f}%; per trade as now "
              f"${NOTIONAL * h.ret[m].mean():+.1f}, booked at $40 ${NOTIONAL * h.tp40[m].mean():+.1f}, at $45 ${NOTIONAL * h.tp45[m].mean():+.1f}")
        if fill == "STRICT":
            PICKS = R.picks()
        report(real(fill == "STRICT"), "REAL PICKS 10-29..11-17, as the bot trades them", w)
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/spike_usd_target.txt").write_text(txt + "\n")


PICKS = []

if __name__ == "__main__":
    main()
