"""Every Spike Sell trade since 2025 with the prices to check it on Delta (owner 2026-11-15: "give me simulation of
each trade taken since 2025"). Two ledgers, STRICT and LENIENT:
  bot   -- the account as built: from $3,184 on 2025-01-15, 2-stops month-off, $1,000 floor (stops when under it);
  every -- every trade the rule takes Jan 2025 .. Nov 2026: same picking / one trade at a time, always sized on $3,184,
           no month-off, no floor (balance = $3,184 + running total).
The ledgers come from implementations A, B and C (asserted equal); this file re-derives each trade's prices and
asserts its P&L equals the ledger's. CSVs: out/careful/trades_<bot|every>_<strict|lenient>.csv

    PYTHONPATH=. python scripts/careful_all_trades.py
"""
import sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_sim_a as A  # noqa: E402
import careful_sim_b as B  # noqa: E402
import careful_sim_c as Cc  # noqa: E402

IST = pd.Timedelta(hours=5, minutes=30)


def ledger(days, var, le):
    r = [M.run(days, var, le) for M in (A, B, Cc)]
    for o in r[1:]:
        assert len(o[0]) == len(r[0][0]) and all(x[:6] == y[:6] and abs(x[6] - y[6]) < 0.01 for x, y in zip(r[0][0], o[0])), var
    return r[0][0]


def detail(day, picks, sym, size_bal, le):
    by = {s: (rk, H, L, Cl) for rk, s, H, L, Cl in picks}
    rank, H, L, Cl = by[sym]
    lim = Cl[2] * 1.05
    k = next(j for j in range(3, 1440) if H[j] >= lim * 1.0005)
    sc = size_bal / D.START
    margin, extra = 0.49 * size_bal, 0.25 * size_bal
    n, ld = B.sizing(sym, margin, lim, extra)
    p = D.PRODUCTS.get(sym) or B.DEFAULT
    T, S, K = lim * (1 - (500 * sc / n + 0.0012)), lim * 1.0225, lim * (1 - (200 * sc / n + 0.0012))
    t = B.first_trade(sym, H, L, Cl, size_bal, le)
    j = t["j"]
    if t["how"] == "TARGET":
        xp = T
    elif t["how"] == "LIQ":
        xp = max(S, Cl[j]) * 1.001 if not le else S * 1.001
    elif t["how"] in ("STOP",):
        xp = max(S, Cl[j]) * 1.001 if not le else S * 1.001
    else:                                               # LOCKED (stop hit, or at market) or TIME: recompute the stop path
        stop, locked, low = S, False, lim
        xp = None
        for m in range(k + 1, j + 1):
            if H[m] >= stop:
                xp = (stop * 1.001 if le else max(stop, Cl[m]) * 1.001)
                break
            low = min(low, L[m])
            if not locked and L[m] <= 0.99 * lim:
                locked, stop = True, min(stop, K)
                if Cl[m] >= stop:
                    xp = Cl[m] * 1.001
                    break
            if locked:
                stop = min(stop, 1.008 * low)
                if Cl[m] >= stop:
                    xp = Cl[m] * 1.001
                    break
        if xp is None:
            xp = Cl[j] * 1.001
    pnl = (n * (-(xp / lim - 1) - 0.0002 - 0.0005 - 0.0005 * xp / lim))
    pnl = -(margin + extra) if t["how"] == "LIQ" else max(pnl, -(margin + extra))
    assert abs(pnl - t["pnl"]) < 0.01, (day, sym, pnl, t)
    other = [s for s in by if s != sym]
    return dict(day=day.date(), coin=sym, rank=rank, other_pick=",".join(other) or "-",
                price_0002=Cl[2], sell_limit=lim, fill_utc=(day + pd.Timedelta(minutes=k)).strftime("%Y-%m-%d %H:%M"),
                fill_ist=(day + pd.Timedelta(minutes=k) + IST).strftime("%Y-%m-%d %H:%M"),
                contracts=round(n / (p["cv"] * lim)), position_usd=round(n, 2), leverage=round(n / margin, 2),
                margin=round(margin, 2), extra_margin=round(extra, 2), stop=S, target=T, lock_level=K,
                liquidation=lim * (1 + ld), exit=t["how"].replace("TIME", "24h"),
                exit_utc=(day + pd.Timedelta(minutes=j)).strftime("%Y-%m-%d %H:%M"),
                exit_ist=(day + pd.Timedelta(minutes=j) + IST).strftime("%Y-%m-%d %H:%M"),
                exit_price=xp, minutes_held=j - k, pnl=round(t["pnl"], 2))


def build(days, var, le):
    led = ledger(days, var, le)
    dmap = dict(days)
    fixed = D.VARIANTS[var].get("fixed")
    rows, bal = [], D.START
    for x in led:
        day = pd.Timestamp(x[0])
        r = detail(day, dmap[day], x[1], D.START if fixed else bal, le)
        assert abs(r["pnl"] - x[6]) < 0.01 and r["exit"] == x[5].replace("TIME", "24h"), (x, r)
        bal = x[7]
        r["balance_after"] = round(bal, 2)
        rows.append(r)
    return pd.DataFrame(rows)


def main():
    days = D.load()
    out = {}
    for tag, var in (("bot", "run4"), ("every", "run4 every trade")):
        for le in (False, True):
            df = build(days, var, le)
            df.to_csv(f"out/careful/trades_{tag}_{'lenient' if le else 'strict'}.csv", index=False)
            out[(tag, le)] = df
    print("All trades re-derived and equal to the A/B/C ledgers (asserted). Times IST. P&L after fees.\n")
    df = out[("bot", False)]
    print(f"THE BOT AS BUILT from $3,184 on 2025-01-15, STRICT ({len(df)} trades; it stops under $1,000):")
    print(f"  {'#':>3} {'date':10} {'coin':12} {'fill IST':16} {'contracts':>10} {'lev':>5} {'sell':>9} {'stop':>9} {'target':>9} "
          f"{'exit':6} {'exit IST':16} {'exit px':>9} {'P&L':>8} {'balance':>8}")
    for i, r in enumerate(df.itertuples(), 1):
        print(f"  {i:3d} {str(r.day):10} {r.coin:12} {r.fill_ist:16} {r.contracts:10,d} {r.leverage:5.1f} {r.sell_limit:9.5g} "
              f"{r.stop:9.5g} {r.target:9.5g} {r.exit:6} {r.exit_ist:16} {r.exit_price:9.5g} {r.pnl:+8,.0f} {r.balance_after:8,.0f}")
    lb = out[("bot", True)]
    print(f"  LENIENT: {len(lb)} trades, ends ${lb.balance_after.iloc[-1]:,.2f} (full list in trades_bot_lenient.csv)\n")
    for le in (False, True):
        e = out[("every", le)]
        e["m"] = pd.to_datetime(e.day).dt.strftime("%Y-%m")
        print(f"EVERY TRADE, fixed $3,184 size, {'LENIENT' if le else 'STRICT'}: {len(e)} trades, total ${e.pnl.sum():+,.0f}, "
              f"avg ${e.pnl.mean():+.0f}; " + ", ".join(f"{k} {v}" for k, v in e.exit.value_counts().items()))
        if not le:
            print(f"  {'month':7} {'trades':>6} {'T':>3} {'L':>3} {'S':>3} {'LIQ':>3} {'24h':>3} {'P&L':>8} {'running':>9}")
            run = 0.0
            for m, g in e.groupby("m"):
                run += g.pnl.sum()
                c = g.exit.value_counts()
                print(f"  {m:7} {len(g):6d} {c.get('TARGET', 0):3d} {c.get('LOCKED', 0):3d} {c.get('STOP', 0):3d} "
                      f"{c.get('LIQ', 0):3d} {c.get('24h', 0):3d} {g.pnl.sum():+8,.0f} {run:+9,.0f}")
    print("\nCSV files (one row per trade, every price and time): out/careful/trades_bot_strict.csv, trades_bot_lenient.csv,"
          " trades_every_strict.csv, trades_every_lenient.csv")


if __name__ == "__main__":
    main()
