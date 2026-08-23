"""H-XSec-1: cross-sectional momentum across the liquid perpetual universe.

    PYTHONPATH=. python3 -u -m deltabt.research.run_hxsec1

PRE-REGISTERED IN docs/hxsec1_prereg.md, frozen BEFORE any bar was pulled, at
SHA-256 128059cf84dbddef0786d54f8146f857e6f05617e2966ac9471e9f217b460226.
Read that first; this file only executes what it specifies.

WHY THIS IS STRUCTURALLY DIFFERENT FROM THE TWELVE NULLS BEFORE IT. Every
prior test predicted DIRECTION on a single symbol over minutes to hours at a
fixed R multiple, and every one landed on a win rate of 1/(1+R). This is
relative rather than absolute -- it can pay while every symbol falls -- and at
a daily holding period a round trip is ~4% of a one-sigma move instead of the
15-136% that killed the rest.

IT IS ALSO UNDERPOWERED, AND THAT WAS RECORDED BEFORE THE RUN. Only ~20 names
clear the liquidity floor and they all follow BTC, so effective breadth is a
handful of independent bets. Roughly an annualised Sharpe above 1.3 is
detectable over the available window; anything smaller returns UNDECIDED and
MUST NOT be reported as a refutation.

THE ELIGIBILITY FILTER IS CAUSAL AND THAT IS THE WHOLE POINT. Selecting on
today's turnover would pick the symbols that survived and are liquid NOW --
survivorship bias inside a 2025 backtest. Liquidity is recomputed at every
rebalance from trailing volume in the bars themselves, so the universe at any
date contains only what was genuinely tradeable then.

TIMING, STATED EXACTLY BECAUSE THE PREREG'S PARENTHETICAL WAS LOOSE. Positions
are formed from data through close_{d-1} and held over day d, so the realised
return is log(close_d / close_{d-1}). "Skipping the most recent day" is
resolved as skipping the most recent COMPLETED return r_{d-1}, giving

    feature(d) = log(close_{d-2} / close_{d-16})       # 14 days, skip-1
    feature(d) = log(close_{d-1} / close_{d-15})       # 14 days, no skip

The prereg wrote this as "d-15 -> d-1", which reads as the no-skip window. The
skip-1 reading is used for the primary because the surrounding sentence states
the intent (excluding short-term reversal), and BOTH are in the pre-declared
robustness grid, so nothing hinges on the resolution. Recorded as a deviation.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from deltabt.config import GST_MULTIPLIER, OUT_DIR
from deltabt.research.stats import block_bootstrap_mean

OUT = OUT_DIR / "xsec"

TAKER, SLIP = 0.0005, 0.0002
COST_PER_SIDE = TAKER * GST_MULTIPLIER + SLIP          # 0.00079

MIN_HISTORY = 90
VOL_WINDOW = 30
MAX_ZERO_FRAC = 0.20
PRIMARY = dict(lookback=14, buckets=3, skip=True, floor=250_000)

SPLITS = {
    "train": ("2025-01-01", "2025-07-01"),
    "valid": ("2025-07-01", "2026-01-01"),
    "test":  ("2026-01-01", "2027-01-01"),
}


def load() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Wide close / USD-volume / return panels indexed by UTC date."""
    df = pd.read_parquet(OUT / "daily.parquet")
    prod = json.load(open("data/meta/products.json"))
    cv = {s: float(p["contract_value"]) for s, p in prod.items()}
    # `volume` is contracts. USD notional needs contract_value and price, or a
    # 10,000x contract like AKEUSD looks a thousand times more liquid than BTC.
    df["usd"] = df.volume * df.symbol.map(cv).fillna(1.0) * df.close
    df["date"] = pd.to_datetime(df.time, unit="s", utc=True).dt.normalize()
    close = df.pivot_table(index="date", columns="symbol", values="close")
    usd = df.pivot_table(index="date", columns="symbol", values="usd")
    zero = df.assign(z=(df.volume <= 0).astype(float)).pivot_table(
        index="date", columns="symbol", values="z")
    return close.sort_index(), usd.sort_index(), zero.sort_index()


def eligible(close, usd, zero, floor):
    """Boolean panel: was this symbol tradeable, judged only on prior bars?"""
    # shift(1) everywhere -- nothing from day d may inform the day-d universe.
    hist = close.notna().shift(1).rolling(MIN_HISTORY, min_periods=MIN_HISTORY).sum()
    liq = usd.shift(1).rolling(VOL_WINDOW, min_periods=VOL_WINDOW).median()
    zf = zero.shift(1).rolling(VOL_WINDOW, min_periods=VOL_WINDOW).mean()
    return (hist >= MIN_HISTORY) & (liq >= floor) & (zf <= MAX_ZERO_FRAC)


def backtest(close, elig, *, lookback, buckets, skip, cost=COST_PER_SIDE,
             reverse=False):
    """Daily long/short tercile portfolio. Returns a frame indexed by date."""
    logc = np.log(close)
    lag = 2 if skip else 1
    feat = logc.shift(lag) - logc.shift(lag + lookback)
    fwd = logc.diff()                       # r_d = log(close_d/close_{d-1})

    dates, gross, net, n_used, turn = [], [], [], [], []
    prev = pd.Series(dtype="float64")
    for d in close.index:
        f = feat.loc[d][elig.loc[d].fillna(False) & feat.loc[d].notna()
                        & fwd.loc[d].notna()]
        k = len(f) // buckets
        if k < 1 or len(f) < buckets * 2:
            # Universe too thin to populate both ends: flat, and the previous
            # book is closed rather than silently carried.
            w = pd.Series(dtype="float64")
        else:
            r = f.rank(ascending=True)
            lo = r <= k
            hi = r > len(f) - k
            w = pd.Series(0.0, index=f.index)
            w[hi] = 0.5 / k
            w[lo] = -0.5 / k
            if reverse:
                w = -w
        g = float((w * fwd.loc[d].reindex(w.index)).sum()) if len(w) else 0.0
        # Turnover is the L1 change in the book, so a name held two days in a
        # row is not charged twice.
        idx = w.index.union(prev.index)
        t = float((w.reindex(idx).fillna(0) - prev.reindex(idx).fillna(0))
                  .abs().sum())
        dates.append(d); gross.append(g); net.append(g - t * cost)
        n_used.append(len(w[w != 0]) if len(w) else 0); turn.append(t)
        prev = w
    return pd.DataFrame(dict(gross=gross, net=net, n=n_used, turnover=turn),
                        index=pd.DatetimeIndex(dates))


def stats(r: pd.Series, label: str) -> dict:
    r = r.dropna()
    n = len(r)
    if n < 20:
        return dict(label=label, days=n, note="too few days")
    m, sd = float(r.mean()), float(r.std(ddof=1))
    sharpe = (m / sd * np.sqrt(365)) if sd > 0 else float("nan")
    b = block_bootstrap_mean(r.to_numpy(), mean_block=5.0, n_boot=2000, seed=17)
    return dict(label=label, days=n, mean_daily=m, sd_daily=sd,
                sharpe_ann=sharpe, total=float(r.sum()),
                ci_low=b.get("ci_low"), ci_high=b.get("ci_high"),
                t_boot=b.get("t"))


def window(df: pd.DataFrame, name: str) -> pd.DataFrame:
    a, b = SPLITS[name]
    return df[(df.index >= pd.Timestamp(a, tz="UTC"))
              & (df.index < pd.Timestamp(b, tz="UTC"))]


def main() -> int:
    close, usd, zero = load()
    print(f"panel: {close.shape[1]} symbols, {close.shape[0]} days, "
          f"{close.index.min().date()} -> {close.index.max().date()}")

    el = eligible(close, usd, zero, PRIMARY["floor"])
    counts = el.sum(axis=1)
    study = counts[(counts.index >= pd.Timestamp("2025-01-01", tz="UTC"))]
    print(f"\nELIGIBLE UNIVERSE (causal, ${PRIMARY['floor']:,} floor)")
    print(f"  median {study.median():.0f}   min {study.min():.0f}   "
          f"max {study.max():.0f}")
    thin = float((study < 8).mean())
    print(f"  days with fewer than 8 eligible: {100*thin:.1f}%"
          + ("   <- INSUFFICIENT DATA per the rule" if thin > 0.20 else ""))
    for y in (2025, 2026):
        s = study[study.index.year == y]
        if len(s):
            print(f"  {y}: median {s.median():.0f}")

    res = backtest(close, el, lookback=PRIMARY["lookback"],
                   buckets=PRIMARY["buckets"], skip=PRIMARY["skip"])
    out = {"universe": dict(median=float(study.median()), thin_frac=thin)}

    print("\n" + "=" * 104)
    print(f"PRIMARY  {PRIMARY['lookback']}d momentum, terciles, skip-1, "
          f"${PRIMARY['floor']:,} floor   TEST LOCKED unless train+valid pass")
    print("=" * 104)
    print(f"  {'window':>7} {'days':>5} {'avg n':>6} {'turn':>6} "
          f"{'GROSS sharpe':>13} {'NET sharpe':>11} {'net mean':>10} "
          f"{'t':>7} {'95% CI':>22}")
    print("  " + "-" * 100)
    for w in ("train", "valid"):
        sub = window(res, w)
        g, nt = stats(sub.gross, w), stats(sub.net, w)
        out[w] = dict(gross=g, net=nt, avg_n=float(sub.n.mean()),
                      avg_turnover=float(sub.turnover.mean()))
        if "note" in nt:
            print(f"  {w:>7}  {nt['note']}")
            continue
        print(f"  {w:>7} {nt['days']:>5} {sub.n.mean():>6.1f} "
              f"{sub.turnover.mean():>6.2f} {g['sharpe_ann']:>13.2f} "
              f"{nt['sharpe_ann']:>11.2f} {nt['mean_daily']:>+10.5f} "
              f"{nt['t_boot']:>7} [{nt['ci_low']:+.5f},{nt['ci_high']:+.5f}]")

    gtr = out["train"]["gross"].get("sharpe_ann", float("nan"))
    gva = out["valid"]["gross"].get("sharpe_ann", float("nan"))
    ntr = out["train"]["net"].get("sharpe_ann", float("nan"))
    nva = out["valid"]["net"].get("sharpe_ann", float("nan"))

    # ---- robustness grid, reported but never a candidate ---------------
    print("\n" + "=" * 104)
    print("PRE-DECLARED ROBUSTNESS GRID (24 cells; none of these is the primary)")
    print("=" * 104)
    print(f"  {'lookback':>8} {'buckets':>8} {'skip':>5} {'floor':>9} "
          f"{'train net Sh':>13} {'valid net Sh':>13} {'train gross':>12} "
          f"{'valid gross':>12}")
    grid = {}
    for floor in (250_000, 1_000_000):
        e2 = eligible(close, usd, zero, floor)
        for lb in (7, 14, 30):
            for bk in (3, 5):
                for sk in (True, False):
                    r2 = backtest(close, e2, lookback=lb, buckets=bk, skip=sk)
                    tr, va = window(r2, "train"), window(r2, "valid")
                    a, b = stats(tr.net, "t"), stats(va.net, "v")
                    ga, gb = stats(tr.gross, "t"), stats(va.gross, "v")
                    key = f"{lb}_{bk}_{int(sk)}_{floor}"
                    grid[key] = dict(train_net=a.get("sharpe_ann"),
                                     valid_net=b.get("sharpe_ann"),
                                     train_gross=ga.get("sharpe_ann"),
                                     valid_gross=gb.get("sharpe_ann"))
                    star = " *" if (lb, bk, sk, floor) == (
                        PRIMARY["lookback"], PRIMARY["buckets"],
                        PRIMARY["skip"], PRIMARY["floor"]) else ""
                    print(f"  {lb:>8} {bk:>8} {str(sk):>5} {floor:>9,} "
                          f"{a.get('sharpe_ann', float('nan')):>13.2f} "
                          f"{b.get('sharpe_ann', float('nan')):>13.2f} "
                          f"{ga.get('sharpe_ann', float('nan')):>12.2f} "
                          f"{gb.get('sharpe_ann', float('nan')):>12.2f}{star}")
    out["grid"] = grid

    # ---- pre-declared secondary arm: 1-day reversal --------------------
    print("\n" + "=" * 104)
    print("SECONDARY ARM (pre-declared, NOT primary): 1-day cross-sectional reversal")
    print("=" * 104)
    rev = backtest(close, el, lookback=1, buckets=3, skip=False, reverse=True)
    for w in ("train", "valid"):
        sub = window(rev, w)
        g, nt = stats(sub.gross, w), stats(sub.net, w)
        out[f"reversal_{w}"] = dict(gross=g, net=nt)
        if "note" not in nt:
            print(f"  {w:>7} days={nt['days']:>4} turn={sub.turnover.mean():.2f}  "
                  f"gross Sh {g['sharpe_ann']:>+6.2f}   net Sh "
                  f"{nt['sharpe_ann']:>+6.2f}   net mean {nt['mean_daily']:>+.5f}  "
                  f"t={nt['t_boot']}")

    # ---- verdict, on train+validation only -----------------------------
    print("\n" + "=" * 104)
    print("PRE-REGISTERED VERDICT")
    print("=" * 104)
    if thin > 0.20:
        verdict = "INSUFFICIENT DATA"
    elif not (gtr > 0 and gva > 0):
        verdict = "NO SIGNAL"
    elif not (ntr > 0 and nva > 0):
        verdict = "NO ECONOMIC EDGE"
    else:
        st = backtest(close, el, lookback=PRIMARY["lookback"],
                      buckets=PRIMARY["buckets"], skip=PRIMARY["skip"],
                      cost=COST_PER_SIDE * 1.5)
        s_ok = stats(window(st, "valid").net, "v").get("sharpe_ann", -9) > 0
        print(f"  1.5x cost stress on validation: net Sharpe "
              f"{stats(window(st,'valid').net,'v').get('sharpe_ann', float('nan')):.2f}")
        verdict = "PROMISING BUT UNPROVEN" if s_ok else "NO ECONOMIC EDGE"
    print(f"  gross Sharpe  train {gtr:+.2f}   valid {gva:+.2f}")
    print(f"  net   Sharpe  train {ntr:+.2f}   valid {nva:+.2f}")
    print(f"\n  VERDICT: {verdict}")
    out["verdict"] = verdict

    if verdict == "PROMISING BUT UNPROVEN":
        print("\n  train and validation both passed -- TEST is now unlocked:")
        te = window(res, "test")
        t = stats(te.net, "test")
        print(f"    test days={t['days']} net Sharpe {t['sharpe_ann']:+.2f} "
              f"t={t['t_boot']}")
        out["test"] = t
    else:
        print("\n  TEST WINDOW NOT COMPUTED -- it stays locked for this family.")

    (OUT / "hxsec1.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nwrote {OUT / 'hxsec1.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
