"""Which symbols suit a $250 prod dry run of manual_scalp_both_t3 @5m? (2026-10-05)

    # per symbol (needs data/candles/<SYMBOL>/, fetched via deltabt.data.store.CandleStore):
    PYTHONPATH=. .venv/bin/python scripts/symbol_screen.py one SOLUSD OUT >> OUT/screen.jsonl
    # then:
    PYTHONPATH=. .venv/bin/python scripts/symbol_screen.py summary OUT
    (OUT = out/sweep/five_min_arm_lab/symbol_screen; it holds tickers_<date>.json,
    the live turnover/spread snapshot from the public tickers endpoint.)

WHY. The owner asked whether the dry run's universe should change (SOLUSD,
BEATUSD were proposed). Each symbol is run through the SAME paired three-exit
lab as scripts/paired_exits_lab.py -- the dry run's own spec, engine, costs,
4xATR stop, 3R/72h, filler bars dropped -- on 2025-12-20..2026-09-30, and sized
as the pilot sizes: $250, 2%, one-contract floor at 3%, 3x leverage cap.

THE RULES WERE WRITTEN BEFORE THE RESULTS (see `RULES` below): a symbol is
judged on whether it can be MEASURED well (sizes, trades, is liquid) and on the
cost law (fees / stop width), not on its backtest P&L.

RESULT 2026-10-05, 32 symbols (the 27 most-traded crypto perps on Delta India
with enough history, plus the five already cached), 4,950 paired trades:
  * The entries earn NOTHING before fees: pooled gross -0.009R per trade. After
    fees: hold-to-3R -0.083R, ladder -0.085R, trail -0.082R per trade. Net
    positive on 8 / 5 / 5 of 32 symbols. Clustered by the day a trade closed
    (symbols share the market's days): t -2.2 / -5.4 / -5.7; by week -2.5 /
    -5.1 / -5.9. The trail and the ladder losing to fees is not noise.
  * BACKTEST P&L DOES NOT PERSIST: rank correlation of a symbol's first-half
    and second-half average R is -0.12 (hold-to-3R) and -0.08 (trail); the
    five best symbols of the first half averaged -0.077R in the second, the
    five worst -0.078R. Choosing symbols by past result is choosing noise.
  * The cost law holds: lower fee cost per R goes with a better net result
    (rank correlation +0.33 / +0.39). Fees are 0.11-0.13R on the majors
    (1.2-1.4% stops) and 0.02R on the thin wide-stop symbols.
  * MEASURABLE on $250 (sizes, liquid, >= 15 trades per 21 days): ETHUSD,
    XRPUSD, SOLUSD only. BTCUSD misses on activity. BEATUSD sizes and is
    active but 31% of its minutes have no trade and it turns over $0.3M a day.
    AKEUSD is one $325 contract: 46% of entries skipped, 34% a single contract.
  * No symbol passes the COST rule, because there is no gross edge to pay
    fees from.
"""
import contextlib, glob, io, json, math, pathlib, sys
import numpy as np, pandas as pd

RULES = """RULES (a symbol must pass all four to be a candidate for a $250 prod dry run):
  SIZE    the 2% budget buys a position: skipped <= 5% of entries and at most
          25% of entries are a single contract (one contract = coarse risk).
  LIQUID  it trades: <= 5% of minutes without a trade, 24h turnover >= $1M,
          and the live spread is <= 0.05R of the typical stop.
  ACTIVE  >= 15 trades per 21 days in the backtest (a 21-day run needs data).
  COST    fee cost per trade is below what these entries earn BEFORE fees,
          pooled over every screened symbol (the cost law: fees / stop width).
Backtest P&L is reported but is NOT a rule: see the persistence check."""


def one():
    sym, outdir = sys.argv[2], pathlib.Path(sys.argv[3])
    src = pathlib.Path("scripts/paired_exits_lab.py").read_text()
    src = src.replace('UNIVERSE = ["BEATUSD", "AKEUSD", "BANKUSD"]', f"UNIVERSE = [{sym!r}]")
    src = src[:src.index("key = lambda d:")]
    g = {"__name__": "lab", "__file__": str(pathlib.Path("scripts/paired_exits_lab.py").resolve())}
    raw = pd.read_parquet(f"data/candles/{sym}/ltp_1m.parquet")
    filler = float(((raw.volume == 0) & (raw.open == raw.close) & (raw.high == raw.low)).mean())
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(src, "lab_prefix", "exec"), g)
    except Exception as e:
        print(json.dumps({"symbol": sym, "error": repr(e)[:160]})); return
    res = g["res"]; key = ["symbol", "side", "entry_time"]
    common = set(map(tuple, res["baseline"][key].values))
    for k in ("ladder", "trail"): common &= set(map(tuple, res[k][key].values))
    R = {k: v[v[key].apply(tuple, axis=1).isin(common)].sort_values("entry_time").reset_index(drop=True) for k, v in res.items()}
    b = R["baseline"]
    if len(b) < 10:
        print(json.dumps({"symbol": sym, "n": len(b), "error": "too few trades"})); return
    cv = float(g["fb"][sym].costs.contract_value)
    EQ, RISK, CAP, LEV = 250.0, 0.02, 0.03, 3.0
    per = b.risk_per_unit * cv; notional = b.entry_price * cv
    n = np.minimum((EQ * RISK / per).astype(int), (EQ * LEV / notional).astype(int))
    floor_ok = (n <= 0) & (per <= EQ * CAP) & (notional <= EQ * LEV)
    contracts = np.where(n > 0, n, np.where(floor_ok, 1, 0)); usd_r = contracts * per
    days = (b.entry_time.max() - b.entry_time.min()) / 86400
    half = b.entry_time.median()
    def m(d): return float(d.r_multiple.mean()) if len(d) else None
    def sd(d): return float(d.r_multiple.std()) if len(d) > 1 else None
    out = {"symbol": sym, "n": int(len(b)), "days": round(float(days)), "per21": round(len(b) / days * 21, 1),
           "first": str(pd.to_datetime(int(b.entry_time.min()), unit="s").date()),
           "filler": round(filler, 3), "cv": cv, "price": float(b.entry_price.iloc[-1]),
           "stop_pct": round(float(100 * (b.risk_per_unit / b.entry_price).median()), 2),
           "cost_r": round(float(b.cost_per_r.median()), 3),
           "contracts_med": float(np.median(contracts)), "skipped": round(float((contracts == 0).mean()), 3),
           "one_contract": round(float((contracts == 1).mean()), 3),
           "risk_usd_med": round(float(np.median(usd_r[contracts > 0])), 2) if (contracts > 0).any() else None}
    # THE LAST 60 DAYS: stop width, and so fee cost and contract count, drift with
    # volatility; a forward run meets the recent values, not the nine-month ones.
    rec = (b.entry_time >= b.entry_time.max() - 60 * 86400).to_numpy()
    if rec.sum() >= 8:
        out["recent"] = {"n": int(rec.sum()), "per21": round(float(rec.sum()) / 60 * 21, 1),
                         "stop_pct": round(float(100 * (b.risk_per_unit / b.entry_price)[rec].median()), 2),
                         "cost_r": round(float(b.cost_per_r[rec].median()), 3),
                         "contracts_med": float(np.median(contracts[rec])),
                         "skipped": round(float((contracts[rec] == 0).mean()), 3),
                         "one_contract": round(float((contracts[rec] == 1).mean()), 3)}
    for k in ("baseline", "ladder", "trail"):
        d = R[k]
        out[k] = {"mean": round(m(d), 3), "sd": round(sd(d), 2), "h1": round(m(d[d.entry_time <= half]), 3),
                  "h2": round(m(d[d.entry_time > half]), 3), "win": round(float((d.r_multiple > 0).mean()), 2),
                  "gross": round(m(d) + float(d.cost_per_r.mean()), 3),
                  "usd": round(float((d.r_multiple.to_numpy() * usd_r).sum()), 0)}
    (outdir / "trades").mkdir(parents=True, exist_ok=True)
    tr = b[["symbol", "side", "entry_time", "exit_time", "cost_per_r"]].copy()
    for k in ("baseline", "ladder", "trail"):
        tr[f"r_{k}"] = R[k].r_multiple.to_numpy(); tr[f"exit_{k}"] = R[k].exit_time.to_numpy()
    tr.to_csv(outdir / "trades" / f"{sym}.csv", index=False)
    print(json.dumps(out))


def summary():
    print(RULES + "\n")
    S = sys.argv[2]
    rows = [json.loads(l) for l in open(f"{S}/screen.jsonl") if l.strip()]
    tick = {t["symbol"]: t for t in json.load(open(sorted(glob.glob(f"{S}/tickers_*.json"))[-1]))}
    bad = [r for r in rows if "error" in r]; rows = [r for r in rows if "error" not in r]
    N = sum(r["n"] for r in rows)
    pooled = {k: sum(r[k]["mean"] * r["n"] for r in rows) / N for k in ("baseline", "ladder", "trail")}
    gross = {k: sum(r[k]["gross"] * r["n"] for r in rows) / N for k in ("baseline", "ladder", "trail")}
    print(f"{len(rows)} symbols, {N} paired trades. Pooled per trade: " + "; ".join(
        f"{k} net {pooled[k]:+.3f}R gross {gross[k]:+.3f}R" for k in pooled))
    G = gross["baseline"]
    print(f"COST rule threshold = pooled gross edge of the entries (hold to 3R) = {G:+.3f}R per trade\n")
    for r in rows:
        t = tick.get(r["symbol"], {})
        r["turnover"] = t.get("turnover") or 0; sp = t.get("spread_bps")
        r["spread_r"] = None if sp is None else (sp / 1e4) / (r["stop_pct"] / 100)
        r["SIZE"] = r["skipped"] <= 0.05 and r["one_contract"] <= 0.25
        r["LIQUID"] = r["filler"] <= 0.05 and r["turnover"] >= 1e6 and (r["spread_r"] is not None and r["spread_r"] <= 0.05)
        r["ACTIVE"] = r["per21"] >= 15
        r["COST"] = r["cost_r"] < G
        r["passes"] = sum(r[k] for k in ("SIZE", "LIQUID", "ACTIVE", "COST"))
    rows.sort(key=lambda r: (-r["passes"], r["cost_r"]))
    f = lambda ok: "ok" if ok else "NO"
    print(f"{'symbol':11s}{'trades':>7s}{'/21d':>6s}{'stop%':>7s}{'fee R':>7s}{'1 ctr $':>9s}{'ctrs':>5s}{'skip%':>6s}{'1ctr%':>6s}{'idle%':>6s}{'turn $M':>9s}{'sprd R':>7s}"
          f"  {'SIZE':4s} {'LIQ':3s} {'ACT':3s} {'COST':4s}  {'hold3R':>7s}{'trail':>7s}{'ladder':>7s}  {'trail $':>8s}")
    for r in rows:
        print(f"{r['symbol']:11s}{r['n']:7d}{r['per21']:6.0f}{r['stop_pct']:7.2f}{r['cost_r']:7.3f}{r['price']*r['cv']:9.2f}{r['contracts_med']:5.0f}"
              f"{100*r['skipped']:6.0f}{100*r['one_contract']:6.0f}{100*r['filler']:6.0f}{r['turnover']/1e6:9.1f}"
              f"{(r['spread_r'] if r['spread_r'] is not None else float('nan')):7.3f}"
              f"  {f(r['SIZE']):4s} {f(r['LIQUID']):3s} {f(r['ACTIVE']):3s} {f(r['COST']):4s}"
              f"  {r['baseline']['mean']:+7.3f}{r['trail']['mean']:+7.3f}{r['ladder']['mean']:+7.3f}  {r['trail']['usd']:+8.0f}")
    if bad: print("\nnot screened:", [(b["symbol"], b.get("error")) for b in bad])
    # persistence: does a symbol's first-half result predict its second half?
    def spearman(xs, ys):
        def rank(v):
            o = sorted(range(len(v)), key=lambda i: v[i]); r = [0] * len(v)
            for k, i in enumerate(o): r[i] = k
            return r
        rx, ry = rank(xs), rank(ys); n = len(xs)
        if n < 3: return float("nan")
        d2 = sum((a - b) ** 2 for a, b in zip(rx, ry)); return 1 - 6 * d2 / (n * (n * n - 1))
    big = [r for r in rows if r["n"] >= 100]
    print(f"\nPERSISTENCE over {len(big)} symbols with >= 100 trades (rank correlation of first-half vs second-half average R):")
    for k in ("baseline", "trail"):
        rho = spearman([r[k]["h1"] for r in big], [r[k]["h2"] for r in big])
        top = sorted(big, key=lambda r: -r[k]["h1"])[:5]; bot = sorted(big, key=lambda r: r[k]["h1"])[:5]
        print(f"  {k:9s} rho {rho:+.2f}   best 5 in first half -> second half {sum(r[k]['h2'] for r in top)/len(top):+.3f}R"
              f"   worst 5 in first half -> second half {sum(r[k]['h2'] for r in bot)/len(bot):+.3f}R")
    rho_c = spearman([-r["cost_r"] for r in big], [r["baseline"]["mean"] for r in big])
    print(f"  cost law: rank correlation of (low fee cost) with net R per trade, hold to 3R: {rho_c:+.2f}; with trail: "
          f"{spearman([-r['cost_r'] for r in big], [r['trail']['mean'] for r in big]):+.2f}")

    # IS THE POOLED LOSS REAL? Trades on different symbols share the market's days,
    # so a per-trade t overstates it. Cluster by the day (and week) the trade closed.
    T = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(f"{S}/trades/*.csv"))])
    print(f"\nPOOLED RESULT, {len(T)} trades on {T.symbol.nunique()} symbols:")
    for k in ("baseline", "ladder", "trail"):
        r = T[f"r_{k}"]; per_trade_t = r.mean() / (r.std() / math.sqrt(len(r)))
        line = f"  {k:9s} {r.mean():+.3f}R per trade (gross {(r + T.cost_per_r).mean():+.3f}R, fees {T.cost_per_r.mean():.3f}R)  per-trade t {per_trade_t:+.1f}"
        for name, width in (("day", 86400), ("week", 7 * 86400)):
            g = r.groupby(T[f"exit_{k}"] // width).sum()
            line += f"  by {name}: {len(g)} {name}s, t {g.mean() / (g.std() / math.sqrt(len(g))):+.1f}"
        print(line + f"  symbols net positive {sum(1 for x in rows if x[k]['mean'] > 0)} of {len(rows)}")
    print("\nLAST 60 DAYS against the nine months (stop width drifts with volatility):")
    print(f"{'symbol':11s}{'stop% 9m':>9s}{'fee R':>7s}{'/21d':>6s}{'ctrs':>6s} | {'stop% 60d':>9s}{'fee R':>7s}{'/21d':>6s}{'ctrs':>6s}{'skip%':>6s}{'1ctr%':>6s}")
    for r in rows:
        c = r.get("recent")
        print(f"{r['symbol']:11s}{r['stop_pct']:9.2f}{r['cost_r']:7.3f}{r['per21']:6.0f}{r['contracts_med']:6.0f} | " + (
            f"{c['stop_pct']:9.2f}{c['cost_r']:7.3f}{c['per21']:6.0f}{c['contracts_med']:6.0f}{100*c['skipped']:6.0f}{100*c['one_contract']:6.0f}"
            if c else "  fewer than 8 trades in the last 60 days"))


if __name__ == "__main__":
    {"one": one, "summary": summary}[sys.argv[1]]()
