"""THE STATED RULE + AN ADX FILTER, ON TRAIN, VALIDATION AND THE TEST SET.

    PYTHONPATH=. python3 -u -m deltabt.research.run_user_adx

THIS RUN SPENDS THE LOCKED TEST WINDOW (2026-04-16 -> 2026-08-12). It has never
been read by any earlier script in this repository. It is spent here because
the request that prompted it is a genuine pre-specification -- the rule, the
filter to add, the reward ratios and the sizing were all fixed before anything
was measured -- and that is the only condition under which a held-out set is
worth anything.

TWO CONSEQUENCES, BOTH BINDING:

  1. Every cell is reported. Twelve configurations run; twelve are printed.
     Reporting the best one would convert a held-out set into another training
     set, which is the exact failure it exists to prevent.
  2. There is no second look. After this there is no unseen data left in the
     store, which ends 2026-08-12. Any further idea is untestable without new
     market history accumulating first.

THE RULE, entirely on 1m:
    LONG   %R(140) crosses up through -80   and Supertrend(10,2) up
    SHORT  %R(140) crosses down through -20 and Supertrend(10,2) down
plus, in the ADX cells, ADX(28) >= the stated threshold on the same 1m bar.

SIZING IS THE TRADED SIZING, NOT THE RESEARCH DEFAULT: $500 of capital, 25% of
it as notional per trade. That is not cosmetic. At $125 of notional a SOLUSD
contract (~$150) cannot be bought at all, so the position rounds to zero
contracts and the trade never happens -- a constraint that does not exist at
the research default of $10,000 and would be invisible if the default were
kept. Skipped-for-size counts are reported per configuration for that reason.

Risk per trade follows from the two: 25% of capital behind a 1% stop risks
0.25% of equity, so RISK_PCT is set to 0.0025 rather than the research 0.005.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from deltabt import indicators as ind
from deltabt.config import OUT_DIR
from deltabt.costs import SymbolCosts
from deltabt.data.quality import tradable_mask
from deltabt.data.store import CandleStore, ProductCatalog
from deltabt.research import hwpr
from deltabt.research.stats import trade_design_effect

OUT = OUT_DIR / "user_adx"
STUDY = int(pd.Timestamp("2025-01-01", tz="UTC").timestamp())

CAPITAL = 500.0
CAPITAL_FRACTION = 0.25
SL_PCT = 0.010
RR_GRID = (1.0, 1.5, 2.0)
ADX_GRID = (None, 20.0, 25.0, 30.0)
IST = 5.5 * 3600


def masks(C: dict, df: pd.DataFrame, adx_min: float | None):
    """The stated rule, optionally gated on 1m ADX."""
    wpr = C["wpr"]
    prev = np.concatenate(([np.nan], wpr[:-1]))
    with np.errstate(invalid="ignore"):
        lo = C["st1_long"] & (wpr > -80.0) & (prev <= -80.0)
        sh = C["st1_short"] & (wpr < -20.0) & (prev >= -20.0)
    if adx_min is not None:
        h = df["high"].to_numpy("float64")
        l = df["low"].to_numpy("float64")
        c = df["close"].to_numpy("float64")
        _p, _m, adx = ind.dmi(h, l, c, hwpr.DI_PERIOD, hwpr.ADX_PERIOD)
        with np.errstate(invalid="ignore"):
            gate = adx >= adx_min
        lo, sh = lo & gate, sh & gate
    return lo, sh


def conditions(C: dict, lo, sh):
    n = len(C["close"])
    c = C["close"]
    D = dict(C)
    D["f5_long"], D["f5_short"] = lo, sh
    D["wprA_long"] = np.ones(n, dtype=bool)
    D["wprA_short"] = np.ones(n, dtype=bool)
    D["st1"] = c
    D["leg_lo"] = c * (1.0 - SL_PCT)
    D["leg_hi"] = c * (1.0 + SL_PCT)
    return D


def stats(df: pd.DataFrame) -> dict:
    if df.empty:
        return dict(trades=0)
    net = df.r_net.to_numpy("float64")
    de = trade_design_effect(df)
    n_eff = max(de["n_eff"], 1.0)
    se = float(net.std(ddof=1)) / np.sqrt(n_eff) if len(net) > 1 else float("nan")
    frac = CAPITAL_FRACTION * SL_PCT
    wins = net[net > 0]
    losses = net[net <= 0]
    return dict(
        trades=int(len(df)), wins=int(wins.size), losses=int(losses.size),
        win_rate=round(float((net > 0).mean()), 4),
        avg_win_r=round(float(wins.mean()), 3) if wins.size else None,
        avg_loss_r=round(float(losses.mean()), 3) if losses.size else None,
        net_r=round(float(net.mean()), 4),
        gross_r=round(float(df.r_gross.mean()), 4),
        cost_r=round(float(df.cost_r.mean()), 4),
        t=round(float(net.mean() / se), 2) if se and np.isfinite(se) else None,
        end_equity=round(float(CAPITAL * np.prod(1.0 + net * frac)), 2),
        trades_per_day=round(len(df) / max(
            (df.entry_time.max() - df.entry_time.min()) / 86400, 1), 2))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    universe = pd.read_csv(OUT_DIR / "hwpr_universe.csv").symbol.tolist()
    store, cat = CandleStore(), ProductCatalog()
    data = {}
    for s in universe:
        ltp = store.read(s, "ltp", "1m")
        ltp = ltp[ltp.time >= STUDY].reset_index(drop=True)
        spec = cat.get(s)
        data[s] = dict(df=ltp, mark=store.read(s, "mark", "1m"),
                       funding=store.read(s, "funding", "1h"),
                       costs=SymbolCosts.from_spec(spec, slippage_bps=2.0),
                       tradable=tradable_mask(ltp))
        data[s]["C"] = hwpr.build_conditions(ltp)

    last = min(int(d["df"].time.iloc[-1]) for d in data.values())
    span = last - STUDY
    TR = (STUDY, STUDY + int(span * 0.6))
    VA = (STUDY + int(span * 0.6), STUDY + int(span * 0.8))
    TE = (STUDY + int(span * 0.8), last)

    # 25% of capital behind a 1% stop = 0.25% of equity at risk.
    hwpr.RISK_PCT = CAPITAL_FRACTION * SL_PCT
    hwpr.START_EQUITY = CAPITAL

    print("RULE (1m only): long = %R(140) crosses up through -80 + Supertrend(10,2) up")
    print("                short = %R(140) crosses down through -20 + Supertrend down")
    print(f"CAPITAL ${CAPITAL:,.0f}, {CAPITAL_FRACTION:.0%} per trade, "
          f"fixed SL {SL_PCT:.2%}, risk/trade {hwpr.RISK_PCT:.3%}")
    print(f"train {pd.Timestamp(TR[0],unit='s').date()} -> {pd.Timestamp(TR[1],unit='s').date()}")
    print(f"valid {pd.Timestamp(VA[0],unit='s').date()} -> {pd.Timestamp(VA[1],unit='s').date()}")
    print(f"TEST  {pd.Timestamp(TE[0],unit='s').date()} -> {pd.Timestamp(TE[1],unit='s').date()}"
          "   <-- HELD OUT UNTIL NOW, SPENT BY THIS RUN\n")

    for s, d in data.items():
        c0 = float(d["df"]["close"].iloc[-1])
        one = d["costs"].contract_value * c0
        print(f"  {s:8} contract ~${one:>8,.2f}   "
              f"{'TRADEABLE' if one <= CAPITAL*CAPITAL_FRACTION else 'ROUNDS TO ZERO CONTRACTS'}")
    print()

    def measure(lo_sh, target_r, window):
        frames, skipped = [], 0
        for s, d in data.items():
            lo, sh = lo_sh[s]
            r = hwpr.run(d["df"], d["mark"], d["funding"], d["costs"],
                         conditions(d["C"], lo, sh), arm="E", wpr_variant="A",
                         target_r=target_r, start=window[0], end=window[1],
                         tradable=d["tradable"], max_stop_pct=0.10)
            skipped += r.skipped_size
            f = r.to_frame()
            if len(f):
                f = f[(f.entry_time >= window[0]) & (f.entry_time < window[1])]
            if len(f):
                frames.append(f)
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        return df, skipped

    rows, dumps = [], {}
    print("=" * 138)
    print(f"{'ADX':>6} {'RR':>5} | {'window':6} {'n':>6} {'W':>6} {'L':>6} "
          f"{'win%':>7} {'net R':>9} {'t':>7} {'equity':>10} {'skip':>7}")
    print("=" * 138)
    for adx_min in ADX_GRID:
        lo_sh = {s: masks(d["C"], d["df"], adx_min) for s, d in data.items()}
        for rr in RR_GRID:
            for nm, win in (("train", TR), ("valid", VA), ("TEST", TE)):
                df, skipped = measure(lo_sh, rr, win)
                m = stats(df)
                rows.append(dict(adx=adx_min or 0, rr=rr, window=nm,
                                 skipped_size=skipped, **m))
                lbl = f"{'off' if adx_min is None else f'>={adx_min:.0f}':>6} {rr:>5.1f}"
                if not m["trades"]:
                    print(f"{lbl} | {nm:6} {'-- no trades --':>40}")
                    continue
                print(f"{lbl} | {nm:6} {m['trades']:>6,} {m['wins']:>6,} "
                      f"{m['losses']:>6,} {100*m['win_rate']:>6.2f}% "
                      f"{m['net_r']:>+9.4f} {m['t']:>7.1f} "
                      f"${m['end_equity']:>9,.2f} {skipped:>7,}")
                if nm == "TEST" and not df.empty:
                    dumps[(adx_min, rr)] = df
            print("-" * 138)

    pd.DataFrame(rows).to_csv(OUT / "summary.csv", index=False)

    # Trade-level export for the TEST window, every configuration.
    for (adx_min, rr), df in dumps.items():
        t = df.sort_values("entry_time").reset_index(drop=True)
        for col in ("entry_time", "exit_time"):
            t[col + "_utc"] = pd.to_datetime(t[col], unit="s", utc=True)
            t[col + "_ist"] = pd.to_datetime(t[col] + IST, unit="s")
        t["side_txt"] = np.where(t.side > 0, "LONG", "SHORT")
        t["win"] = t.r_net > 0
        t["equity_pct"] = t.r_net * CAPITAL_FRACTION * SL_PCT * 100
        cols = ["symbol", "side_txt", "entry_time_utc", "entry_time_ist",
                "exit_time_utc", "bars_held", "entry_price", "stop_price",
                "target_price", "exit_price", "exit_reason", "r_gross",
                "cost_r", "r_net", "win", "equity_pct", "contracts", "notional"]
        tag = f"adx{'off' if adx_min is None else int(adx_min)}_rr{rr}"
        t[cols].to_csv(OUT / f"TEST_trades_{tag}.csv", index=False)

    print(f"\nwrote {OUT}/summary.csv and {len(dumps)} TEST trade files")
    te = pd.DataFrame(rows)
    te = te[(te.window == "TEST") & (te.trades.fillna(0) > 0)]
    if len(te):
        print(f"\nTEST-SET VERDICT over {len(te)} pre-specified configurations")
        print(f"  net_r: min {te.net_r.min():+.4f}  median {te.net_r.median():+.4f}  "
              f"max {te.net_r.max():+.4f}")
        print(f"  positive: {(te.net_r > 0).sum()} / {len(te)}")
        print(f"  ending above ${CAPITAL:,.0f}: {(te.end_equity > CAPITAL).sum()} / {len(te)}")
    print("\nTHE HELD-OUT SET IS NOW SPENT. There is no unseen data left.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
