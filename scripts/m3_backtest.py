"""Does the deployed cell survive ETHUSD and SOLUSD? Measured, not argued.

    PYTHONPATH=. python3 scripts/m3_backtest.py

Asked 2026-09-13: run `manual_scalp_both_t3` (the live arm: %R variant_a on
the 5m AND the 1m, no Supertrend/DI/ADX, 4xATR(10) stop, 3R target, 72h hold)
on BEATUSD, ETHUSD and SOLUSD. This is the BACKTEST answering that question;
nothing here touches the running experiment, the universe, or any host.

It needs the 1m archive (data/<SYMBOL>/ltp_1m.parquet). Run it on the machine
that produced out/sweep/five_min_arm_lab/two_arms.txt -- ETHUSD and SOLUSD
are already cached there from the v5-era measurements, and refreshing the
archive past 2026-08-12 first makes the answer current rather than a rerun
of what the 2026-09-04 ranking already saw.

WHAT IS ALREADY ON RECORD, so this run is read against it and not as news:
  - SOLUSD, this family, 2026-09-04: net -0.129 at 3R/72h, 0/4 anchored
    blocks, gross -0.035 -- negative BEFORE fees.
  - ETHUSD has never been measured under this cell. The cost law prices its
    4xATR 5m stop at cost_r ~0.10-0.12 against 0.03-0.04 on the thin three.
  - The deployed thin-three cell: +0.140 net, 3/4 blocks, n=373,
    bootstrap [-0.041, +0.321].

THE CRITERIA, FIXED BEFORE ANY NUMBER IS READ. A symbol PASSES only if all
three hold on its own anchored blocks:
    (a) pooled net R > 0
    (b) at least 3 of 4 anchored blocks positive
    (c) n >= 40 closed trades
The ETH/SOL/BEAT universe earns a deployment CONVERSATION (not a deployment)
only if ETHUSD passes AND SOLUSD passes AND the one-shared-account run under
the live gates does not carry more than 1.5x the thin-three account's
maximum drawdown. Anything less prints DO NOT DEPLOY, and a near miss is a
miss: the selection premium measured across this family's labs (+0.24 to
+0.58) is larger than any edge in its tables, so a criterion bent after the
fact is worth less than no criterion.

Per-symbol reads on fewer than ~40 trades are weather. The verdict section
applies the rule mechanically so the reader does not have to trust the
author's mood.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd

from deltabt import rulecore
from deltabt.catalog import build_spec
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.harness import CACHE_DIR, _resampled, load_symbol, params_for
from deltabt.portfolio import Book, RiskGates, run_portfolio

M3 = ["BEATUSD", "ETHUSD", "SOLUSD"]
THIN = ["BEATUSD", "AKEUSD", "BANKUSD"]      # the measured baseline universe
HOLD = 72
SPEC = build_spec("manual_scalp_both_t3", 5, 1)

#: The live stack's gates as deployed (breakers off, 6 slots, 20 trades/day),
#: for the shared-account comparison. Per-symbol measurement runs ungated.
LIVE_GATES = RiskGates(max_open_positions=6, max_trades_per_day=20,
                       max_daily_loss_pct=1.0, max_drawdown_pct=1.0,
                       max_consecutive_losses=0)

SYMBOLS = sorted(set(M3) | set(THIN))
missing = [s for s in SYMBOLS if load_symbol(s) is None]
if missing:
    raise SystemExit(
        f"no 1m archive for {', '.join(missing)} under {CACHE_DIR} -- run this "
        f"where the two_arms.py archive lives, or backfill those symbols first")

cat = ProductCatalog()
LOAD = {s: load_symbol(s) for s in SYMBOLS}
FR = {s: _resampled(LOAD[s], 5, {}) for s in SYMBOLS}
CONF = {s: _resampled(LOAD[s], SPEC.confirm_minutes, {})[0] for s in SYMBOLS}
BE = {s: np.linspace(FR[s][0]["time"].min(), FR[s][0]["time"].max() + 1, 5)
        .astype(np.int64) for s in SYMBOLS}
SIG = {s: rulecore.to_engine_signals(
          rulecore.compute(FR[s][0], CONF[s] if SPEC.confirm.enabled else None,
                           SPEC)) for s in SYMBOLS}


def book(s: str) -> Book:
    P, mark, tr = FR[s]
    return Book(symbol=s, bars=P, signals=SIG[s],
                costs=SymbolCosts.from_spec(cat.get(s)), mark=mark, tradable=tr)


def arm_trades(s: str) -> pd.DataFrame:
    res = run_portfolio({s: book(s)}, params_for(SPEC, 5, HOLD),
                        RiskGates.off(), initial_capital=10_000.0,
                        funding={s: LOAD[s]["funding"]})
    d = pd.DataFrame([dataclasses.asdict(t) for t in res.trades])
    if len(d):
        d["symbol"] = s
        d["blk"] = np.searchsorted(BE[s][1:-1], d.entry_time, side="right")
    return d


def account(universe: list[str]) -> tuple[float, float, int]:
    """One shared account under the live gates: (return %, max DD %, trades)."""
    res = run_portfolio({s: book(s) for s in universe},
                        params_for(SPEC, 5, HOLD), LIVE_GATES,
                        initial_capital=10_000.0,
                        funding={s: LOAD[s]["funding"] for s in universe})
    eq = np.asarray(res.equity_curve, dtype=float)
    ret = eq[-1] / eq[0] - 1.0
    dd = float(((np.maximum.accumulate(eq) - eq) / np.maximum.accumulate(eq)).max())
    return 100 * ret, 100 * dd, len(res.trades)


T = {s: arm_trades(s) for s in SYMBOLS}
rng = np.random.default_rng(11)

print(f"== manual_scalp_both_t3 per symbol  (ungated, 4xATR, 3R, hold {HOLD}h; "
      f"spec {SPEC.config_hash[:12]})")
print(f"  {'symbol':<9}{'net':>8}{'blocks':>8}{'n':>6}{'win':>6}{'DD':>8}"
      f"{'top3':>6}{'boot 95%':>19}{'/wk':>6}{'1R bps':>8}{'cost/R':>8}"
      f"  target/stop/time")
rows = {}
for s in SYMBOLS:
    d = T[s]
    if not len(d):
        print(f"  {s:<9}  no trades -- see the bar-density note in the verdict")
        rows[s] = dict(net=np.nan, pos=0, n=0)
        continue
    g = d.groupby("blk").r_multiple.mean()
    days = (FR[s][0]["time"].max() - FR[s][0]["time"].min()) / 86400
    tot = d.r_multiple.sum()
    top3 = d.r_multiple.nlargest(3).sum() / tot if tot > 0 else np.nan
    dd = float((d.r_multiple.cumsum().cummax() - d.r_multiple.cumsum()).max())
    bs = np.array([rng.choice(d.r_multiple.to_numpy(), len(d)).mean()
                   for _ in range(3000)])
    ex = d.exit_reason.value_counts()
    onebps = (d.risk_per_unit / d.entry_price * 1e4).median()
    rows[s] = dict(net=d.r_multiple.mean(), pos=int((g > 0).sum()), n=len(d))
    t3 = f"{top3:>5.0%}" if np.isfinite(top3) else "   --"
    print(f"  {s:<9}{d.r_multiple.mean():>+8.3f}{rows[s]['pos']:>5}/4 "
          f"{len(d):>5}{(d.r_multiple > 0).mean():>6.0%}{dd:>7.1f}R{t3}"
          f"  [{np.percentile(bs, 2.5):+.3f},{np.percentile(bs, 97.5):+.3f}]"
          f"{len(d) / (days / 7):>6.1f}{onebps:>8.0f}{d.cost_per_r.mean():>8.3f}"
          f"  {ex.get('target', 0)}/{ex.get('stop', 0)}/{ex.get('max_hold', 0)}")

print("\n== the two universes pooled, and as one account under the live gates")
for name, uni in (("ETH/SOL/BEAT", M3), ("thin 3 (deployed basis)", THIN)):
    p = pd.concat([T[s] for s in uni if len(T[s])], ignore_index=True)
    ret, dd, n = account(uni)
    print(f"  {name:<24} pooled net {p.r_multiple.mean():+.3f} over {len(p)}"
          f"  | one account: {ret:+.2f}%  maxDD {dd:.2f}%  {n} trades")

acct_m3, acct_thin = account(M3), account(THIN)

print("\n== verdict, by the criteria in the docstring")
verdicts = {}
for s in ("ETHUSD", "SOLUSD"):
    r = rows[s]
    ok = np.isfinite(r["net"]) and r["net"] > 0 and r["pos"] >= 3 and r["n"] >= 40
    why = (f"net {r['net']:+.3f}, {r['pos']}/4 blocks, n={r['n']}"
           if r["n"] else "no trades")
    verdicts[s] = ok
    print(f"  {s}: {'PASS' if ok else 'FAIL'} ({why})")
dd_ok = acct_m3[1] <= 1.5 * acct_thin[1]
print(f"  drawdown: m3 {acct_m3[1]:.2f}% vs thin-3 {acct_thin[1]:.2f}% "
      f"-- {'within' if dd_ok else 'EXCEEDS'} the 1.5x bound")
if all(verdicts.values()) and dd_ok:
    print("  => the criteria are met. That earns a deployment CONVERSATION -- "
          "as a second stack, never by editing bot_symbols under the running "
          "arm -- and it would be the first evidence anywhere that this entry "
          "survives major-symbol costs. Check top3 and the out-of-block "
          "premium before believing it.")
else:
    print("  => DO NOT DEPLOY. This is the pre-registered expectation, not a "
          "surprise: the cost law priced the majors at 0.10-0.12R per trade "
          "before this script ran.")
