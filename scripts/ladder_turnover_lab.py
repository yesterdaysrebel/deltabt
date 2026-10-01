"""Does the ladder's edge come from entries created in freed slots? Pre-registered.

    PYTHONPATH=. .venv/bin/python scripts/ladder_turnover_lab.py

WHY THIS EXISTS. The paper ladder arm (MANUAL_SCALP_BOTH_T3_LADDER, 09-16 ->
10-01) beat the baseline on money (+$266 vs -$81) while LOSING on the trades
both arms shared (-0.12R/trade, 7 of 8 shared 3R winners cut). Its whole lead
sat in 93 trades the baseline never took, because a laddered position closes
in a median 3.2h instead of 6.0h and frees the per-symbol slot. Within those,
one row carried everything: entries within 6h of a PROMOTED-stop exit on the
OPPOSITE side, n=45, +0.25R each. That row was found by looking at the data
afterwards, and the 09-14 backtest said entries created by freed slots run at
-0.26R. One of those is wrong, or the live row is noise. This settles which,
on CPU, before any paper time is spent on it.

THE QUESTION, WRITTEN BEFORE ANY NUMBER WAS READ (2026-10-01):
    Over the archive EXCLUDING the live window (everything before
    2026-09-16 00:00Z), on the paper universe (BEATUSD, AKEUSD, BANKUSD,
    WIFUSD), one shared $10k account, one position per symbol, four slots,
    gates off, 72h hold, stop_fill="ltp_close" with 1m fill series:
    what is the mean net R of ladder entries that the baseline did NOT take
    at the same bar, split by what preceded them on that symbol?

DECISION RULE:
    opposite-side entries within 6h of a promoted exit
        >= +0.15R on >= 150 trades  -> the live row has support; a fresh,
                                       pre-registered paper sample is justified
        <= 0                        -> the live 45 are noise; option 3 closes
        in between                  -> undecided, say so
    Secondary, reported but not deciding: ladder vs baseline total R with
    slots (the 09-14 question), the paired component on shared entries, the
    created-entry mean overall, per symbol and per anchored quarter.

THE LIVE WINDOW IS A REPRODUCTION CHECK, NOT EVIDENCE. The same engine run
over 2026-09-16 06:25Z -> 2026-10-01 18:00Z is compared trade by trade with
the paper arms' own records. If the engine does not take the same entries at
the same bars and exit them the same way, the ladder implementation is wrong
and nothing in the out-of-sample section counts.

CAVEAT KNOWN IN ADVANCE: AKEUSD and BANKUSD list from 2026-07-22, so the
out-of-sample set leans on BEATUSD and WIFUSD. The per-symbol split is printed
so that is visible rather than averaged away.

RESULT 2026-10-01 (out/sweep/five_min_arm_lab/ladder_turnover.txt; the first
run, with the archive's filler bars kept, is ladder_turnover_fillerkept.txt)

    THE DECIDING ROW IS <= 0 IN BOTH RUNS. OPTION 3 CLOSES.
        filler dropped (live-equivalent bars):  -0.074R  n=312  t=-1.36
        filler kept (archive as-is):            -0.029R  n=345  t=-0.57
    Negative on every symbol and in 3 of 4 anchored quarters (best +0.03).
    Every other created-entry row is <= 0 as well: all created entries
    -0.066R on 1,016 (t=-2.23). The live +0.25R/45 was a realised-path fluke.

    SECONDARY. Ladder vs baseline with four slots, before 09-16: baseline
    +11.98R on 406 trades, ladder -48.44R on 1,233 (net -$2,234, $1,650 of it
    fees). Paired on 217 shared entries -0.133R/trade (t=-1.34; 52 baseline
    targets became promoted stops). The 09-14 finding stands.

    REPRODUCTION, WHICH FAILED FIRST AND THEN PASSED FOR THE RIGHT REASONS.
    Entries did not match at the bar (8 of 51 baseline, 25 of 127 ladder) and
    the engine fired 2.4-3.7x the bot's signals on BEAT/BANK/WIF. Cause: the
    archive's filler bars (see DROP_FLAT). Not the cause: ws-vs-REST close
    differences (9% of 1m closes differ by ~0.04%; perturbing that keeps 96% of
    signal bars). With filler dropped the engine fires the bot's bar count and
    hits 97% / 97% / 93% of its bars (BEAT/AKE/BANK; WIFUSD 69%, open). Exits
    on identical entries reproduce: all 20 matched ladder entries exited the
    same way, promotions 15 = 15; and forcing the paper arm's own 127 entries
    through the engine gave 74/76 exit types, 70/76 promotion decisions,
    median R difference 0.000 (sd 0.36). Trade LISTS still diverge after one
    differing bar because the rule fires ~80x/day per symbol on one slot --
    "same entries at the same bars" is not an attainable standard here; the
    two above are.

    Also recorded: the baseline itself on the live-equivalent bar set is
    +0.030R/trade (t=+0.33) on this window; with filler +0.038 (t=+0.50).
    Different window and universe from the catalog's +65.3R/342, so not a
    like-for-like retraction, but the arm's edge does not survive here either.
"""
from __future__ import annotations

import dataclasses
import json
import math
import statistics as st
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from deltabt import rulecore
from deltabt.catalog import build_spec
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.harness import _resampled, load_symbol, params_for
from deltabt.portfolio import Book, RiskGates, run_portfolio
from deltabt.data.quality import tradable_mask

UNIVERSE = ["BEATUSD", "AKEUSD", "BANKUSD", "WIFUSD"]
HOLD_H = 72
LIVE_START = int(pd.Timestamp("2026-09-16T06:25:00Z").timestamp())
LIVE_END = int(pd.Timestamp("2026-10-01T18:00:00Z").timestamp())
PREREG_END = int(pd.Timestamp("2026-09-16T00:00:00Z").timestamp())
WARMUP_S = 3 * 86_400
PAPER_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else None   # dir holding rows_ladder.json / rows_atr.json
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("out/sweep/five_min_arm_lab")
#: DROP THE EXCHANGE'S FILLER BARS. Delta's REST history returns a flat,
#: zero-volume bar for every minute nobody traded; the live candle builder only
#: has minutes that traded (REST bars never overwrite its own, see
#: ingest_backfill). resample_complete counts minutes, so on the archive every
#: 5m bucket is "complete" while the bot keeps 29% (BEATUSD), 10% (BANKUSD) and
#: 8% (WIFUSD) of them -- 49-80% of those symbols' minutes are filler. Measured
#: 2026-10-01 against the ladder bot's own strategy_signals over 09-16..10-01:
#: with filler the engine fires 2.4-3.7x the bot's signals on the thin three;
#: without it the counts match and 93-97% of the bot's bars are hit (WIFUSD
#: 69%, unexplained). Decided AFTER the reproduction check failed; the decision
#: rule above is unchanged and both runs are recorded in the RESULT block.
DROP_FLAT = (sys.argv[3] if len(sys.argv) > 3 else "drop") == "drop"

cat = ProductCatalog()
RAW = {s: load_symbol(s) for s in UNIVERSE}
RAW = {s: d for s, d in RAW.items() if d is not None}


def ts(e) -> str:
    return pd.Timestamp(int(e), unit="s").strftime("%m-%d %H:%M")


def window(d: dict, start: int, end: int) -> dict:
    ltp = d["ltp"][(d["ltp"].time >= start) & (d["ltp"].time <= end)].reset_index(drop=True)
    if DROP_FLAT:
        flat = (ltp.volume == 0) & (ltp.open == ltp.close) & (ltp.high == ltp.low)
        ltp = ltp[~flat].reset_index(drop=True)
    mark = d["mark"][(d["mark"].time >= start) & (d["mark"].time <= end)].reset_index(drop=True)
    return dict(symbol=d["symbol"], ltp=ltp, mark=mark, funding=d["funding"],
                tradable=tradable_mask(ltp))


def books_for(data: dict[str, dict], spec, cache: dict) -> dict[str, Book]:
    out = {}
    for s, d in data.items():
        if len(d["ltp"]) < 2000:
            continue
        P, mk, tr = _resampled(d, 5, cache)
        confirm = _resampled(d, spec.confirm_minutes, cache)[0] if spec.confirm.enabled else None
        sig = rulecore.to_engine_signals(rulecore.compute(P, confirm, spec))
        out[s] = Book(symbol=s, bars=P, signals=sig, costs=SymbolCosts.from_spec(cat.get(s)),
                      mark=mk, tradable=tr, fill_ltp=d["ltp"], fill_mark=d["mark"])
    return out


def run(family: str, data: dict[str, dict], cache: dict) -> pd.DataFrame:
    spec = build_spec(family, 5)
    books = books_for(data, spec, cache)
    params = replace(params_for(spec, 5, HOLD_H), stop_fill="ltp_close")
    gates = replace(RiskGates.off(), max_open_positions=len(books))
    res = run_portfolio(books, params, gates, initial_capital=10_000.0,
                        funding={s: data[s]["funding"] for s in books})
    df = pd.DataFrame([dataclasses.asdict(t) for t in res.trades])
    df.attrs["rejects"] = res.rejects
    return df.sort_values("entry_time").reset_index(drop=True) if len(df) else df


def tstat(xs) -> float:
    xs = list(xs)
    if len(xs) < 2:
        return float("nan")
    sd = st.stdev(xs)
    return st.mean(xs) / (sd / math.sqrt(len(xs))) if sd > 0 else float("nan")


def maxdd_r(df: pd.DataFrame) -> float:
    e = p = m = 0.0
    for r in df.sort_values("exit_time").r_multiple:
        e += r; p = max(p, e); m = max(m, p - e)
    return m


def summary(name: str, df: pd.DataFrame) -> None:
    if not len(df):
        print(f"--- {name}: NO TRADES; rejects {df.attrs.get('rejects')}")
        return
    R = df.r_multiple
    ex = Counter(df.exit_reason)
    print(f"--- {name}: n={len(df)} sumR {R.sum():+.2f} meanR {R.mean():+.3f} sd {R.std():.2f} "
          f"t={tstat(R):+.2f} win {(R > 0).mean():.0%} | exits {dict(ex)} | promoted {int(df.stop_promoted.sum())} "
          f"| maxDD {maxdd_r(df):.1f}R | hold h median {df.bars_held.median() * 5 / 60:.1f} "
          f"| net ${df.pnl.sum():+.0f} fees ${df.fees.sum():.0f} funding ${df.funding.sum():+.0f}")
    for s, g in df.groupby("symbol"):
        print(f"      {s:8s} n={len(g):4d} sumR {g.r_multiple.sum():+7.2f} meanR {g.r_multiple.mean():+.3f} "
              f"win {(g.r_multiple > 0).mean():.0%} TP {(g.exit_reason == 'target').sum()}")
    print(f"      rejects {df.attrs.get('rejects')}")


def shared_key(df: pd.DataFrame) -> set:
    return set(zip(df.symbol, df.side, df.entry_time))


def classify(lad: pd.DataFrame, base: pd.DataFrame) -> pd.DataFrame:
    """Tag each ladder trade: created or shared, and what preceded it on the symbol."""
    shared = shared_key(base)
    lad = lad.copy()
    lad["shared"] = [k in shared for k in zip(lad.symbol, lad.side, lad.entry_time)]
    prev_kind, prev_gap_h, prev_same = [], [], []
    by_sym = {s: g.sort_values("exit_time") for s, g in lad.groupby("symbol")}
    for _, r in lad.iterrows():
        g = by_sym[r.symbol]
        p = g[g.exit_time <= r.entry_time]
        if not len(p):
            prev_kind.append("first"); prev_gap_h.append(np.nan); prev_same.append(None); continue
        p = p.iloc[-1]
        if p.exit_reason == "stop" and p.stop_promoted:
            kind = "promoted"
        elif p.exit_reason == "stop":
            kind = "plain_stop"
        else:
            kind = p.exit_reason
        prev_kind.append(kind)
        prev_gap_h.append((r.entry_time - p.exit_time) / 3600)
        prev_same.append(bool(p.side == r.side))
    lad["prev_kind"] = prev_kind
    lad["prev_gap_h"] = prev_gap_h
    lad["prev_same_side"] = prev_same
    return lad


def row(label: str, g: pd.DataFrame) -> None:
    if not len(g):
        print(f"  {label:58s} n=  0"); return
    R = g.r_multiple
    print(f"  {label:58s} n={len(R):4d} sumR {R.sum():+7.2f} meanR {R.mean():+.3f} t={tstat(R):+.2f} "
          f"win {(R > 0).mean():.0%} TP {(g.exit_reason == 'target').sum():3d}")


def created_breakdown(lad: pd.DataFrame, base: pd.DataFrame, title: str) -> pd.DataFrame | None:
    lad = classify(lad, base)
    created = lad[~lad.shared]
    print(f"\n{title}")
    print(f"  ladder trades {len(lad)}, shared with baseline at the same bar {int(lad.shared.sum())}, created {len(created)}")
    row("created, all", created)
    key = created[(created.prev_kind == "promoted") & (created.prev_gap_h <= 6) & (created.prev_same_side == False)]  # noqa: E712
    row("created, <=6h after PROMOTED exit, OPPOSITE side   [DECIDES]", key)
    row("created, <=6h after promoted exit, same side", created[(created.prev_kind == "promoted") & (created.prev_gap_h <= 6) & (created.prev_same_side == True)])  # noqa: E712
    row("created, <=6h after plain -1R stop, opposite side", created[(created.prev_kind == "plain_stop") & (created.prev_gap_h <= 6) & (created.prev_same_side == False)])  # noqa: E712
    row("created, <=6h after plain -1R stop, same side", created[(created.prev_kind == "plain_stop") & (created.prev_gap_h <= 6) & (created.prev_same_side == True)])  # noqa: E712
    row("created, >6h after any exit or after target/max_hold", created[~(((created.prev_kind.isin(["promoted", "plain_stop"])) & (created.prev_gap_h <= 6)))])
    print("  the deciding row, per symbol:")
    for s, g in key.groupby("symbol"):
        row(f"    {s}", g)
    if len(key):
        q = np.quantile(key.entry_time, [0.25, 0.5, 0.75])
        blk = np.searchsorted(q, key.entry_time, side="right")
        print("  the deciding row, per anchored quarter of its own span:")
        for b in range(4):
            row(f"    quarter {b}", key[blk == b])
    # the same row on ALL ladder trades (shared included), for the live comparison
    allkey = lad[(lad.prev_kind == "promoted") & (lad.prev_gap_h <= 6) & (lad.prev_same_side == False)]  # noqa: E712
    row("all ladder trades, <=6h after promoted exit, opposite side", allkey)
    return lad


def paired(base: pd.DataFrame, lad: pd.DataFrame, title: str) -> None:
    b = base.set_index(["symbol", "side", "entry_time"]); l = lad.set_index(["symbol", "side", "entry_time"])
    idx = b.index.intersection(l.index)
    if not len(idx):
        print(f"\n{title}: no shared entries"); return
    d = l.loc[idx].r_multiple.to_numpy() - b.loc[idx].r_multiple.to_numpy()
    print(f"\n{title}: shared entries {len(idx)} | baseline sumR {b.loc[idx].r_multiple.sum():+.2f} vs ladder {l.loc[idx].r_multiple.sum():+.2f} "
          f"| paired diff mean {d.mean():+.3f}R t={tstat(d):+.2f} | ladder better {(d > 0.05).sum()} same {(abs(d) <= 0.05).sum()} worse {(d < -0.05).sum()}")
    ct = Counter(zip(b.loc[idx].exit_reason, l.loc[idx].exit_reason))
    print(f"  (baseline exit, ladder exit): {dict(ct)}")


def reproduce(engine: pd.DataFrame, paper_path: Path, exp_prefix: str, title: str) -> None:
    rows = [r for r in json.load(open(paper_path)) if r["experiment_id"].startswith(exp_prefix)]
    rows.sort(key=lambda r: r["opened_at"])
    eng = engine[(engine.entry_time + 300 >= LIVE_START - 300) & (engine.entry_time + 300 <= LIVE_END)]
    used, pairs = set(), []
    for r in rows:
        side = 1 if r["side"] > 0 else -1
        cand = eng[(eng.symbol == r["symbol"]) & (eng.side == side) & (abs(eng.entry_time + 300 - r["opened_at"]) <= 150)]
        cand = cand[~cand.index.isin(used)]
        if len(cand):
            j = cand.index[0]; used.add(j); pairs.append((r, eng.loc[j]))
    print(f"\n{title}")
    print(f"  paper positions {len(rows)} (closed {sum(1 for r in rows if r['status'] == 'CLOSED')}) | engine trades in window {len(eng)} | entries matched within 150s: {len(pairs)}")
    closed = [(r, e) for r, e in pairs if r["status"] == "CLOSED"]
    if closed:
        same_exit = sum(1 for r, e in closed if {"STOP_LOSS": "stop", "TAKE_PROFIT": "target", "TIME_EXIT": "max_hold"}.get(r["exit_reason"]) == e.exit_reason)
        dR = [e.r_multiple - r["r_multiple"] for r, e in closed]
        dpx = [abs(e.entry_price - r["entry_price"]) / r["entry_price"] for r, e in closed]
        print(f"  of {len(closed)} matched+closed: same exit type {same_exit} | engine R - paper R: mean {st.mean(dR):+.3f} median {st.median(dR):+.3f} | entry price rel diff median {st.median(dpx):.4%}")
        print(f"  paper sumR on matched {sum(r['r_multiple'] for r, _ in closed):+.2f} vs engine {sum(e.r_multiple for _, e in closed):+.2f}")
        if "stop_promoted" in engine:
            pp = sum(1 for r, e in closed if r["exit_reason"] == "STOP_LOSS" and r["r_multiple"] > -0.3)
            ep = sum(1 for r, e in closed if e.exit_reason == "stop" and e.stop_promoted)
            print(f"  promoted-stop exits: paper (inferred R>-0.3) {pp} vs engine (recorded) {ep}")
    unmatched_paper = [r for r in rows if r["symbol"] not in {p[0]["symbol"] for p in pairs} or all(p[0] is not r for p in pairs)]
    print(f"  paper entries with no engine match: {len(rows) - len(pairs)}; engine entries with no paper match: {len(eng) - len(pairs)}")
    cnt = Counter(r["symbol"] for r in rows if all(p[0] is not r for p in pairs))
    print(f"  unmatched paper by symbol: {dict(cnt)} | engine by symbol: {dict(Counter(eng.symbol))} | paper by symbol: {dict(Counter(r['symbol'] for r in rows))}")


def signal_agreement(data: dict[str, dict], paper_dir: Path, cache: dict) -> None:
    """The bot's own fired bars (strategy_signals) vs the engine's, same window."""
    f = paper_dir / "db_signals_ladder.json"
    if not f.exists():
        return
    code = {"B": "BEATUSD", "A": "AKEUSD", "K": "BANKUSD", "W": "WIFUSD"}
    bot: dict[str, set] = defaultdict(set)
    for s, mins, d, _k in json.load(open(f)):
        if mins * 60 >= LIVE_START:
            bot[code[s]].add((mins * 60, d))
    spec = build_spec("manual_scalp_both_t3_ladder", 5)
    print("\nsignal stream, live window: bars where the rule fired (approved or rejected), bot vs engine")
    for s, d in data.items():
        P, _mk, _tr = _resampled(d, 5, cache)
        confirm = _resampled(d, spec.confirm_minutes, cache)[0]
        sig = rulecore.to_engine_signals(rulecore.compute(P, confirm, spec))
        t = P.time.to_numpy("int64"); m = t >= LIVE_START
        eng = {(int(x), 1) for x in t[m & sig.long_entry]} | {(int(x), -1) for x in t[m & sig.short_entry]}
        b = bot.get(s, set())
        print(f"  {s:8s} bot {len(b):5d}  engine {len(eng):5d}  bot bars also fired by engine {len(eng & b) / max(1, len(b)):4.0%}  5m bars in window {int(m.sum())}")


def main() -> None:
    print(f"universe {list(RAW)}; archive ends {ts(max(d['ltp'].time.max() for d in RAW.values()))}; filler bars {'DROPPED' if DROP_FLAT else 'KEPT'}")
    for s, d in RAW.items():
        print(f"  {s:8s} 1m bars {len(d['ltp']):7d}  {ts(d['ltp'].time.min())} -> {ts(d['ltp'].time.max())}")

    # ---------------------------------------------------------------- 1. reproduction
    print("\n================ 1. REPRODUCTION CHECK: engine vs the paper arms, 09-16 06:25Z -> 10-01 18:00Z")
    live = {s: window(d, LIVE_START - WARMUP_S, LIVE_END) for s, d in RAW.items()}
    cache: dict = {}
    base_live = run("manual_scalp_both_t3", live, cache)
    lad_live = run("manual_scalp_both_t3_ladder", live, cache)
    base_live = base_live[base_live.entry_time + 300 >= LIVE_START].reset_index(drop=True)
    lad_live = lad_live[lad_live.entry_time + 300 >= LIVE_START].reset_index(drop=True)
    summary("engine baseline, live window", base_live)
    summary("engine ladder,   live window", lad_live)
    if PAPER_DIR is not None:
        signal_agreement(live, PAPER_DIR, cache)
        reproduce(base_live, PAPER_DIR / "rows_atr.json", "MANUAL_SCALP_BOTH_T3-5-20260915", "baseline: engine vs paper atr arm")
        reproduce(lad_live, PAPER_DIR / "rows_ladder.json", "MANUAL_SCALP_BOTH_T3_LADDER", "ladder: engine vs paper ladder arm")
    paired(base_live, lad_live, "live window, engine paired on shared entries")
    created_breakdown(lad_live, base_live, "live window (IN-SAMPLE for the hypothesis -- reproduction only)")

    # ---------------------------------------------------------------- 2. pre-registered
    print("\n\n================ 2. PRE-REGISTERED: archive before 2026-09-16 00:00Z")
    oos = {s: window(d, 0, PREREG_END) for s, d in RAW.items()}
    cache = {}
    base = run("manual_scalp_both_t3", oos, cache)
    lad = run("manual_scalp_both_t3_ladder", oos, cache)
    summary("engine baseline, out-of-sample", base)
    summary("engine ladder,   out-of-sample", lad)
    paired(base, lad, "out-of-sample, paired on shared entries")
    tagged = created_breakdown(lad, base, "out-of-sample created entries   <<< THE QUESTION")

    OUT.mkdir(parents=True, exist_ok=True)
    base.to_csv(OUT / "ladder_turnover_baseline_oos.csv", index=False)
    (tagged if tagged is not None else lad).to_csv(OUT / "ladder_turnover_ladder_oos.csv", index=False)
    base_live.to_csv(OUT / "ladder_turnover_baseline_live.csv", index=False)
    lad_live.to_csv(OUT / "ladder_turnover_ladder_live.csv", index=False)
    print(f"\ntrade frames written under {OUT}")


if __name__ == "__main__":
    main()
