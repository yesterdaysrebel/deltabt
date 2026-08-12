"""Execute H-Pair on TRAIN + VALIDATION only. TEST STAYS LOCKED.

    PYTHONPATH=. python3 -u -m deltabt.research.run_hpair
"""

from __future__ import annotations

import itertools
import json

import numpy as np
import pandas as pd

from deltabt.config import OUT_DIR
from deltabt.costs import SymbolCosts
from deltabt.data.store import CandleStore, ProductCatalog
from deltabt.research import hpair as hp
from deltabt.research.hpair import EXEC_MODELS, build_panel, signals
from deltabt.research.stats import block_bootstrap_mean, bootstrap_diff

OUT = OUT_DIR / "hpair"


def summarise(df: pd.DataFrame, label: str) -> dict:
    if df.empty:
        return dict(label=label, trades=0, note="no trades")
    net = df["net_bps"].to_numpy("float64")
    bs = block_bootstrap_mean(net, mean_block=4.0, n_boot=3000, seed=11)
    wins = net[net > 0]; losses = net[net <= 0]
    eq = np.cumsum(net)
    return dict(
        label=label, trades=int(len(df)),
        win_rate=round(float((net > 0).mean()), 4),
        gross_bps=round(float(df.gross_bps.mean()), 2),
        funding_bps=round(float((df.xaut_funding_bps + df.paxg_funding_bps).mean()), 2),
        fee_bps=round(float((df.xaut_fee_bps + df.paxg_fee_bps).mean()), 2),
        slip_bps=round(float((df.xaut_slip_bps + df.paxg_slip_bps).mean()), 2),
        legging_bps=round(float(df.legging_bps.mean()), 2),
        cost_bps=round(float(df.total_cost_bps.mean()), 2),
        net_bps=round(float(net.mean()), 2),
        median_bps=round(float(np.median(net)), 2),
        ci_low=round(bs["ci_low"], 2), ci_high=round(bs["ci_high"], 2),
        t=round(bs["t"], 3) if np.isfinite(bs["t"]) else None,
        profit_factor=round(float(wins.sum() / -losses.sum()), 3) if losses.sum() < 0 else None,
        max_dd_bps=round(float(np.max(np.maximum.accumulate(eq) - eq)), 1),
        hold_median=float(df.hold_hours.median()),
        spread_entry_med=round(float(df.spread_entry.abs().median()), 1),
        spread_exit_med=round(float(df.spread_exit.abs().median()), 1),
        convergence_med=round(float((df.spread_entry.abs() - df.spread_exit.abs()).median()), 1),
        cost_pct_of_gross=(round(100 * float(df.total_cost_bps.mean() / df.gross_bps.mean()), 1)
                           if df.gross_bps.mean() > 0 else None),
        pct_converged=round(100 * float(df.converged.mean()), 1),
        pct_stopped=round(100 * float((df.exit_reason == "stop").mean()), 1),
        pct_timeout=round(100 * float((df.exit_reason == "time").mean()), 1),
        pct_legged=round(100 * float(df.legged.mean()), 1),
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    store, cat = CandleStore(), ProductCatalog()
    X = store.read("XAUTUSD", "ltp", "1m"); P = store.read("PAXGUSD", "ltp", "1m")
    XF = store.read("XAUTUSD", "funding", "1h"); PF = store.read("PAXGUSD", "funding", "1h")
    xc = SymbolCosts.from_spec(cat.get("XAUTUSD"), slippage_bps=1.0)
    pc = SymbolCosts.from_spec(cat.get("PAXGUSD"), slippage_bps=1.0)

    lo = max(int(X.time.iloc[0]), int(P.time.iloc[0]))
    hi = min(int(X.time.iloc[-1]), int(P.time.iloc[-1]))
    span = hi - lo
    a = lo + int(span * 0.60); b = lo + int(span * 0.80)
    print(f"common window {pd.Timestamp(lo,unit='s').date()} -> {pd.Timestamp(hi,unit='s').date()}"
          f"  ({span/86400:.0f} days, {span/86400/365:.2f} yr)")
    print(f"  train {pd.Timestamp(lo,unit='s').date()} -> {pd.Timestamp(a,unit='s').date()}")
    print(f"  valid {pd.Timestamp(a,unit='s').date()} -> {pd.Timestamp(b,unit='s').date()}")
    print(f"  test  {pd.Timestamp(b,unit='s').date()} -> {pd.Timestamp(hi,unit='s').date()}   [LOCKED]")
    print(f"\n  POWER: t = Sharpe * sqrt(years); at {span/86400/365:.2f} yr even Sharpe 2.0 gives "
          f"t = {2.0*np.sqrt(span/86400/365):.2f}. Significance is unreachable by construction.\n")

    xh, ph = build_panel(X, P, lo, None)
    print(f"aligned 1H bars: {len(xh)}  (no forward-fill; unmatched bars dropped)")

    def run(exec_model, params, fill=1.0, end=None, slip=1.0):
        return hp.run(X, P, XF, PF, xc, pc, start=lo, end=end,
                      exec_model=exec_model, maker_fill_prob=fill,
                      slippage_bps=slip, **params)

    # ---- EXECUTION COMPARISON (the essential table) ----------------------
    print("\n" + "=" * 96)
    print("EXECUTION COMPARISON — primary arm (z>=2.0, exit z=0, max hold 24h), TRAIN+VALID")
    print("=" * 96)
    rows = []
    for em in EXEC_MODELS:
        r = run(em, hp.PRIMARY, end=b)
        df = r.to_frame()
        s = summarise(df, em)
        s.update(exec_model=em, signals=r.signals, unfilled=r.unfilled,
                 fill_rate=round(100 * (1 - r.unfilled / max(r.signals, 1)), 1))
        rows.append(s)
        if df.empty:
            print(f"  {em:<14} no trades (signals {r.signals})"); continue
        print(f"  {em:<14} n={s['trades']:<4} fill={s['fill_rate']:>5.1f}% "
              f"GROSS {s['gross_bps']:+7.2f} | fee {s['fee_bps']:5.2f} slip {s['slip_bps']:5.2f} "
              f"legging {s['legging_bps']:5.2f} fund {s['funding_bps']:+6.2f} "
              f"=> NET {s['net_bps']:+7.2f} bps  t={s['t']}")
    ex = pd.DataFrame(rows); ex.to_csv(OUT / "execution_comparison.csv", index=False)

    # ---- maker fill sensitivity -----------------------------------------
    print("\nMAKER FILL SENSITIVITY (maker/maker; queue position is NOT modellable here)")
    fills = []
    for fp in (1.0, 0.7, 0.5, 0.3):
        r = run("maker/maker", hp.PRIMARY, fill=fp, end=b)
        d = r.to_frame()
        s = summarise(d, f"fill={fp}")
        s["fill_prob"] = fp; s["legged_pct"] = s.get("pct_legged")
        fills.append(s)
        if d.empty:
            print(f"  fill_prob {fp:.1f}: no trades"); continue
        print(f"  fill_prob {fp:.1f}: n={s['trades']:<4} legged {s['pct_legged']:>5.1f}%  "
              f"gross {s['gross_bps']:+7.2f}  net {s['net_bps']:+7.2f} bps")
    pd.DataFrame(fills).to_csv(OUT / "fill_sensitivity.csv", index=False)

    # ---- slippage sensitivity -------------------------------------------
    print("\nSLIPPAGE SENSITIVITY (taker/taker)")
    for sl in (0.5, 1.0, 2.0, 3.0):
        d = run("taker/taker", hp.PRIMARY, end=b, slip=sl).to_frame()
        if d.empty:
            continue
        s = summarise(d, f"slip={sl}")
        print(f"  {sl:.1f} bps/exec: gross {s['gross_bps']:+7.2f}  cost {s['cost_bps']:6.2f}  "
              f"net {s['net_bps']:+7.2f} bps")

    # ---- train / validation on the primary execution ---------------------
    print("\nTRAIN / VALIDATION (maker/maker primary)")
    tv = {}
    for nm, (s_, e_) in (("train", (lo, a)), ("valid", (a, b))):
        d = run("maker/maker", hp.PRIMARY, end=e_).to_frame()
        d = d[(d.entry_time >= s_) & (d.entry_time < e_)] if not d.empty else d
        tv[nm] = summarise(d, nm)
        m = tv[nm]
        if not m["trades"]:
            print(f"  {nm}: no trades"); continue
        print(f"  {nm}: n={m['trades']:<4} gross {m['gross_bps']:+7.2f} net {m['net_bps']:+7.2f} "
              f"conv {m['pct_converged']:.0f}% timeout {m['pct_timeout']:.0f}% t={m['t']}")

    # ---- detail on the primary -------------------------------------------
    full = run("maker/maker", hp.PRIMARY, end=b).to_frame()
    if not full.empty:
        full.to_csv(OUT / "trades_primary.csv", index=False)
        print("\nPER-TRADE OUTCOME MIX (primary)")
        print(f"  converged {100*full.converged.mean():.1f}% | stopped "
              f"{100*(full.exit_reason=='stop').mean():.1f}% | timed out "
              f"{100*(full.exit_reason=='time').mean():.1f}% | legged {100*full.legged.mean():.1f}%")
        # The beta-adjusted spread carries a constant offset (beta != 1 times a
        # log price level), so its absolute value is not a dislocation measure.
        # Convergence is the CHANGE, which is offset-free.
        conv = (full.spread_entry.abs() - full.spread_exit.abs())
        print(f"  median convergence (|spread| entry - exit) = {conv.median():+.1f} bps; "
              f"mean {conv.mean():+.1f}")
        print(f"  median |z| at entry {full.z_entry.abs().median():.2f} -> at exit "
              f"{full.z_exit.abs().median():.2f}")

        # ---- NULLS ------------------------------------------------------
        print("\nNULL MODELS (primary, maker/maker)")
        xh2, ph2 = build_panel(X, P, lo, b)
        beta, spread, z = signals(xh2, ph2)
        strat = full["net_bps"].to_numpy("float64")
        rng = np.random.default_rng(5)
        n = len(xh2)
        xcl = xh2["close"].to_numpy("float64"); pcl = ph2["close"].to_numpy("float64")
        fee = 2 * (xc.effective_taker + pc.effective_taker) * 1e4
        slip_all = 4 * 1.0
        hold = hp.PRIMARY["max_hold_h"]
        tt = xh2["time"].to_numpy("int64")
        # The nulls MUST carry the same funding exposure as the strategy.
        # Omitting it made the first run's comparison meaningless: the pair
        # carries ~52 bps/day, which dwarfs everything else.
        fxm = {int(a_): b_ for a_, b_ in zip(XF.time.to_numpy("int64"),
                                             XF.close.to_numpy("float64"))
               if np.isfinite(b_)}
        fpm = {int(a_): b_ for a_, b_ in zip(PF.time.to_numpy("int64"),
                                             PF.close.to_numpy("float64"))
               if np.isfinite(b_)}

        def _fund(i, k, side, b_):
            iv = xc.funding_interval_seconds
            first = ((int(tt[i]) + iv - 1) // iv) * iv
            tot = 0.0
            for s_ in range(first, int(tt[k]) + 1, iv):
                rx = fxm.get(s_); rp = fpm.get(s_)
                if rx is not None:
                    tot += -side * (rx / 100.0)
                if rp is not None:
                    tot += side * b_ * (rp / 100.0)
            return tot * 1e4

        def sim(i, side, b_):
            k = min(i + hold, n - 1)
            if k <= i:
                return None
            xr = side * (xcl[k] - xcl[i]) / xcl[i] * 1e4
            pr = -side * b_ * (pcl[k] - pcl[i]) / pcl[i] * 1e4
            return xr + pr + _fund(i, k, side, b_) - fee - slip_all

        ent = np.searchsorted(xh2["time"].to_numpy("int64"),
                              full["entry_time"].to_numpy("int64"))
        sides = full["side"].to_numpy("int64"); betas = full["beta"].to_numpy("float64")
        lo_i, hi_i = (hp.HEDGE_LOOKBACK_D + hp.Z_LOOKBACK_D) * 24, n - hold - 1
        nulls = {"A": [], "B": [], "C": []}
        shift = 24 * 10
        for _ in range(400):
            for idx, sd_, b_ in zip(ent, sides, betas):
                i0 = int(idx)
                if lo_i <= i0 < hi_i:
                    # B: randomised sign, real timing
                    v = sim(i0, int(rng.choice([-1, 1])), b_)
                    if v is not None:
                        nulls["B"].append(v)
                    # C: one leg time-shifted, destroying the contemporaneous link
                    j = i0 + shift
                    if j < hi_i:
                        k = min(i0 + hold, n - 1); k2 = min(j + hold, n - 1)
                        xr = sd_ * (xcl[k] - xcl[i0]) / xcl[i0] * 1e4
                        pr = -sd_ * b_ * (pcl[k2] - pcl[j]) / pcl[j] * 1e4
                        nulls["C"].append(xr + pr + _fund(i0, k, sd_, b_)
                                          - fee - slip_all)
                # A: random timing, same side and hedge
                v = sim(int(rng.integers(lo_i, hi_i)), int(sd_), b_)
                if v is not None:
                    nulls["A"].append(v)
        names = {"A": "A random timing (same hedge)", "B": "B randomised sign",
                 "C": "C one leg time-shifted 10d"}
        null_out = {}
        for k_, lbl in names.items():
            if not nulls[k_]:
                continue
            pool = np.asarray(nulls[k_])
            cmp = bootstrap_diff(strat, pool, mean_block=4.0, n_boot=3000, seed=17)
            null_out[k_] = dict(null_mean=float(pool.mean()), n=int(pool.size),
                                diff=float(cmp["diff"]), t=float(cmp["t"]))
            print(f"  {lbl:<30} null={pool.mean():+7.2f} bps  strat-null={cmp['diff']:+7.2f}  "
                  f"CI[{cmp['ci_low']:+.2f},{cmp['ci_high']:+.2f}]  t={cmp['t']:.2f}")
        json.dump(null_out, open(OUT / "nulls.json", "w"), indent=2)

    # ---- pre-declared grid, validation -----------------------------------
    print("\nPRE-DECLARED GRID (12 arms x 3 execution models, VALIDATION)")
    rows = []
    for em in EXEC_MODELS:
        for combo in itertools.product(*(hp.GRID[k] for k in ("entry_z", "exit_z", "max_hold_h"))):
            p = dict(zip(("entry_z", "exit_z", "max_hold_h"), combo))
            d = run(em, p, end=b).to_frame()
            d = d[(d.entry_time >= a) & (d.entry_time < b)] if not d.empty else d
            s = summarise(d, f"{em}|z={p['entry_z']}|x={p['exit_z']}|h={p['max_hold_h']}")
            s.update(exec_model=em, **p)
            rows.append(s)
    grid = pd.DataFrame(rows); grid.to_csv(OUT / "grid.csv", index=False)
    ok = grid[grid.trades >= 5]
    print(f"  arms {len(grid)} | with >=5 trades {len(ok)}")
    if len(ok):
        print(f"  positive NET on validation: {int((ok.net_bps>0).sum())}/{len(ok)}")
        print(f"  positive GROSS: {int((ok.gross_bps>0).sum())}/{len(ok)}")
        print(ok.nlargest(5, "net_bps")[["exec_model", "entry_z", "exit_z", "max_hold_h",
                                         "trades", "gross_bps", "cost_bps", "net_bps"]]
              .to_string(index=False))

    tr, va = tv.get("train", {}), tv.get("valid", {})
    promising = (tr.get("trades") and va.get("trades")
                 and (tr.get("net_bps") or 0) > 0 and (va.get("net_bps") or 0) > 0)
    print("\n" + "=" * 78)
    print(f"PROMISING (net > 0 on train AND validation): {'MET' if promising else 'NOT MET'}")
    print("TEST NOT COMPUTED (locked).")
    json.dump(dict(train=tr, valid=va, execution=rows[:3]),
              open(OUT / "summary.json", "w"), indent=2, default=str)
    print(f"\nwritten to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
