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


BIG = 10 ** 7


def first_after(mask, start, end):
    """First column c with start < c <= end where mask is True (per row); BIG if none."""
    W = mask.shape[1]
    cols = np.arange(W)[None, :]
    n = mask.shape[0]
    st = np.broadcast_to(np.asarray(start), (n,))[:, None]
    en = np.broadcast_to(np.asarray(end), (n,))[:, None]
    m = mask & (cols > st) & (cols <= en)
    return np.where(m.any(1), m.argmax(1), BIG)


def daily_feats(p):
    """Prev-day pump, wick, 3-day return, volume surge, 7-day median quote volume (known at 00:00)."""
    d = pd.concat([pd.read_parquet(f"out/{x}/daily.parquet") for x in ("binance", "binance_control")])
    d = d.drop_duplicates(["symbol", "time"]).sort_values(["symbol", "time"])
    g = d.groupby("symbol")
    d["pump"] = d.close / d.open - 1
    d["wick"] = (d.high - d.close) / (d.high - d.low).replace(0, np.nan)
    d["ret3"] = d.close / g.close.shift(3) - 1
    d["volx"] = d.quote_volume / g.quote_volume.transform(lambda v: v.shift(1).rolling(7, min_periods=4).mean())
    d["vol7"] = g.quote_volume.transform(lambda v: v.rolling(7, min_periods=4).median())
    d["time"] = d.time + 86400
    return p[["symbol", "time"]].merge(d[["symbol", "time", "pump", "wick", "ret3", "volx", "vol7"]], on=["symbol", "time"], how="left")


def round2() -> None:
    """Gemini and DeepSeek proposals (owner pasted 2026-11-13). Same data, fills and PASS BAR as main(). Thresholds are
    the proposers' own numbers (no tuning) unless noted. Not available here: top-3 gainers (ranks 1-2 only), 1-minute
    volume (Gemini 1's volume clause dropped), live intraday premium/funding (previous-day values used)."""
    p, hi, lo, cl = load()
    n = len(p)
    rows = np.arange(n)
    yrs, t = p.year.astype(int).to_numpy(), p.time.to_numpy()
    atr = p.dist.to_numpy() / 2
    o = p.open.to_numpy()
    f = daily_feats(p)
    z = np.zeros(n, int)
    W = cl.shape[1]
    base, _, _ = sim(hi, lo, cl, z, -1, 2 * atr)
    print(f"ROUND 2: Gemini + DeepSeek proposals. Rule A picks Binance 2021-26, n {n}, realistic fills; same columns as round 1")
    bt = line("rule A baseline: 2x ATR stop, 24 h", base, yrs, t)
    fl, fd, pr = p.fund_last.to_numpy(), p.fund_day.to_numpy(), p.prem_close.to_numpy()
    entry0 = cl[:, 2]
    b = np.load(S.OUT / "btc.npz")
    bmap = {int(dd): pd.Series(c).ffill().to_numpy(dtype=float) for dd, c in zip(b["days"], b["close"])}
    btc = np.stack([bmap.get(int(x), np.full(DAY, np.nan)) for x in t])

    def cut_close_above(level, start, end):
        m = first_after(cl > np.asarray(level)[:, None], start, end)
        return m, cl[rows, np.minimum(m, W - 1)]

    def merge(*ex):
        ms = np.stack([e[0] for e in ex])
        ps = np.stack([e[1] for e in ex])
        j = ms.argmin(0)
        return ms[j, rows], ps[j, rows]

    print("\nGEMINI")
    # G1: funding >= +0.05% & premium > 0; short 00:03; target min(0.35 ATR, 3%); stop 1x ATR; cut at 04:03 if up > 0.3 ATR; out 07:55
    for thr in (0.0003, 0.0005, 0.0008):
        m = (fl >= thr) & (pr > 0)
        cut = np.where(cl[:, 243] > entry0 * (1 + 0.3 * atr), 243, BIG)
        r, _, _ = sim(hi, lo, cl, z, -1, atr, np.minimum(0.35 * atr, 0.03), x_end=np.full(n, 475), extra=(cut, cl[:, 243]))
        line(f"G1 funding >= {100 * thr:.2f}% & premium > 0, out 07:55", np.where(m, r, np.nan), yrs, t, base=bt)
    # G2: 08:03 entry after Asia push and 1.5% roll-off
    H08 = np.max(hi[:, :481], 1)
    c08 = cl[:, 480]
    ok2 = (c08 > entry0) & (H08 - c08 >= 0.015 * H08)
    e2 = cl[:, 482]
    d2 = H08 * 1.015 / e2 - 1
    ok2 &= (d2 >= 0.025) & (d2 <= 0.08)
    halfR = first_after(lo <= (e2 * (1 - 0.5 * d2))[:, None], 482, 722)
    c12 = np.where((cl[:, 722] > e2) & (halfR == BIG), 722, BIG)
    ex = merge(cut_close_above(H08, 482, 1435), (c12, cl[:, 722]))
    r, _, _ = sim(hi, lo, cl, np.full(n, 480), -1, d2, 1.5 * d2, x_end=np.full(n, 1435), extra=ex)
    line("G2 post-Asia 08:03 fade, stop above 00-08 high, 1.5R", np.where(ok2, r, np.nan), yrs, t, base=bt)
    # G3: perp-only / low spot, taker chasing; prev-day >= 12%
    sr, oi, ts, hs = p.spot_ratio.to_numpy(float), p.oi_chg.to_numpy(float), p.taker_share.to_numpy(float), p.has_spot.to_numpy()
    ok3 = (f.pump.to_numpy() >= 0.12) & ((hs == 0) | ((sr < 0.15) & (oi > 0.25))) & (ts > 0.54)
    d3 = np.minimum(0.8 * atr, 0.12)
    c2 = np.where((cl[:, 122] > entry0) & (np.min(lo[:, 3:123], 1) > entry0 * 0.985), 122, BIG)
    r, _, _ = sim(hi, lo, cl, z, -1, d3, 0.75 * d3, x_end=np.full(n, 722), extra=(c2, cl[:, 122]))
    line("G3 perp-only/low-spot + taker > 0.54, 0.8 ATR stop, 12 h", np.where(ok3, r, np.nan), yrs, t, base=bt)
    # G4: 3-day blow-off + BTC excess-return cut
    ok4 = (f.ret3.to_numpy() >= 0.45) & (f.pump.to_numpy() >= 0.15) & (f.wick.to_numpy() >= 0.30)
    d4 = np.minimum(0.75 * atr, 0.12)
    exc = cl[:, :DAY] / entry0[:, None] - btc / btc[:, [2]]
    m4 = first_after(np.pad(exc >= 0.035, ((0, 0), (0, W - DAY))), np.full(n, 30), np.full(n, 962))
    r, _, _ = sim(hi, lo, cl, z, -1, d4, 0.8 * d4, x_end=np.full(n, 962), extra=(m4, cl[rows, np.minimum(m4, W - 1)]))
    line("G4 3-day >= 45% blow-off, cut if +3.5% vs BTC, 16:03", np.where(ok4, r, np.nan), yrs, t, base=bt)
    # G5: sweep +3% above open then close <= open x 0.995, between 00:15 and 03:00
    rh = np.maximum.accumulate(hi[:, :181], 1)
    swept = rh >= (o * 1.03)[:, None]
    trig = swept & (cl[:, :181] <= (o * 0.995)[:, None])
    trig[:, :15] = False
    m5 = np.where(trig.any(1), trig.argmax(1), -1)
    ok5 = m5 >= 0
    m5c = np.maximum(m5, 0)
    e5 = cl[rows, m5c + 2]
    d5 = rh[rows, m5c] * 1.005 / e5 - 1
    ok5 &= (d5 >= 0.025) & (d5 <= 0.07)
    ex5 = cut_close_above(o * 1.01, m5c + 2, np.full(n, 1080))
    r, _, _ = sim(hi, lo, cl, m5c, -1, d5, 1.5 * d5, x_end=np.full(n, 1080), extra=ex5)
    line("G5 liquidity sweep (+3% then back under open), 1.5R", np.where(ok5, r, np.nan), yrs, t, base=bt)
    # G6: long 00:03 -> 04:03 (+4% / -2.5%, cut 01:03 if red); short 04:03 only if leg 1 hit +4%
    ok6 = (p.age_days.to_numpy() > 30) & (f.volx.to_numpy() >= 2.5)
    c1 = np.where(cl[:, 62] < entry0, 62, BIG)
    r1, k1, _ = sim(hi, lo, cl, z, +1, np.full(n, 0.025), np.full(n, 0.04), x_end=np.full(n, 242), extra=(c1, cl[:, 62]))
    H4 = np.max(hi[:, :243], 1)
    ex6 = cut_close_above(H4, np.full(n, 242), np.full(n, 1435))
    r2, _, _ = sim(hi, lo, cl, np.full(n, 240), -1, np.full(n, 0.06), np.full(n, 0.06), x_end=np.full(n, 1435), extra=ex6)
    line("G6 leg 1: LONG 00:03-04:03, +4% / -2.5%", np.where(ok6, r1, np.nan), yrs, t)
    line("G6 leg 2: short 04:03 after leg 1 hit +4%, 6% / 6%", np.where(ok6 & (k1 == 1), r2, np.nan), yrs, t)
    line("G6 both legs (R summed)", np.where(ok6, r1 + np.where(k1 == 1, r2, 0), np.nan), yrs, t, base=bt)

    print("\nDEEPSEEK")
    vol7 = f.vol7.to_numpy(float)
    okA = ((hs == 1) & (p.age_days.to_numpy() >= 120) & (vol7 >= 20e6) & (f.pump.to_numpy() >= np.maximum(0.25, atr))
           & (fl >= 0.0003) & (fd > 0) & (pr >= 0.003) & (oi >= 0.05) & ~(sr > 0.9) & (np.abs(p.btc_prev.to_numpy()) <= 0.03))
    dA = 1.75 * atr
    r, _, _ = sim(hi, lo, cl, z, -1, dA, 0.75 * dA, x_end=np.full(n, 482))
    line("A crowded perp unwind, 1.75 ATR, 0.75 target, out 08:03", np.where(okA & (dA <= 0.25), r, np.nan), yrs, t, base=bt)
    okA2 = (fl >= 0.0003) & (fd > 0) & (oi >= 0.05)
    line("A' looser: funding >= 0.03% & sum > 0 & OI +5% only", np.where(okA2 & (dA <= 0.25), r, np.nan), yrs, t, base=bt)
    Hh = np.max(hi[:, 3:63], 1)
    okB = (Hh >= entry0 * (1 + 0.3 * atr)) & (cl[:, 62] < entry0)
    eB = cl[:, 62]
    dB = Hh * 1.001 / eB - 1
    okB &= dB <= np.minimum(atr, 0.12)
    r, _, _ = sim(hi, lo, cl, np.full(n, 60), -1, dB, dB, x_end=np.full(n, 722))
    line("B first-hour failed auction, stop above 1 h high, 1R, 12:03", np.where(okB, r, np.nan), yrs, t, base=bt)
    Hc, Lc = np.max(hi[:, 3:182], 1), np.min(lo[:, 3:182], 1)
    for comp in (0.012, 0.03, 0.05):
        okC = ((Hc - Lc) / entry0 <= comp) & (cl[:, 182] <= Lc)
        dC = Hc * 1.002 / cl[:, 182] - 1
        okC &= dC <= 0.10
        r, _, _ = sim(hi, lo, cl, np.full(n, 180), -1, dC, dC, x_end=np.full(n, 602))
        line(f"C compression <= {100 * comp:.1f}% (00-03) then breakdown, 1R" + ("" if comp == 0.012 else " (looser)"),
             np.where(okC, r, np.nan), yrs, t, base=bt)
    H15 = np.max(hi[:, :903], 1)
    okD = (H15 <= 1.03 * entry0) & (cl[:, 902] < entry0)
    dD = H15 * 1.002 / cl[:, 902] - 1
    okD &= dD <= 0.12
    r, _, _ = sim(hi, lo, cl, np.full(n, 900), -1, dD, 1.5 * dD, x_end=np.full(n, 1442))
    line("D 15:03 short after a stalled day, 1.5R", np.where(okD, r, np.nan), yrs, t, base=bt)
    dv = np.isin(yrs, DEV)
    okE = (vol7 >= 50e6) & (atr <= np.nanmedian(atr[dv]))
    r, _, _ = sim(hi, lo, cl, z, -1, 1.5 * atr, 0.75 * atr)
    line("E liquid (7d vol >= $50m) & low ATR, 1.5 ATR stop, 0.5x target", np.where(okE, r, np.nan), yrs, t, base=bt)
    fh = cl[:, 62] / entry0 - 1
    df = pd.DataFrame({"t": t, "fh": fh})
    lag = (df.groupby("t").fh.transform("count") == 2) & (df.fh == df.groupby("t").fh.transform("min"))
    dF = 1.2 * atr
    r, _, _ = sim(hi, lo, cl, np.full(n, 60), -1, dF, 0.6 * dF)
    line("F short only the pair's first-hour laggard, 1.2 ATR, 0.6x", np.where(lag.to_numpy() & (dF <= 0.14), r, np.nan), yrs, t, base=bt)


if __name__ == "__main__":
    if "--round2" in sys.argv:
        round2()
    else:
        main()
