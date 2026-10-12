"""Idea 4: does forced selling reverse? (2026-10-12, docs/ideas_1_to_4_prereg.md)

    PYTHONPATH=. .venv/bin/python scripts/forced_selling_lab.py

A big 1-hour move on a coin while its open interest COLLAPSES is taken as
positions being forced shut (liquidations, margin calls); such flow should
overshoot, so the price should partly come back over the next day. The
control is a move of the same size with no OI drop ("ordinary").

Data: 1h bars built from the 1m LTP caches (data_old/ then data/, one series
per coin), Delta's OI:<coin> 1h history (scripts/fetch_oi_1h.py).
Thresholds are each coin's own TRAILING 90 days (at least 30 days of history),
read before the hour: |1h return| >= its 99.5th percentile; forced = OI change
<= its 1st percentile; ordinary = OI change >= its median. Liquidity: trailing
30-day median daily dollar volume >= $1M. One event per coin per 24h (first).

Entry at the open of the next hour, exit 24h later (4h reported), against the
move, taker both ways: 0.158% round trip. Returns in units of the coin's
trailing daily volatility (sd of the previous 24 hourly returns x sqrt(24)).
Day-clustered statistics. STEP 0 runs first and stops the script if either
window has fewer than 60 distinct days with forced events.
"""
import glob, pathlib, sys
import numpy as np, pandas as pd
sys.path.insert(0, ".")
from deltabt.data.store import ProductCatalog

SYMS = sorted(pathlib.Path(f).stem for f in glob.glob("out/sweep/five_min_arm_lab/symbol_screen/trades/*.csv"))
DEV0 = int(pd.Timestamp("2025-12-20", tz="UTC").timestamp()); OLD0 = int(pd.Timestamp("2024-06-01", tz="UTC").timestamp())
H, DAY = 3600, 86400; COST = 0.00158


def minutes(sym):
    parts = [pd.read_parquet(p) for p in (f"data_old/candles/{sym}/ltp_1m.parquet", f"data/candles/{sym}/ltp_1m.parquet")
             if pathlib.Path(p).exists()]
    d = pd.concat(parts).drop_duplicates("time").sort_values("time")
    return d[~((d.volume == 0) & (d.open == d.close) & (d.high == d.low))]


def events_for(sym, cv):
    m = minutes(sym)
    m["hour"] = m.time // H * H; m["usd"] = m.close * m.volume * cv
    h = m.groupby("hour").agg(open=("open", "first"), close=("close", "last"), usd=("usd", "sum"))
    h = h.reindex(np.arange(h.index.min(), h.index.max() + H, H))
    r = np.log(h.close / h.close.shift(1))
    oi = pd.read_parquet(f"data/candles/{sym}/oi_1h.parquet").set_index("time").close
    oi = oi[oi > 0].reindex(h.index)
    o = np.log(oi / oi.shift(1))
    W, MINP = 90 * 24, 30 * 24
    q_r = r.abs().rolling(W, min_periods=MINP).quantile(0.995).shift(1)
    q_lo = o.rolling(W, min_periods=MINP).quantile(0.01).shift(1)
    q_md = o.rolling(W, min_periods=MINP).median().shift(1)
    daily_usd = h.usd.groupby(h.index // DAY * DAY).sum()
    liq = daily_usd.rolling(30, min_periods=10).median().shift(1)
    liq_h = pd.Series(h.index // DAY * DAY, index=h.index).map(liq)
    vol = r.rolling(24, min_periods=20).std().shift(1) * np.sqrt(24)
    big = (r.abs() >= q_r) & (liq_h >= 1e6) & vol.gt(0)
    kind = pd.Series("", index=h.index)
    kind[big & (o <= q_lo)] = "forced"; kind[big & (o >= q_md)] = "ordinary"
    ev = h.index[kind != ""]
    keep, last = [], -10 ** 12
    for t in ev:                                   # one event per coin per 24h
        if t - last >= DAY:
            keep.append(t); last = t
    rows = []
    close = h.close; opn = h.open
    for t in keep:
        e_t = t + H                                # the next hour
        if e_t + DAY not in close.index or not np.isfinite(opn.get(e_t, np.nan)):
            continue
        entry = opn[e_t]; fade = -np.sign(r[t])
        x4, x24 = close.get(e_t + 4 * H - H, np.nan), close.get(e_t + DAY - H, np.nan)
        rows.append(dict(symbol=sym, t=int(t), day=int(t // DAY), kind=kind[t], move=float(r[t]), oi_chg=float(o[t]),
                         vol=float(vol[t]), ret4=float(fade * np.log(x4 / entry)), ret24=float(fade * np.log(x24 / entry))))
    return pd.DataFrame(rows)


def day_t(x, day):
    g = pd.DataFrame(dict(x=x, d=day)).groupby("d").x.agg(["sum", "count"]); n = g["count"].sum(); mu = g["sum"].sum() / n
    se = np.sqrt(((g["sum"] - mu * g["count"]) ** 2).sum()) / n
    return mu, (mu / se if se > 0 else np.nan)


def diff_t(a, b, draws=2000, seed=3):
    days = np.array(sorted(set(a.day) | set(b.day))); rng = np.random.default_rng(seed)
    ga = a.groupby("day").z24.agg(["sum", "count"]).reindex(days).fillna(0); gb = b.groupby("day").z24.agg(["sum", "count"]).reindex(days).fillna(0)
    d = []
    for _ in range(draws):
        w = np.bincount(rng.integers(0, len(days), len(days)), minlength=len(days))
        d.append((w * ga["sum"]).sum() / max((w * ga["count"]).sum(), 1) - (w * gb["sum"]).sum() / max((w * gb["count"]).sum(), 1))
    d = np.array(d); return a.z24.mean() - b.z24.mean(), (a.z24.mean() - b.z24.mean()) / d.std(ddof=1)


if __name__ == "__main__":
    cat = ProductCatalog()
    E = pd.concat([events_for(s, float(cat.get(s)["contract_value"])) for s in SYMS], ignore_index=True)
    E["window"] = np.where(E.t >= DEV0, "DEV", "OLD")
    E = E[E.t >= OLD0]
    print("STEP 0 -- event counts before any outcome is read")
    for w in ("DEV", "OLD"):
        x = E[E.window == w]
        print(f"  {w}: forced {int((x.kind == 'forced').sum())} events on {x[x.kind == 'forced'].day.nunique()} days; "
              f"ordinary {int((x.kind == 'ordinary').sum())} on {x[x.kind == 'ordinary'].day.nunique()} days; "
              f"coins with forced events {x[x.kind == 'forced'].symbol.nunique()}")
    if min(E[(E.window == w) & (E.kind == "forced")].day.nunique() for w in ("DEV", "OLD")) < 60:
        print("STOP: fewer than 60 forced-event days in a window; the test cannot answer."); sys.exit(0)
    E["z24"] = E.ret24 / E.vol; E["z4"] = E.ret4 / E.vol; E["net24"] = E.ret24 - COST
    print("\nOUTCOMES (fade the move; z = return / trailing daily vol; net = raw return - 0.158%)")
    for w in ("DEV", "OLD"):
        for k in ("forced", "ordinary"):
            x = E[(E.window == w) & (E.kind == k)]
            mz, tz = day_t(x.z24, x.day); mn, tn = day_t(x.net24, x.day); m4, t4 = day_t(x.z4, x.day)
            print(f"  {w} {k:8s} n {len(x):4d}  24h z {mz:+.3f} (t {tz:+.2f})  24h net {100 * mn:+.3f}% (t {tn:+.2f})  "
                  f"4h z {m4:+.3f} (t {t4:+.2f})  win {100 * (x.net24 > 0).mean():.0f}%")
    d = E[E.window == "DEV"]; f, o = d[d.kind == "forced"], d[d.kind == "ordinary"]
    mz, tz = day_t(f.z24, f.day); dd, td = diff_t(f, o); mn, _ = day_t(f.net24, f.day)
    split = np.median(f.t); h1, h2 = f[f.t < split].z24.mean(), f[f.t >= split].z24.mean()
    old = E[(E.window == "OLD") & (E.kind == "forced")]; on, ot = day_t(old.net24, old.day)
    F = {"F1 forced fade > 0, t >= 2.5 (DEV)": mz > 0 and tz >= 2.5, "F2 forced beats ordinary, t >= 2 (DEV)": dd > 0 and td >= 2,
         "F3 positive after costs (DEV)": mn > 0, f"F4 same sign both halves (DEV: {h1:+.3f} / {h2:+.3f})": np.sign(h1) == np.sign(h2) == np.sign(mz),
         "F5 OLD after costs > 0, t >= 2": on > 0 and ot >= 2}
    print(f"\n  forced minus ordinary (DEV): {dd:+.3f} z, t {td:+.2f}")
    for k, v in F.items():
        print(f"  {k}: {'PASS' if v else 'fail'}")
    print(f"\nIDEA 4: {'PASS' if all(F.values()) else 'FAIL'}")
    E.to_csv("out/sweep/five_min_arm_lab/ideas_2026-10-12/forced_selling_events.csv", index=False)
