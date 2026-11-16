"""Wide-stop Spike Sell (stop +10%, no target, 24 h; chosen on 2025): smaller risk levels, and the project's earlier
funding filter (skip a pick whose Delta funding on D-1 was negative: shorts already paying = a crowded short; known
at 00:00; found earlier on rule A, so here it is a re-check on a different rule, split 2025 / 2026).

    PYTHONPATH=. python scripts/careful_wide_more.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_wide_account as WA  # noqa: E402

X = WA.MAIN[0]
s, l_ = WA.trades(WA.MAIN, False), WA.trades(WA.MAIN, True)
assert (s.sym == l_.sym).all() and (s.t_in == l_.t_in).all()


def prev_funding(day, sym):
    d = day - pd.Timedelta(days=1)
    return WA.F.at[d, sym] if d in WA.F.index and sym in WA.F.columns else np.nan


pf = np.array([prev_funding(d, sy) for d, sy in zip(s.day, s.sym)])
s["pf"], l_["pf"] = pf, pf
print(f"previous-day funding known for {np.isfinite(pf).sum()} of {len(pf)} trades; negative in {(pf < 0).mean():.0%}\n")
rng = np.random.default_rng(1)
print("FUNDING FILTER, R per trade STRICT .. LENIENT (kept trades only):")
for nm, keep in (("no filter", np.ones(len(s), bool)), ("skip if D-1 funding < 0", ~(pf < 0)), ("skip if D-1 funding < -0.1%", ~(pf < -0.001)),
                 ("keep only D-1 funding >= +0.05%", pf >= 0.0005)):
    row = []
    for lo_, hi_ in (("2025", "2026"), ("2026", "2027"), ("2025", "2027")):
        m = keep & (s.day >= lo_).to_numpy() & (s.day < hi_).to_numpy()
        row.append(f"{m.sum():3d} trades {s.R[m].mean():+.3f} .. {l_.R[m].mean():+.3f}")
    m = keep & (s.day >= "2026").to_numpy()
    te = s.R[(s.day >= "2026")].to_numpy()
    rnd = np.array([te[rng.choice(len(te), m.sum(), replace=False)].mean() for _ in range(5000)])
    x = s.R[keep].to_numpy()
    print(f"  {nm:32s} | 2025: {row[0]} | 2026+real: {row[1]} | all: {row[2]} | all t {x.mean() / (x.std() / np.sqrt(len(x))):+.1f}; "
          f"2026 random-skip as good {(rnd >= s.R[m].mean()).mean():.0%}")
keep = ~(pf < 0)
sf, lf = s[keep].reset_index(drop=True), l_[keep].reset_index(drop=True)
print("\nACCOUNT from $3,184 (2025-01-15 .. 2026-11-14), compounding, no month-off, floor $1,000. STRICT .. LENIENT:")
print(f"  {'':34s} | {'end balance':>21s} | {'max drawdown':>12s} | {'lowest':>15s} | 22 starts (strict): median end, worst end, under $1,000, above start")
for nm, (a, b) in (("all trades", (s, l_)), ("skip D-1 funding < 0", (sf, lf))):
    for f in (0.02, 0.03, 0.05, 0.075):
        ra, rb = WA.acct1(a, X, f, False), WA.acct1(b, X, f, False)
        chk = WA.acct2(a, X, f, False)
        assert abs(ra[0] - chk[0]) < 0.01
        ends = np.array([WA.acct1(a[a.day >= m.to_timestamp()], X, f, False)[0] for m in pd.period_range("2025-01", "2026-10", freq="M")])
        print(f"  {nm:22s} risk {f:4.1%} (${f * D.START:3.0f}) | {ra[0]:8,.0f} .. {rb[0]:8,.0f} | {ra[2]:4.0%} .. {rb[2]:4.0%} | {ra[1]:6,.0f} .. {rb[1]:6,.0f} | "
              f"{np.median(ends):7,.0f}, {ends.min():6,.0f}, {(ends < 1000).sum():2d}, {(ends > D.START).sum():2d} of 22")
print("\nPER MONTH at a FIXED risk (no compounding), all trades, STRICT .. LENIENT:")
s["m"], l_["m"] = s.day.dt.strftime("%Y-%m"), l_.day.dt.strftime("%Y-%m")
ms, ml = s.groupby("m").R.sum(), l_.groupby("m").R.sum()
print(f"   R per month: mean {ms.mean():+.2f} .. {ml.mean():+.2f}, median {ms.median():+.2f} .. {ml.median():+.2f}, worst {ms.min():+.1f} .. {ml.min():+.1f}, "
      f"best {ms.max():+.1f} .. {ml.max():+.1f}; winning months {(ms > 0).sum()} .. {(ml > 0).sum()} of {len(ms)}")
for risk in (95, 160, 750):
    print(f"   risking ${risk} a trade (position ${risk / WA.unit(X):,.0f}): average ${ms.mean() * risk:+,.0f} .. ${ml.mean() * risk:+,.0f} a month, "
          f"worst month ${ms.min() * risk:+,.0f} .. ${ml.min() * risk:+,.0f}")
