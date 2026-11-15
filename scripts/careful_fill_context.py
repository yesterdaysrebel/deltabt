"""Owner 2026-11-15: "can you not find a solution to the SL problem with the spike sell setup?" -- the last untested
information: the MARKET CONTEXT just before the resting sell fills (the bot could cancel the order when it is bad).
Spike Sell exactly as built (20x, +$500 target, 2.25% stop, lock, trail), per pick at a fixed $3,184.
Readings use only minutes BEFORE the fill minute k (Delta 1m: trade candles with volume, MARK candles, BTCUSD):
  vol5      volume of the last 5 min / average 5-min volume of the hour before        (climax?)
  prem      last close / MARK close - 1                                               (perp running above its index?)
  rv60      std of 1m returns over the last hour                                      (noise vs the 2.25% stop)
  range60   (high - low) of the last hour / limit
  run_low   limit / the day's low so far - 1                                          (how far it has run today)
  gap_high  limit / the day's high so far - 1                                         (fresh jump vs retest)
  press30   share of the last 30 min closing within 1.5% under the limit              (grinding under the order?)
  last1     last close / limit - 1                                                    (jump fill vs walk-up)
  btc60 / btc_day   BTC's return over the last hour / since 00:00
Rules 'skip if reading above/below a 2025 quantile' are chosen on 2025 by the average $ per KEPT trade and judged on
2026 + real; a rule passes only if its kept trades average above $0 on 2026 (STRICT) -- not merely 'lose less'.

    PYTHONPATH=. python scripts/careful_fill_context.py
"""
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_sim_b as B  # noqa: E402

FEATS = ["vol5", "prem", "rv60", "range60", "run_low", "gap_high", "press30", "last1", "btc60", "btc_day"]


def table():
    P = pd.read_csv("out/careful/picks.csv", parse_dates=["day"])
    z, mk, bz = np.load("out/careful/dataset.npz"), np.load("out/careful/mark.npz"), np.load("out/careful/btc.npz")
    btc = {pd.Timestamp(int(d)): bz["close"][i] for i, d in enumerate(bz["days"])}
    rows = []
    for i, x in enumerate(P.itertuples()):
        H, L, Cl = z["hi"][i], z["lo"][i], z["cl"][i]
        s = B.first_trade(x.symbol, H, L, Cl, D.START, False)
        if s is None:
            continue
        le = B.first_trade(x.symbol, H, L, Cl, D.START, True)
        k, e = s["k"], Cl[2] * 1.05
        t0 = int(x.day.timestamp())
        vol = np.zeros(1441)
        for c in json.loads(Path(f"out/careful/candles/{x.symbol}_{t0}.json").read_text()):
            m = (int(c["time"]) - t0) // 60
            if 0 <= m < 1441 and c.get("volume") is not None:
                vol[m] = float(c["volume"])
        a = max(0, k - 60)
        r = dict(day=x.day, sym=x.symbol, k=k, how=s["how"], pnl_s=s["pnl"], pnl_l=le["pnl"])
        base = vol[max(0, k - 65):max(0, k - 5)]
        r["vol5"] = vol[max(0, k - 5):k].mean() / base.mean() if len(base) >= 10 and base.mean() > 0 else np.nan
        mc = pd.Series(mk["cl"][i]).ffill().to_numpy()
        r["prem"] = Cl[k - 1] / mc[k - 1] - 1 if np.isfinite(mc[k - 1]) else np.nan
        rets = np.diff(np.log(Cl[a:k]))
        r["rv60"] = rets.std() if len(rets) >= 10 else np.nan
        hh, ll = H[a:k], L[a:k]
        r["range60"] = (hh[np.isfinite(hh)].max() - ll[np.isfinite(ll)].min()) / e if np.isfinite(hh).any() else np.nan
        lo_all, hi_all = L[:k], H[:k]
        r["run_low"] = e / lo_all[np.isfinite(lo_all)].min() - 1 if np.isfinite(lo_all).any() else np.nan
        r["gap_high"] = e / hi_all[np.isfinite(hi_all)].max() - 1 if np.isfinite(hi_all).any() else np.nan
        r["press30"] = float(np.mean(Cl[max(0, k - 30):k] >= 0.985 * e))
        r["last1"] = Cl[k - 1] / e - 1
        b = btc[x.day]
        r["btc60"] = b[k - 1] / b[a] - 1
        r["btc_day"] = b[k - 1] / b[0] - 1
        rows.append(r)
    return pd.DataFrame(rows)


def main():
    d = table()
    d.to_csv("out/careful/fill_context_table.csv", index=False)
    tr, te = d[d.day < "2026"], d[d.day >= "2026"]
    stp = lambda g: g.how.isin(["STOP", "LIQ"])  # noqa: E731
    print(f"Spike Sell fills: 2025 {len(tr)} (stops {stp(tr).sum()}), 2026 + real {len(te)} (stops {stp(te).sum()}). "
          f"No filter: 2025 ${tr.pnl_s.mean():+.0f} .. ${tr.pnl_l.mean():+.0f}; 2026 ${te.pnl_s.mean():+.0f} .. ${te.pnl_l.mean():+.0f} per trade\n")
    print("Median reading before the fill -- trades that STOPPED vs the rest:")
    for f in FEATS:
        print(f"   {f:9s} 2025: stops {tr[stp(tr)][f].median():+9.4f}  rest {tr[~stp(tr)][f].median():+9.4f} | "
              f"2026: stops {te[stp(te)][f].median():+9.4f}  rest {te[~stp(te)][f].median():+9.4f}   (missing {d[f].isna().sum()})")
    rng = np.random.default_rng(0)
    rules = []
    for f in FEATS:
        for q in (0.2, 0.33, 0.5, 0.67, 0.8):
            cut = tr[f].quantile(q)
            for side in ("above", "below"):
                keep = lambda g, f=f, cut=cut, side=side: ~(g[f] > cut) if side == "above" else ~(g[f] < cut)  # noqa: E731
                k = keep(tr)
                if k.sum() >= 60:
                    rules.append((tr[k].pnl_s.mean(), f, side, cut, keep))
    rules.sort(key=lambda r: -r[0])
    print(f"\n{len(rules)} skip rules. Top 12 by the 2025 average per KEPT trade (strict), then the same rule on 2026 + real:")
    passed = 0
    for avg, f, side, cut, keep in rules[:12]:
        k = keep(te)
        n = int(k.sum())
        rnd = np.array([te.pnl_s.to_numpy()[rng.choice(len(te), n, replace=False)].mean() for _ in range(5000)])
        ok = te[k].pnl_s.mean() > 0
        passed += ok
        print(f"   skip if {f:9s} {side} {cut:+.4f}: 2025 keeps {int(keep(tr).sum()):3d} avg {avg:+5.0f} | 2026 keeps {n:3d} avg {te[k].pnl_s.mean():+5.0f} .. {te[k].pnl_l.mean():+5.0f}, "
              f"stops {stp(te[k]).mean():3.0%}; random as good {(rnd >= te[k].pnl_s.mean()).mean():3.0%}{'   <-- PASS' if ok else ''}")
    all_te = np.array([te[r[4](te)].pnl_s.mean() for r in rules])
    all_tr = np.array([r[0] for r in rules])
    print(f"\nrules positive on 2025 (strict): {(all_tr > 0).sum()} of {len(rules)}; of those positive on 2026 (strict): {((all_tr > 0) & (all_te > 0)).sum()}")
    print(f"all rules on 2026 (strict): positive {(all_te > 0).sum()} of {len(rules)}; best ${all_te.max():+.0f}, median ${np.median(all_te):+.0f} per kept trade")
    # a combined model: logistic regression on 2025 (stop vs not), keep the 2026 trades it calls safest
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        med = tr[FEATS].median()
        Xtr, Xte = tr[FEATS].fillna(med), te[FEATS].fillna(med)
        m = make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=1000)).fit(Xtr, stp(tr))
        ptr, pte = m.predict_proba(Xtr)[:, 1], m.predict_proba(Xte)[:, 1]
        print("\nCOMBINED MODEL (all readings, fitted on 2025 to predict a stop), keeping the trades it calls safest:")
        for q in (0.25, 0.5, 0.75):
            cut = np.quantile(ptr, q)
            ktr, kte = ptr <= cut, pte <= cut
            print(f"   safest {q:.0%}: 2025 keeps {ktr.sum():3d} avg ${tr[ktr].pnl_s.mean():+5.0f} (stops {stp(tr[ktr]).mean():.0%}) | "
                  f"2026 keeps {kte.sum():3d} avg ${te[kte].pnl_s.mean():+5.0f} .. ${te[kte].pnl_l.mean():+5.0f} (stops {stp(te[kte]).mean():.0%})")
    except ImportError:
        print("\n(sklearn not available: combined model skipped)")


if __name__ == "__main__":
    main()
