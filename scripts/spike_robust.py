"""Tests meant to BREAK the recommended trade (owner 2026-11-16: "Run more tests"): the spike fade -- rest a sell 5%
above the 00:02 close of the day's picks, stop 10%, out 8 h after the fill. Careful dataset, both picks, 2025-01 ..
2026-11. Before fees, STRICT / LENIENT fills as scripts/spike_flip_back.py (the base case is asserted equal to it).

  1  CONTROLS on the same picks: does the result come from waiting for the spike, or would any short have done it?
       random minute  short at a random minute of the pick's day (20 draws per pick)
       at the open    short at the 00:02 close, every pick
       no spike       the picks whose price never reached +5%: shorted at the 00:02 close
  2  MARKET: what a short of BTC over exactly the same windows made (Delta BTCUSD 5m), and the result after taking
     the market's share out
  3  WHERE the result sits: by year, half-year, pick rank, source of the pick; months up / down; without the 5 best
     days; resampling days with replacement (2,000 times)
  4  NEIGHBOURS: entry 3 / 5 / 7 / 10% above, stop 8 / 10 / 12 / 15%, clock 6 / 8 / 10 / 12 h -- a real effect
     changes smoothly, a lucky cell stands alone

    PYTHONPATH=. python scripts/spike_robust.py        # -> out/careful/spike_robust.txt
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
from spike_flip_back import trade as base_trade  # noqa: E402

OUT = Path("out/careful")


def sim(H, L, Cl, k, e, stop, hours, fill):
    """Short from minute k at price e. -> (return, how, minutes held)"""
    last = min(2880, k + hours * 60)
    h, c = H[k + 1:last + 1], Cl[k + 1:last + 1]
    sp = e * (1 + stop)
    hit = np.flatnonzero(h >= sp)
    if hit.size:
        j = int(hit[0])
        px = (max(sp, c[j]) if fill == "STRICT" else sp) * 1.001
        return -(px / e - 1), "STOP", j + 1
    return -(Cl[last] * 1.001 / e - 1), "CLOCK", last - k


def spike_entry(H, Cl, off):
    e = Cl[2] * (1 + off)
    f = np.flatnonzero(H[3:1440] >= e * 1.0005)
    return (3 + int(f[0]), e) if f.size else None


def stat(x, col="ret"):
    if len(x) < 20:
        return f"{len(x):5d} trades  -"
    r = x[col]
    day = r.groupby(x.day).sum()
    t = day.mean() / (day.std(ddof=1) / np.sqrt(len(day)))
    return f"{len(x):5d} trades  win {100 * (r > 0).mean():3.0f}%  avg win {100 * r[r > 0].mean():+6.2f}%  avg loss {100 * r[r <= 0].mean():+6.2f}%  mean {100 * r.mean():+.3f}%  t {t:+5.2f}"


def btc_5m():
    p = OUT / "btc_5m.parquet"
    if p.exists():
        return pd.read_parquet(p)
    rows, x, end = [], int(pd.Timestamp("2025-01-10", tz="UTC").timestamp()), int(pd.Timestamp("2026-11-17", tz="UTC").timestamp())
    while x < end:
        e = min(end, x + 1999 * 300)
        req = urllib.request.Request(f"https://api.india.delta.exchange/v2/history/candles?resolution=5m&symbol=BTCUSD&start={x}&end={e}",
                                     headers={"User-Agent": "deltabt-research"})
        with urllib.request.urlopen(req, timeout=30) as r:
            rows += json.load(r).get("result") or []
        x = e
        time.sleep(0.3)
    d = pd.DataFrame(rows).drop_duplicates("time").astype({"time": "int64"}).sort_values("time")[["time", "close"]]
    d.to_parquet(p, index=False)
    return d


def main():
    rng = np.random.default_rng(20261116)
    P = pd.read_csv(OUT / "picks.csv", parse_dates=["day"]).set_index(["day", "symbol"])
    picks = [(d, p) for d, ps in D.load() for p in ps]
    out = ["TESTS MEANT TO BREAK THE SPIKE FADE (sell 5% above the 00:02 close, stop 10%, out after 8 h). Before fees, % of the position."]
    w = out.append
    rows = {f: [] for f in ("STRICT", "LENIENT")}
    ctrl = {k: [] for k in ("random minute", "at the open", "no spike")}
    for d, p in picks:
        rank, sym, H, L, Cl = p
        en = spike_entry(H, Cl, 0.05)
        for fill in ("STRICT", "LENIENT"):
            a = base_trade(H, L, Cl, None, 8, fill)
            if en:
                r = sim(H, L, Cl, en[0], en[1], 0.10, 8, fill)
                assert a is not None and abs(a[0] - r[0]) < 1e-12, (d, sym)
                rows[fill].append((d, sym, rank, P.loc[(d, sym), "source"], en[0], r[0], r[1], r[2]))
            else:
                assert a is None
        slip = 0.999                                                  # a market sell, 0.1% worse (STRICT convention)
        ok = np.flatnonzero(np.isfinite(Cl[3:1440])) + 3
        for k in rng.choice(ok, size=20):
            ctrl["random minute"].append((d, sim(H, L, Cl, int(k), Cl[int(k)] * slip, 0.10, 8, "STRICT")[0]))
        if np.isfinite(Cl[2]):
            ctrl["at the open"].append((d, sim(H, L, Cl, 2, Cl[2] * slip, 0.10, 8, "STRICT")[0]))
            if not en:
                ctrl["no spike"].append((d, sim(H, L, Cl, 2, Cl[2] * slip, 0.10, 8, "STRICT")[0]))
    S = pd.DataFrame(rows["STRICT"], columns=["day", "sym", "rank", "source", "k", "ret", "how", "hold"])
    Ln = pd.DataFrame(rows["LENIENT"], columns=S.columns)
    w(f"\nBASE   STRICT  {stat(S)}\n       LENIENT {stat(Ln)}      ({len(picks)} picks, {len(S)} filled; equal to spike_flip_back.trade on every trade)")

    w("\n1  CONTROLS on the same picks (STRICT, market sell 0.1% worse, same 10% stop and 8 h clock)")
    for k, v in ctrl.items():
        w(f"   {k:14} {stat(pd.DataFrame(v, columns=['day', 'ret']))}")
    w(f"   {'the rule':14} {stat(S)}")

    b = btc_5m()
    bt, bc = b.time.to_numpy(), b.close.to_numpy()
    at = lambda ts: bc[np.clip(np.searchsorted(bt, ts // 300 * 300), 0, len(bc) - 1)]  # noqa: E731
    t_in = (S.day.astype("int64") // 10 ** 9 if str(S.day.dtype).endswith("ns]") else S.day.map(lambda x: int(x.timestamp()))).to_numpy() + S.k.to_numpy() * 60
    t_in = np.array([int(pd.Timestamp(d).timestamp()) for d in S.day]) + S.k.to_numpy() * 60
    S["btc_short"] = -(at(t_in + S.hold.to_numpy() * 60) / at(t_in) - 1)
    beta = np.cov(S.ret, S.btc_short)[0, 1] / S.btc_short.var()
    S["alpha"] = S.ret - beta * S.btc_short
    w("\n2  MARKET: a BTC short over exactly the same windows")
    w(f"   BTC short     {stat(S, 'btc_short')}")
    w(f"   the trade moves {beta:.2f}x with BTC (correlation {np.corrcoef(S.ret, S.btc_short)[0, 1]:+.2f}); with the market's share taken out:")
    w(f"   what is left  {stat(S, 'alpha')}")

    w("\n3  WHERE the result sits (STRICT)")
    S["ym"] = S.day.dt.strftime("%Y-%m")
    for nm, m in (("2025", S.day.dt.year == 2025), ("2026", S.day.dt.year >= 2026), ("2025 H1", (S.day < "2025-07-01")), ("2025 H2", (S.day >= "2025-07-01") & (S.day < "2026-01-01")),
                  ("2026 H1", (S.day >= "2026-01-01") & (S.day < "2026-07-01")), ("2026 H2", S.day >= "2026-07-01"), ("pick 1", S["rank"] == 1), ("pick 2", S["rank"] == 2),
                  ("history picks", S.source == "history"), ("paper runner's picks", S.source == "real"), ("filled 00:03-06:00", S.k < 360), ("filled later", S.k >= 360)):
        w(f"   {nm:22} {stat(S[m])}")
    mo = S.groupby("ym").ret.agg(["sum", "size"])
    w(f"   months: {int((mo['sum'] > 0).sum())} up, {int((mo['sum'] <= 0).sum())} down of {len(mo)}; best {100 * mo['sum'].max():+.0f}%, worst {100 * mo['sum'].min():+.0f}% (sum of trade returns); "
      f"last 6 months: " + " ".join(f"{k[2:]} {100 * v:+.0f}%" for k, v in mo["sum"].tail(6).items()))
    day = S.groupby("day").ret.sum()
    top5 = day.nlargest(5).index
    w(f"   without the 5 best days:   {stat(S[~S.day.isin(top5)])}")
    w(f"   without the 5 worst days:  {stat(S[~S.day.isin(day.nsmallest(5).index)])}")
    dv, counts = day.to_numpy(), S.groupby("day").size().to_numpy()
    idx = rng.integers(0, len(dv), size=(2000, len(dv)))
    bs = dv[idx].sum(1) / counts[idx].sum(1)
    w(f"   resampling days 2,000 times: mean per trade 5%..95% = {100 * np.quantile(bs, .05):+.3f}% .. {100 * np.quantile(bs, .95):+.3f}%; zero or worse in {100 * (bs <= 0).mean():.1f}% of resamples")

    w("\n4  NEIGHBOURS (STRICT mean % per trade; trades in brackets for the 8 h row)")
    grid = {}
    for hours in (6, 8, 10, 12):
        w(f"   clock {hours} h      " + " ".join(f"{'stop ' + format(s, '.0%'):>18}" for s in (0.08, 0.10, 0.12, 0.15)))
        for off in (0.03, 0.05, 0.07, 0.10):
            cells = []
            for s in (0.08, 0.10, 0.12, 0.15):
                r = []
                for d, p in picks:
                    en = spike_entry(p[2], p[4], off)
                    if en:
                        r.append(sim(p[2], p[3], p[4], en[0], en[1], s, hours, "STRICT")[0])
                grid[(hours, off, s)] = np.mean(r)
                cells.append(f"{100 * np.mean(r):+8.3f}% ({len(r):3d})")
            w(f"   entry +{off:.0%}".ljust(17) + " ".join(f"{c:>18}" for c in cells))
    g = np.array(list(grid.values()))
    w(f"   {int((g > 0).sum())} of {len(g)} neighbours positive; range {100 * g.min():+.3f}% .. {100 * g.max():+.3f}%; the chosen cell (+5%, 10%, 8 h) {100 * grid[(8, 0.05, 0.10)]:+.3f}%")
    txt = "\n".join(out)
    print(txt)
    (OUT / "spike_robust.txt").write_text(txt + "\n")
    S.to_pickle(OUT / "spike_robust.pkl")


if __name__ == "__main__":
    main()
