"""Rule A "next pick": replace a skipped top-2 gainer with the next that fits (docs/fade_next_pick_prereg.md).

    PYTHONPATH=. python scripts/fade_next_pick.py              # judged: Binance 2021-2024
    PYTHONPATH=. python scripts/fade_next_pick.py --control    # reported only: Binance 2025-2026

Archive, eligibility, stop, fills and fees are scripts/fade_binance_test.py's (imported, not copied); only the
walk down the ranks is new. Data is cached in that script's out dir (gitignored).
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_binance_test as B  # noqa: E402  (reads --control from sys.argv)

MAX_RANK, SLOTS = 10, 2


def daily() -> pd.DataFrame:
    dp = B.OUT / "daily.parquet"
    if dp.exists():
        return pd.read_parquet(dp)
    B.OUT.mkdir(parents=True, exist_ok=True)
    syms = B.symbols()
    print(f"{len(syms)} symbols; daily candles ...", flush=True)
    frames = []
    with ThreadPoolExecutor(12) as ex:
        for k, f in enumerate(ex.map(B.daily_one, syms), 1):
            if len(f):
                frames.append(f)
            if k % 50 == 0:
                print(f"  {k}/{len(syms)}", flush=True)
    d = pd.concat(frames, ignore_index=True)
    for c in ("open", "high", "low", "close", "quote_volume"):
        d[c] = d[c].astype(float)
    d.to_parquet(dp, index=False)
    return d


def ranked(d: pd.DataFrame) -> pd.DataFrame:
    """Every eligible coin per day, ranked by D-1 change: B.panel() without its head(2). Parity-checked below."""
    d = d.sort_values(["symbol", "time"]).drop_duplicates(["symbol", "time"]).reset_index(drop=True)
    d["change"] = d.close / d.open - 1
    pc = d.groupby("symbol").close.shift(1)
    tr = np.maximum.reduce([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()])
    d["atrp"] = pd.Series(tr, index=d.index).groupby(d.symbol).transform(lambda s: s.rolling(14).mean()) / d.close
    prev = d[["symbol", "time", "change", "quote_volume", "atrp"]].copy()
    prev["time"] += B.DAY
    prev.columns = ["symbol", "time", "prev_change", "prev_turnover", "atrp"]
    p = d[["symbol", "time", "open"]].merge(prev, on=["symbol", "time"])
    p = p[(p.time >= B.PICK_FROM) & (p.time < B.PICK_TO)]
    p = p[(p.prev_change > 0) & (p.prev_turnover >= B.MIN_TURNOVER) & np.isfinite(p.atrp) & (p.atrp > 0)]
    p = p.sort_values(["time", "prev_change"], ascending=[True, False]).copy()
    p["rank"] = p.groupby("time").cumcount() + 1
    p["dist"] = B.K_STOP * p.atrp
    p["year"] = pd.to_datetime(p.time, unit="s").dt.year.astype(str)
    ref = B.panel(d)[["symbol", "time", "rank", "dist"]].reset_index(drop=True)
    mine = p[p["rank"] <= 2][["symbol", "time", "rank", "dist"]].reset_index(drop=True)
    pd.testing.assert_frame_equal(mine, ref, check_dtype=False)     # same top 2 as the judged harness
    return p.reset_index(drop=True)


def choose(p: pd.DataFrame) -> pd.DataFrame:
    """A-next: per day, walk ranks 1..MAX_RANK and keep the first SLOTS with a stop <= 32.5%."""
    p = p[p["rank"] <= MAX_RANK]
    ok = p[p.dist <= B.MAX_DIST].sort_values(["time", "rank"])
    ch = ok.groupby("time").head(SLOTS).copy()
    ch["slot"] = ch.groupby("time").cumcount() + 1
    return ch


def z(v) -> float:
    v = np.asarray(v, float)
    return float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if len(v) > 1 else float("nan")


def main() -> None:
    p = ranked(daily())
    ch = choose(p)
    sub = ch[ch["rank"] > SLOTS]
    days = p.time.nunique()
    cache = B.OUT / "next_pick_trades.parquet"
    if cache.exists():
        tr = pd.read_parquet(cache)
    else:
        print(f"{len(sub)} substitute picks; 1m ...", flush=True)
        rows = []
        with ThreadPoolExecutor(12) as ex:
            for k, rec in enumerate(ex.map(B.minute_one, sub.itertuples()), 1):
                if rec:
                    rows.append(rec)
                if k % 250 == 0:
                    print(f"  {k}/{len(sub)}", flush=True)
        tr = pd.DataFrame(rows)
        tr.to_parquet(cache, index=False)

    top2 = p[p["rank"] <= SLOTS]
    print(f"Binance {'2025-2026 (CONTROL, reported only)' if '--control' in sys.argv else '2021-2024 (JUDGED)'}")
    print(f"days {days}; top-2 picks {len(top2)}, skipped (stop > 32.5%) {int((top2.dist > B.MAX_DIST).sum())} "
          f"({100 * (top2.dist > B.MAX_DIST).mean():.1f}%)")
    print(f"substitutes taken {len(sub)} (1m data for {len(tr)}); slots still empty at rank {MAX_RANK}: "
          f"{SLOTS * days - len(ch)}; substitute rank median {sub['rank'].median():.0f}, "
          f"counts {sub['rank'].value_counts().sort_index().to_dict()}")
    print("\nsubstitute trades, net R per trade before funding:")
    print(f"  {'year':6s} {'n':>5s} {'A mean':>8s} {'z':>6s} {'win':>5s} {'QUICK':>8s}")
    years_pos = 0
    for y in B.YEARS:
        t = tr[tr.year == y]
        if len(t):
            years_pos += t.A.mean() > 0
            print(f"  {y:6s} {len(t):5d} {t.A.mean():+8.3f} {z(t.A):+6.2f} {100 * (t.A > 0).mean():4.0f}% {t.QUICK.mean():+8.3f}")
        else:
            print(f"  {y:6s}     0")
    zp = z(tr.A)
    print(f"  {'pooled':6s} {len(tr):5d} {tr.A.mean():+8.3f} {zp:+6.2f} {100 * (tr.A > 0).mean():4.0f}% {tr.QUICK.mean():+8.3f}")
    need = 3 if len(B.YEARS) == 4 else len(B.YEARS)
    verdict = "PASS" if tr.A.mean() > 0 and zp >= 2.0 and years_pos >= need else "FAIL"
    tag = "RULE (pre-registered)" if "--control" not in sys.argv else "control years, NOT a verdict"
    print(f"\n{tag}: pooled z >= 2.0 and > 0 in >= {need}/{len(B.YEARS)} years -> "
          f"z {zp:+.2f}, {years_pos}/{len(B.YEARS)} years > 0 -> {verdict}")


if __name__ == "__main__":
    main()
