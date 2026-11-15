"""The wide-stop version of Spike Sell as an ACCOUNT, with Delta's funding (owner 2026-11-15: "find a way to solve
the stop-loss problem"). Design chosen on 2025 only (scripts/careful_wide_stop.py, best on TRAIN): resting limit sell
5% above the 00:02 price; stop +10% (touch); no target; out 24 h after the fill. Neighbours shown for robustness.
  funding   Delta's daily funding sum per coin (out/binance_paths/delta_funding_daily.parquet; a short RECEIVES a
            positive rate), accrued evenly over the minutes held on each UTC day; the 20 real picks (after
            2026-10-27) have no funding data -> 0 (noted).
  size      risk f x balance at the fill: notional = risk / (X + 0.1% + 0.12% + X x 0.05%); isolated margin =
            notional x (X + 2.5% + 1%), so liquidation is beyond the stop; a fill is skipped if the open margins
            plus its own exceed the balance.
  account   both picks may fill and be open together (24 h holds overlap); P&L booked at the exit; floor $1,000
            (no new trades under it); optional month-off after 2 stops.
Two account implementations (sorted sweep / heap) must agree.

    PYTHONPATH=. python scripts/careful_wide_account.py
"""
import heapq
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_wide_stop as W  # noqa: E402

F = pd.read_parquet("out/binance_paths/delta_funding_daily.parquet")
MAIN = (0.10, None, 1440)
NEIGH = [(0.075, None, 720), (0.10, None, 720), (0.10, 0.10, 1440), (0.15, None, 1440), (0.20, 0.10, 720)]


def unit(X):
    return X + 0.001 + 0.0012 + X * 0.0005


def funding(day, sym, k, j):
    """Short's funding return over minutes k..j (minute 0 = day 00:00): + received, - paid."""
    tot = 0.0
    for i in range(0, 3):
        a, b = max(k, i * 1440), min(j, (i + 1) * 1440)
        if b > a:
            d = day + pd.Timedelta(days=i)
            f = F.at[d, sym] if d in F.index and sym in F.columns else np.nan
            if np.isfinite(f):
                tot += f * (b - a) / 1440
    return tot


def trades(cfg, le, with_funding=True):
    X = cfg[0]
    out = []
    for day, picks in D.load():
        t0 = int(day.timestamp()) // 60
        for rank, sym, H, L, Cl in picks:
            a = W.w1(H, L, Cl, le, *cfg)
            b = W.w2(H, L, Cl, le, *cfg)
            assert (a is None) == (b is None) and (a is None or abs(a[1] - b[1]) < 1e-9)
            if a is None:
                continue
            fr = funding(day, sym, a[2], a[3]) / unit(X) if with_funding else 0.0
            out.append(dict(day=day, sym=sym, rank=rank, how=a[0], R=a[1] + fr, Rf=fr, t_in=t0 + a[2], t_out=t0 + a[3]))
    return pd.DataFrame(out).sort_values(["t_in", "rank"]).reset_index(drop=True)


def acct1(T, X, f, month_off, start=D.START, floor=1000.0, log=None):
    bal, open_, stops, month = start, [], 0, None
    low, peak, dd = start, start, 0.0
    mfrac = X + 0.035
    for r in T.itertuples():
        open_.sort(key=lambda o: o[0])
        while open_ and open_[0][0] <= r.t_in:
            t, pnl, m_, was_stop, mon = open_.pop(0)
            bal += pnl
            peak = max(peak, bal)
            dd = max(dd, 1 - bal / peak)
            low = min(low, bal)
            if log is not None:
                log.append((t, pnl, bal))
        m = r.day.strftime("%Y-%m")
        if m != month:
            month, stops = m, 0
        if bal < floor or (month_off and stops >= 2):
            continue
        risk = f * bal
        margin = risk / unit(X) * mfrac
        if sum(o[2] for o in open_) + margin > bal:
            continue
        pnl = max(r.R * risk, -margin)
        if r.how == "STOP":
            stops += 1                                    # counted when taken (the bot learns it at the exit; simplification)
        open_.append((r.t_out, pnl, margin, r.how == "STOP", m))
    for t, pnl, m_, s_, mon in sorted(open_):
        bal += pnl
        peak = max(peak, bal)
        dd = max(dd, 1 - bal / peak)
        low = min(low, bal)
        if log is not None:
            log.append((t, pnl, bal))
    return bal, low, dd


def acct2(T, X, f, month_off, start=D.START, floor=1000.0):
    """Independent: a heap of open positions keyed by exit time."""
    cash, heap, seq = start, [], 0
    cur, nstop = "", 0
    worst, top, depth = start, start, 0.0

    def settle(until):
        nonlocal cash, worst, top, depth
        while heap and heap[0][0] <= until:
            _, _, gain, _ = heapq.heappop(heap)
            cash += gain
            top = max(top, cash)
            depth = max(depth, (top - cash) / top)
            worst = min(worst, cash)

    for i in range(len(T)):
        row = T.iloc[i]
        settle(row.t_in)
        ym = f"{row.day.year:04d}-{row.day.month:02d}"
        if ym != cur:
            cur, nstop = ym, 0
        if cash < floor:
            continue
        if month_off and nstop >= 2:
            continue
        at_risk = cash * f
        need = at_risk / unit(X) * (X + 0.035)
        if need + sum(h[3] for h in heap) > cash:
            continue
        if row.how == "STOP":
            nstop += 1
        seq += 1
        heapq.heappush(heap, (row.t_out, seq, max(row.R * at_risk, -need), need))
    settle(10 ** 18)
    return cash, worst, depth


def main():
    print("Per trade, with funding. R = P&L / planned loss at the stop. STRICT .. LENIENT.\n")
    print(f"  {'design':44s} | {'2025':>16s} | {'2026 + real':>16s} | {'all':>16s} | all strict: t, 95% range (bootstrap) | funding R")
    rng = np.random.default_rng(0)
    for cfg in [MAIN] + NEIGH:
        s, l_ = trades(cfg, False), trades(cfg, True)
        cells = []
        for a, b in ((s[s.day < "2026"], l_[l_.day < "2026"]), (s[s.day >= "2026"], l_[l_.day >= "2026"]), (s, l_)):
            cells.append(f"{a.R.mean():+.3f} .. {b.R.mean():+.3f}")
        x = s.R.to_numpy()
        bs = np.array([rng.choice(x, len(x)).mean() for _ in range(5000)])
        print(f"  {W.label(cfg):44s} | {cells[0]} | {cells[1]} | {cells[2]} | t {x.mean() / (x.std() / np.sqrt(len(x))):+.1f}, "
              f"{np.percentile(bs, 2.5):+.3f} .. {np.percentile(bs, 97.5):+.3f}, P(<=0) {(bs <= 0).mean():.0%} | {s.Rf.mean():+.3f}"
              + ("  <- chosen on 2025" if cfg == MAIN else ""))
    X = MAIN[0]
    s, l_ = trades(MAIN, False), trades(MAIN, True)
    print(f"\nCHOSEN DESIGN ({W.label(MAIN)}): {len(s)} trades = {len(s) / 22:.0f} a month; stops {(s.how == 'STOP').mean():.0%}; "
          f"win rate {(s.R > 0).mean():.0%}; avg win {s[s.R > 0].R.mean():+.2f} R, avg loss {s[s.R <= 0].R.mean():+.2f} R; std {s.R.std():.2f} R")
    s["h"] = s.day.dt.year.astype(str) + np.where(s.day.dt.month <= 6, " H1", " H2")
    l_["h"] = s["h"]
    for h, g in s.groupby("h"):
        gl = l_[l_.h == h]
        print(f"   {h}: {len(g):3d} trades, avg {g.R.mean():+.3f} .. {gl.R.mean():+.3f} R, total {g.R.sum():+6.1f} .. {gl.R.sum():+6.1f} R, stops {(g.how == 'STOP').mean():.0%}")
    kelly = s.R.mean() / s.R.var()
    print(f"   Kelly fraction (strict, all): {kelly:.1%} of the balance at risk per trade; half-Kelly {kelly / 2:.1%}")
    for f in (0.05, 0.10, 0.15, 0.235):
        for mo in (False, True):
            for T in (s, l_):
                a, b = acct1(T, X, f, mo), acct2(T, X, f, mo)
                assert abs(a[0] - b[0]) < 0.01 and abs(a[1] - b[1]) < 0.01 and abs(a[2] - b[2]) < 1e-9, (f, mo, a, b)
    print("\nACCOUNT CHECK PASSED: two account implementations agree (all risk levels, with / without month-off, both fills).\n")
    print(f"ACCOUNT from ${D.START:,.0f} on 2025-01-15 to 2026-11-14, compounding, STRICT .. LENIENT:")
    print(f"  {'risk per trade':28s} | {'end balance':>21s} | {'lowest balance':>17s} | {'max drawdown':>12s} | position at start | 22 starts: median end, under $1,000, above start (strict)")
    for f in (0.05, 0.10, 0.15, 0.235):
        for mo in (False, True):
            a, b = acct1(s, X, f, mo), acct1(l_, X, f, mo)
            ends = []
            for m in pd.period_range("2025-01", "2026-10", freq="M"):
                ends.append(acct1(s[s.day >= m.to_timestamp()], X, f, mo)[0])
            ends = np.array(ends)
            print(f"  {f:5.1%} (${f * D.START:,.0f}){', month-off' if mo else '':11s}    | {a[0]:8,.0f} .. {b[0]:8,.0f} | {a[1]:6,.0f} .. {b[1]:6,.0f} | "
                  f"{a[2]:4.0%} .. {b[2]:4.0%} | ${f * D.START / unit(X):7,.0f}          | {np.median(ends):7,.0f}, {(ends < 1000).sum():2d}, {(ends > D.START).sum():2d} of 22")
    for f in (0.10, 0.235):
        print(f"\n  month by month, risk {f:.1%} of the balance, no month-off, STRICT .. LENIENT:")
        logs = {}
        for nm, T in (("s", s), ("l", l_)):
            lg = []
            acct1(T, X, f, False, log=lg)
            d = pd.DataFrame(lg, columns=["t", "pnl", "bal"])
            d["m"] = pd.to_datetime(d.t * 60, unit="s").dt.strftime("%Y-%m")
            logs[nm] = d.groupby("m").agg(n=("pnl", "size"), pnl=("pnl", "sum"), end=("bal", "last"))
        for m in logs["s"].index:
            a, b = logs["s"].loc[m], logs["l"].loc[m] if m in logs["l"].index else logs["s"].loc[m]
            print(f"    {m} trades {int(a.n):2d}  P&L {a.pnl:+8,.0f} .. {b.pnl:+8,.0f}  end {a.end:8,.0f} .. {b.end:8,.0f}")
    s.to_csv("out/careful/wide_trades_strict.csv", index=False)
    l_.to_csv("out/careful/wide_trades_lenient.csv", index=False)


if __name__ == "__main__":
    main()
