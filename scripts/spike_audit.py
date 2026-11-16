"""Audit of the recommended trade on its own 617 trades (owner 2026-11-16: "run as many tests as you want, check the
strategy"): the spike fade -- sell 5% above the 00:02 close, stop 10%, out 8 h after the fill.

  1  FUNDING the short actually paid or received while it was open (Delta charges at the settlement instant; rates
     from the stored funding series, the product's own interval) -- never included before
  2  LIQUIDITY: could a $1,000 order have been filled? USD traded in the fill minute and the exit minute
  3  ALL COSTS together: real fees + funding, STRICT and LENIENT
  4  WALK-FORWARD: settings chosen on 2025 alone, judged on 2026 (and the other way round)
  5  THE LOSS TAIL: how bad the stops really were
  6  THE ACCOUNT: $3,184 at 2% and 3% risk a trade with every cost in: end, deepest drop, worst month, longest
     losing run, longest stretch under water

    PYTHONPATH=. python scripts/spike_audit.py         # -> out/careful/spike_audit.txt
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
from deltabt.costs import funding_timestamps  # noqa: E402
from deltabt.data.store import CandleStore, ProductCatalog  # noqa: E402
from deltabt.engine import _funding_lookup  # noqa: E402
from spike_robust import sim, spike_entry  # noqa: E402

OUT = Path("out/careful")
FEE_IN, FEE_OUT = 0.000212, 0.000531
START = 3184.0


def stat(x, col):
    r = x[col].dropna()
    if len(r) < 20:
        return f"{len(r):5d} trades  -"
    day = r.groupby(x.loc[r.index, "day"]).sum()
    t = day.mean() / (day.std(ddof=1) / np.sqrt(len(day)))
    return f"{len(r):5d} trades  win {100 * (r > 0).mean():3.0f}%  avg win {100 * r[r > 0].mean():+6.2f}%  avg loss {100 * r[r <= 0].mean():+6.2f}%  mean {100 * r.mean():+.3f}%  t {t:+5.2f}"


def account(x, col, risk):
    """Chronological, compounding: position = risk x balance / 10% stop. -> end, max drawdown, worst month, longest
    losing run, longest days under water."""
    x = x.sort_values("t_in")
    bal, peak, dd, run, worst_run, eq = START, START, 0.0, 0, 0, []
    for r in x[col]:
        bal += bal * risk / 0.10 * r
        peak = max(peak, bal)
        dd = max(dd, 1 - bal / peak)
        run = run + 1 if r <= 0 else 0
        worst_run = max(worst_run, run)
        eq.append(bal)
    e = pd.Series(eq, index=pd.to_datetime(x.t_in.to_numpy(), unit="s"))
    m = e.resample("ME").last().ffill()
    mret = m.pct_change().fillna(m.iloc[0] / START - 1)
    under = (e < e.cummax()).to_numpy()
    longest, cur0 = 0.0, None
    for ts, u in zip(e.index, under):
        if u and cur0 is None:
            cur0 = ts
        if not u and cur0 is not None:
            longest, cur0 = max(longest, (ts - cur0).days), None
    if cur0 is not None:
        longest = max(longest, (e.index[-1] - cur0).days)
    return bal, dd, mret.min(), mret.max(), int((mret > 0).sum()), len(mret), worst_run, longest


def main():
    out = ["AUDIT OF THE SPIKE FADE (sell +5%, stop 10%, out after 8 h) -- 617 trades, 2025-01 .. 2026-11. % of the position."]
    w = out.append
    st, cat = CandleStore(), ProductCatalog()
    rows = []
    for d, ps in D.load():
        t0 = int(pd.Timestamp(d).timestamp())
        for rank, sym, H, L, Cl in ps:
            en = spike_entry(H, Cl, 0.05)
            if not en:
                continue
            k, e = en
            s, ln = sim(H, L, Cl, k, e, 0.10, 8, "STRICT"), sim(H, L, Cl, k, e, 0.10, 8, "LENIENT")
            rows.append(dict(day=d, sym=sym, rank=rank, k=k, entry=e, strict=s[0], lenient=ln[0], how=s[1], hold=s[2], t0=t0, t_in=t0 + k * 60, t_out=t0 + (k + s[2]) * 60))
    T = pd.DataFrame(rows)

    # 1 funding
    fund_cache, cv = {}, {}
    f_sum, f_n = [], []
    for r in T.itertuples():
        if r.sym not in fund_cache:
            try:
                fund_cache[r.sym] = st.read(r.sym, "funding", "1h")
            except Exception:  # noqa: BLE001
                fund_cache[r.sym] = pd.DataFrame()
            try:
                p = cat.get(r.sym)
                cv[r.sym] = (float(p["contract_value"]), int(p["funding_interval_seconds"]))
            except Exception:  # noqa: BLE001
                cv[r.sym] = (np.nan, 8 * 3600)
        fd = fund_cache[r.sym]
        stamps = funding_timestamps(r.t_in + 1, r.t_out - 1, cv[r.sym][1])
        if fd is None or fd.empty or fd.time.min() > r.t_in or fd.time.max() < r.t_out - 7200:
            f_sum.append(np.nan)
            f_n.append(len(stamps))
            continue
        f_sum.append(sum(_funding_lookup(fd, stamps).values()) / 100)   # + = the short is paid
        f_n.append(len(stamps))
    T["funding"], T["n_settle"] = f_sum, f_n
    known = T.funding.notna()
    w(f"\n1  FUNDING while the short was open (+ = paid to the short). Known for {int(known.sum())} of {len(T)} trades; settlements per trade: median {T.n_settle.median():.0f}, max {T.n_settle.max()}")
    q = T.funding[known]
    w(f"   mean {100 * q.mean():+.4f}% of the position; median {100 * q.median():+.4f}%; worst {100 * q.min():+.2f}%, best {100 * q.max():+.2f}%; "
      f"the short PAID on {100 * (q < 0).mean():.0f}% of trades, was paid on {100 * (q > 0).mean():.0f}%, no settlement on {100 * (q == 0).mean():.0f}%")
    T["funding0"] = T.funding.fillna(q.mean())

    # 2 liquidity
    usd_in, usd_in5, usd_out = [], [], []
    for r in T.itertuples():
        f = OUT / "candles" / f"{r.sym}_{r.t0}.json"
        vol = {}
        if f.exists() and np.isfinite(cv[r.sym][0]):
            for c in json.loads(f.read_text()):
                vol[(int(c["time"]) - r.t0) // 60] = float(c.get("volume") or 0) * cv[r.sym][0] * float(c["close"])
            usd_in.append(vol.get(r.k, 0.0))
            usd_in5.append(sum(vol.get(r.k + j, 0.0) for j in range(5)))
            usd_out.append(vol.get(r.k + r.hold, 0.0))
        else:
            usd_in.append(np.nan); usd_in5.append(np.nan); usd_out.append(np.nan)
    T["usd_in"], T["usd_in5"], T["usd_out"] = usd_in, usd_in5, usd_out
    kn = T.usd_in.notna()
    w(f"\n2  LIQUIDITY (USD traded on Delta; known for {int(kn.sum())} trades). A 3%-risk position on $3,184 is about $950.")
    w("   in the fill minute: " + "  ".join(f"{int(100 * p)}th pct ${T.usd_in[kn].quantile(p):,.0f}" for p in (0.1, 0.25, 0.5, 0.75)) +
      f";  under $1,000 on {100 * (T.usd_in[kn] < 1000).mean():.0f}% of trades, under $5,000 on {100 * (T.usd_in[kn] < 5000).mean():.0f}%")
    w("   in the exit minute: " + "  ".join(f"{int(100 * p)}th pct ${T.usd_out[kn].quantile(p):,.0f}" for p in (0.1, 0.25, 0.5, 0.75)) +
      f";  under $1,000 on {100 * (T.usd_out[kn] < 1000).mean():.0f}% of trades")
    for nm, m in (("fill minute traded $5,000+", T.usd_in >= 5000), ("fill minute traded $1,000-5,000", (T.usd_in >= 1000) & (T.usd_in < 5000)), ("fill minute traded under $1,000", T.usd_in < 1000)):
        w(f"   {nm:32} {stat(T[m], 'strict')}")

    # 3 all costs
    for f in ("strict", "lenient"):
        T[f + "_net"] = T[f] - FEE_IN - FEE_OUT * (1 - T[f]) + T.funding0
    w("\n3  ALL COSTS IN (real fees 0.0212% in / 0.0531% out, funding as measured; unknown funding = the average)")
    w(f"   STRICT  before costs {stat(T, 'strict')}")
    w(f"   STRICT  after costs  {stat(T, 'strict_net')}")
    w(f"   LENIENT after costs  {stat(T, 'lenient_net')}")
    w(f"   STRICT after costs, 2025 {100 * T.strict_net[T.day.dt.year == 2025].mean():+.3f}%, 2026 {100 * T.strict_net[T.day.dt.year >= 2026].mean():+.3f}%; "
      f"only trades the fill minute could carry ($5,000+): {stat(T[T.usd_in >= 5000], 'strict_net')}")

    # 4 walk-forward
    grid = {}
    picks = [(d, p) for d, ps in D.load() for p in ps]
    for off in (0.03, 0.05, 0.07, 0.10):
        for s in (0.08, 0.10, 0.12, 0.15):
            for hours in (6, 8, 10, 12):
                a, b = [], []
                for d, p in picks:
                    en = spike_entry(p[2], p[4], off)
                    if en:
                        (a if d.year == 2025 else b).append(sim(p[2], p[3], p[4], en[0], en[1], s, hours, "STRICT")[0])
                grid[(off, s, hours)] = (np.mean(a), np.mean(b), len(a), len(b))
    G = pd.DataFrame([(k[0], k[1], k[2], *v) for k, v in grid.items()], columns=["off", "stop", "hours", "m25", "m26", "n25", "n26"])
    b25, b26 = G.loc[G.m25.idxmax()], G.loc[G.m26.idxmax()]
    w("\n4  WALK-FORWARD over the 64 settings (STRICT, before costs)")
    w(f"   best on 2025: entry +{b25.off:.0%}, stop {b25['stop']:.0%}, {b25.hours:.0f} h -> 2025 {100 * b25.m25:+.3f}%, then 2026 {100 * b25.m26:+.3f}%")
    w(f"   best on 2026: entry +{b26.off:.0%}, stop {b26['stop']:.0%}, {b26.hours:.0f} h -> 2026 {100 * b26.m26:+.3f}%, then 2025 {100 * b26.m25:+.3f}%")
    w(f"   settings positive in 2025: {int((G.m25 > 0).sum())} of 64; in 2026: {int((G.m26 > 0).sum())}; in both: {int(((G.m25 > 0) & (G.m26 > 0)).sum())}; "
      f"rank correlation between the two years across settings {G.m25.rank().corr(G.m26.rank()):+.2f}")
    w(f"   the chosen setting (+5%, 10%, 8 h): 2025 {100 * grid[(0.05, 0.10, 8)][0]:+.3f}%, 2026 {100 * grid[(0.05, 0.10, 8)][1]:+.3f}%")

    # 5 loss tail
    stp = T[T.how == "STOP"]
    w(f"\n5  THE LOSS TAIL (STRICT): {len(stp)} stops = {100 * len(stp) / len(T):.0f}% of trades; average stop -{-100 * stp.strict.mean():.2f}%; "
      f"worse than -12%: {int((stp.strict < -0.12).sum())}, worse than -15%: {int((stp.strict < -0.15).sum())}, worse than -20%: {int((stp.strict < -0.20).sum())}; "
      f"worst five: " + ", ".join(f"{100 * v:.1f}%" for v in stp.strict.nsmallest(5)))
    w(f"   stopped within 15 min of the fill: {100 * (stp.hold <= 15).mean():.0f}% of stops; within 60 min: {100 * (stp.hold <= 60).mean():.0f}%")

    # 6 account
    w(f"\n6  THE ACCOUNT from ${START:,.0f}, every cost in, compounding, position = risk x balance / 10%")
    w(f"   {'':22} {'end':>9} {'deepest drop':>12} {'worst month':>11} {'best month':>10} {'months up':>9} {'longest losing run':>18} {'longest under water':>19}")
    for risk in (0.02, 0.03):
        for f in ("strict_net", "lenient_net"):
            e, dd, wm, bm, up, nm_, run, uw = account(T, f, risk)
            w(f"   risk {risk:.0%} {f.split('_')[0].upper():8}       ${e:8,.0f} {100 * dd:11.0f}% {100 * wm:+10.0f}% {100 * bm:+9.0f}% {up:5d}/{nm_:<3d} {run:14d} trades {uw:14.0f} days")
    txt = "\n".join(out)
    print(txt)
    (OUT / "spike_audit.txt").write_text(txt + "\n")
    T.to_pickle(OUT / "spike_audit.pkl")


if __name__ == "__main__":
    main()
