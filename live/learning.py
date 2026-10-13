"""Label recorded signals and grade the signal model (owner, 2026-10-13).

    python -m live label-signals [--limit 500]
    python -m live grade-model   [--history out/signal_lab/signals.parquet] [--week 2026-10-19]

LABEL. Every signal in strategy_signals with a direction, entry, stop and
target and no row in signal_outcomes is simulated AS IF IT HAD BEEN TAKEN,
with the engine the signal lab used for its 5,273 historical labels
(deltabt.portfolio.run_portfolio on a one-signal Book; ltp_close stop fill on
1m LTP and MARK; 3R target; 72h hold; fees and slippage from SymbolCosts;
funding), on public 1m candles from Delta's REST API -- no keys. A signal is
labelled once it has resolved (stop, target or 72h); younger ones are left
for the next run, so the command is idempotent and resumable. Write-once:
an existing outcome is never changed.

GRADE. Pools the lab's historical signals (if the parquet is present) with
every labelled live signal, trains the lab's fixed model on everything that
ENTERED before the graded week minus a 72h purge (a signal's label can take
72h to resolve), and scores the most recent complete UTC week (Monday to
Monday): AUC, the top 30% by score vs all, and how often random 30% subsets do
as well. One row in model_grades per week.

NEITHER TOUCHES A TRADE. Nothing in app/ or the live trading modules imports
this file (tests/live_exec/test_model_never_trades.py); the model's score is
never read by any order decision.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from app.persistence.models import ModelGradeRecord, SignalOutcomeRecord
from deltabt import signal_features as SF
from deltabt import signal_model as SM

log = logging.getLogger("live.learning")

DAY, WEEK = 86400, 7 * 86400
HOLD_HOURS = 72
#: Data fetched past the entry. 72h is the time stop; the margin covers
#: buckets resample_complete drops (the stop counts BARS, as in the lab).
LOOKAHEAD = (HOLD_HOURS + 8) * 3600
#: The lab's account settings, so R matches the history (size only matters
#: through contract rounding).
LAB_CAPITAL, LAB_RISK_PCT, LAB_MAX_LEV = 10_000.0, 2.0, 5.0
#: A signal still unresolved this long after entry gets a terminal
#: "unlabellable" row so it is not refetched forever.
GIVE_UP = (HOLD_HOURS + 24) * 3600
FAMILY = "manual_scalp_both_t3_st"
ENV_HISTORY = "DELTABT_SIGNAL_HISTORY"


def _frame(rows) -> pd.DataFrame:
    df = pd.DataFrame(list(rows))
    cols = ["time", "open", "high", "low", "close", "volume"]
    if df.empty:
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in cols}).astype({"time": "int64"})
    for c in cols[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64") if c in df else 0.0
    df["time"] = df["time"].astype("int64")
    return df[cols].sort_values("time", ignore_index=True)


def _spec(minutes: int = 5):
    from deltabt.catalog import build_spec
    return build_spec(FAMILY, minutes, 1)


# =====================================================================
# LABEL
# =====================================================================

def simulate(row: dict, ltp: pd.DataFrame, mark: pd.DataFrame, funding: pd.DataFrame,
             costs, *, now: int, minutes: int = 5) -> SignalOutcomeRecord | str:
    """The outcome of one recorded signal, or why it cannot be labelled yet.

    Returns a record (resolved, or terminally unlabellable) or a string
    reason to try again later.
    """
    from deltabt.data.quality import tradable_mask
    from deltabt.harness import _resampled, params_for
    from deltabt.portfolio import Book, RiskGates, run_portfolio
    from deltabt.strategy import Signals

    bar_open = int(row["bar_open"])
    side = int(row["direction"])
    entry, stop = float(row["entry_price"]), float(row["stop_price"])
    age = now - (bar_open + minutes * 60)

    def terminal(why: str) -> SignalOutcomeRecord:
        return SignalOutcomeRecord(
            signal_id=int(row["id"]), idempotency_key=row["idempotency_key"],
            symbol=row["symbol"], side=side, entry_time=bar_open, entry_price=entry,
            stop_price=stop, r_net=None, win=None, exit_reason=f"unlabellable: {why}",
            exit_time=None, exit_price=None, mfe_r=None, mae_r=None, bars_to_mfe=None,
            bars_held=None)

    if ltp is None or ltp.empty:
        return terminal("no 1m candles") if age > GIVE_UP else "no candles yet"
    d = dict(symbol=row["symbol"], ltp=ltp, mark=mark if mark is not None and not mark.empty else None,
             funding=funding if funding is not None else pd.DataFrame(),
             tradable=tradable_mask(ltp))
    P, mk, tr = _resampled(d, minutes, {})
    P = P.reset_index(drop=True)
    i0 = np.flatnonzero(P["time"].to_numpy("int64") == bar_open)
    if not len(i0):
        return terminal("signal bar missing from the venue's candles") if age > GIVE_UP \
            else "signal bar not in the candles"
    i0 = int(i0[0])
    P = P.iloc[i0:].reset_index(drop=True)
    tr = np.asarray(tr)[i0:].copy() if tr is not None else np.ones(len(P), bool)
    # THE RECORDED DECISION IS THE ENTRY. The bot saw this bar close at the
    # recorded price on its own (websocket) bars; the engine enters at the
    # bar's close, so the bar is pinned to it, and the entry bar counts as
    # tradable whatever the REST filler flags say.
    rest_close = float(P.at[0, "close"])
    P.at[0, "close"] = entry
    P.at[0, "high"] = max(float(P.at[0, "high"]), entry)
    P.at[0, "low"] = min(float(P.at[0, "low"]), entry)
    tr[0] = True
    n = len(P)
    z = np.zeros(n, bool)
    nan = np.full(n, np.nan)
    long_e, short_e = z.copy(), z.copy()
    (long_e if side > 0 else short_e)[0] = True
    stop_l, stop_s = nan.copy(), nan.copy()
    (stop_l if side > 0 else stop_s)[0] = stop
    sig = Signals(long_entry=long_e, short_entry=short_e, stop_long=stop_l, stop_short=stop_s,
                  supertrend=nan, direction=nan, atr=nan, wpr=nan, adx_1m=nan, adx_5m=nan,
                  bull_1m=z, bear_1m=z, long_base=long_e, short_base=short_e, warmup=0)
    book = Book(symbol=row["symbol"], bars=P, signals=sig, costs=costs, mark=mk,
                tradable=tr, fill_ltp=ltp, fill_mark=mark if mark is not None and not mark.empty else None)
    params = replace(params_for(_spec(minutes), minutes, HOLD_HOURS), stop_fill="ltp_close",
                     risk_percent=LAB_RISK_PCT, max_leverage=LAB_MAX_LEV, max_cost_per_r=None)
    gates = replace(RiskGates.off(), max_open_positions=1)
    res = run_portfolio({row["symbol"]: book}, params, gates, initial_capital=LAB_CAPITAL,
                        funding={row["symbol"]: d["funding"]})
    if not res.trades:
        refused = {k: v for k, v in res.rejects.items() if v}
        if refused:
            return terminal(f"the engine refused the entry: {refused}")
        return terminal("no exit within the data") if age > GIVE_UP else "not resolved yet"
    t = res.trades[0]
    rpu = abs(t.entry_price - t.stop_price)
    path = SF.post_entry_path(P["time"].to_numpy("int64"), P["high"].to_numpy(float),
                              P["low"].to_numpy(float), 0, int(t.exit_time), side,
                              float(t.entry_price), rpu)
    return SignalOutcomeRecord(
        signal_id=int(row["id"]), idempotency_key=row["idempotency_key"], symbol=row["symbol"],
        side=side, entry_time=bar_open, entry_price=float(t.entry_price),
        stop_price=float(t.stop_price), r_net=float(t.r_multiple), win=bool(t.r_multiple > 0),
        exit_reason=t.exit_reason, exit_time=int(t.exit_time), exit_price=float(t.exit_price),
        mfe_r=path["mfe_R"], mae_r=path["mae_R"], bars_to_mfe=path["bars_to_mfe"],
        bars_held=path["bars_held"], fav=path["fav"], adv=path["adv"],
        detail={"engine": "deltabt.portfolio.run_portfolio", "stop_fill": "ltp_close",
                "target_r": params.reward_risk, "max_hold_bars": params.max_hold_bars,
                "fees": float(t.fees), "funding": float(t.funding),
                "contracts": int(t.contracts), "rest_close_of_signal_bar": rest_close,
                "funding_rows": int(len(d["funding"]))})


async def label_signals(repo, client, costs_for, *, now: int | None = None,
                        limit: int = 500) -> dict:
    """Label what has resolved. `client` is a public DeltaClient-like object
    (candles(symbol, resolution, start, end)); `costs_for(symbol)` gives
    SymbolCosts. Returns counts for the summary line."""
    now = int(time.time()) if now is None else int(now)
    rows = await repo.signals_to_label(limit=limit)
    out = {"candidates": len(rows), "labelled": 0, "unlabellable": 0, "waiting": 0, "errors": 0}
    for row in rows:
        minutes = int(str(row.get("primary_timeframe") or "5m").rstrip("m") or 5)
        start = int(row["bar_open"])
        if now - (start + minutes * 60) < 300:
            out["waiting"] += 1
            continue
        end = min(now - 60, start + LOOKAHEAD)
        try:
            ltp, mark, fund = await asyncio.to_thread(_fetch, client, row["symbol"], start, end)
            costs = costs_for(row["symbol"])
            got = simulate(row, ltp, mark, fund, costs, now=now, minutes=minutes)
        except Exception as exc:                            # noqa: BLE001
            log.warning("could not label %s %s: %s", row["symbol"], start, exc)
            out["errors"] += 1
            continue
        if isinstance(got, str):
            out["waiting"] += 1
            continue
        if await repo.record_signal_outcome(got):
            out["unlabellable" if got.r_net is None else "labelled"] += 1
    return out


def _fetch(client, symbol, start, end):
    ltp = _frame(client.candles(symbol, "1m", start, end))
    try:
        mark = _frame(client.candles("MARK:" + symbol, "1m", start, end))
    except Exception as exc:                                # noqa: BLE001
        log.warning("no MARK candles for %s: %s (stops trigger on LTP)", symbol, exc)
        mark = None
    try:
        fund = _frame(client.candles("FUNDING:" + symbol, "1h", start - 3600, end))
    except Exception as exc:                                # noqa: BLE001
        log.warning("no FUNDING candles for %s: %s (funding not charged)", symbol, exc)
        fund = pd.DataFrame()
    return ltp, mark, fund


# =====================================================================
# GRADE
# =====================================================================

def live_frame(rows: list[dict]) -> pd.DataFrame:
    """Labelled live signals in the lab's signals.parquet shape."""
    recs = []
    for r in rows:
        if r.get("r_net") is None:
            continue
        lab = ((r.get("features") or {}).get("lab")) or {}
        rec = {"symbol": r["symbol"], "entry_time": int(r["bar_open"]),
               "side": float(r["direction"]), "R": float(r["r_net"]),
               "win": int(bool(r["win"])), "exit_reason": r.get("exit_reason")}
        for k in SF.FEATURES:
            v = lab.get(k)
            rec[k] = float(v) if v is not None else np.nan
        recs.append(rec)
    cols = ["symbol", "entry_time", "side", "R", "win", "exit_reason", *SF.FEATURES]
    return pd.DataFrame(recs, columns=cols)


def last_complete_week(now: int) -> int:
    """Monday 00:00 UTC of the most recent week that has fully ended."""
    monday = (now // DAY - ((now // DAY + 3) % 7)) * DAY     # this week's Monday
    return monday - WEEK


def grade(history: pd.DataFrame | None, live: pd.DataFrame, week_start: int) -> ModelGradeRecord:
    from sklearn.metrics import roc_auc_score
    week_end = week_start + WEEK
    cols = ["symbol", "entry_time", "side", "R", "win", "exit_reason", *SF.FEATURES]
    hist = history[cols] if history is not None and not history.empty else pd.DataFrame(columns=cols)
    pool = pd.concat([hist.assign(_live=0), live.assign(_live=1)], ignore_index=True)
    pool = pool.astype({c: float for c in ["side", "R", *SF.FEATURES]})
    pool["win"] = pool["win"].astype(int)
    train = pool[pool.entry_time < week_start - HOLD_HOURS * 3600]
    test = pool[(pool.entry_time >= week_start) & (pool.entry_time < week_end)]
    n_tr, n_tr_live, n_te = len(train), int(train._live.sum()), len(test)
    detail = {"purge_hours": HOLD_HOURS, "history_rows": int(len(hist)),
              "live_rows": int(len(live)), "test_live": int(test._live.sum()) if n_te else 0}
    auc = top = allr = p = None
    k = 0
    if n_tr >= 100 and train.win.nunique() == 2 and n_te >= 1:
        X = lambda df: SM.features(df.drop(columns=["_live"]))
        mdl = SM.model().fit(X(train), train.win)
        s = mdl.predict_proba(X(test))[:, 1]
        allr = float(test.R.mean())
        top, p, k = SM.top_vs_random(test.R.to_numpy(float), s)
        top = float(top)
        if test.win.nunique() == 2:
            auc = float(roc_auc_score(test.win, s))
    rec = ModelGradeRecord(week_start=week_start, week_end=week_end, n_train=n_tr,
                           n_train_live=n_tr_live, n_test=n_te, auc=auc, top_mean_r=top,
                           all_mean_r=allr, random_p=p, top_k=k, summary="", detail=detail)
    rec.summary = summary(rec)
    return rec


def summary(g: ModelGradeRecord) -> str:
    day = lambda ts: time.strftime("%d %b", time.gmtime(ts))
    head = f"Week {day(g.week_start)}–{day(g.week_end - 1)}: "
    if g.top_mean_r is None:
        return head + (f"not graded ({g.n_test} signals that week, {g.n_train} to learn from; "
                       f"needs at least 1 and 100).")
    auc = "n/a (all one result)" if g.auc is None else f"{g.auc:.2f}"
    verdict = ("the model's picks did better than chance"
               if g.auc is not None and g.auc >= 0.55 and g.random_p <= 0.05 and g.top_mean_r > 0
               else "no sign the model can pick winners")
    return (head + f"{g.n_test} signals graded; model learned from {g.n_train} "
            f"({g.n_train_live} live). AUC {auc} (0.50 = coin flip). Its top {g.top_k} "
            f"averaged {g.top_mean_r:+.2f}R vs {g.all_mean_r:+.2f}R for all; random picks did "
            f"as well {100 * g.random_p:.0f}% of the time. Verdict: {verdict}. "
            f"It does not filter trades.")


def load_history(path: str | Path | None) -> pd.DataFrame | None:
    p = Path(path) if path else None
    if p is None or not p.exists():
        log.warning("no historical signals at %s; grading on live signals only", p)
        return None
    return pd.read_parquet(p)


async def grade_model(repo, *, history_path=None, now: int | None = None,
                      week_start: int | None = None) -> ModelGradeRecord:
    now = int(time.time()) if now is None else int(now)
    ws = last_complete_week(now) if week_start is None else int(week_start)
    rows = await repo.labelled_signals()
    rec = grade(load_history(history_path), live_frame(rows), ws)
    await repo.record_model_grade(rec)
    return rec


# =====================================================================
# CLI
# =====================================================================

def _default_history() -> str:
    from deltabt.config import OUT_DIR
    return os.environ.get(ENV_HISTORY) or str(OUT_DIR / "signal_lab" / "signals.parquet")


def main(argv: list[str]) -> int:
    from app.config.settings import Settings
    from app.persistence.repository import PostgresRepository
    ap = argparse.ArgumentParser(prog="python -m live")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lab = sub.add_parser("label-signals", help="label resolved signals as if taken")
    lab.add_argument("--limit", type=int, default=500)
    gr = sub.add_parser("grade-model", help="grade the signal model on the last full week")
    gr.add_argument("--history", default=None, help=f"signals.parquet (default ${ENV_HISTORY} "
                                                    f"or out/signal_lab/signals.parquet)")
    gr.add_argument("--week", default=None, help="Monday (YYYY-MM-DD, UTC) of the week to grade")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()

    async def run() -> int:
        repo = PostgresRepository(settings.database_url, min_size=1, max_size=2)
        await repo.connect()
        try:
            if args.cmd == "label-signals":
                from deltabt.costs import SymbolCosts
                from deltabt.data.client import DeltaClient
                from deltabt.data.store import ProductCatalog
                cat = ProductCatalog()
                client = DeltaClient(per_second=2.0, timeout=20.0)
                got = await label_signals(
                    repo, client, lambda s: SymbolCosts.from_spec(cat.get(s)), limit=args.limit)
                print(f"Labelled {got['labelled']} signals ({got['unlabellable']} could not be "
                      f"simulated); {got['waiting']} still running or too new; "
                      f"{got['errors']} errors; {got['candidates']} looked at.")
                return 1 if got["errors"] and not got["labelled"] else 0
            ws = (int(pd.Timestamp(args.week, tz="UTC").value // 10**9)
                  if args.week else None)
            rec = await grade_model(repo, history_path=args.history or _default_history(),
                                    week_start=ws)
            print(rec.summary)
            return 0
        finally:
            await repo.close()

    return asyncio.run(run())

