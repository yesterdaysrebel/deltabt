"""Seven intraday families on Delta's liquid perps -- docs/intraday_families_prereg.md (families, variants, selection
and bar are frozen there).

    PYTHONPATH=. python scripts/intraday_families.py       # -> out/intraday_families.txt   (F2 needs out/marks/)
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

T0 = int(pd.Timestamp("2026-03-01", tz="UTC").timestamp())
T1 = int(pd.Timestamp("2026-11-16", tz="UTC").timestamp())
SPLIT = (int(pd.Timestamp("2026-08-01", tz="UTC").timestamp()) - T0) // 60
HALF = (int(pd.Timestamp("2026-09-23", tz="UTC").timestamp()) - T0) // 60
NMIN = (T1 - T0) // 60
FEES = 0.00118


def load():
    coins = [c["symbol"] for c in json.loads(Path("out/pullback/universe.json").read_text())["coins"]]
    X = {}
    for sym in coins:
        parts = [pd.read_parquet(p) for p in (Path("out/breakout_oos") / f"{sym}.parquet", Path("out/pullback") / f"{sym}.parquet") if p.exists()]
        if not parts:
            continue
        d = pd.concat(parts).drop_duplicates("time").sort_values("time")
        d = d[(d.time >= T0) & (d.time < T1)]
        i = ((d.time.to_numpy() - T0) // 60).astype(int)
        a = {k: np.full(NMIN, np.nan, np.float32) for k in ("o", "h", "l", "c", "v")}
        for k, col in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"), ("v", "volume")):
            a[k][i] = d[col].to_numpy(np.float32)
        flat = pd.Series((d.high == d.low).to_numpy(), index=i // 1440).groupby(level=0).mean()
        cnt = pd.Series(1, index=i // 1440).groupby(level=0).sum()
        liq = np.zeros(NMIN // 1440 + 1, bool)
        good = flat.index[(flat <= 0.20) & (cnt >= 1300)]
        liq[good] = True
        a["liq"] = liq[np.arange(NMIN) // 1440]
        a["ok"] = a["liq"] & (np.nan_to_num(a["v"]) > 0) & np.isfinite(a["o"])
        m = Path("out/marks") / f"{sym}.parquet"
        if m.exists():
            md = pd.read_parquet(m)
            md = md[(md.time >= T0) & (md.time < T1)]
            a["m"] = np.full(NMIN, np.nan, np.float32)
            a["m"][((md.time.to_numpy() - T0) // 60).astype(int)] = md.close.to_numpy(np.float32)
        X[sym] = a
    return X


def thin(idx, gap):
    out, last = [], -10 ** 9
    for i in idx:
        if i >= last + gap:
            out.append(i)
            last = i
    return np.array(out, int)


def rets(a, idx, s, hold):
    """In at the open of idx+1, out at the close `hold` minutes later. -> (entry minute, return)"""
    idx = np.asarray(idx, int)
    s = np.broadcast_to(np.asarray(s, float), idx.shape)
    k = idx + 1
    m = k + hold - 1 < NMIN
    idx, s, k = idx[m], s[m], k[m]
    x = k + hold - 1
    m = a["ok"][k] & np.isfinite(a["c"][x])
    r = s[m] * (a["c"][x][m].astype(float) / a["o"][k][m].astype(float) - 1)
    return np.c_[k[m], r]


def summary(tr):
    if len(tr) < 30:
        return None
    r = tr[:, 1]
    day = pd.Series(r).groupby(tr[:, 0].astype(int) // 1440).sum()
    t = day.mean() / (day.std(ddof=1) / np.sqrt(len(day))) if len(day) > 3 else 0.0
    return dict(n=len(r), mean=r.mean(), t=t, win=(r > 0).mean(), aw=r[r > 0].mean() if (r > 0).any() else 0, al=r[r <= 0].mean() if (r <= 0).any() else 0)


def variants(X):
    """yield (family, variant name, trades array [entry minute, return])"""
    alts = [s for s in X if s != "BTCUSD"]
    btc = X["BTCUSD"]["c"].astype(float)
    for k in (1, 3, 5):
        rb = np.r_[np.full(k, np.nan), btc[k:] / btc[:-k] - 1]
        for th in (0.003, 0.005, 0.01):
            ev = np.flatnonzero(np.abs(np.nan_to_num(rb)) >= th)
            for hold in (3, 10, 30):
                out = []
                for sym in alts:
                    c = X[sym]["c"].astype(float)
                    ra = np.r_[np.full(k, np.nan), c[k:] / c[:-k] - 1]
                    sg = np.sign(rb[ev])
                    with np.errstate(invalid="ignore"):
                        lag = sg * ra[ev] < 0.5 * np.abs(rb[ev])
                    i = thin(ev[lag], hold)
                    out.append(rets(X[sym], i, np.sign(rb[i]), hold))
                yield "F1 LEAD-LAG", f"BTC {th:.1%} in {k} min, hold {hold}", np.vstack(out)
    if all("m" in X[s] for s in X):
        for g in (0.001, 0.002, 0.004):
            for hold in (3, 10, 30):
                out = []
                for sym in X:
                    a = X[sym]
                    gap = a["c"].astype(float) / a["m"].astype(float) - 1
                    i = thin(np.flatnonzero((np.abs(np.nan_to_num(gap)) >= g) & (np.nan_to_num(a["v"]) > 0)), hold)
                    out.append(rets(a, i, -np.sign(gap[i]), hold))
                yield "F2 LAST vs FAIR", f"gap {g:.1%}, hold {hold}", np.vstack(out)
    for r0 in (0.005, 0.01):
        for hold in (5, 15, 60):
            out = []
            for sym in X:
                a = X[sym]
                v = pd.Series(np.nan_to_num(a["v"]).astype(float))
                base = v.shift(1).rolling(60).mean().to_numpy()
                c = a["c"].astype(float)
                r1 = np.r_[np.nan, c[1:] / c[:-1] - 1]
                with np.errstate(invalid="ignore"):
                    i = thin(np.flatnonzero((v.to_numpy() >= 10 * base) & (base > 0) & (np.abs(r1) >= r0)), hold)
                out.append(rets(a, i, -np.sign(r1[i]), hold))
            yield "F3 VOLUME CLIMAX", f"move {r0:.1%}, hold {hold} (fade)", np.vstack(out)
    syms = list(X)
    C = np.vstack([X[s]["c"].astype(float) for s in syms])
    LQ = np.vstack([X[s]["liq"] for s in syms])
    for L in (60, 240):
        out = []
        for h in range(L + 60, NMIN - 61, 60):
            r = C[:, h - 1] / C[:, h - 1 - L] - 1
            ok = np.isfinite(r) & LQ[:, h]
            if ok.sum() < 8:
                continue
            order = np.argsort(np.where(ok, r, np.nan))[: ok.sum()]
            for j in order[:3]:
                out.append(rets(X[syms[j]], [h - 1], 1.0, 60))
            for j in order[-3:]:
                out.append(rets(X[syms[j]], [h - 1], -1.0, 60))
        yield "F4 HOURLY CROSS-SECTION", f"lookback {L // 60} h (buy weak, sell strong)", np.vstack(out)
    for hold in (15, 60, 240):
        out5, out6 = [], []
        for sym in X:
            a = X[sym]
            H, Lo, c = pd.Series(a["h"].astype(float)), pd.Series(a["l"].astype(float)), a["c"].astype(float)
            hh, ll = H.shift(1).rolling(60).max().to_numpy(), Lo.shift(1).rolling(60).min().to_numpy()
            rng = pd.Series(hh / ll - 1)
            q = rng.rolling(1440, min_periods=720).quantile(0.25).to_numpy()
            with np.errstate(invalid="ignore"):
                sq = rng.to_numpy() <= q
                up, dn = sq & (c > hh), sq & (c < ll)
            i = thin(np.flatnonzero(up | dn), max(hold, 60))
            out5.append(rets(a, i, np.where(up[i], 1.0, -1.0), hold))
            nd = NMIN // 1440
            hd = np.nanmax(a["h"][: nd * 1440].reshape(nd, 1440).astype(float), axis=1)
            ld = np.nanmin(a["l"][: nd * 1440].reshape(nd, 1440).astype(float), axis=1)
            for d in range(1, nd):
                if not (a["liq"][d * 1440] and a["liq"][(d - 1) * 1440]) or not np.isfinite(hd[d - 1]):
                    continue
                cd = c[d * 1440:(d + 1) * 1440]
                with np.errstate(invalid="ignore"):
                    u, v_ = np.flatnonzero(cd > hd[d - 1]), np.flatnonzero(cd < ld[d - 1])
                if u.size:
                    out6.append(rets(a, [d * 1440 + int(u[0])], 1.0, hold))
                if v_.size:
                    out6.append(rets(a, [d * 1440 + int(v_[0])], -1.0, hold))
        yield "F5 SQUEEZE BREAKOUT", f"hold {hold} (follow)", np.vstack(out5)
        yield "F6 YESTERDAY'S HIGH / LOW", f"hold {hold} (follow)", np.vstack(out6)
    hours = {}
    for sym in X:
        a = X[sym]
        for h in range(0, NMIN - 60, 60):
            if a["ok"][h] and np.isfinite(a["c"][h + 59]):
                hours.setdefault((h // 60) % 24, []).append((h, float(a["c"][h + 59]) / float(a["o"][h]) - 1))
    for hr in range(24):
        yield "F7 HOUR OF DAY", f"{hr:02d}:00 UTC (long)", np.array(hours.get(hr, [(0, 0.0)]))


def main():
    X = load()
    out = ["SEVEN INTRADAY FAMILIES -- docs/intraday_families_prereg.md. DEV 2026-03..07, TEST 2026-08..11, liquid days, before fees.",
           f"coins loaded: {len(X)}; liquid coin-days: {int(sum(X[s]['liq'][::1440].sum() for s in X))}; mark data: {'yes' if all('m' in X[s] for s in X) else 'NOT YET (F2 skipped)'}"]
    fam = {}
    for f, name, tr in variants(X):
        dev, test = tr[tr[:, 0] < SPLIT], tr[tr[:, 0] >= SPLIT]
        fam.setdefault(f, []).append((name, summary(dev), test))
    passed = []
    for f, vs in fam.items():
        out.append(f"\n===== {f}: {len(vs)} variants (each also mirrored)")
        ok = [(n, s, t) for n, s, t in vs if s and s["n"] >= 200]
        if not ok:
            out.append("   no variant with 200 DEV trades")
            continue
        for n, s, t in sorted(ok, key=lambda z: -abs(z[1]["t"]))[:4]:
            ts = summary(t)
            out.append(f"   DEV {n:42} {s['n']:6d} trades  mean {100 * s['mean']:+.4f}%  t {s['t']:+5.2f}" +
                       (f"   | TEST {ts['n']:6d}  mean {100 * ts['mean']:+.4f}%  t {ts['t']:+5.2f}" if ts else ""))
        n, s, t = max(ok, key=lambda z: abs(z[1]["t"]))
        sign = 1.0 if s["mean"] > 0 else -1.0
        t = t.copy()
        t[:, 1] *= sign
        ts = summary(t)
        if not ts:
            out.append("   chosen variant has under 30 TEST trades")
            continue
        h1, h2 = t[t[:, 0] < HALF, 1].mean(), t[t[:, 0] >= HALF, 1].mean()
        good = ts["mean"] > 0 and ts["t"] >= 2.7 and h1 > 0 and h2 > 0
        out.append(f"   CHOSEN on DEV: {n} {'as stated' if sign > 0 else 'MIRRORED'} (DEV mean {100 * sign * s['mean']:+.4f}%, t {sign * s['t']:+.2f})")
        out.append(f"   TEST: {ts['n']} trades  win {100 * ts['win']:.0f}%  avg win {100 * ts['aw']:+.2f}%  avg loss {100 * ts['al']:+.2f}%  mean {100 * ts['mean']:+.4f}%  t {ts['t']:+.2f}  "
                   f"halves {100 * h1:+.4f}% / {100 * h2:+.4f}%  after fees {100 * (ts['mean'] - FEES):+.4f}%   -> {'PASS' if good else 'FAIL'}")
        if good:
            passed.append(f)
    out.append(f"\nPASSED before fees: {passed if passed else 'none'}")
    txt = "\n".join(out)
    print(txt)
    Path("out/intraday_families.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
