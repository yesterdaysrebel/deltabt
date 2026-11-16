"""Five more families on Delta's liquid perps -- docs/intraday_families2_prereg.md.

    PYTHONPATH=. python scripts/intraday_families2.py      # -> out/intraday_families2.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import intraday_families as F  # noqa: E402

NM = F.NMIN


def variants(X):
    syms = list(X)
    nd = NM // 1440
    # H1 opening range, H2 first part predicts the rest
    for R in (30, 60):
        for hold in (120, 360):
            out = []
            for sym in syms:
                a = X[sym]
                c = a["c"].astype(float)
                for d in range(nd):
                    d0 = d * 1440
                    if not a["liq"][d0]:
                        continue
                    hi, lo = np.nanmax(a["h"][d0:d0 + R].astype(float)), np.nanmin(a["l"][d0:d0 + R].astype(float))
                    seg = c[d0 + R:d0 + 1440 - hold]
                    with np.errstate(invalid="ignore"):
                        u, v = np.flatnonzero(seg > hi), np.flatnonzero(seg < lo)
                    iu, iv = (u[0] if u.size else 10 ** 9), (v[0] if v.size else 10 ** 9)
                    if min(iu, iv) < 10 ** 9:
                        out.append(F.rets(a, [d0 + R + int(min(iu, iv))], 1.0 if iu < iv else -1.0, hold))
            yield "H1 OPENING RANGE", f"first {R} min, hold {hold} (follow)", np.vstack(out)
    for T in (480, 720):
        for m in (0.0, 0.01):
            out = []
            for sym in syms:
                a = X[sym]
                for d in range(nd):
                    d0 = d * 1440
                    if not a["liq"][d0] or not np.isfinite(a["o"][d0]) or not np.isfinite(a["c"][d0 + T - 1]):
                        continue
                    r = float(a["c"][d0 + T - 1]) / float(a["o"][d0]) - 1
                    if abs(r) >= m and r != 0:
                        out.append(F.rets(a, [d0 + T - 1], np.sign(r), 480))
            yield "H2 FIRST PART PREDICTS THE REST", f"00:00 to {T // 60} h, move >= {m:.0%}, hold 8 h (follow)", np.vstack(out)
    # H3 pairs
    maj = [s for s in ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD") if s in X]
    for zt in (2.0, 3.0):
        for hold in (60, 240, 720):
            out = []
            for i_a in range(len(maj)):
                for i_b in range(i_a + 1, len(maj)):
                    A, B = X[maj[i_a]], X[maj[i_b]]
                    sp = pd.Series(np.log(A["c"].astype(float) / B["c"].astype(float)))
                    z = ((sp - sp.rolling(1440, min_periods=720).mean()) / sp.rolling(1440, min_periods=720).std()).to_numpy()
                    zp = np.r_[np.nan, z[:-1]]
                    with np.errstate(invalid="ignore"):
                        idx = F.thin(np.flatnonzero((np.abs(z) >= zt) & (np.abs(zp) < zt)), hold)
                    idx = idx[idx + hold < NM]
                    k, x = idx + 1, idx + hold
                    ok = A["ok"][k] & B["ok"][k] & np.isfinite(A["c"][x]) & np.isfinite(B["c"][x])
                    k, x, s = k[ok], x[ok], -np.sign(z[idx][ok])       # s = side of leg A: sell the rich one
                    ra = s * (A["c"][x].astype(float) / A["o"][k].astype(float) - 1)
                    rb = -s * (B["c"][x].astype(float) / B["o"][k].astype(float) - 1)
                    out.append(np.c_[k, (ra + rb) / 2])
            yield "H3 PAIRS", f"z {zt:.0f}, hold {hold} (sell rich / buy cheap)", np.vstack(out)
    # H4 breadth
    C = np.vstack([X[s]["c"].astype(float) for s in syms])
    V = np.vstack([np.nan_to_num(X[s]["v"]) > 0 for s in syms])
    grid = np.arange(120, NM - 300, 15)
    r60 = C[:, grid] / C[:, grid - 60] - 1
    okc = np.isfinite(r60) & V[:, grid] & V[:, grid - 60]
    cnt = okc.sum(0)
    up = np.where(cnt >= 10, (np.where(okc, r60 > 0, False).sum(0)) / np.maximum(cnt, 1), np.nan)
    basket = [s for s in ("BTCUSD", "ETHUSD", "SOLUSD") if s in X]
    for thr in (0.8, 0.9):
        for hold in (60, 240):
            with np.errstate(invalid="ignore"):
                sel = np.flatnonzero((up >= thr) | (up <= 1 - thr))
            idx = F.thin(grid[sel], hold)
            sg = np.where(up[np.searchsorted(grid, idx)] >= thr, 1.0, -1.0)
            yield "H4 MARKET BREADTH", f"{thr:.0%} of coins one way, hold {hold} (follow)", np.vstack([F.rets(X[b], idx, sg, hold) for b in basket])
    # H5 slow lead-lag
    btc = X["BTCUSD"]["c"].astype(float)
    rb = np.r_[np.full(60, np.nan), btc[60:] / btc[:-60] - 1]
    for th in (0.01, 0.02):
        ev = np.flatnonzero(np.abs(np.nan_to_num(rb)) >= th)
        ev = ev[ev % 5 == 0]
        for hold in (60, 240):
            for lagged in (False, True):
                out = []
                for sym in syms:
                    if sym == "BTCUSD":
                        continue
                    c = X[sym]["c"].astype(float)
                    ra = np.r_[np.full(60, np.nan), c[60:] / c[:-60] - 1]
                    e = ev
                    if lagged:
                        with np.errstate(invalid="ignore"):
                            e = ev[np.sign(rb[ev]) * ra[ev] < 0.5 * np.abs(rb[ev])]
                    idx = F.thin(e, hold)
                    out.append(F.rets(X[sym], idx, np.sign(rb[idx]), hold))
                yield "H5 SLOW LEAD-LAG", f"BTC {th:.0%} in 1 h, hold {hold}{', coin lagging' if lagged else ''} (follow)", np.vstack(out)


def main():
    X = F.load()
    out = ["FIVE MORE FAMILIES -- docs/intraday_families2_prereg.md. DEV 2026-03..07, TEST 2026-08..11, liquid days, before fees."]
    fam = {}
    for f, name, tr in variants(X):
        fam.setdefault(f, []).append((name, F.summary(tr[tr[:, 0] < F.SPLIT]), tr[tr[:, 0] >= F.SPLIT]))
    passed = []
    for f, vs in fam.items():
        out.append(f"\n===== {f}: {len(vs)} variants (each also mirrored)")
        ok = [(n, s, t) for n, s, t in vs if s and s["n"] >= 200]
        for n, s, t in sorted(vs, key=lambda z: -abs(z[1]["t"]) if z[1] else 0)[:5]:
            ts = F.summary(t)
            out.append(f"   DEV {n:58} " + (f"{s['n']:6d} trades  mean {100 * s['mean']:+.4f}%  t {s['t']:+5.2f}" if s else "under 30 trades") +
                       (f"   | TEST {ts['n']:6d}  mean {100 * ts['mean']:+.4f}%  t {ts['t']:+5.2f}" if ts else ""))
        if not ok:
            out.append("   no variant with 200 DEV trades -> cannot be judged")
            continue
        n, s, t = max(ok, key=lambda z: abs(z[1]["t"]))
        sign = 1.0 if s["mean"] > 0 else -1.0
        t = t.copy()
        t[:, 1] *= sign
        ts = F.summary(t)
        if not ts:
            out.append("   chosen variant has under 30 TEST trades")
            continue
        h1, h2 = t[t[:, 0] < F.HALF, 1].mean(), t[t[:, 0] >= F.HALF, 1].mean()
        good = ts["mean"] > 0 and ts["t"] >= 2.7 and h1 > 0 and h2 > 0
        out.append(f"   CHOSEN on DEV: {n} {'as stated' if sign > 0 else 'MIRRORED'} (DEV mean {100 * sign * s['mean']:+.4f}%, t {sign * s['t']:+.2f})")
        out.append(f"   TEST: {ts['n']} trades  win {100 * ts['win']:.0f}%  avg win {100 * ts['aw']:+.2f}%  avg loss {100 * ts['al']:+.2f}%  mean {100 * ts['mean']:+.4f}%  t {ts['t']:+.2f}  "
                   f"halves {100 * h1:+.4f}% / {100 * h2:+.4f}%  after fees {100 * (ts['mean'] - F.FEES):+.4f}%   -> {'PASS' if good else 'FAIL'}")
        if good:
            passed.append(f)
    out.append(f"\nPASSED before fees: {passed if passed else 'none'}")
    txt = "\n".join(out)
    print(txt)
    Path("out/intraday_families2.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
