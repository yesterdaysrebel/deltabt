"""Owner 2026-11-17: "run test on quick with 50% margin, break even lock and 1% exit".

QUICK's picks and entry (short the day's top-2 gainers at the 00:00 close), then, written before the run:
  margin   each pick posts 50% of the day-start balance as ISOLATED margin (2 picks = the whole balance), at
           leverage L = 2, 3, 5, 10, 20 (notional = 0.5 x L x balance). Liquidation at entry x (1 + 1/L - mm), mm = the
           product's maintenance margin (2.5% if unknown); a liquidation loses the whole margin (50% of the balance).
  stop     QUICK's 2 x ATR% above the entry, or liquidation if that is nearer
  lock     once a 1m low is LOCK below the entry, the stop moves to the entry from the next minute (break-even);
           LOCK = none, 0.3%, 0.5% (the main cell), 0.7%
  target   1% below the entry (resting limit)
  clock    out at 23:59 UTC
FILLS  STRICT  entry 0.05% worse; stop / lock at the worse of its level and the minute's close +0.1%; the target must be
               passed by 0.1%; the stop wins a shared minute; the lock starts the minute AFTER the low reached it.
       LENIENT stop / lock at its level; target on touch and wins a shared minute.
       Liquidation is on the traded high here (Delta uses the mark price, which spikes less: a slightly harsh choice).
Two implementations (a minute loop and an array version) must agree on every trade. Before fees; one line with fees.

    PYTHONPATH=. python scripts/quick_margin50.py      # -> out/careful/quick_margin50.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402

START, MARGIN, TP = 3184.0, 0.50, 0.01
MAKER, TAKER = 0.000212, 0.000531
LEVS, LOCKS = (2, 3, 5, 10, 20), (None, 0.003, 0.005, 0.007)


def levels(e, atrp, lev, mm):
    stop = e * (1 + 2 * atrp)
    liq = e * (1 + 1 / lev - mm)
    return min(stop, liq), liq < stop


def sim1(H, L, Cl, e, sp0, liq_first, lock, strict):
    """minute loop -> (exit minute, return on the position, how)"""
    sp, locked = sp0, False
    tp = e * (1 - TP)
    for j in range(1, 1440):
        if not np.isfinite(H[j]):
            continue
        hs = H[j] >= sp
        ht = L[j] <= (tp * 0.999 if strict else tp)
        if hs and (strict or not ht):
            if not locked and liq_first:
                return j, None, "LIQUIDATED"
            px = max(sp, Cl[j]) * 1.001 if strict else sp
            return j, -(px / e - 1), "LOCK" if locked else "STOP"
        if ht:
            return j, TP, "TARGET"
        if lock is not None and not locked and L[j] <= e * (1 - lock):
            sp, locked = e, True
    x = Cl[:1440][np.isfinite(Cl[:1440])][-1]
    return 1439, -((x * 1.0005 if strict else x) / e - 1), "CLOCK"


def sim2(H, L, Cl, e, sp0, liq_first, lock, strict):
    """arrays"""
    m = np.arange(1, 1440)
    ok = np.isfinite(H[m])
    hh, ll, cc = np.where(ok, H[m], -np.inf), np.where(ok, L[m], np.inf), Cl[m]
    sp = np.full(len(m), sp0)
    lk = np.zeros(len(m), bool)
    if lock is not None:
        a = np.flatnonzero(ll <= e * (1 - lock))
        if a.size:
            lk[a[0] + 1:] = True
            sp[a[0] + 1:] = e
    hit_s = hh >= sp
    hit_t = ll <= e * (1 - TP) * (0.999 if strict else 1.0)
    f = np.flatnonzero(hit_s | hit_t)
    if f.size:
        j = int(f[0])
        if hit_s[j] and (strict or not hit_t[j]):
            if not lk[j] and liq_first:
                return int(m[j]), None, "LIQUIDATED"
            px = max(sp[j], cc[j]) * 1.001 if strict else sp[j]
            return int(m[j]), -(px / e - 1), "LOCK" if lk[j] else "STOP"
        return int(m[j]), TP, "TARGET"
    x = Cl[:1440][np.isfinite(Cl[:1440])][-1]
    return 1439, -((x * 1.0005 if strict else x) / e - 1), "CLOCK"


def run(days, atr, lev, lock, strict):
    rows = []
    for day, ps in days:
        for rank, sym, H, L, Cl in ps:
            if not np.isfinite(Cl[0]):
                continue
            e = Cl[0] * (0.9995 if strict else 1.0)
            mm = D.PRODUCTS.get(sym, {}).get("mm", 2.5) / 100
            sp0, liq_first = levels(e, atr[(day, sym)], lev, mm)
            a = sim1(H, L, Cl, e, sp0, liq_first, lock, strict)
            b = sim2(H, L, Cl, e, sp0, liq_first, lock, strict)
            assert a[0] == b[0] and a[2] == b[2] and (a[1] is None) == (b[1] is None) and (a[1] is None or abs(a[1] - b[1]) < 1e-9), (day, sym, a, b)
            acct = -MARGIN if a[1] is None else max(-MARGIN, MARGIN * lev * a[1])    # change in the balance, as a fraction
            fee = MARGIN * lev * (TAKER + (0.0 if a[0] <= 15 else (MAKER if a[2] == "TARGET" else TAKER)))
            rows.append((day, sym, a[0], a[2], acct, fee))
    return pd.DataFrame(rows, columns=["day", "sym", "j", "how", "acct", "fee"])


def account(d, fees=False):
    bal, peak, mdd, lo, dead = START, START, 0.0, START, None
    for day, g in d.groupby("day"):
        b0 = bal
        bal += b0 * (g.acct - (g.fee if fees else 0.0)).sum()
        peak, lo = max(peak, bal), min(lo, bal)
        mdd = max(mdd, 1 - bal / peak) if peak > 0 else 1.0
        if bal <= 0.05 * START:
            dead = day
            break
    s = f"${bal:>10,.0f} end, lowest ${lo:,.0f}, worst fall {100 * min(mdd, 1):.0f}%"
    return s + (f", BUST {dead:%Y-%m-%d}" if dead is not None else "")


def line(d):
    usd = d.acct * START
    w = usd > 0
    hy = [d.acct[(d.day >= a) & (d.day < b)].mean() * START for a, b in
          (("2025-01-01", "2025-07-01"), ("2025-07-01", "2026-01-01"), ("2026-01-01", "2026-07-01"), ("2026-07-01", "2027-01-01"))]
    ex = d.how.value_counts(normalize=True)
    return (f"win {100 * w.mean():3.0f}%  avg win ${usd[w].mean():+6.0f}  avg loss ${usd[~w].mean():+6.0f}  per trade ${usd.mean():+6.1f}  "
            f"| half-years {' '.join(f'{x:+.0f}' for x in hy)} | " + " ".join(f"{k} {100 * v:.0f}%" for k, v in ex.items()))


def main():
    days = D.load()
    P = pd.read_csv("out/careful/picks_atr.csv", parse_dates=["day"])
    atr = {(d, s): a for d, s, a in zip(P.day, P.symbol, P.atrp)}
    out = ["QUICK WITH 50% MARGIN PER PICK, A BREAK-EVEN LOCK AND A 1% TARGET (scripts/quick_margin50.py). Entries 00:00 UTC, top-2 gainers, "
           f"{days[0][0]:%Y-%m-%d}..{days[-1][0]:%Y-%m-%d}. $ = the trade at a fixed $3,184 balance ($1,592 margin per pick). Before fees unless said.",
           "Half-years = mean $ per trade in 2025 H1, H2, 2026 H1, H2. Account = $3,184 compounding, both picks each day, 50% margin each."]
    w = out.append
    for lev in LEVS:
        w(f"\n===== {lev}x  (notional {MARGIN * lev:.1f}x the balance per pick; a 1% target = {100 * MARGIN * lev * TP:.1f}% of the balance)")
        for lock in LOCKS:
            S, Ln = run(days, atr, lev, lock, True), run(days, atr, lev, lock, False)
            nm = "no lock" if lock is None else f"lock at {lock:.1%}"
            w(f"  {nm:12} STRICT  {len(S)} trades  {line(S)}")
            w(f"  {'':12} LENIENT          {line(Ln)}")
            w(f"  {'':12} account: STRICT {account(S)} | LENIENT {account(Ln)} | STRICT + fees {account(S, True)}")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/quick_margin50.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
