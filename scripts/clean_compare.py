"""ONE fill model for every version (owner 2026-11-15: "run tests again and present clean numbers, no mistakes").

    PYTHONPATH=. python scripts/clean_compare.py

FILL RULES (the same for every row; nothing optimistic):
  entry at 00:03   market sell at the 00:03 close; taker fee 0.05% + 0.1% spread/slippage.
  entry spike +5%  resting limit sell 5% above the 00:03 close, filled only when a 1m high trades 0.05% through it,
                   at the limit; maker fee 0.02% + 0.05% slippage. Not filled by 24:00 = no trade.
  stop             market stop: triggers when a 1m HIGH reaches it; fills at the WORSE of the stop and that minute's
                   close, plus 0.1%. If that fill is at or past liquidation, the whole margin is lost.
  target           take-profit: fills when a 1m LOW trades 0.1% through it, at the target; taker fee on exit.
  same minute      stop and target in the same minute = the stop.
  lock (+$200)     when a 1m low first reaches -1% from the fill, the bot moves the stop at that minute's close to
                   the price that nets +$200; if that close is already above it, the stop fires at once at the close.
  24 h             otherwise out at the close 24 h after the fill.
  size (20x)       margin / (5% + 0.12%) up to Delta's per-coin 20x limit; above it Delta's rising margin applies;
                   never above the coin's contract limit (out/delta_margin/product_limits_2026-11-15.json; unknown
                   coins: $50,000 at 20x). Liquidation at entry x (1 + 1/L - 2.5%).
  rule A           the real bot's own sizing (live/fade.lever_plan, ladder 10/5/3), its 2x-ATR stop with the same
                   fill rule, 24 h (scripts/fade_rr12.simulate); 'steady' = funding filter + 3x + 10% margin + stop
                   just before liquidation + take at a 10% fall (scripts/opt1_opt2 rules), same fill rule.
DATA: Delta rule A picks 2025-01-15..2026-10-27 (1,022; 1m paths, out/binance_paths) and the 20 real picks
2026-10-29..11-14 (Delta 1m candles). Start $3,184. Accounts compound.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
import fade_ladder_5x as L5  # noqa: E402
import fade_profit_ladder as PL  # noqa: E402
import fade_rr12 as R12  # noqa: E402
import quick_20x_rules as Q  # noqa: E402

FEE, SPREAD, MAKER = 0.0005, 0.001, 0.0002
START = 3184.0
LIM = json.load(open("out/delta_margin/product_limits_2026-11-15.json"))
DEF = dict(cv=1.0, limit=10 ** 12, max_lev_notional=50000.0, im=5.0, mm=2.5, ims=1e-05, mms=5e-06)


def size(sym: str, margin: float, e: float) -> tuple[float, float]:
    """(position $, liquidation distance) at the most leverage Delta gives for `margin` on this coin."""
    p = LIM.get(sym, DEF)
    lo_, hi_ = 0.0, margin / ((p["im"] + 0.12) / 100)
    for _ in range(50):
        n = (lo_ + hi_) / 2
        im = p["im"] + p["ims"] * max(0.0, n - p["max_lev_notional"])
        lo_, hi_ = (n, hi_) if n * (im + 0.12) / 100 <= margin else (lo_, n)
    n = min(lo_, p["limit"] * p["cv"] * e)
    x = max(0.0, n - p["max_lev_notional"])
    im, mm = p["im"] + p["ims"] * x, p["mm"] + p["mms"] * x
    return n, (im - mm) / 100                                          # Delta: liquidation = entry x (1 + IM - MM)


def short(sym, hi, lo, cl, k0, margin, target, entry, lock, stop=0.0225, lev=None, take_frac=None, extra=0.0,
          lenient=False):
    """One short (20x unless `lev`). take_frac: target as a fixed fall instead of $. extra: $ of additional isolated
    margin added after the fill (same contracts; liquidation moves out by extra/position; a liquidation then loses
    margin + extra). lenient=True: a TRIGGERED stop fills at its price +0.1% instead of the worse of it and the minute's
    close (an immediate fire when the lock is placed under the market is a market exit at the close either way).
    Returns (how, pnl, fill minute, exit minute) or None (no fill)."""
    n = len(cl)
    if entry == "spike":
        lim = cl[k0 - 1] * 1.05
        kf = next((k for k in range(k0, min(n, 1440)) if hi[k] >= lim * 1.0005), None)
        if kf is None:
            return None
        e, fin, sin = lim, MAKER, SPREAD / 2
    else:
        kf, e, fin, sin = k0 - 1, cl[k0 - 1], FEE, SPREAD
    if lev:
        pos, liqd = margin * lev, 1 / lev - 0.025
    else:
        pos, liqd = size(sym, margin, e)
    liqd += extra / pos
    at_risk = margin + extra
    cost = fin + FEE + sin
    pnl = lambda xp: max(pos * (-(xp / e - 1) - fin - FEE * xp / e - sin), -at_risk)   # noqa: E731
    tp = e * (1 - take_frac) if take_frac else e * (1 - (target / pos + cost))
    sp, liq, moved = e * (1 + stop), e * (1 + liqd), False
    lock_px = e * (1 - (lock / pos + cost)) if lock else None
    end = min(n - 1, kf + 1440)
    for k in range(kf + 1, end + 1):
        if hi[k] >= sp:
            xp = sp * 1.001 if lenient else max(sp, cl[k]) * 1.001
            return ("LIQ" if xp >= liq else ("LOCKED" if moved else "STOP")), (-at_risk if xp >= liq else pnl(xp)), kf, k
        if lo[k] <= tp * 0.999:
            return "TARGET", pos * (-(tp / e - 1) - fin - FEE * tp / e - sin), kf, k
        if lock_px and not moved and lo[k] <= e * 0.99:
            moved, sp = True, lock_px
            if cl[k] >= lock_px:
                return "LOCKED", pnl(cl[k] * 1.001), kf, k
    return "24h", pnl(cl[end] * 1.001), kf, end


def paths_history():
    d = L5.trades().reset_index(drop=True)
    opens, ps = R12.paths(d)
    out = []
    for i in range(len(d)):
        a = ps[i]
        hi = np.where(np.isfinite(a[0]), a[0], -np.inf)
        lo = np.where(np.isfinite(a[1]), a[1], np.inf)
        cl = pd.Series(a[2].astype(float)).ffill().to_numpy()
        if np.isfinite(cl[2]) and np.isfinite(cl[-1]):
            out.append((d.date.iat[i], d.symbol.iat[i], hi, lo, cl, 3))
    return out, d, opens, ps


def paths_real():
    return [(day, sym, np.array(hi, float), np.array(lo, float), np.array(cl, float), 3)
            for day, ts in Q.real_picks() for sym, e, hi, lo, cl, k0, cv in ts]


def account(paths, cfg, start=START, months_off_after=None):
    """Compounding. 00:03 entry: both picks of a day share the day's balance (50% each). Spike: one position at
    a time. months_off_after: off for the rest of the month after this many full stops (None = never)."""
    bal, low, busy, per, stops = start, start, -1, None, 0
    by_day = {}
    for p in paths:
        by_day.setdefault(p[0], []).append(p)
    for day in sorted(by_day):
        m = day.strftime("%Y-%m")
        if m != per:
            per, stops = m, 0
        if bal < 50 or (months_off_after and stops >= months_off_after):
            continue
        pnl = 0.0
        for _, sym, hi, lo, cl, k0 in by_day[day]:
            if months_off_after and stops >= months_off_after:
                break
            if cfg.get("filter") and not cfg["filter"](sym, day):
                continue
            r = short(sym, hi, lo, cl, k0, cfg["margin"] * bal, cfg["target"], cfg["entry"], cfg["lock"],
                      cfg.get("stop", 0.0225), cfg.get("lev"), cfg.get("take_frac"), cfg.get("extra", 0.0) * bal)
            if r is None:
                continue
            if cfg["entry"] == "spike":
                t0 = day.value // 60_000_000_000
                if t0 + r[2] < busy:
                    continue
                busy = t0 + r[3]
                bal = max(bal + r[1], 0.0)
            else:
                pnl += r[1]
            stops += r[0] in ("STOP", "LIQ")
        bal = max(bal + pnl, 0.0)
        low = min(low, bal)
    return bal, low


CFGS = [
    ("20x, sell at 00:03, $500 target", dict(entry="0003", margin=0.5, target=500, lock=None), None),
    ("20x, sell at 00:03, $1,000 target", dict(entry="0003", margin=0.5, target=1000, lock=None), None),
    ("20x, spike +5%, $500 target", dict(entry="spike", margin=0.5, target=500, lock=None), None),
    ("20x, spike +5%, $500, lock +$200", dict(entry="spike", margin=0.5, target=500, lock=200), None),
    ("20x, spike +5%, $900, lock +$200", dict(entry="spike", margin=0.5, target=900, lock=200), None),
    ("20x, spike +5%, $500, lock +$200, off 2 stops/mo", dict(entry="spike", margin=0.5, target=500, lock=200), 2),
]


FUND: dict = {}


def funding_ok(sym, day):
    """Delta's previous-day funding sum >= 0.05% (history: out/binance_paths/delta_funding_daily.parquet; real
    picks: Delta's FUNDING candles)."""
    key = (sym, day)
    if key not in FUND:
        import explore_delta_basis as EB
        t0 = int(pd.Timestamp(day).timestamp())
        try:
            iv = int(EB.ProductCatalog().get(sym)["funding_interval_seconds"])
        except Exception:                                              # noqa: BLE001
            iv = 8 * 3600
        f = EB.funding_daily(sym, iv, t0 - 2 * 86400, t0)
        FUND[key] = float(f.get(t0 - 86400, np.nan)) if len(f) else np.nan
    return FUND[key] >= 0.0005


def checks():
    """The fill model reproduces known facts before any result is shown."""
    e = 1.0
    hi = np.array([1.0, 1.0, 1.0, 1.03, 1.0]); lo = np.array([1.0, 1.0, 1.0, 1.0, 0.9]); cl = np.array([1.0, 1.0, 1.0, 1.028, 0.95])
    r = short("X", hi, lo, cl, 3, 1000, 500, "0003", None)
    assert r[0] in ("STOP", "LIQ") and r[3] == 3, r                     # the stop minute comes first
    xp = max(1.0225, 1.028) * 1.001
    assert r[0] == "LIQ" and abs(r[1] + 1000) < 1e-6, ("a stop filled past +2.5% liquidation must lose the margin", r)
    pos, liqd = size("SOPHUSD", 0.5 * 1534.98, 0.001792)                 # 50% of the wallet on 14 Nov
    assert abs(pos / (0.001792 * 100) - 83682) / 83682 < 0.01, pos     # the owner's 83,682 SOPH contracts on 50% of $1,534.98
    assert abs(liqd - 0.025) < 0.001, liqd                             # 20x liquidation ~ +2.5% (Delta-reported)
    pos2, _ = size("PIEVERSEUSD", 1592.0, 2.0)
    assert pos2 < 31000, pos2                                          # PIEVERSE: 20x only up to $10,000
    # extra margin: same contracts, liquidation moves out by 25% x $3,184 / $31,094 = +2.56%; a liquidation loses both
    hi = np.ones(5); lo = np.ones(5); cl = np.ones(5)
    hi[3], cl[3] = 1.04, 1.04                                          # a jump to +4%: past +2.5%, short of +5.06%
    r0 = short("XUSD", hi, lo, cl, 3, 1592.0, 500, "0003", None, 0.0225, None, None, 0.0)
    r1 = short("XUSD", hi, lo, cl, 3, 1592.0, 500, "0003", None, 0.0225, None, None, 796.0)
    assert r0[0] == "LIQ" and abs(r0[1] + 1592) < 1e-6, r0             # no extra margin: liquidated, margin lost
    assert r1[0] == "STOP" and -1592 < r1[1] < -1200, r1               # with extra: stopped at +4%, not liquidated
    hi[3], cl[3] = 1.08, 1.08
    r2 = short("XUSD", hi, lo, cl, 3, 1592.0, 500, "0003", None, 0.0225, None, None, 796.0)
    assert r2[0] == "LIQ" and abs(r2[1] + 1592 + 796) < 1e-6, r2       # past both: margin AND extra lost
    print("CHECKS PASSED: stop before target in the same minute; a stop filled past liquidation loses the margin; "
          "83,682 SOPH contracts on 50% of $1,534.98; 20x liquidation at +2.5%; PIEVERSE capped by its $10,000 20x limit; extra margin moves liquidation out and is lost with it.\n")


def main() -> None:
    checks()
    hist, d, opens, ps = paths_history()
    real = paths_real()
    F = pd.read_parquet("out/binance_paths/delta_funding_daily.parquet")
    for day, sym, *_ in hist:
        t = day - pd.Timedelta(days=1)
        FUND[(sym, day)] = F.at[t, sym] if t in F.index and sym in F.columns else np.nan
    CFGS.append(("steady: filter, 3x, 10% margin, stop ~27.8%, take 10%",
                 dict(entry="0003", margin=0.10, target=0, lock=None, lev=3, stop=0.9 * (1 / 3 - 0.025), take_frac=0.10,
                      filter=funding_ok), None))
    print(f"ALL ROWS: the same fill rules (see the script header). Start ${START:,.0f}.\n")
    print("A) EACH TRADE ALONE on a fixed $3,184 (full history, 1,022 picks)")
    print(f"  {'version':48s} {'trades':>6s} {'target':>6s} {'locked':>6s} {'stop':>5s} {'liq.':>5s} {'24h':>4s} | {'avg/trade':>9s} {'full stop avg':>13s} {'2025':>9s} {'2026':>9s} {'green mo':>8s}")
    for name, cfg, off in CFGS:
        if off:
            continue
        res = [(day, short(sym, hi, lo, cl, k0, cfg["margin"] * START, cfg["target"], cfg["entry"], cfg["lock"],
                           cfg.get("stop", 0.0225), cfg.get("lev"), cfg.get("take_frac")))
               for day, sym, hi, lo, cl, k0 in hist if not cfg.get("filter") or cfg["filter"](sym, day)]
        res = [(dd, r) for dd, r in res if r]
        v = np.array([r[1] for _, r in res]); h = np.array([r[0] for _, r in res]); y = np.array([dd.year for dd, _ in res])
        mo = pd.Series(v).groupby([dd.strftime("%Y-%m") for dd, _ in res]).sum()
        print(f"  {name:48s} {len(v):6d} {100 * (h == 'TARGET').mean():5.0f}% {100 * (h == 'LOCKED').mean():5.0f}% {100 * (h == 'STOP').mean():4.0f}% "
              f"{100 * (h == 'LIQ').mean():4.0f}% {100 * (h == '24h').mean():3.0f}% | {v.mean():+9.1f} {v[h == 'STOP'].mean():13,.0f} "
              f"{v[y == 2025].sum():+9,.0f} {v[y == 2026].sum():+9,.0f} {(mo > 0).sum():>4d}/{len(mo):<3d}")
    print("\nB) AS AN ACCOUNT, compounding from $3,184")
    print(f"  {'version':48s} | {'20 real picks Oct29-Nov14':>25s} | {'history Jan 2025-Oct 2026':>25s} {'lowest':>7s} | "
          f"{'21 monthly starts: under $1,000':>31s} {'up after 3 mo':>13s} {'median 6 mo':>11s} {'median 12 mo':>12s}")
    rows = [(n, c, o) for n, c, o in CFGS]
    for name, cfg, off in rows:
        rb, _ = account(real, cfg, months_off_after=off)
        hb, hl = account(hist, cfg, months_off_after=off)
        gone = up3 = 0
        b6, b12 = [], []
        for m in pd.period_range("2025-02", "2026-10", freq="M"):
            s = m.to_timestamp()
            sub = [x for x in hist if x[0] >= s]
            _, la = account(sub, cfg, months_off_after=off)
            gone += la < 1000
            b3, _ = account([x for x in sub if x[0] < s + pd.Timedelta(days=90)], cfg, months_off_after=off)
            up3 += b3 > START
            if s + pd.Timedelta(days=180) <= hist[-1][0]:
                b6.append(account([x for x in sub if x[0] < s + pd.Timedelta(days=180)], cfg, months_off_after=off)[0])
            if s + pd.Timedelta(days=365) <= hist[-1][0]:
                b12.append(account([x for x in sub if x[0] < s + pd.Timedelta(days=365)], cfg, months_off_after=off)[0])
        print(f"  {name:48s} | {rb:25,.0f} | {hb:25,.0f} {hl:7,.0f} | {gone:24d} of 21 {up3:7d} of 21 {np.median(b6):11,.0f} {np.median(b12):12,.0f}")
    # rule A exactly as the real bot runs it: its own simulator (same stop-fill rule), from the same start
    R12.START = START
    rA = R12.simulate(d, opens, ps, 2, None)
    srs, below = R12.account(d, rA, "ladder")
    print(f"  {'rule A as the real bot runs it (ladder 10/5/3)':48s} | {'(see C)':>25s} | {srs.iloc[-1]:25,.0f} {srs.min():7,.0f} |")
    print("\nC) The real account ran rule A on these 20 picks (Nov 9 on): $1,661.84 on Nov 9 -> $1,530.20 on Nov 14.")


if __name__ == "__main__":
    main()
