"""Anchored walk-forward of a higher-timeframe direction source, CAUSAL.

Reproduces the numbers recorded beside manual_scalp_st_banded in
deltabt/catalog.py (2026-09-03). Two earlier attempts at this analysis were
wrong in opposite directions -- one read the HTF bar containing each 5m bar
(look-ahead), the other took bar opens via DatetimeIndex.asi8 // 1e9, which
returns 1 for every bar on a tz-aware index in this pandas build and made
the mask constant. This version takes opens as int(ts.timestamp()) and
ASSERTS them monotone, and asserts the mask is 20-80% bull, before any
number is read. Any engine implementation of a context timeframe must
reproduce these figures.

    PYTHONPATH=. python3 scripts/htf_direction_walkforward.py BEATUSD,AKEUSD,BANKUSD
"""
import sys, dataclasses, numpy as np, pandas as pd
from deltabt import rulecore, indicators as ind
from deltabt.catalog import build_spec
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.harness import _resampled, load_symbol, params_for
from deltabt.metrics import compute
from deltabt.portfolio import Book, RiskGates, run_portfolio
symbols = sys.argv[1].split(","); cat = ProductCatalog()
live = build_spec("manual_scalp_st_banded", 5, 1); noST = build_spec("manual_scalp_banded", 5, 1)
LOADED = {s: load_symbol(s) for s in symbols}
def htf_dir(primary, rule):
    t5 = primary["time"].to_numpy(); sec = int(pd.Timedelta(rule).total_seconds())
    idx = pd.to_datetime(t5, unit="s", utc=True)
    hh = primary.set_index(idx).resample(rule, label="left", closed="left").agg({"high":"max","low":"min","close":"last"}).dropna()
    _, d = ind.supertrend(hh["high"].to_numpy(), hh["low"].to_numpy(), hh["close"].to_numpy(), 2.0, 10)
    opens = np.array([int(ts.timestamp()) for ts in hh.index], dtype=np.int64)
    assert opens[0] > 1_600_000_000 and np.all(np.diff(opens) > 0), "HTF opens are not sane epoch seconds"
    j = np.searchsorted(opens, t5 + 300 - sec, side="right") - 1          # last CLOSED HTF bar
    dd = np.where(j >= 0, d[np.clip(j, 0, len(d)-1)], np.nan)
    b = np.isfinite(dd); assert 0.2 < (dd[b] < 0).mean() < 0.8, "HTF mask is nearly constant -- alignment broken"
    return dd
def books(spec, rule):
    b, f = {}, {}
    for sym, data in LOADED.items():
        if data is None: continue
        primary, mark, tradable = _resampled(data, 5, {})
        if len(primary) < spec.warmup_bars*3: continue
        sig = rulecore.to_engine_signals(rulecore.compute(primary, None, spec))
        if rule:
            d = htf_dir(primary, rule)
            sig = dataclasses.replace(sig, long_entry=sig.long_entry & np.isfinite(d) & (d<0), short_entry=sig.short_entry & np.isfinite(d) & (d>0))
        try: costs = SymbolCosts.from_spec(cat.get(sym))
        except (KeyError, LookupError): continue
        b[sym] = (primary, mark, tradable, sig, costs); f[sym] = data["funding"]
    return b, f
allt = np.concatenate([b[0]["time"].to_numpy() for b in books(live, None)[0].values()])
edges = np.linspace(allt.min(), allt.max()+1, 5).astype(np.int64)
V = [("live: 5m ST + %R", live, None),
     ("ADD  15m ST gate", live, "15min"), ("ADD  1h ST gate", live, "1h"), ("ADD  4h ST gate", live, "4h"),
     ("MOVE dir -> 15m", noST, "15min"), ("MOVE dir -> 1h", noST, "1h"), ("MOVE dir -> 4h", noST, "4h")]
print(f"  {'variant':<20}" + "".join(f"{'blk'+str(i):>16}" for i in range(4)) + "   +ve   full-window")
for name, spec, rule in V:
    bk, fund = books(spec, rule); params = params_for(spec, 5, 24); cells=[]; pos=0
    for i in range(4):
        lo, hi = int(edges[i]), int(edges[i+1]); bb={}
        for sym,(p,mk,tr,s,c) in bk.items():
            t=p["time"].to_numpy(); inb=(t>=lo)&(t<hi)
            bb[sym]=Book(symbol=sym,bars=p,signals=dataclasses.replace(s,long_entry=s.long_entry&inb,short_entry=s.short_entry&inb),costs=c,mark=mk,tradable=tr)
        m=dataclasses.asdict(compute(run_portfolio(bb,params,RiskGates.off(),initial_capital=10_000.0,funding=fund)))
        cells.append(f"{m['expectancy_r']:>+7.3f} n={m['trades']:<4}"); pos+=m['expectancy_r']>0
    bb={sym:Book(symbol=sym,bars=p,signals=s,costs=c,mark=mk,tradable=tr) for sym,(p,mk,tr,s,c) in bk.items()}
    m=dataclasses.asdict(compute(run_portfolio(bb,params,RiskGates.off(),initial_capital=10_000.0,funding=fund)))
    print(f"  {name:<20}" + "".join(f"{c:>16}" for c in cells) + f"   {pos}/4   {m['return_pct']:>+7.1f}%  {m['trades']:>5}t  win {m['win_rate']:.0%}  DD {m['max_drawdown_pct']:.1f}%")
