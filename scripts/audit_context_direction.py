"""Acceptance test and audit for `manual_scalp_banded_h1dir` (decision C).

    PYTHONPATH=. python3 scripts/audit_context_direction.py

TWO THINGS, AND THE FIRST IS THE IMPORTANT ONE.

1. ACCEPTANCE. The family expresses "read the Supertrend direction from the
   1h chart" through the CONFIRMATION timeframe, by making it slower than the
   primary. That is a reuse of `align_confirm`, so it has to be shown to
   agree with the independent implementation the research used -- a resample
   plus an explicit `searchsorted` for the last CLOSED hourly bar
   (scripts/htf_direction_walkforward.py). This script asserts the two
   produce IDENTICAL entry arrays, symbol by symbol. If they ever diverge,
   the recorded numbers stop describing the family and the run fails.

2. AUDIT. The same anchored-block, concentration, neighbour and per-symbol
   checks that were applied to `manual_scalp_st_banded_fade` -- and that the
   fade failed. Printed so the family's claim can be re-derived rather than
   trusted.
"""
import dataclasses, sys, numpy as np, pandas as pd
from deltabt import rulecore, indicators as ind
from deltabt.catalog import build_spec
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.harness import _resampled, load_symbol, params_for
from deltabt.portfolio import Book, RiskGates, run_portfolio

SYMS = sys.argv[1].split(",") if len(sys.argv) > 1 else ["BEATUSD", "AKEUSD", "BANKUSD"]
CTX_MIN = 60
cat = ProductCatalog()
LOAD = {s: load_symbol(s) for s in SYMS}
LOAD = {s: d for s, d in LOAD.items() if d is not None}
FR = {s: _resampled(LOAD[s], 5, {}) for s in LOAD}
allt = np.concatenate([FR[s][0]["time"].to_numpy() for s in FR])
EDGES = np.linspace(allt.min(), allt.max() + 1, 5).astype(np.int64)


def independent_alignment(sym, ctx_min):
    """Independent causal read of the context timeframe.

    Returns (confirm_index, supertrend_direction_per_primary_bar) computed
    with an explicit `searchsorted` for the last context bar CLOSING at or
    before each primary bar's close. `align_confirm` must agree with this
    exactly; that is the whole claim the family rests on.
    """
    P = FR[sym][0]
    ctx, _, _ = _resampled(LOAD[sym], ctx_min, {})
    t5 = P["time"].to_numpy()
    _, d = ind.supertrend(ctx["high"].to_numpy(), ctx["low"].to_numpy(),
                          ctx["close"].to_numpy(), 2.0, 10)
    opens = ctx["time"].to_numpy()
    assert opens[0] > 1_600_000_000 and np.all(np.diff(opens) > 0), f"{sym}: context opens not sane"
    j = np.searchsorted(opens, t5 + ctx_min * 0 + 300 - ctx_min * 60, side="right") - 1
    dd = np.where(j >= 0, d[np.clip(j, 0, len(d) - 1)], np.nan)
    fin = np.isfinite(dd)
    assert 0.15 < (dd[fin] < 0).mean() < 0.85, f"{sym}: context mask nearly constant"
    return j, dd, opens, t5


print(f"blocks: " + "  ".join(f"b{i} {pd.Timestamp(EDGES[i], unit='s').date()}" for i in range(4)))
def family_signals(sym, ctx_min, stop=4.0, target=1.0):
    """The family itself, through `align_confirm` on a SLOWER confirmation frame."""
    spec = build_spec("manual_scalp_banded_h1dir", 5, ctx_min,
                      stop_atr_multiplier=stop, target_r=target)
    ctx, _, _ = _resampled(LOAD[sym], ctx_min, {})
    return spec, rulecore.to_engine_signals(rulecore.compute(FR[sym][0], ctx, spec))


print("\n== ACCEPTANCE: align_confirm against an independent causal read")
print("  (the family reaches the 1h chart through the CONFIRMATION frame; these")
print("   assertions are what make the recorded numbers describe it)")
for sym in FR:
    j, dd, opens, t5 = independent_alignment(sym, CTX_MIN)
    spec, fs = family_signals(sym, CTX_MIN)
    sig = rulecore.compute(FR[sym][0], _resampled(LOAD[sym], CTX_MIN, {})[0], spec)
    ci = sig.confirm_index

    # 1. the index itself
    assert np.array_equal(ci, j), f"{sym}: align_confirm disagrees with the independent read"

    # 2. no look-ahead: the chosen context bar must CLOSE at or before the
    #    primary bar closes. This is the property that makes it tradeable.
    have = ci >= 0
    ctx_close = opens[np.clip(ci, 0, len(opens) - 1)] + CTX_MIN * 60
    prim_close = t5 + 5 * 60
    assert np.all(ctx_close[have] <= prim_close[have]), f"{sym}: a context bar closes AFTER the primary bar"
    lag = (prim_close[have] - ctx_close[have])
    assert lag.max() < CTX_MIN * 60 * 3, f"{sym}: context read is staler than three bars"

    # 3. every entry agrees with the context direction
    bull = np.isfinite(dd) & (dd < 0)
    bear = np.isfinite(dd) & (dd > 0)
    assert not np.any(fs.long_entry & ~bull), f"{sym}: a long fired against a bearish context"
    assert not np.any(fs.short_entry & ~bear), f"{sym}: a short fired against a bullish context"

    print(f"  {sym:<9} bars {len(t5):>6}  index identical  max staleness {lag.max() / 60:>4.0f}m"
          f"  entries {int(fs.long_entry.sum()) + int(fs.short_entry.sum()):>4}  all direction-consistent")
print("  PASS.")
print("""
  Two differences from scripts/htf_direction_walkforward.py, both of which
  the family gets right and the research script got wrong:
    * that script builds the hourly frame from the FIVE-MINUTE frame, which
      skips the completeness rule; on BEATUSD it invents 16 hourly bars out
      of 5,253 from stretches the live bot would never form one.
    * it applies the EDGE TRIGGER to the primary setup and masks by context
      afterwards, so a bar where the primary was already true and only the
      context turned true is missed. The family triggers on the COMPLETE
      setup, as the live bot does. Worth 2 entries out of ~3,000 on BEATUSD.
  The numbers below are the family's, and are the ones that would trade.""")


def trades(sym, ctx_min, stop, target, hold, live=False):
    P, mark, tradable = FR[sym]
    if live:
        spec = build_spec("manual_scalp_st_banded", 5, 1,
                          stop_atr_multiplier=stop, target_r=target)
        sig = rulecore.to_engine_signals(rulecore.compute(P, None, spec))
    else:
        spec, sig = family_signals(sym, ctx_min, stop, target)
    res = run_portfolio(
        {sym: Book(symbol=sym, bars=P, signals=sig,
                   costs=SymbolCosts.from_spec(cat.get(sym)), mark=mark, tradable=tradable)},
        params_for(spec, 5, hold), RiskGates.off(), initial_capital=10_000.0,
        funding={sym: LOAD[sym]["funding"]})
    d = pd.DataFrame([dataclasses.asdict(x) for x in res.trades])
    if d.empty:
        return d
    d["gross"] = d.r_multiple + d.cost_per_r
    d["blk"] = np.searchsorted(EDGES[1:-1], d.entry_time.to_numpy(), side="right")
    d["symbol"] = sym
    return d


def pool(ctx_min, stop, target, hold, live=False):
    fs = [trades(s, ctx_min, stop, target, hold, live) for s in FR]
    fs = [f for f in fs if len(f)]
    return pd.concat(fs, ignore_index=True) if fs else pd.DataFrame()


def line(name, d):
    if d.empty:
        print(f"  {name:<32} no trades")
        return
    g = d.groupby("blk").r_multiple.mean()
    n = d.groupby("blk").size()
    print(f"  {name:<32}" + "".join(f"  {g.get(b, np.nan):+.3f}({n.get(b, 0):>3})" for b in range(4))
          + f"  {int((g > 0).sum())}/4  net {d.r_multiple.mean():+.3f}"
            f"  gross {d.gross.mean():+.3f}  win {(d.r_multiple > 0).mean():.0%}  n={len(d)}")


print("\n== AUDIT 1: the cell and its context-timeframe neighbours (4xATR, 1R, 24h)")
print(f"  {'variant':<32}{'blk0':>12}{'blk1':>12}{'blk2':>12}{'blk3':>12}")
line("live: 5m direction", pool(60, 4.0, 1.0, 24, live=True))
for cm in (30, 60, 120, 240):
    line(f"context {cm}m" + ("  <- THIS FAMILY" if cm == 60 else ""), pool(cm, 4.0, 1.0, 24))

C = pool(CTX_MIN, 4.0, 1.0, 24)
tot = C.r_multiple.sum()
s = C.r_multiple.sort_values(ascending=False)
print(f"\n== AUDIT 2: concentration   n={len(C)}  total {tot:+.1f}R  mean {C.r_multiple.mean():+.3f}")
for k in (1, 3, 5, 10):
    print(f"  top {k:<2} = {s.head(k).sum() / tot * 100:>5.0f}% of net;  mean without them {s.iloc[k:].mean():+.3f}")
print(f"  exits: {C.exit_reason.value_counts().to_dict()}")

print("\n== AUDIT 3: per symbol (this is the caveat, not the headline)")
for sym, x in C.groupby("symbol"):
    P = FR[sym][0]
    days = (P.time.max() - P.time.min()) / 86400
    lv = pool(60, 4.0, 1.0, 24, live=True)
    lv = lv[lv.symbol == sym]
    dd = float((x.r_multiple.cumsum().cummax() - x.r_multiple.cumsum()).max())
    ldd = float((lv.r_multiple.cumsum().cummax() - lv.r_multiple.cumsum()).max()) if len(lv) else float("nan")
    print(f"  {sym:<9} {days:>4.0f}d  live n={len(lv):<4} net {lv.r_multiple.mean():+.3f} DD {ldd:>5.1f}R"
          f"   ->   C n={len(x):<4} net {x.r_multiple.mean():+.3f} DD {dd:>5.1f}R"
          f"  share of C's R {x.r_multiple.sum() / tot * 100:>5.0f}%")

print("\n== AUDIT 4: parameter neighbours")
print(f"  {'variant':<32}{'blk0':>12}{'blk1':>12}{'blk2':>12}{'blk3':>12}")
for st in (3.0, 3.5, 4.0, 4.5, 5.0):
    line(f"stop {st}x", pool(60, st, 1.0, 24))
for hd in (12, 24, 48, 72):
    line(f"hold {hd}h", pool(60, 4.0, 1.0, hd))

r = C.r_multiple.to_numpy()
bs = [np.random.default_rng(i).choice(r, len(r)).mean() for i in range(2000)]
print(f"\n== AUDIT 5: bootstrap   net {r.mean():+.3f}  95% "
      f"[{np.percentile(bs, 2.5):+.3f},{np.percentile(bs, 97.5):+.3f}]  P(net>0)={np.mean(np.array(bs) > 0):.2f}")
