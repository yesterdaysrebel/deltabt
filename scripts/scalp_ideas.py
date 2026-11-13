"""Ideas from three other models (Fable, Sonnet, Haiku; out/review_pack/2026-11-13-scalp/reviews/) for the owner's
style (2026-11-13: "if we increase leverage and cut trades at some points movement we take more profit, for loosers we
cut them before SL is hit"; then "find ways to make what I am asking work, use diffrent models").

    PYTHONPATH=. python scripts/scalp_ideas.py

Rule A picks on Binance 2021-26 (out/binance_paths: 3,608 picks, 3-day 1m paths). REALISTIC fills as
scripts/fade_reward_risk: entry at the close of minute e+2 (00:03 for a 00:00 decision), target must trade 0.1% through,
stop at the worse of the stop and that minute's close, 0.1% spread + 0.05% fee a side; a stop and target in the same
minute = stop. R = net return / stop distance. Funding cash is NOT included (Binance trade-day funding not in this set).

PASS BAR (written before the run): TEST = 2024-26 mean R > 0 in EACH of 2024, 2025, 2026 AND TEST t >= 2 AND TEST mean
above the baseline's TEST mean on the same picks universe. Thresholds are the proposer's numbers, or picked on DEV =
2021-23 only (said so per row). ~30 rows: expect about one t > 2 by chance.
Also: GROWTH -- log growth at the best fixed risk (Kelly, chosen on all years, so optimistic for every row equally).
"""
from __future__ import annotations

import sys
from pathlib import Path

import warnings

import numpy as np
warnings.filterwarnings("ignore")
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_families_search as S  # noqa: E402

FEE, EXTRA, THROUGH = S.FEE, 0.001, 0.001
YEARS = (2021, 2022, 2023, 2024, 2025, 2026)
DEV, TEST = (2021, 2022, 2023), (2024, 2025, 2026)
DAY = 1440


def load():
    p = pd.read_parquet(S.OUT / "picks.parquet").reset_index(drop=True)
    a = np.load(S.OUT / "paths.npz")["paths"].astype(np.float64)
    hi = np.where(np.isfinite(a[:, 0]), a[:, 0], -np.inf)
    lo = np.where(np.isfinite(a[:, 1]), a[:, 1], np.inf)
    cl = pd.DataFrame(a[:, 2]).ffill(axis=1).to_numpy()
    ok = np.isfinite(cl[:, 2]) & (cl[:, 2] > 0) & np.isfinite(cl[:, DAY - 1])
    f = pd.read_parquet(S.OUT / "features.parquet")
    p = p.merge(f, on=["symbol", "time"], how="left", suffixes=("", "_f"))
    pr = pd.read_parquet(S.OUT / "premium.parquet")[["symbol", "time", "prem_close"]].copy()
    pr["time"] = pr.time + 86400                                    # D-1's closing premium, known at D's 00:00
    p = p.merge(pr, on=["symbol", "time"], how="left")
    return p[ok].reset_index(drop=True), hi[ok], lo[ok], cl[ok]


def sim(hi, lo, cl, e, side, stop, tgt=None, x_end=None, extra=None):
    """Vector trade. e: decision minute (array); fill at close of e+2. side -1 short / +1 long. stop, tgt: fractions
    from entry (tgt None = no target). x_end: last minute (array; default e+2+1440). extra: (minute, price) early exit or
    None. Returns R, kind (0 stop 1 target 2 time 3 extra)."""
    n, W = cl.shape
    rows = np.arange(n)
    e = np.broadcast_to(np.asarray(e), (n,)).astype(int) + 2
    entry = cl[rows, e]
    stop = np.broadcast_to(np.asarray(stop, float), (n,))
    end = np.minimum(np.broadcast_to(np.asarray(x_end if x_end is not None else e + DAY), (n,)).astype(int), W - 1)
    cols = np.arange(W)[None, :]
    live = (cols > e[:, None]) & (cols <= end[:, None])
    if side < 0:
        sp = entry * (1 + stop)
        sm = live & (hi >= sp[:, None])
    else:
        sp = entry * (1 - stop)
        sm = live & (lo <= sp[:, None])

    def first(m):
        return np.where(m.any(1), m.argmax(1), W + 5)
    ms = first(sm)
    cand = [(ms, None, 0)]
    if tgt is not None:
        tg = np.broadcast_to(np.asarray(tgt, float), (n,))
        tp = entry * (1 + side * tg)
        tm = live & ((lo <= (tp * (1 - THROUGH))[:, None]) if side < 0 else (hi >= (tp * (1 + THROUGH))[:, None]))
        cand.append((first(tm), tp, 1))
    if extra is not None:
        xm, xpx = extra
        xm = np.where((xm > e) & (xm <= end), xm, W + 5)
        cand.append((xm, xpx, 3))
    cand.append((end, cl[rows, end], 2))
    M = np.stack([c[0] * 10 + c[2] for c in cand])
    j = M.argmin(0)
    kind = np.array([c[2] for c in cand])[j]
    xmin = np.stack([c[0] for c in cand])[j, rows]
    xp = np.empty(n)
    for ci, (m, px, k) in enumerate(cand):
        s = j == ci
        if k == 0:
            c = cl[rows[s], np.minimum(m[s], W - 1)]
            xp[s] = np.maximum(sp[s], c) if side < 0 else np.minimum(sp[s], c)
        else:
            xp[s] = px[s] if np.ndim(px) else px
    g = side * (xp / entry - 1) - FEE - FEE * xp / entry - EXTRA
    return g / stop, kind, xmin


def growth(t, r):
    """Best fixed risk (grid) by summed log growth of daily compounding; returns (risk, $1,500 ->)."""
    df = pd.DataFrame({"t": t, "r": r}).groupby("t").r.sum().to_numpy()
    best = (0.0, 1500.0)
    for f in np.arange(0.01, 0.31, 0.01):
        x = 1 + f * df
        if (x <= 0).any():
            break
        end = 1500 * np.exp(np.log(x).sum())
        if end > best[1]:
            best = (f, end)
    return best


BASE_TEST = None


def line(name, r, yrs, t, note="", base=None):
    ok = np.isfinite(r)
    r, yrs, t = r[ok], yrs[ok], t[ok]
    te = np.isin(yrs, TEST)
    rt = r[te]
    tt = rt.mean() / (rt.std(ddof=1) / np.sqrt(len(rt))) if len(rt) > 2 else 0.0
    by = [r[yrs == y].mean() if (yrs == y).any() else np.nan for y in YEARS]
    passed = all(np.isfinite(by[i]) and by[i] > 0 for i in (3, 4, 5)) and tt >= 2 and (base is None or rt.mean() > base)
    f, end = growth(t, r)
    print(f"  {name:52s} n {len(r):4d} ({len(r) / 5.8:3.0f}/yr) {r.mean():+.3f}R win {100 * (r > 0).mean():3.0f}% | TEST {rt.mean():+.3f}R "
          f"t {tt:+5.2f} | " + " ".join(f"{v:+.3f}" if np.isfinite(v) else "   n/a" for v in by)
          + f" | best risk {100 * f:2.0f}% -> ${end:>9,.0f}  {'PASS' if passed else 'fail'} {note}")
    return rt.mean()


def dev_pick(r, yrs, cands):
    """Choose among (label, mask) by DEV mean R only."""
    dv = np.isin(yrs, DEV)
    sc = [(np.nanmean(r[m & dv]) if (m & dv).sum() >= 60 else -9, lab, m) for lab, m in cands]
    return max(sc, key=lambda x: x[0])


def main() -> None:
    p, hi, lo, cl = load()
    n = len(p)
    yrs, t = p.year.astype(int).to_numpy(), p.time.to_numpy()
    atr = p.dist.to_numpy() / 2
    z = np.zeros(n, int)
    base, _, _ = sim(hi, lo, cl, z, -1, 2 * atr)
    q2, _, _ = sim(hi, lo, cl, z, -1, 2 * atr, np.full(n, 0.02))
    print(f"RULE A picks, Binance 2021-26, n {n}, realistic fills. Columns: mean R, win, TEST 2024-26 mean and t, R by year "
          + " ".join(map(str, YEARS)) + ", best fixed risk and $1,500 ->")
    print("\n0. BASELINES")
    bt = line("rule A: 2x ATR stop, 24 h", base, yrs, t)
    line("2% quick target + 2x ATR disaster stop (Fable 'nearest')", q2, yrs, t, base=bt)

    print("\n1. WHICH PICKS (rule A exit; and the 2% quick target exit)")
    fl, fd = p.fund_last.to_numpy(), p.fund_day.to_numpy()
    pr = p.prem_close.to_numpy()
    sel = {
        "Fable 1: funding last>=0 & prev-day sum>=0 & premium>=-0.05%": (fl >= 0) & (fd >= 0) & ~(pr < -0.0005),
        "Fable 1b: funding gate only (no premium)": (fl >= 0) & (fd >= 0),
        "Fable 3: seasoned (spot market & listed >= 180 d)": (p.has_spot.to_numpy() == 1) & (p.age_days.to_numpy() >= 180),
        "Sonnet 3: BTC prev day down": p.btc_prev.to_numpy() < 0,
    }
    for lab, m in sel.items():
        line(lab + " [rule A]", np.where(m, base, np.nan), yrs, t, base=bt)
        line(lab + " [2% quick]", np.where(m, q2, np.nan), yrs, t, base=bt)
    age, hs = p.age_days.to_numpy(), p.has_spot.to_numpy()
    s, lab, m = dev_pick(base, yrs, [(f"no spot & age <= {d} d", (hs == 0) & (age <= d)) for d in (30, 60, 90, 180, 365)])
    line(f"Haiku 2: {lab} (D picked on DEV)", np.where(m, base, np.nan), yrs, t, base=bt)
    for col, who in (("prem_close", "Haiku 1: premium"), ("taker_share", "Sonnet 1: taker buy share"),
                     ("spot_ratio", "Sonnet 1: spot/perp volume"), ("ls_top", "Haiku 3: top-trader L/S")):
        v = p[col].to_numpy(float)
        okv = np.isfinite(v)
        q1, qq = np.nanquantile(v[np.isin(yrs, DEV) & okv], [1 / 3, 2 / 3])
        s, lab, m = dev_pick(base, yrs, [("low third", okv & (v <= q1)), ("mid third", okv & (v > q1) & (v <= qq)),
                                          ("high third", okv & (v > qq))])
        line(f"{who} {lab} (third picked on DEV)", np.where(m, base, np.nan), yrs, t, base=bt)

    print("\n2. WHEN TO GET OUT (same picks, paired with rule A)")
    for h in (48, 72):
        r, _, _ = sim(hi, lo, cl, z, -1, 2 * atr, x_end=np.full(n, 2 + h * 60))
        line(f"Fable 4: hold {h} h, no target", r, yrs, t, base=bt)
    for h, k in ((4, 0.82), (8, 1.15), (4, 2.0), (8, 2.0), (12, 2.0)):
        r, _, _ = sim(hi, lo, cl, z, -1, k * atr, x_end=np.full(n, 2 + h * 60))
        line(f"Fable 5: out at {h:02d}:00, stop {k}x ATR (lev ~{0.8 / np.median(k * atr):.0f}x)", r, yrs, t, base=bt)
    # Sonnet 4: 12 h profit lock -- at 12:03 close if up >= 1%, else hold to 24 h
    entry = cl[:, 2]
    m12 = 2 + 720
    lock = (cl[:, m12] <= entry * 0.99)
    r, _, _ = sim(hi, lo, cl, z, -1, 2 * atr, extra=(np.where(lock, m12, 10 ** 6), cl[:, m12]))
    line("Sonnet 4: close at 12:00 if >= 1% in profit, else 24 h", r, yrs, t, base=bt)

    print("\n3. WHEN TO GET IN / DIRECTION")
    # Haiku 5: noon entry if still >= 5% above the 00:00 open
    o = p.open.to_numpy()
    still = cl[:, 720] >= o * 1.05
    r, _, _ = sim(hi, lo, cl, np.full(n, 720), -1, 2 * atr)
    line("Haiku 5: short at 12:03 if still >= 5% above the open", np.where(still, r, np.nan), yrs, t, base=bt)
    # Sonnet 5: long 00:03 -> 02:00 (stop 1x ATR), then short 02:03 -> 24 h after
    rl, _, _ = sim(hi, lo, cl, z, +1, atr, x_end=np.full(n, 120))
    rs, _, _ = sim(hi, lo, cl, np.full(n, 120), -1, 2 * atr)
    line("Sonnet 5a: LONG the pump 00:03-02:00, stop 1x ATR", rl, yrs, t)
    line("Sonnet 5b: then short from 02:03, 2x ATR, 24 h", rs, yrs, t, base=bt)
    line("Sonnet 5: both legs (R summed, long leg R x 0.5)", 0.5 * rl + rs, yrs, t, base=bt)
    # Haiku 4: long the top gainer after 3 higher 1 h lows (00,01,02) and prev-day OI up >= 5%; enter 03:03
    hl = np.stack([lo[:, h * 60:(h + 1) * 60].min(1) for h in range(3)], 1)
    up3 = (hl[:, 1] > hl[:, 0]) & (hl[:, 2] > hl[:, 1]) & (p.oi_chg.to_numpy() >= 0.05) & (p["rank"].to_numpy() == 1)
    r, _, _ = sim(hi, lo, cl, np.full(n, 180), +1, atr)
    line("Haiku 4: LONG rank 1 after 3 higher 1 h lows & OI +5%, 1x ATR", np.where(up3, r, np.nan), yrs, t)
    print("\nNOT TESTED: Fable 2 (top-5 breadth: this set has ranks 1-2 only), Fable 6 / Haiku hedge (spot leg, intraday "
          "premium not in this set), Sonnet 6 (risk throttle: sizing only, cannot change R).")


if __name__ == "__main__":
    main()
