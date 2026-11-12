"""Rule A ladder with a CONSTANT 5x leverage (owner 2026-11-12: "for ladder keeping 5x constant levrage how does the
simulation work, give me year on year each mont numbners").

    PYTHONPATH=. python scripts/fade_ladder_5x.py

Trades: Delta's own rule A picks 2025-02..2026-10 as scripts/backtest_survivors.rule_a_trades (top 2 gainers, stop 2x
ATR14%, skip stops > 32.5%, 24 h, Delta fee + funding, Binance 1m path for the stop), plus each trade's worst move
against the short before its exit (from the same 1m highs) to test liquidation.
Accounts from $1,500, risk 10% / 5% / 3% by drawdown (live/fade.ladder_risk), compounding daily:
  NOW      the live sizing: notional = risk / stop, capped at 60% of the balance and 30% margin x 1-3x
           (live/fade.lever_plan; liquidation always >= 2x the stop away, so never liquidated)
  5x-RISK  same risk per trade, leverage always 5x: notional = risk / stop, capped at 30% margin x 5 = 150%.
           Liquidation 1/5 - 2.5% = 17.5% above entry: any trade whose price reaches it before the stop or the
           24 h exit loses its margin (notional / 5).
  5x-FULL  every pick gets 30% of the balance as margin at 5x (notional 150%), whatever the stop; same liquidation.
The real run stops for good at $1,000 (its floor); each table marks the first day an account is below it, and
keeps going so the whole period shows. Liquidation is tested on Binance last-trade highs (Delta uses the smoother
mark price, so a few may be over-counted). Open exploration.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import backtest_survivors as B  # noqa: E402
import fade_families_data as FD  # noqa: E402
import fade_families_search as S  # noqa: E402
import gainer_fade_2025_lab as G  # noqa: E402
from live.fade import PAPER_MM_PCT, ladder_risk, lever_plan  # noqa: E402

START, FLOOR, LEV, MM = 1500.0, 1000.0, 5.0, 0.025
LIQ = 1 / LEV - MM


def trades() -> pd.DataFrame:
    """backtest_survivors.rule_a_trades, plus `mae`: the worst rise above entry before the exit."""
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
        end = s[0] if len(s) else len(hi) - 1
        xp = max(stop, cl[s[0]]) if len(s) else cl[-1]
        r = (-(xp / x.bopen - 1) - S.FEE - S.FEE * xp / x.bopen) / x.dist
        day = pd.Timestamp(x.time, unit="s")
        f_day = F.at[day, x.symbol] if day in F.index and x.symbol in F.columns else np.nan
        held = (s[0] + 1) / 1440 if len(s) else 1.0
        rf = r + (f_day * held / x.dist if np.isfinite(f_day) else 0.0)
        mae = np.max(hi[:end + 1]) / x.bopen - 1
        liq_at = np.nonzero(hi[:end + 1] >= x.bopen * (1 + LIQ))[0]
        rows.append(dict(date=day, symbol=x.symbol, dist=x.dist, r=rf, mae=mae, liquidated=len(liq_at) > 0, prev_change=x.prev_change))
    return pd.DataFrame(rows).sort_values(["date", "symbol"]).reset_index(drop=True)


def account(d: pd.DataFrame, mode: str):
    eq = hw = START
    path, below, n_liq = {}, None, 0
    for t, g in d.groupby("date"):
        risk = ladder_risk(eq, hw)
        pnl = 0.0
        for x in g.itertuples():
            if mode == "NOW":
                pnl += eq * lever_plan(risk, x.dist, PAPER_MM_PCT)[0] * x.dist * x.r
                continue
            notional = min(risk / x.dist, 0.30 * LEV) if mode == "5x-RISK" else 0.30 * LEV
            if x.liquidated:
                n_liq += 1
                pnl -= eq * notional / LEV
            else:
                pnl += eq * notional * x.dist * x.r
        eq = max(eq + pnl, 0.0)
        hw = max(hw, eq)
        path[t] = eq
        if below is None and eq < FLOOR:
            below = t
    return pd.Series(path), below, n_liq


def account_15(d: pd.DataFrame, ladder: bool):
    """Owner 2026-11-12: "for ladder how about we use 5% leverage and 15% risk per trade" (read as 5x). Notional = risk /
    stop (risk 15%, or 15/7.5/4.5% at < 20% / 20-35% / > 35% under the high-water mark), leverage 5x, so margin =
    notional / 5; liquidation at 17.5% above entry loses that margin."""
    eq = hw = START
    path, below, n_liq, dead = {}, None, 0, None
    for t, g in d.groupby("date"):
        under = 1 - eq / hw if hw > 0 else 1
        risk = (0.15 if under < 0.20 else 0.075 if under < 0.35 else 0.045) if ladder else 0.15
        pnl = 0.0
        for x in g.itertuples():
            notional = risk / x.dist
            if x.liquidated:
                n_liq += 1
                pnl -= eq * notional / LEV
            else:
                pnl += eq * notional * x.dist * x.r
        eq = max(eq + pnl, 0.0)
        hw = max(hw, eq)
        path[t] = eq
        if below is None and eq < FLOOR:
            below = t
        if dead is None and eq < 1:
            dead = t
    return pd.Series(path), below, n_liq, dead


def main15() -> None:
    d = trades()
    print(f"Delta rule A picks {d.date.min().date()} .. {d.date.max().date()}: {len(d)} trades; 5x liquidation at 17.5%: "
          f"{d.liquidated.sum()} trades ({100 * d.liquidated.mean():.0f}%) reach it before their exit")
    for ladder, nm in ((False, "15% fixed, 5x"), (True, "ladder 15/7.5/4.5%, 5x")):
        s, below, nl, dead = account_15(d, ladder)
        ye = s.groupby(s.index.year).last()
        m = s.groupby(s.index.to_period("M")).last()
        mret = m / m.shift(1).fillna(START) - 1
        print(f"  {nm:24s} -> ${s.iloc[-1]:>8,.0f}  2025 end ${ye.get(2025):>7,.0f}  worst drop {100 * (1 - s / s.cummax()).max():3.0f}%  "
              f"worst month {100 * mret.min():+.0f}%  liquidations {nl}  under $1,000 {below.date() if below is not None else 'never'}"
              + (f"  $0 on {dead.date()}" if dead is not None else ""))
        print("    by month: " + " ".join(f"{str(k)[2:]} {100 * v:+.0f}%" for k, v in mret.items()))


def main() -> None:
    d = trades()
    print(f"Delta rule A picks {d.date.min().date()} .. {d.date.max().date()}: {len(d)} trades, mean {d.r.mean():+.3f}R; "
          f"reach 17.5% above entry before exiting (5x liquidation): {100 * d.liquidated.mean():.0f}% "
          f"(of them, {100 * (d[d.liquidated].r > 0).mean():.0f}% would have ended as winners)\n")
    res = {m: account(d, m) for m in ("NOW", "5x-RISK", "5x-FULL")}
    for m, (s, below, nl) in res.items():
        dd = (1 - s / s.cummax()).max()
        print(f"  {m:8s} $1,500 -> ${s.iloc[-1]:>10,.0f}  worst drop {100 * dd:3.0f}%  liquidations {nl}"
              + (f"  | below the $1,000 floor first on {below.date()} (the real run would have stopped)" if below is not None else ""))
    print("\nYEAR BY YEAR (balance at year end, return in the year)")
    for m, (s, _, _) in res.items():
        prev, cells = START, []
        for y in (2025, 2026):
            e = s[s.index.year == y].iloc[-1]
            cells.append(f"{y}: ${prev:,.0f} -> ${e:,.0f} ({100 * (e / prev - 1):+.0f}%)")
            prev = e
        print(f"  {m:8s} " + "   ".join(cells))
    print("\nMONTH BY MONTH (% return in the month, then month-end balance)")
    names = list(res)
    print(f"  {'month':8s}" + "".join(f"{n:>24s}" for n in names))
    months = sorted(set(res["NOW"][0].index.to_period("M")))
    prev = {n: START for n in names}
    for mth in months:
        cells = []
        for n in names:
            s = res[n][0]
            e = s[s.index.to_period("M") == mth].iloc[-1]
            r = e / prev[n] - 1 if prev[n] > 0 else 0.0
            cells.append(f"{100 * r:+8.1f}% ${e:>10,.0f}")
            prev[n] = e
        print(f"  {str(mth):8s}" + "".join(f"{c:>24s}" for c in cells))
    for n in names:
        s = res[n][0]
        m = s.groupby(s.index.to_period("M")).last()
        mret = m / m.shift(1).fillna(START) - 1
        print(f"  {n:8s} green months {(mret > 0).sum()} of {len(mret)}; best {100 * mret.max():+.0f}%, worst {100 * mret.min():+.0f}%")


if __name__ == "__main__":
    main15() if "--r15" in sys.argv else main()
