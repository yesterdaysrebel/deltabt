"""Owner 2026-11-17: "I think quick leg is making good results, can you run tests there, see if we modify exit and add
margin post fill how does it work?"

QUICK (live/fade.py, the paper account): short the day's top-2 gainers at 00:00 UTC, stop 2 x 14-day ATR% above the
entry, take profit 0.2 x the stop distance below, out at the day's last 1m close. This grid was written BEFORE the run;
every cell is printed.

  stop     k x ATR%: k = 2 (QUICK), 1, 0.5
  target   f x the stop distance: f = 0.1, 0.2 (QUICK), 0.3, 0.5, 1.0
  clock    out after 60 min, 4 h, or at 23:59 (QUICK)

ADDING MARGIN AFTER THE FILL: enter isolated at 20x, then top the margin up until liquidation sits past the stop (as the
live bot's EXTRA_MARGIN does). Then the stop, not liquidation, closes a losing trade, and the money at risk per trade is
exactly the loss at the stop. That is what is sized: each pick risks RISK of the day-start balance (2 picks a day), so
notional = RISK x balance / stop distance, and the margin that must be posted is about notional x (stop + 1%).

Entry: the close of 00:00 (STRICT 0.05% worse). Fills as scripts/spike_verify.py (STRICT / LENIENT), whose two
implementations are reused and must agree on every trade. Before fees; one line with real fees.

CHECK AGAINST THE PAPER BOT: the picks marked "real" are the paper runner's own picks; for QUICK as-is the simulated exit
(TARGET / STOP / end of day) must match out/fade_quick_full/quick_paper.json on most trades.

    PYTHONPATH=. python scripts/quick_exits.py      # -> out/careful/quick_exits.txt
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
from spike_verify import MAKER, TAKER, trade1, trade2  # noqa: E402

RISKS, START = (0.02, 0.045), 3184.0
KS, FS, CLOCKS = (2.0, 1.0, 0.5), (0.1, 0.2, 0.3, 0.5, 1.0), (60, 240, 1439)


def trades(days, atr, k, f, mins, strict, rng=None):
    rows = []
    for day, ps in days:
        for rank, sym, H, L, Cl in ps:
            a0 = atr[(day, sym)]
            if rng is None:
                if not np.isfinite(Cl[0]):
                    continue
                m0 = 0
            else:
                ok = np.flatnonzero(np.isfinite(Cl[:1200]) & np.isfinite(H[:1200]))
                m0 = int(rng.choice(ok))
            e = Cl[m0] * (0.9995 if strict else 1.0)
            c = dict(entry="x", stop=k * a0, base="entry", trail=0.0, be=False, mins=min(mins, 1439 - m0), tp=f * k * a0)
            ent = (m0, e, e, False)
            a = trade1(H, L, Cl, c, strict, ent)
            if rng is None:
                b = trade2(H, L, Cl, c, strict, ent)
                assert a[:2] == b[:2] and abs(a[2] - b[2]) < 1e-9 and a[3] == b[3], (day, sym, a, b)
            rows.append((day, sym, rank, *a))
    return pd.DataFrame(rows, columns=["day", "sym", "rank", "k", "j", "ret", "how", "dist"])


def line(d, risk=0.045):
    R = d.ret / d.dist
    usd = R * risk * START
    day = R.groupby(d.day).sum()
    t = day.mean() / (day.std(ddof=1) / np.sqrt(len(day)))
    w = R > 0
    return (f"{len(d):4d}  win {100 * w.mean():3.0f}%  avg win ${usd[w].mean():+5.0f}  avg loss ${usd[~w].mean():+5.0f}  per trade ${usd.mean():+5.1f}  "
            f"({R.mean():+.3f}R, t {t:+5.2f})")


def halves(d):
    R = d.ret / d.dist
    out = []
    for a, b in (("2025-01-01", "2025-07-01"), ("2025-07-01", "2026-01-01"), ("2026-01-01", "2026-07-01"), ("2026-07-01", "2027-01-01")):
        m = (d.day >= a) & (d.day < b)
        out.append(f"{R[m].mean():+.3f}")
    return " ".join(out)


def account(d, risk, fees=False):
    bal, peak, mdd, lo = START, START, 0.0, START
    for day, g in d.groupby("day"):
        b0 = bal
        for x in g.itertuples():
            notional = risk * b0 / x.dist
            r = x.ret
            if fees:
                r -= TAKER + (0.0 if x.j - x.k <= 15 else (MAKER if x.how == "TARGET" else TAKER))
            bal += notional * r
        peak, lo = max(peak, bal), min(lo, bal)
        mdd = max(mdd, 1 - bal / peak)
    return f"${bal:>9,.0f} at the end, lowest ${lo:,.0f}, worst fall {100 * mdd:.0f}%"


def main():
    days = D.load()
    P = pd.read_csv("out/careful/picks_atr.csv", parse_dates=["day"])
    atr = {(d, s): a for d, s, a in zip(P.day, P.symbol, P.atrp)}
    src = {(d, s): x for d, s, x in zip(P.day, P.symbol, P.source)}
    out = [f"QUICK WITH OTHER EXITS, margin topped up after the fill so the stop (not liquidation) closes a loser. {sum(len(p) for _, p in days)} picks "
           f"{days[0][0]:%Y-%m-%d}..{days[-1][0]:%Y-%m-%d}, entry at the 00:00 close, before fees. $ = each pick risking 4.5% of $3,184 ($143) at the stop "
           "(2 picks a day = 9% a day, the live bot's risk). R = the result in stop-distances. Half-years: mean R in 2025 H1, H2, 2026 H1, H2."]
    w = out.append

    q = trades(days, atr, 2.0, 0.2, 1439, True)
    paper = {(pd.Timestamp(x["day"][:10]), x["symbol"]): x["exit_reason"] for x in json.load(open("out/fade_quick_full/quick_paper.json")) if x.get("exit_reason")}
    mine = {(x.day, x.sym): x.how for x in q.itertuples()}
    both = [k for k in paper if k in mine]
    same = sum(1 for k in both if {"TARGET": "TARGET", "STOP": "STOP"}.get(paper[k], "CLOCK") == mine[k])
    w(f"\nCHECK vs the paper bot's own QUICK ledger: {same} of {len(both)} shared trades end the same way "
      f"(paper: {pd.Series([paper[k] for k in both]).value_counts().to_dict()}; this sim: {pd.Series([mine[k] for k in both]).value_counts().to_dict()})")
    real = q[[src[(x.day, x.sym)] == "real" for x in q.itertuples()]]
    w(f"   the paper-period picks in this sim: {line(real)}")

    res = []
    for k in KS:
        for f in FS:
            for mins in CLOCKS:
                S = trades(days, atr, k, f, mins, True)
                Ln = trades(days, atr, k, f, mins, False)
                res.append((k, f, mins, S, Ln))
    w(f"\n{'stop':>8} {'target':>9} {'clock':>6} | STRICT{'':70}| LENIENT per trade | half-years (STRICT R)")
    for k, f, mins, S, Ln in res:
        tag = "  <- QUICK as it runs" if (k, f, mins) == (2.0, 0.2, 1439) else ""
        R = Ln.ret / Ln.dist
        w(f"{k:>4.1f}xATR {f:>5.1f}xstop {('day' if mins == 1439 else str(mins) + 'm'):>6} | {line(S)} | ${(R * 0.045 * START).mean():+5.1f} | {halves(S)}{tag}")

    best = sorted(res, key=lambda r: -(r[3].ret / r[3].dist).mean())[:3]
    rng = np.random.default_rng(20261117)
    w("\nQUICK AS-IS and the 3 best STRICT cells (best of 45, so expect shrinkage):")
    for k, f, mins, S, Ln in [r for r in res if r[:3] == (2.0, 0.2, 1439)] + best:
        nm = f"{k}xATR stop, target {f}x, {'day' if mins == 1439 else str(mins) + 'm'}"
        top = (S.ret / S.dist).groupby(S.day).sum().nlargest(5).index
        mo = (S.ret / S.dist).groupby(S.day.dt.strftime("%Y-%m")).sum()
        ctrl = trades(days, atr, k, f, mins, True, rng)
        w(f"  {nm}")
        w(f"     STRICT {line(S)};  months up {int((mo > 0).sum())} of {len(mo)};  median stop {100 * S.dist.median():.1f}% away -> notional {0.045 / S.dist.median():.2f}x the balance per pick")
        w(f"     without the 5 best days {line(S[~S.day.isin(top)])}")
        w(f"     CONTROL same exit after a short at a random minute: {line(ctrl)}")
        for risk in RISKS:
            w(f"     account, {risk:.1%} of the balance at risk per pick: STRICT {account(S, risk)} | LENIENT {account(Ln, risk)} | STRICT + real fees {account(S, risk, True)}")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/quick_exits.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
