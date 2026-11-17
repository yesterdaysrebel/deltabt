"""Owner 2026-11-17: "verify all of them plus quick with high leverage quick exit".

Verifies the candidates from scripts/spike_fewer_losers.py (found after ~70 cells were looked at, so they are
expected to shrink) and a fixed set of quick, high-leverage exits written down here BEFORE the run. Nothing is
dropped; every cell below is printed.

ENTRIES (same picks as live, ranks 1-2, careful dataset 2025-01..2026-11; minute 0 = 00:00 UTC of the pick day)
  live    resting sell at 5% above the 00:02 close; fills when a high reaches it +0.05%
  turn    after a high reaches +5%, sell at the first 1m close 2% under the highest high since (a market sell)
CELLS
  LIVE   live entry, stop 2.25% above the entry, 5% trail from the fill, out after 24 h (what runs now)
  A      turn entry, stop 5% above the spike high, 10% trail once the trail is under the entry, 24 h
  B      turn entry, stop 5% above the spike high, 5% trail once under the entry, 8 h
  C      live entry, stop 10% above the entry, 10% trail once under the entry, 24 h
  Q*     QUICK, high leverage: entry live or turn; stop 2.25% above the ENTRY (so 4x the account, 20x at 20% margin);
         take profit 1% or 2% below the entry (a resting limit); out at market after 15 or 60 min. 8 cells.
FILLS  STRICT  stop at the worse of stop and the minute's close +0.1%; market sells 0.05% worse; the target must be
               passed by 0.1%; the stop wins a shared minute.
       LENIENT stop at its price; market at the close; target on touch; the target wins a shared minute.
CHECKS
  1  two implementations (a minute loop and an array version written separately) must agree on every trade
  2  STRICT and LENIENT; by half-year; t by day; months up; without the 5 best days
  3  CONTROL: the same exit after a short at a random minute of the same pick days (does the entry matter?)
  4  ACCOUNT: $3,184, one position at a time (first fill of the day wins, ties by rank, nothing new while a
     position is open), compounding; each trade sized so a full stop costs 9% of the balance (the live risk),
     capped at 4x the balance and at the product's full-leverage notional
  5  one line with real fees (maker 0.0212%, taker 0.0531%, exits inside 15 min free under the scalper offer)

    PYTHONPATH=. python scripts/spike_verify.py      # -> out/careful/spike_verify.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402

RISK, CAP, START = 0.09, 4.0, 3184.0
MAKER, TAKER = 0.000212, 0.000531

CELLS = {"LIVE": dict(entry="live", stop=0.0225, base="entry", trail=0.05, be=False, mins=1440, tp=None),
         "A": dict(entry="turn", stop=0.05, base="high", trail=0.10, be=True, mins=1440, tp=None),
         "B": dict(entry="turn", stop=0.05, base="high", trail=0.05, be=True, mins=480, tp=None),
         "C": dict(entry="live", stop=0.10, base="entry", trail=0.10, be=True, mins=1440, tp=None),
         # owner 2026-11-17 "use 10% from the fill" (after the trail table): C with the trail tightening from the fill
         "C from the fill": dict(entry="live", stop=0.10, base="entry", trail=0.10, be=False, mins=1440, tp=None)}
for en in ("live", "turn"):
    for tp in (0.01, 0.02):
        for m in (15, 60):
            CELLS[f"Q {en} tp{tp:.0%} {m}m"] = dict(entry=en, stop=0.0225, base="entry", trail=0.0, be=False, mins=m, tp=tp)


# ---------------------------------------------------------------- implementation 1: a minute loop
def entry1(H, Cl, kind, strict):
    trig = Cl[2] * 1.05
    if kind == "live":
        for k in range(3, 1440):
            if np.isfinite(H[k]) and H[k] >= trig * 1.0005:
                return k, trig, trig, True                         # (minute, price, spike high, filled inside minute k)
        return None
    k0 = None
    for k in range(3, 1440):
        if np.isfinite(H[k]) and H[k] >= trig:
            k0 = k
            break
    if k0 is None:
        return None
    hi = 0.0
    for k in range(k0, 1440):
        if not np.isfinite(H[k]):
            continue
        hi = max(hi, H[k])
        if Cl[k] <= hi * 0.98:
            return k, Cl[k] * (0.9995 if strict else 1.0), hi, False
    return None


def trade1(H, L, Cl, c, strict, ent=None):
    en = ent or entry1(H, Cl, c["entry"], strict)
    if en is None:
        return None
    k, e, hi, inside = en
    sp = (e if c["base"] == "entry" else hi) * (1 + c["stop"])
    dist = sp / e - 1
    tp = e * (1 - c["tp"]) if c["tp"] else None
    last = min(len(Cl) - 1, k + c["mins"])

    def stopped(j):
        return -((max(sp, Cl[j]) * 1.001 if strict else sp) / e - 1)
    if inside and H[k] >= sp:
        return k, k, stopped(k), "STOP", dist
    low = np.inf
    for j in range(k + 1, last + 1):
        if not np.isfinite(H[j]):
            continue
        hs = H[j] >= sp
        ht = tp is not None and L[j] <= (tp * 0.999 if strict else tp)
        if hs and (strict or not ht):
            return k, j, stopped(j), "STOP", dist
        if ht:
            return k, j, c["tp"], "TARGET", dist
        low = min(low, L[j])
        if c["trail"]:
            t = low * (1 + c["trail"])
            if not c["be"] or t <= e:
                sp = min(sp, t)
    fin = np.flatnonzero(np.isfinite(Cl[:last + 1]))
    x = Cl[fin[-1]]
    return k, last, -((x * 1.0005 if strict else x) / e - 1), "CLOCK", dist


# ---------------------------------------------------------------- implementation 2: arrays
def entry2(H, Cl, kind, strict):
    trig = Cl[2] * 1.05
    h = np.where(np.isfinite(H[:1440]), H[:1440], -np.inf)
    h[:3] = -np.inf
    if kind == "live":
        f = np.flatnonzero(h >= trig * 1.0005)
        return (int(f[0]), trig, trig, True) if f.size else None
    f = np.flatnonzero(h >= trig)
    if not f.size:
        return None
    k0 = int(f[0])
    run = np.maximum.accumulate(h[k0:])
    c = np.where(np.isfinite(Cl[k0:1440]) & np.isfinite(H[k0:1440]), Cl[k0:1440], np.inf)
    g = np.flatnonzero(c <= run * 0.98)
    if not g.size:
        return None
    k = k0 + int(g[0])
    return k, Cl[k] * (0.9995 if strict else 1.0), run[g[0]], False


def trade2(H, L, Cl, c, strict, ent=None):
    en = ent or entry2(H, Cl, c["entry"], strict)
    if en is None:
        return None
    k, e, hi, inside = en
    sp0 = (e if c["base"] == "entry" else hi) * (1 + c["stop"])
    dist = sp0 / e - 1
    if inside and H[k] >= sp0:
        return k, k, -((max(sp0, Cl[k]) * 1.001 if strict else sp0) / e - 1), "STOP", dist
    last = min(len(Cl) - 1, k + c["mins"])
    idx = np.arange(k + 1, last + 1)
    ok = np.isfinite(H[idx])
    hh, ll, cc = np.where(ok, H[idx], -np.inf), np.where(ok, L[idx], np.inf), Cl[idx]
    prevlow = np.r_[np.inf, np.minimum.accumulate(ll)[:-1]]        # lowest low BEFORE each minute
    sp = np.full(len(idx), sp0)
    if c["trail"]:
        t = prevlow * (1 + c["trail"])
        sp = np.minimum(sp0, np.where(t <= e, t, np.inf) if c["be"] else t)
    hit_s = hh >= sp
    hit_t = np.zeros(len(idx), bool) if not c["tp"] else ll <= e * (1 - c["tp"]) * (0.999 if strict else 1.0)
    first = np.flatnonzero(hit_s | hit_t)
    if first.size:
        j = int(first[0])
        if hit_s[j] and (strict or not hit_t[j]):
            px = max(sp[j], cc[j]) * 1.001 if strict else sp[j]
            return k, int(idx[j]), -(px / e - 1), "STOP", dist
        return k, int(idx[j]), c["tp"], "TARGET", dist
    x = Cl[:last + 1][np.isfinite(Cl[:last + 1])][-1]
    return k, last, -((x * 1.0005 if strict else x) / e - 1), "CLOCK", dist


# ---------------------------------------------------------------- reporting
def summary(d):
    if len(d) < 10:
        return f"{len(d):4d} trades"
    r = d.ret
    day = r.groupby(d.day).sum()
    t = day.mean() / (day.std(ddof=1) / np.sqrt(len(day)))
    return (f"{len(d):4d} trades  win {100 * (r > 0).mean():3.0f}%  avg win {100 * r[r > 0].mean():+6.2f}%  avg loss {100 * r[r <= 0].mean():+6.2f}%  "
            f"mean {100 * r.mean():+6.2f}%  t {t:+5.2f}")


def fees(row, c):
    """fee as a fraction of the position"""
    fin = MAKER if c["entry"] == "live" else TAKER
    held = row.j - row.k
    fout = 0.0 if held <= 15 else (MAKER if row.how == "TARGET" else TAKER)
    return fin + fout


def account(d, c, with_fees=False):
    t0 = d.day.map(lambda x: int(pd.Timestamp(x).timestamp())).to_numpy()
    d = d.assign(tin=t0 + 60 * d.k.to_numpy(), tout=t0 + 60 * d.j.to_numpy()).sort_values(["tin", "rank"])
    bal, free, peak, mdd, lo, n, cash = START, -1, START, 0.0, START, 0, []
    for x in d.itertuples():
        if x.tin <= free:
            continue
        lim = D.PRODUCTS.get(x.sym, {}).get("max_lev_notional", np.inf)
        size = min(RISK * bal / max(x.dist, 1e-9), CAP * bal, lim)
        r = x.ret - (fees(x, c) if with_fees else 0.0)
        pnl = size * r
        cash.append((START * min(RISK / max(x.dist, 1e-9), CAP)) * r)   # the same trade at a fixed $3,184
        bal += pnl
        n += 1
        free = x.tout
        peak = max(peak, bal)
        mdd = max(mdd, 1 - bal / peak)
        lo = min(lo, bal)
        if bal <= 0:
            break
    cash = np.array(cash)
    w = cash > 0
    return (f"{n:4d} taken  ${bal:>12,.0f} at the end  lowest ${lo:,.0f}  worst fall {100 * mdd:3.0f}%  |  at a fixed $3,184: "
            f"avg win ${cash[w].mean():+5.0f}  avg loss ${cash[~w].mean():+5.0f}  per trade ${cash.mean():+5.0f}")


def main():
    days = D.load()
    rng = np.random.default_rng(20261117)
    out = ["VERIFYING THE FEWER-LOSERS CANDIDATES AND THE QUICK HIGH-LEVERAGE EXITS (scripts/spike_verify.py). Before fees unless said; % of the position.",
           f"{sum(len(p) for _, p in days)} picks, {days[0][0]:%Y-%m-%d} .. {days[-1][0]:%Y-%m-%d}."]
    w = out.append
    for name, c in CELLS.items():
        res = {}
        for fill in ("STRICT", "LENIENT"):
            rows = []
            for day, ps in days:
                for rank, sym, H, L, Cl in ps:
                    a = trade1(H, L, Cl, c, fill == "STRICT")
                    b = trade2(H, L, Cl, c, fill == "STRICT")
                    assert (a is None) == (b is None) and (a is None or (a[:2] == b[:2] and abs(a[2] - b[2]) < 1e-9 and a[3] == b[3])), (name, fill, day, sym, a, b)
                    if a:
                        rows.append((day, sym, rank, *a))
            res[fill] = pd.DataFrame(rows, columns=["day", "sym", "rank", "k", "j", "ret", "how", "dist"])
        S, Ln = res["STRICT"], res["LENIENT"]
        w(f"\n===== {name}   {c}   (the two implementations agree on all {len(S) + len(Ln)} trades)")
        w(f"   STRICT   {summary(S)}   stop {100 * S.dist.median():.1f}% away (median); exits: " + ", ".join(f"{k} {100 * v:.0f}%" for k, v in S.how.value_counts(normalize=True).items()))
        w(f"   LENIENT  {summary(Ln)}")
        for lab, m in (("2025 H1", S.day < "2025-07-01"), ("2025 H2", (S.day >= "2025-07-01") & (S.day < "2026-01-01")),
                       ("2026 H1", (S.day >= "2026-01-01") & (S.day < "2026-07-01")), ("2026 H2", S.day >= "2026-07-01")):
            w(f"     {lab}  {summary(S[m])}")
        mo = S.groupby(S.day.dt.strftime("%Y-%m")).ret.sum()
        top = S.groupby("day").ret.sum().nlargest(5).index
        w(f"   months up {int((mo > 0).sum())} of {len(mo)};  without the 5 best days: {summary(S[~S.day.isin(top)])}")
        ctrl = []
        for day, ps in days:
            for rank, sym, H, L, Cl in ps:
                ok = np.flatnonzero(np.isfinite(Cl[3:1440]) & np.isfinite(H[3:1440])) + 3
                for k in rng.choice(ok, size=5):
                    k = int(k)
                    e = Cl[k] * 0.9995
                    a = trade1(H, L, Cl, c, True, ent=(k, e, e * (1 + S.dist.median()) / (1 + c["stop"]) if c["base"] == "high" else e, False))
                    ctrl.append((day, a[2]))
        w(f"   CONTROL random-minute short, same exit (STRICT): {summary(pd.DataFrame(ctrl, columns=['day', 'ret']))}")
        w(f"   ACCOUNT  STRICT  {account(S, c)}")
        w(f"            LENIENT {account(Ln, c)}")
        w(f"            STRICT with real fees {account(S, c, True)}")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/spike_verify.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
