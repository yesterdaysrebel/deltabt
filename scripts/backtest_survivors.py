"""One backtest of every idea still standing (owner 2026-11-11: "run backtest on all of these").

    PYTHONPATH=. python scripts/backtest_survivors.py

DELTA-NATIVE wherever possible: Delta's daily prices and turnover (G.daily(): perps listed today, 2025-26 -- a
survivor-only sample), Delta's real funding (out/binance_paths/delta_funding_daily.parquet), Delta's taker fee 0.079%
a side + each coin's live spread (out/delta_spreads_2026-11-11.json; 0.10% if unknown). Rule A trades use the same
coins' Binance 1m prices for the stop path (weekly returns correlate 0.998). Cut-offs such as "largest third" come
from EARLIER picks only. Accounts start at $1,500. Portfolios are 1x gross: half long, half short.
Not a pre-registered test: every idea here was found in exploration, so treat each line as a lead.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import explore_wide as W  # noqa: E402
import fade_families_data as FD  # noqa: E402
import fade_families_search as S  # noqa: E402
import gainer_fade_2025_lab as G  # noqa: E402
from live.fade import PAPER_MM_PCT, ladder_risk, lever_plan  # noqa: E402

FEE_SIDE = 0.00079
START = 1500.0


def z(v):
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    return v.mean() / (v.std(ddof=1) / np.sqrt(len(v))) if len(v) > 2 else np.nan


def line(name, per, unit, ann, eq_path, years):
    s = pd.Series(per, dtype=float).dropna()
    eq = pd.Series(eq_path, dtype=float).dropna()
    if len(s) < 3:
        print(f"  {name:52s} (no periods)")
        return
    dd = (1 - eq / eq.cummax()).max() if len(eq) else np.nan
    yr = " ".join(f"{y} {s[s.index.year == int(y)].mean() * (100 if unit == '%' else 1):+.3f}" for y in years)
    pos = sum(s[s.index.year == int(y)].mean() > 0 for y in years)
    m = s.mean() * (100 if unit == "%" else 1)
    end = eq.iloc[-1] if len(eq) else np.nan
    print(f"  {name:52s} n {len(s):4d}  {m:+.3f}{unit}  t {z(s):+5.2f}  {pos}/{len(years)} yrs | {yr} | ${end:8,.0f}  worst drop {100 * dd:3.0f}%")


# --------------------------------------------------------------------------------------------- rule A family
def rule_a_trades() -> pd.DataFrame:
    g = G.panel()
    g = g[g.eligible & np.isfinite(g.atrp) & (g.atrp > 0)].sort_values(["time", "prev_change"], ascending=[True, False])
    g = g.groupby("time").head(2).copy()
    g["dist"] = 2.0 * g.atrp
    g = g[g.dist <= 0.325]
    bd = pd.read_parquet("out/binance_control/daily.parquet").drop_duplicates(["symbol", "time"])
    have = set(bd.symbol)
    to_b = lambda s: next((c for c in (s[:-3] + "USDT", "1000" + s[:-3] + "USDT", s[:-3].replace("1000", "", 1) + "USDT") if c in have), None)  # noqa: E731
    g["bsym"] = g.symbol.map(to_b)
    g = g[g.bsym.notna()].merge(bd[["symbol", "time", "open"]].rename(columns={"symbol": "bsym", "open": "bopen"}), on=["bsym", "time"])
    kl = dict(FD.POOL.map(FD.month_kline, sorted({("fut", s, FD.ymd(t - 86400, "%Y-%m")) for s, t in zip(g.bsym, g.time)})))
    bb = bd[bd.symbol == "BTCUSDT"].set_index("time")
    btc_chg = (bb.close / bb.open - 1).to_dict()
    paths = list(FD.POOL.map(lambda x: FD.path_day(x.bsym, int(x.time)), g.itertuples()))
    F = pd.read_parquet("out/binance_paths/delta_funding_daily.parquet")
    rows = []
    for x, a in zip(g.itertuples(), paths):
        hi = np.where(np.isfinite(a[0]), a[0], -np.inf).astype(float)
        cl = pd.Series(a[2].astype(float)).ffill().to_numpy()
        if not np.isfinite(x.bopen) or not np.isfinite(cl[-1]):
            continue
        stop = x.bopen * (1 + x.dist)
        s = np.nonzero(hi >= stop)[0]
        xp = max(stop, cl[s[0]]) if len(s) else cl[-1]
        r = (-(xp / x.bopen - 1) - S.FEE - S.FEE * xp / x.bopen) / x.dist
        day, prev = pd.Timestamp(x.time, unit="s"), pd.Timestamp(x.time - 86400, unit="s")
        f_prev = F.at[prev, x.symbol] if prev in F.index and x.symbol in F.columns else np.nan
        f_day = F.at[day, x.symbol] if day in F.index and x.symbol in F.columns else np.nan
        held = (s[0] + 1) / 1440 if len(s) else 1.0
        share = kl.get(("fut", x.bsym, FD.ymd(x.time - 86400, "%Y-%m")), {}).get(int(x.time) - 86400, (np.nan, np.nan))[1]
        rows.append(dict(time=int(x.time), date=day, symbol=x.symbol, dist=x.dist, r=r + (f_day * held / x.dist if np.isfinite(f_day) else 0.0),
                         f_prev=f_prev, prev_change=x.prev_change, turnover=x.prev_turnover,
                         btc_prev=btc_chg.get(int(x.time) - 86400, np.nan), taker=share))
    d = pd.DataFrame(rows).sort_values(["time", "symbol"]).reset_index(drop=True)
    for col in ("prev_change", "turnover"):                          # top-third cut-off from EARLIER picks only
        d[f"{col}_cut"] = d[col].expanding(150).quantile(2 / 3).shift(1)
    return d


def account_rule_a(d: pd.DataFrame, mode: str):
    eq = hw = START
    path, per = {}, {}
    for t, g in d.groupby("date"):
        risk = 0.05 if mode == "5%" else ladder_risk(eq, hw)
        pnl = sum(eq * lever_plan(risk, dist, PAPER_MM_PCT)[0] * dist * r for dist, r in zip(g.dist, g.r))
        per[t] = pnl / eq
        eq = max(eq + pnl, 0.0)
        hw = max(hw, eq)
        path[t] = eq
    return per, path


# --------------------------------------------------------------------------------------------- Delta portfolios
def delta_panel():
    d = G.daily()
    d["date"] = pd.to_datetime(d.time, unit="s")
    C = d.pivot(index="date", columns="symbol", values="close").ffill(limit=7)
    V = d.pivot(index="date", columns="symbol", values="turnover")
    F = pd.read_parquet("out/binance_paths/delta_funding_daily.parquet").reindex(index=C.index, columns=C.columns)
    sp = {r["symbol"]: r["spread"] for r in json.load(open("out/delta_spreads_2026-11-11.json"))["rows"]}
    cost = pd.Series({s: FEE_SIDE + sp.get(s, 0.0010) / 2 for s in C.columns})
    return C, V, F, cost


def portfolio(C, F, cost, score, liquid, h, frac, sign=+1):
    """Long the top `frac` by score, short the bottom, equal weight, 1x gross (half each side), rebalanced every h
    days; returns per-period net returns (price + Delta funding - cost of the coins traded) and the equity path."""
    per, path, prev = {}, {}, None
    eq = START
    for t in C.index[60::h]:
        t2 = t + pd.Timedelta(days=h)
        if t2 not in C.index:
            break
        u = liquid.loc[t]
        a = score.loc[t][u].replace([np.inf, -np.inf], np.nan).dropna()
        f = (C.loc[t2] / C.loc[t] - 1)[a.index]
        a = a[f.notna()]
        if len(a) < 20:
            continue
        k = max(int(len(a) * frac), 3)
        o = a.sort_values()
        Ls, Ss = (list(o.index[-k:]), list(o.index[:k])) if sign > 0 else (list(o.index[:k]), list(o.index[-k:]))
        fu = F.loc[t:t2 - pd.Timedelta(days=1)].sum()                # funding over the holding days
        px = 0.5 * (f[Ls].mean() - f[Ss].mean())
        fund = 0.5 * (-fu[Ls].mean() + fu[Ss].mean())               # longs pay positive funding, shorts receive it
        new = set(Ls) | set(Ss) if prev is None else (set(Ls) - prev[0]) | (set(Ss) - prev[1])
        out = set() if prev is None else (prev[0] - set(Ls)) | (prev[1] - set(Ss))
        c = 0.5 * (sum(cost[s] for s in new) + sum(cost[s] for s in out)) / k
        r = px + (fund if np.isfinite(fund) else 0.0) - c
        per[t] = r
        eq *= 1 + r
        path[t] = eq
        prev = (set(Ls), set(Ss))
    return per, path


def binance_portfolio(score, liquid, C, h, frac, cost_side=FEE_SIDE + 0.0005):
    per, path, prev = {}, {}, None
    eq = START
    fwd = C.shift(-h) / C - 1
    for t in C.index[90::h]:
        u = liquid.loc[t]
        a = score.loc[t][u].replace([np.inf, -np.inf], np.nan).dropna()
        f = fwd.loc[t][a.index]
        a = a[f.notna()]
        if len(a) < 25:
            continue
        k = max(int(len(a) * frac), 3)
        o = a.sort_values()
        Ls, Ss = list(o.index[-k:]), list(o.index[:k])
        n_new = 2 * k if prev is None else len(set(Ls) - prev[0]) + len(set(Ss) - prev[1])
        r = 0.5 * (f[Ls].mean() - f[Ss].mean()) - 0.5 * 2 * n_new * cost_side / k
        per[t] = r
        eq *= 1 + r
        path[t] = eq
        prev = (set(Ls), set(Ss))
    return per, path


def main() -> None:
    Y2 = ("2025", "2026")
    print("=" * 150)
    print("A. RULE A FAMILY -- Delta's own picks 2025-26, net R per trade AFTER Delta funding; account at 5% risk (bot's sizing)")
    d = rule_a_trades()
    judged = d[d.prev_change_cut.notna()].copy()
    print(f"   {len(d)} picks; {len(judged)} after the first 150 (needed for the no-look-ahead cut-offs)")
    variants = {
        "rule A as it runs (all picks)": judged.r.notna(),
        "skip if Delta funding < 0 the day before": judged.f_prev >= 0,
        "only the largest third of pumps": judged.prev_change > judged.prev_change_cut,
        "only the most-traded third on Delta": judged.turnover > judged.turnover_cut,
        "funding >= 0 AND (largest third OR most-traded)": (judged.f_prev >= 0) & ((judged.prev_change > judged.prev_change_cut) | (judged.turnover > judged.turnover_cut)),
        "Binance filter (skip strong BTC / buyer-driven)": (judged.btc_prev <= 0.0087) & (judged.taker <= 0.5095),
    }
    for nm, m in variants.items():
        k = judged[m]
        per_trade = k.set_index("date").r
        per, path = account_rule_a(k, "5%")
        line(nm, per_trade, "R", 0, path, Y2)
    per, path = account_rule_a(judged, "ladder")
    line("rule A as it runs, LADDER 10/5/3 (the real account)", judged.set_index("date").r, "R", 0, path, Y2)

    print("\n" + "=" * 150)
    print("B. WEEKLY / DAILY PORTFOLIOS ON DELTA 2025-26 -- Delta prices, Delta funding, fee + live spread; 1x gross; per-period net %")
    C, V, F, cost = delta_panel()
    ret = C.pct_change(fill_method=None)
    hist = C.notna().cumsum()
    v30 = V.rolling(30).mean()
    liquid = (v30 >= 2.5e5) & (hist >= 60)
    five = (-np.log(v30)).rank(axis=1, pct=True) + (-ret.rolling(30).std()).rank(axis=1, pct=True) + (-(V / v30)).rank(axis=1, pct=True) \
        + C.pct_change(30, fill_method=None).rank(axis=1, pct=True) + (-C.pct_change(7, fill_method=None)).rank(axis=1, pct=True)
    for nm, sc, h, fr in (("five-part score, weekly, fifths", five, 7, 0.2), ("five-part score, weekly, thirds", five, 7, 1 / 3),
                          ("quiet coins (low 30-day turnover), weekly", -np.log(v30), 7, 0.2),
                          ("7-day reversal inside the least-traded half, weekly", None, 7, 0.2)):
        if sc is None:
            low = v30.where(liquid).rank(axis=1, pct=True) <= 0.5      # least-traded half of the LIQUID coins
            sc = (-C.pct_change(7, fill_method=None)).where(low)
        per, path = portfolio(C, F, cost, sc, liquid, h, fr)
        line(nm, per, "%", 52, path, Y2)
    F1, F3 = F, F.rolling(3, min_periods=2).mean()
    for nm, sc, h, fr in (("funding collection, daily, thirds", F1, 1, 1 / 3), ("funding collection, every 3 days, fifths", F3, 3, 0.2),
                          ("funding collection, daily, fifths", F1, 1, 0.2)):
        per, path = portfolio(C, F, cost, -sc, liquid, h, fr)           # long LOW funding, short HIGH funding
        line(nm, per, "%", 365 / h, path, Y2)

    print("\n" + "=" * 150)
    print("C. FOR REFERENCE: THE SAME PORTFOLIOS ON THE FULL BINANCE LIST 2021-26 (Binance prices, no funding, 0.13% a side)")
    O, H, L, Cb, Vb = W.panel()
    Cb = Cb.ffill(limit=7)
    rb = Cb.pct_change(fill_method=None)
    vb = Vb.rolling(30).mean()
    lb = (vb >= 5e6) & (Cb.notna().cumsum() >= 60)
    fiveb = (-np.log(vb)).rank(axis=1, pct=True) + (-rb.rolling(30).std()).rank(axis=1, pct=True) + (-(Vb / vb)).rank(axis=1, pct=True) \
        + Cb.pct_change(30, fill_method=None).rank(axis=1, pct=True) + (-Cb.pct_change(7, fill_method=None)).rank(axis=1, pct=True)
    lowb = vb.where(lb).rank(axis=1, pct=True) <= 1 / 3
    Y6 = ("2021", "2022", "2023", "2024", "2025", "2026")
    for nm, sc in (("five-part score, weekly, fifths", fiveb), ("quiet coins, weekly", -np.log(vb)),
                   ("7-day reversal inside the least-traded third", (-Cb.pct_change(7, fill_method=None)).where(lowb))):
        per, path = binance_portfolio(sc, lb, Cb, 7, 0.2)
        line(nm, per, "%", 52, path, Y6)


if __name__ == "__main__":
    main()
