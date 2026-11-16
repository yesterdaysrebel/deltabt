"""Quick trades on Delta's non-crypto perps -- rules in docs/tradfi_quick_prereg.md (written before any data was seen).

    PYTHONPATH=. python scripts/tradfi_quick.py            # -> out/tradfi_quick/report.txt

Calendar note (a fact, not a tuning choice): US daylight time 2026 runs 03-08 .. 11-01, so the open/close are
14:30/21:00 UTC before 03-09 and from 11-02, 13:30/20:00 UTC in between. US market holidays in the data skipped.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("out/tradfi_quick")
DEV_END = pd.Timestamp("2026-08-15", tz="UTC")
TEST_MID = pd.Timestamp("2026-10-01", tz="UTC")
END = pd.Timestamp("2026-11-16", tz="UTC")
SLIP = {"STRICT": 0.0005, "LENIENT": 0.0001}
NONSTOCK = {"XAUTUSD", "PAXGUSD", "SLVONUSD", "OILUSD"}
HOLIDAYS = {pd.Timestamp(d).date() for d in ("2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07")}
FEES = {x["symbol"]: float(x["taker_commission_rate"]) for x in json.load(open(OUT / "products.json"))["result"]}


class Sym:
    """One symbol on a full minute grid. m = minute number (unix // 60)."""

    def __init__(self, sym):
        t = pd.read_parquet(OUT / f"{sym}_trade.parquet")
        # Delta's REST forward-fills no-trade minutes (flat candle, volume 0): 76-90% of stock-token minutes.
        # Only a minute with volume > 0 holds a real trade price (the pre-registration's "minute with a trade").
        t = t[t.volume.fillna(0) > 0]
        i =pd.read_parquet(OUT / f"{sym}_index.parquet") if (OUT / f"{sym}_index.parquet").exists() else None
        self.sym, self.fee = sym, FEES[sym]
        m0, m1 = int(t.time.min()) // 60, int(END.timestamp()) // 60
        self.m0, n = m0, m1 - m0
        self.op = np.full(n, np.nan)
        self.cl = np.full(n, np.nan)
        k = (t.time.values // 60 - m0).astype(int)
        ok = (k >= 0) & (k < n)
        self.op[k[ok]] = t.open.values[ok].astype(float)
        self.cl[k[ok]] = t.close.values[ok].astype(float)
        self.ix = np.full(n, np.nan)
        if i is not None and len(i):
            k = (i.time.values // 60 - m0).astype(int)
            ok = (k >= 0) & (k < n)
            self.ix[k[ok]] = i.close.values[ok].astype(float)
            self.ix = pd.Series(self.ix).ffill(limit=5).values
        self.traded = ~np.isnan(self.cl)
        nxt = np.full(n + 1, n, dtype=np.int64)          # next traded index at or after j
        for j in range(n - 1, -1, -1):
            nxt[j] = j if self.traded[j] else nxt[j + 1]
        self.nxt = nxt
        last = np.full(n, -1, dtype=np.int64)            # last traded index at or before j
        p = -1
        for j in range(n):
            if self.traded[j]:
                p = j
            last[j] = p
        self.last = last

    def idx(self, ts):
        return int(ts.timestamp()) // 60 - self.m0

    def fill(self, s, side, slip, within=2):
        """Order decided at the close of minute s; fills at the open of the next traded minute (within `within`
        minutes for an entry; None = wait as long as needed). side +1 buy, -1 sell. Returns (j, price) or None."""
        if s + 1 >= len(self.op):
            return None
        j = self.nxt[s + 1]
        if j >= len(self.op) or (within is not None and j - s > within):
            return None
        return j, self.op[j] * (1 + side * slip)

    def price_at(self, s, back=10):
        j = self.last[s] if 0 <= s < len(self.last) else -1
        return self.cl[j] if j >= 0 and s - j <= back else np.nan


def net(side, pin, pout, fee):
    return side * (pout / pin - 1) - 2 * fee


# ------------------------------------------------------------------ F1 basis snap-back
def f1(S, k, H, slip):
    gap = S.cl / S.ix - 1
    cand = np.flatnonzero(S.traded & (np.abs(gap) >= k))
    out, free = [], -1
    for s in cand:
        if s <= free:
            continue
        side = -1 if gap[s] > 0 else 1                   # perp rich -> sell
        e = S.fill(s, side, slip)
        if e is None:
            continue
        j, pin = e
        w = np.abs(gap[j:j + H + 1]) <= k / 3
        hit = np.flatnonzero(w & S.traded[j:j + H + 1])
        xs = j + int(hit[0]) if hit.size else min(j + H, len(S.op) - 2)
        x = S.fill(xs, -side, slip, within=None)
        if x is None:
            break
        out.append((S.m0 + s, S.sym, net(side, pin, x[1], S.fee), side * (x[1] / pin - 1) + 2 * side * slip * 0))
        free = x[0]
    return out


# ------------------------------------------------------------------ US session calendar
def sessions():
    days = pd.date_range("2026-02-19", "2026-11-15", freq="B", tz="UTC")
    out = []
    for d in days:
        if d.date() in HOLIDAYS:
            continue
        summer = pd.Timestamp("2026-03-09", tz="UTC") <= d < pd.Timestamp("2026-11-02", tz="UTC")
        o = d + pd.Timedelta(hours=13 if summer else 14, minutes=30)
        c = d + pd.Timedelta(hours=20 if summer else 21)
        out.append((d, o, c))
    return out


SESS = sessions()


# ------------------------------------------------------------------ F2 off-hours move into the open
def f2(S, m, X, slip, fade=True):
    out = []
    for i in range(1, len(SESS)):
        d, o, c = SESS[i]
        pc = SESS[i - 1][2]
        a, s = S.idx(pc) - 1, S.idx(o) - 6               # minute closing at prev close / at open-5
        if a < 0 or s >= len(S.op) - 1:
            continue
        p0, p1 = S.price_at(a), S.price_at(s)
        if not (p0 > 0 and p1 > 0):
            continue
        mv = p1 / p0 - 1
        if abs(mv) < m:
            continue
        side = (-1 if mv > 0 else 1) * (1 if fade else -1)
        e = S.fill(s, side, slip)
        if e is None:
            continue
        x = S.fill(S.idx(o) + X - 1, -side, slip, within=None)
        if x is None:
            continue
        out.append((S.m0 + s, S.sym, net(side, e[1], x[1], S.fee), None))
    return out


# ------------------------------------------------------------------ F4 intraday momentum
def f4(S, slip):
    out = []
    for i in range(1, len(SESS)):
        d, o, c = SESS[i]
        pc = SESS[i - 1][2]
        p0, p1 = S.price_at(S.idx(pc) - 1), S.price_at(S.idx(o) + 29)
        if not (p0 > 0 and p1 > 0) or p1 == p0:
            continue
        side = 1 if p1 > p0 else -1
        s = S.idx(c) - 31
        e = S.fill(s, side, slip)
        if e is None:
            continue
        x = S.fill(S.idx(c) - 1, -side, slip, within=None)
        if x is None:
            continue
        out.append((S.m0 + s, S.sym, net(side, e[1], x[1], S.fee), None))
    return out


# ------------------------------------------------------------------ F3 gold pair
def f3(P, X, d, slip):
    base = max(P.m0, X.m0)
    n = min(P.m0 + len(P.op), X.m0 + len(X.op)) - base
    pa, xa = base - P.m0, base - X.m0
    pc, xc = P.cl[pa:pa + n], X.cl[xa:xa + n]
    both = ~np.isnan(pc) & ~np.isnan(xc)
    r = pd.Series(np.where(both, np.log(pc / xc), np.nan))
    mean = r.rolling(1440, min_periods=300).mean().shift(1)
    dev = (r - mean).values
    cand = np.flatnonzero(both & (np.abs(dev) >= d))
    out, free = [], -1
    for s in cand:
        if s <= free:
            continue
        sp = -1 if dev[s] > 0 else 1                     # PAXG rich -> sell PAXG, buy XAUT
        e1, e2 = P.fill(s + pa, sp, slip), X.fill(s + xa, -sp, slip)
        if e1 is None or e2 is None:
            continue
        j = max(e1[0] - pa, e2[0] - xa)
        seg = dev[j:j + 241]
        cross = np.flatnonzero(np.sign(seg) == -np.sign(dev[s]))
        xs = j + int(cross[0]) if cross.size else min(j + 240, n - 2)
        x1, x2 = P.fill(xs + pa, -sp, slip, within=None), X.fill(xs + xa, sp, slip, within=None)
        if x1 is None or x2 is None:
            break
        ret = sp * (x1[1] / e1[1] - 1) - sp * (x2[1] / e2[1] - 1) - 2 * P.fee - 2 * X.fee
        out.append((base + s, "PAXG/XAUT", ret, None))
        free = max(x1[0] - pa, x2[0] - xa)
    return out


# ------------------------------------------------------------------ stats
def frame(rows):
    d = pd.DataFrame(rows, columns=["m", "sym", "ret", "_"])
    d["t"] = pd.to_datetime(d.m * 60, unit="s", utc=True)
    d["day"] = d.t.dt.floor("D")
    return d


def stat(d):
    if len(d) == 0:
        return dict(n=0, mean=np.nan, t=np.nan, win=np.nan)
    by = d.groupby("day").ret.mean()
    t = by.mean() / (by.std(ddof=1) / np.sqrt(len(by))) if len(by) > 2 else np.nan
    return dict(n=len(d), mean=d.ret.mean() * 100, t=t, win=(d.ret > 0).mean() * 100)


def fmt(s):
    return f"n {s['n']:5d} mean {s['mean']:+.4f}% t {s['t']:+5.2f} win {s['win']:3.0f}%" if s["n"] else "n     0"


def judge(name, grid, run, lines, per_symbol=True):
    """grid: list of (label, params). run(params, slip) -> rows. Freeze the best DEV (STRICT) cell; judge it on TEST."""
    lines.append(f"\n=== {name} ===")
    best = None
    for lab, p in grid:
        d = frame(run(p, SLIP["STRICT"]))
        dv, ts = d[d.t < DEV_END], d[d.t >= DEV_END]
        s = stat(dv)
        lines.append(f"   {lab:28s} DEV {fmt(s)} | TEST {fmt(stat(ts))}")
        if s["n"] >= 30 and (best is None or s["mean"] > best[0]):
            best = (s["mean"], lab, p)
    if best is None:
        lines.append("   no cell with >= 30 DEV trades")
        return
    lab, p = best[1], best[2]
    lines.append(f"   FROZEN on DEV: {lab}")
    for nm, sl in SLIP.items():
        d = frame(run(p, sl))
        ts = d[d.t >= DEV_END]
        h1, h2 = ts[ts.t < TEST_MID], ts[ts.t >= TEST_MID]
        s = stat(ts)
        ps = ts.groupby("sym").ret.mean()
        okp = (ps > 0).mean() if len(ps) else np.nan
        verdict = s["n"] > 0 and s["mean"] > 0 and s["t"] >= 2 and stat(h1)["mean"] > 0 and stat(h2)["mean"] > 0 and (not per_symbol or okp >= 0.6)
        lines.append(f"   TEST {nm:7s} {fmt(s)} | halves {stat(h1)['mean']:+.4f}% / {stat(h2)['mean']:+.4f}% | "
                     f"symbols positive {(ps > 0).sum()}/{len(ps)} | {'PASS' if verdict else 'FAIL'}")


def main():
    syms = sorted(p.name[:-14] for p in OUT.glob("*_trade.parquet"))
    S = {s: Sym(s) for s in syms}
    lines = ["Quick trades on Delta non-crypto perps (docs/tradfi_quick_prereg.md). % of notional per trade, after fees and slip."]
    lines.append("coverage (share of minutes with a trade, since listing): " +
                 ", ".join(f"{s[:-3]} {100 * S[s].traded.mean():.0f}%" for s in syms))
    stocks = [s for s in syms if s not in NONSTOCK]
    grid1 = [(f"k {k:.2%} H {H}", (k, H)) for k in (0.0015, 0.003, 0.005) for H in (5, 15, 60)]
    judge("F1 basis snap-back (all 26)", grid1, lambda p, sl: [r for s in syms for r in f1(S[s], p[0], p[1], sl)], lines)
    grid2 = [(f"m {m:.1%} X {X}", (m, X)) for m in (0.005, 0.01, 0.02) for X in (15, 60, 120)]
    judge("F2 FADE the off-hours move into the open (stock tokens)", grid2,
          lambda p, sl: [r for s in stocks for r in f2(S[s], p[0], p[1], sl, True)], lines)
    judge("F2f FOLLOW the off-hours move into the open (stock tokens)", grid2,
          lambda p, sl: [r for s in stocks for r in f2(S[s], p[0], p[1], sl, False)], lines)
    if "PAXGUSD" in S and "XAUTUSD" in S:
        grid3 = [(f"d {d:.2%}", d) for d in (0.0005, 0.001, 0.002)]
        judge("F3 gold pair PAXG/XAUT", grid3, lambda p, sl: f3(S["PAXGUSD"], S["XAUTUSD"], p, sl), lines, per_symbol=False)
    judge("F4 intraday momentum, last 30 min (stock tokens)", [("no parameters", None)],
          lambda p, sl: [r for s in stocks for r in f4(S[s], sl)], lines)
    txt = "\n".join(lines)
    print(txt)
    (OUT / "report.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
