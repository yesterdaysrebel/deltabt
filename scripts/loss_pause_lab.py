"""Does pausing after losses filter losing trades? (2026-10-09)

    PYTHONPATH=. .venv/bin/python scripts/loss_pause_lab.py

WHY. Owner's request: find a way to filter losing trades of
manual_scalp_both_t3. Two rules, both run on trades the strategy really took:
  * VIRTUAL MODE (an equity-curve filter): after N real losses in a row, stop
    trading for real and follow the signals on paper; go back to real after M
    paper wins in a row. N in {2,3,4}, M in {1,2}, scope = per coin or the
    whole account. 12 cells.
  * COIN PAUSE: after a losing trade on a coin, take no trade on THAT coin for
    T; then trade again. T in {1,2,4,8,12,24,48} hours. 7 cells. The owner asked
    for the best T to be chosen here: it is chosen on the FIRST half of the
    data and judged on the SECOND, so the choice cannot grade itself.
A loss counts only once the trade has CLOSED before the next entry: nothing
peeks ahead. Win = net R > 0.

STEP 1 FIRST, because both rules need it: after a loss on a coin (or k losses
in a row), is the next trade's win rate lower than usual? If not, no pause
rule can work.

DATA.
  A (judged): the prod universe (BEATUSD, AKEUSD, BANKUSD), 2025-12-20..
    2026-09-30, filler bars dropped, the engine's own uncapped trade list,
    separately for hold-to-3R and the trail (trail_after_r 0.5, trail_r 0.5:
    the dry run's account rule).
  B (reported): the symbol screen's 4,950 paired trades on 32 coins.

RULES WRITTEN BEFORE THE RUN, judged on A, per exit:
  P1 across all 19 cells, the best improvement in net R per taken trade over
     taking everything survives studentised max-T against random skipping of
     the same number of trades per coin, family-wise p <= 0.05.
  P2 the cell with the best net R per taken trade on the first half (by entry
     time) also beats taking everything on the second half, with p <= 0.10
     against random skipping of the same count per coin.
  PASS on an exit = P1 and P2. The decision exit is the TRAIL (what the dry
  run's account runs); hold-to-3R must not point the other way.

APPROXIMATION. Rules are applied to the uncapped trade list as a filter. A
real bot that skipped a trade would have that coin's slot free and could enter
a later signal the list does not hold. The paired filter is used because
unpaired re-runs here swing by more than any effect ever found (cooldown_bars
11 -> 12 moved sumR from -7.8 to +49.6). A pass would be re-run exactly before
anything is built.
"""
import dataclasses, glob, sys
from dataclasses import replace
import numpy as np, pandas as pd
sys.path.insert(0, ".")
from deltabt import rulecore
from deltabt.catalog import build_spec
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.data.quality import tradable_mask
from deltabt.harness import _resampled, load_symbol, params_for
from deltabt.portfolio import Book, RiskGates, run_portfolio

H = 3600; DRAWS = 2000; RNG = np.random.default_rng(7)
TS = (1, 2, 4, 8, 12, 24, 48); NS = (2, 3, 4); MS = (1, 2)


# ---- data A: the engine's own trades on the prod universe --------------------------
def prod_universe_trades():
    U = ["BEATUSD", "AKEUSD", "BANKUSD"]; cat = ProductCatalog(); cache = {}; spec = build_spec("manual_scalp_both_t3", 5)
    data = {}
    for s in U:
        d = load_symbol(s); l = d["ltp"]; l = l[~((l.volume == 0) & (l.open == l.close) & (l.high == l.low))].reset_index(drop=True)
        data[s] = dict(symbol=s, ltp=l, mark=d["mark"], funding=d["funding"], tradable=tradable_mask(l))
    books = {}
    for s, d in data.items():
        P, mk, tr = _resampled(d, 5, cache); C = _resampled(d, 1, cache)[0]
        books[s] = Book(symbol=s, bars=P, signals=rulecore.to_engine_signals(rulecore.compute(P, C, spec)),
                        costs=SymbolCosts.from_spec(cat.get(s)), mark=mk, tradable=tr, fill_ltp=d["ltp"], fill_mark=d["mark"])
    out = {}
    for name, kw in (("hold-to-3R", {}), ("trail", dict(trail_after_r=0.5, trail_r=0.5))):
        params = replace(params_for(spec, 5, 72), stop_fill="ltp_close", **kw)
        res = run_portfolio(books, params, replace(RiskGates.off(), max_open_positions=3), initial_capital=10_000.0,
                            funding={s: data[s]["funding"] for s in books})
        t = pd.DataFrame([dataclasses.asdict(x) for x in res.trades])
        out[name] = pd.DataFrame(dict(sym=t.symbol, entry=t.entry_time.astype("int64"), exit=t.exit_time.astype("int64"),
                                      r=t.r_multiple.astype(float)))
    return out


def screen_trades():
    T = pd.concat([pd.read_csv(f) for f in sorted(glob.glob("out/sweep/five_min_arm_lab/symbol_screen/trades/*.csv"))])
    return {"hold-to-3R": pd.DataFrame(dict(sym=T.symbol, entry=T.entry_time, exit=T.exit_baseline, r=T.r_baseline)),
            "trail": pd.DataFrame(dict(sym=T.symbol, entry=T.entry_time, exit=T.exit_trail, r=T.r_trail))}


# ---- rules -----------------------------------------------------------------------
def coin_pause(t, hours):
    take = np.ones(len(t), bool)
    for _, idx in t.groupby("sym").indices.items():
        until = -1
        for i in idx:                                          # trades on one coin never overlap
            if t.entry.iat[i] < until:
                take[i] = False; continue
            if t.r.iat[i] < 0:
                until = t.exit.iat[i] + hours * H
    return take


def virtual_mode(t, n_loss, m_win, scope):
    order_exit = np.argsort(t.exit.to_numpy(), kind="stable"); ex = t.exit.to_numpy(); en = t.entry.to_numpy()
    key = t.sym.to_numpy() if scope == "coin" else np.array(["all"] * len(t))
    state = {}; take = np.zeros(len(t), bool); done = 0
    for i in range(len(t)):                                    # t is sorted by entry
        while done < len(t) and ex[order_exit[done]] < en[i]:  # outcomes known before this entry
            j = order_exit[done]; done += 1; st = state.setdefault(key[j], dict(real=True, losses=0, vwins=0))
            win = t.r.iat[j] > 0
            if take[j]:
                st["losses"] = 0 if win else st["losses"] + 1
                if st["real"] and st["losses"] >= n_loss:
                    st.update(real=False, vwins=0)
            elif not st["real"]:
                st["vwins"] = st["vwins"] + 1 if win else 0
                if st["vwins"] >= m_win:
                    st.update(real=True, losses=0)
        take[i] = state.setdefault(key[i], dict(real=True, losses=0, vwins=0))["real"]
    return take


CELLS = [(f"coin pause {h}h", lambda t, h=h: coin_pause(t, h)) for h in TS] + \
        [(f"virtual N{n} M{m} {sc}", lambda t, n=n, m=m, sc=sc: virtual_mode(t, n, m, sc)) for sc in ("coin", "account") for n in NS for m in MS]


# ---- statistics ---------------------------------------------------------------------
def random_null(t, take, rows=None):
    """Mean R of taken trades under random skipping of the same count per coin."""
    rows = np.ones(len(t), bool) if rows is None else rows
    r = t.r.to_numpy(); out = np.zeros(DRAWS); n_take = 0
    for s, idx in t[rows].groupby("sym").indices.items():
        gi = np.flatnonzero(rows)[idx]; k = int(take[gi].sum()); n_take += k
        if k:
            sel = np.argsort(RNG.random((DRAWS, len(gi))), axis=1)[:, :k]
            out += r[gi][sel].sum(axis=1)
    return out / max(n_take, 1)


def step1(t, label):
    t = t.sort_values("entry").reset_index(drop=True); prev = []; streak = []
    for _, idx in t.groupby("sym").indices.items():
        last = None; k = 0
        for i in idx:
            prev.append((i, last)); streak.append((i, k))
            last = t.r.iat[i] > 0; k = 0 if last else k + 1
    p = pd.Series(dict(prev)).reindex(t.index); k = pd.Series(dict(streak)).reindex(t.index).clip(upper=3)
    win = t.r > 0
    print(f"  {label}: all n {len(t)} win {100 * win.mean():.1f}% mean {t.r.mean():+.3f}R")
    for name, m in (("after a WIN on the coin", p == True), ("after a LOSS on the coin", p == False)):
        print(f"    {name:26s} n {int(m.sum()):5d}  win {100 * win[m].mean():5.1f}%  mean {t.r[m].mean():+.3f}R")
    for kk in range(4):
        m = (k == kk) & p.notna() if kk else (k == 0) & (p == True)
        print(f"    {('after ' + str(kk) + ('+' if kk == 3 else '') + ' losses in a row') if kk else 'after a win (0 losses)':26s} n {int(m.sum()):5d}  win {100 * win[m].mean():5.1f}%  mean {t.r[m].mean():+.3f}R")


def judge(t, exit_name):
    t = t.sort_values("entry").reset_index(drop=True); r = t.r.to_numpy(); allm = r.mean()
    half2 = t.entry.to_numpy() >= np.median(t.entry); half1 = ~half2
    print(f"\n  {exit_name}: take everything n {len(t)}  mean {allm:+.3f}R  win {100 * (r > 0).mean():.1f}%  total {r.sum():+.1f}R")
    print(f"  {'cell':26s}{'taken':>6s}{'mean R':>8s}{'win%':>6s}{'total R':>9s}{'z':>7s}{'h1 mean':>9s}{'h2 mean':>9s}")
    rows = []; Z = []
    for name, rule in CELLS:
        take = rule(t); null = random_null(t, take); mu, sd = null.mean(), null.std(ddof=1)
        obs = r[take].mean(); z = (obs - mu) / sd if sd > 0 else 0.0; Z.append((null - mu) / sd if sd > 0 else np.zeros(DRAWS))
        rows.append(dict(name=name, take=take, obs=obs, z=z, h1=r[take & half1].mean(), h2=r[take & half2].mean()))
        print(f"  {name:26s}{int(take.sum()):6d}{obs:+8.3f}{100 * (r[take] > 0).mean():6.1f}{r[take].sum():+9.1f}{z:+7.2f}"
              f"{rows[-1]['h1']:+9.3f}{rows[-1]['h2']:+9.3f}")
    maxnull = np.max(np.stack(Z), axis=0); best = max(rows, key=lambda x: x["z"])
    fwer = float((maxnull >= best["z"]).mean())
    pick = max(rows, key=lambda x: x["h1"])
    t2 = t[half2].reset_index(drop=True); take2 = pick["take"][half2]
    p2 = float((random_null(t2, take2) >= r[half2][take2].mean()).mean())
    P1 = fwer <= 0.05; P2 = (pick["h2"] > r[half2].mean()) and p2 <= 0.10
    print(f"  P1 best cell '{best['name']}' z {best['z']:+.2f}, family-wise p {fwer:.3f} (<= 0.05): {'PASS' if P1 else 'fail'}")
    print(f"  P2 first-half pick '{pick['name']}' (h1 {pick['h1']:+.3f}R): second half {pick['h2']:+.3f}R vs everything "
          f"{r[half2].mean():+.3f}R, p vs random skip {p2:.3f} (<= 0.10): {'PASS' if P2 else 'fail'}")
    return P1 and P2, best, pick


if __name__ == "__main__":
    A = prod_universe_trades(); B = screen_trades()
    print("STEP 1 -- does a loss on a coin predict the next trade on that coin?")
    for name, t in A.items(): step1(t, f"A prod universe, {name}")
    for name, t in B.items(): step1(t, f"B 32 coins, {name}")
    print("\nSTEP 2 -- the rules, judged on A")
    verdict = {name: judge(t, f"A {name}") for name, t in A.items()}
    print("\nREPORTED, NOT JUDGED -- B, 32 coins")
    for name, t in B.items(): judge(t, f"B {name}")
    v = verdict["trail"][0]; h = verdict["hold-to-3R"]
    print(f"\nVERDICT (decision exit = trail): {'PASS' if v else 'FAIL'}; hold-to-3R {'PASS' if h[0] else 'fail'}")
