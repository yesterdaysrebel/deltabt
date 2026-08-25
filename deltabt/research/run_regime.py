"""Was the TRAIN window structurally different? A diagnostic, not a strategy.

    PYTHONPATH=. python3 -u -m deltabt.research.run_regime

WHY THIS EXISTS. Two unrelated hypotheses split the same way on 2026-08-23:
H-Scalp-3 gave rho(gross, horizon) = -1.000 on train and +0.400 on validation;
H-XSec-1 gave train net Sharpe negative in 22 of 24 grid cells and validation
positive in 15 of 24. 2025H1 was the TRAIN window in every split this program
used. If that half-year was structurally hostile, a rule requiring "positive in
both windows" would reject real effects, and some of the thirteen recorded
nulls are confounded with the sample period rather than clean.

THE DANGER, NAMED UP FRONT. "It would have worked in a different market" is how
dead strategies get resurrected. Three defences:

  1. Nothing here is a strategy. These are properties of the data -- pairwise
     correlation, cross-sectional dispersion, volatility, autocorrelation --
     plus the raw information coefficient, which is signal-to-outcome rank
     correlation BEFORE any portfolio construction or cost.
  2. 2025H1 is compared against FOUR other windows, not against the one that
     happened to beat it. An outlier among five is a fact; an outlier among two
     is a coin flip.
  3. The criterion is stated before the numbers are read (below).

CRITERION, FIXED BEFORE RUNNING
    2025H1 is called PATHOLOGICAL only if BOTH hold:
      (a) it is the extreme of the five windows on at least TWO of the four
          structural measures (mean pairwise correlation, cross-sectional
          dispersion, annualised vol, lag-1 autocorrelation), AND
      (b) its 14d momentum IC has the opposite sign to the majority of the
          other windows.
    Anything less and the thirteen nulls stand as clean. This is a two-sided
    test: the likely outcome is that 2025H1 is unremarkable, which closes the
    question rather than reopening it.

IC IS THE MEASURE THAT MATTERS. Portfolio Sharpe confounds signal, breadth,
weighting and cost. The daily cross-sectional Spearman correlation between the
feature and the next day's return isolates the signal alone. If IC is
consistently positive and only the Sharpe moved, the strategy was badly built.
If IC itself flips sign by window, the signal is not stable and no
construction fixes it.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from deltabt.config import OUT_DIR
from deltabt.research.run_hxsec1 import eligible, load

OUT = OUT_DIR / "xsec"

WINDOWS = [
    ("2024H2", "2024-07-01", "2025-01-01"),
    ("2025H1", "2025-01-01", "2025-07-01"),      # <- TRAIN in every split
    ("2025H2", "2025-07-01", "2026-01-01"),      # <- VALID in every split
    ("2026H1", "2026-01-01", "2026-07-01"),      # <- TEST (locked for strategy)
    ("2026H2", "2026-07-01", "2027-01-01"),      # partial
]
FLOOR = 250_000


def mask(idx, a, b):
    return (idx >= pd.Timestamp(a, tz="UTC")) & (idx < pd.Timestamp(b, tz="UTC"))


def ic_series(close, el, lookback, skip):
    """Daily cross-sectional Spearman IC between feature and next-day return."""
    logc = np.log(close)
    lag = 2 if skip else 1
    feat = logc.shift(lag) - logc.shift(lag + lookback)
    fwd = logc.diff()
    out = {}
    for d in close.index:
        ok = el.loc[d].fillna(False) & feat.loc[d].notna() & fwd.loc[d].notna()
        f, r = feat.loc[d][ok], fwd.loc[d][ok]
        if len(f) >= 8:
            # Spearman by hand: rank both, then Pearson. scipy is not a
            # dependency here and adding one to pyproject.toml would match
            # deploy.yml's allow-list.
            a = f.rank().to_numpy()
            b = r.rank().to_numpy()
            if a.std() > 0 and b.std() > 0:
                rho = float(np.corrcoef(a, b)[0, 1])
                if np.isfinite(rho):
                    out[d] = rho
    return pd.Series(out).sort_index()


def main() -> int:
    close, usd, zero = load()
    el = eligible(close, usd, zero, FLOOR)
    ret = np.log(close).diff()

    ic14 = ic_series(close, el, 14, True)         # H-XSec-1's primary feature
    ic1 = ic_series(close, el, 1, False)          # 1-day reversal feature

    rows = []
    for name, a, b in WINDOWS:
        m = mask(close.index, a, b)
        if m.sum() < 20:
            continue
        sub_ret = ret[m]
        sub_el = el[m]
        # Structural measures, computed only over symbols eligible that day.
        pair, disp = [], []
        for d in sub_ret.index:
            names = sub_el.loc[d][sub_el.loc[d].fillna(False)].index
            r = sub_ret.loc[d, names].dropna()
            if len(r) >= 8:
                disp.append(float(r.std(ddof=1)))
        # Pairwise correlation over the whole window, on symbols eligible for
        # at least half of it -- a per-day correlation is undefined.
        keep = sub_el.fillna(False).mean() >= 0.5
        cm = sub_ret.loc[:, keep[keep].index].corr()
        iu = np.triu_indices_from(cm.to_numpy(), k=1)
        pair = cm.to_numpy()[iu]
        pair = pair[np.isfinite(pair)]

        mkt = sub_ret.loc[:, keep[keep].index].mean(axis=1).dropna()
        i14 = ic14[mask(ic14.index, a, b)]
        i1 = ic1[mask(ic1.index, a, b)]

        rows.append(dict(
            window=name, days=int(m.sum()),
            n_symbols=float(sub_el.sum(axis=1).median()),
            mean_pair_corr=float(np.mean(pair)) if len(pair) else float("nan"),
            dispersion=float(np.mean(disp)) if disp else float("nan"),
            vol_ann=float(mkt.std(ddof=1) * np.sqrt(365)),
            autocorr1=float(mkt.autocorr(1)) if len(mkt) > 3 else float("nan"),
            mkt_total=float(mkt.sum()),
            ic14=float(i14.mean()) if len(i14) else float("nan"),
            ic14_t=float(i14.mean() / (i14.std(ddof=1) / np.sqrt(len(i14))))
            if len(i14) > 2 else float("nan"),
            ic1=float(i1.mean()) if len(i1) else float("nan"),
        ))

    df = pd.DataFrame(rows).set_index("window")

    print("STRUCTURAL MEASURES  (properties of the data; no strategy involved)")
    print("=" * 104)
    print(f"  {'window':>8} {'days':>5} {'n':>4} {'pair corr':>10} "
          f"{'dispersion':>11} {'vol ann':>9} {'autocorr':>9} {'mkt total':>10}")
    for w, r in df.iterrows():
        tag = "  <- TRAIN" if w == "2025H1" else ("  <- VALID" if w == "2025H2" else "")
        print(f"  {w:>8} {r.days:>5.0f} {r.n_symbols:>4.0f} "
              f"{r.mean_pair_corr:>10.3f} {r.dispersion:>11.4f} "
              f"{r.vol_ann:>9.3f} {r.autocorr1:>+9.3f} {r.mkt_total:>+10.3f}{tag}")

    print("\nINFORMATION COEFFICIENT  (signal alone: rank corr of feature vs next-day return)")
    print("=" * 104)
    print(f"  {'window':>8} {'IC 14d skip1':>14} {'t':>8} {'IC 1d':>10}")
    for w, r in df.iterrows():
        tag = "  <- TRAIN" if w == "2025H1" else ("  <- VALID" if w == "2025H2" else "")
        print(f"  {w:>8} {r.ic14:>+14.4f} {r.ic14_t:>+8.2f} {r.ic1:>+10.4f}{tag}")

    # ---- the pre-stated criterion ------------------------------------
    print("\n" + "=" * 104)
    print("THE CRITERION, AS FIXED BEFORE THE RUN")
    print("=" * 104)
    measures = ["mean_pair_corr", "dispersion", "vol_ann", "autocorr1"]
    extremes = []
    for m in measures:
        v = df[m].dropna()
        is_ext = "2025H1" in (v.idxmax(), v.idxmin())
        where = "highest" if v.idxmax() == "2025H1" else (
            "lowest" if v.idxmin() == "2025H1" else "mid-range")
        extremes.append(is_ext)
        print(f"  {m:>16}: 2025H1 is {where:>10}  "
              f"({v.get('2025H1', float('nan')):+.4f} vs range "
              f"[{v.min():+.4f}, {v.max():+.4f}])")
    n_ext = sum(extremes)

    ics = df.ic14.dropna()
    h1 = ics.get("2025H1", float("nan"))
    others = ics.drop("2025H1", errors="ignore")
    majority = np.sign(others.median()) if len(others) else 0.0
    flips = bool(np.sign(h1) != majority and majority != 0)
    print(f"\n  (a) extreme on {n_ext} of 4 structural measures "
          f"(need >= 2): {'PASS' if n_ext >= 2 else 'FAIL'}")
    print(f"  (b) IC sign {h1:+.4f} vs others' median {others.median():+.4f}: "
          f"{'OPPOSITE - PASS' if flips else 'SAME - FAIL'}")
    verdict = "PATHOLOGICAL" if (n_ext >= 2 and flips) else "UNREMARKABLE"
    print(f"\n  CRITERION SAYS: 2025H1 is {verdict}")
    print("""
  READ THIS BEFORE BELIEVING THE LINE ABOVE. On 2026-08-23 this criterion
  returned PATHOLOGICAL and the criterion was WRONG. Two design faults:

    * It tests RANK, not magnitude. 2025H1 is the "highest" pairwise
      correlation at 0.678 -- against 2025H2's 0.655 -- and the "highest"
      vol at 0.803 against 0.799. Those are ties. The real structure is a
      monotone trend through time (correlation 0.60 -> 0.68 -> 0.66 -> 0.45
      -> 0.34, dispersion rising throughout), so 2025H1 and 2025H2 are the
      SAME regime, split by an arbitrary calendar boundary.
    * Part (b) compares one IC against the MEDIAN of four alternating
      near-zero ICs, which has no meaningful sign.

  The IC table is the measurement that settles it: every window is within
  |t| <= 1.15 of zero. And 2026H1/H2 -- lowest correlation, highest
  dispersion, the most favourable regime a cross-sectional signal could ask
  for -- give IC -0.0041 and +0.0162. If regime were the explanation those
  would be strongly positive. The signal carries no information in ANY
  regime, and the thirteen recorded nulls stand as clean.

  The criterion is left unchanged rather than retuned, because editing it
  after seeing the answer is the failure it was written to prevent. It is
  wrong, it is recorded as wrong, and the IC table is what to read.""")
    if verdict == "UNREMARKABLE":
        print("  -> the thirteen recorded nulls stand as clean.")
    else:
        print("  -> the train window was hostile; splits were confounded and the")
        print("     fix is MORE DATA, not more hypotheses.")

    df.to_csv(OUT / "regime.csv")
    (OUT / "regime.json").write_text(json.dumps(
        dict(table=df.reset_index().to_dict(orient="records"),
             extremes=n_ext, ic_flips=flips, verdict=verdict),
        indent=2, default=str))
    print(f"\nwrote {OUT / 'regime.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
