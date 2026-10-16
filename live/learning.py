"""Label recorded signals and grade the signal model (owner, 2026-10-13).

    python -m live label-signals [--limit 2000]
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

TAKEABLE. The bot records EVERY signal edge, and on the live top-gainer
universe the %R edge re-fires about twice an hour per coin; the lab's 5,273
history rows are only the TAKEABLE ones (each coin run on its own through
run_portfolio, one position per coin, no daily cap or pause): about two a day
per coin. So after labelling, `mark_takeable` walks each coin's signals in
time order and stores `signal_outcomes.takeable`: a signal is takeable iff
the engine accepted its entry (it has an R), its 5m bar is tradable on the
REST candles (the lab's tradable mask), and no earlier TAKEABLE signal on the
coin is still open or inside the engine's cooldown (`cooldown_bars` 5m bars
after the exit bar; exits are processed before entries on the same bar) --
exactly run_portfolio's per-coin gating, reproduced from each label's exit
(tests/live_exec/test_takeable.py checks it against the lab's own run on the
golden market). A signal whose predecessor is not yet labelled waits.

GRADE. Pools the lab's historical signals (if the parquet is present) with
every labelled TAKEABLE live signal, trains the lab's fixed model on everything that
ENTERED before the graded week minus a 72h purge (a signal's label can take
72h to resolve), and scores the most recent complete UTC week (Monday to
Monday): AUC, the top 30% by score vs all, and how often random 30% subsets do
as well. One row in model_grades per week.

RECORD-ONLY COINS (2026-10-16). The bot also records signals on coins it never
trades (live/universe.py). They are labelled, marked takeable and trained on
like any other; each scored row carries a trade-universe flag (5th element),
and the switch-on rule must pass on all signals AND on the trade-universe
subset alone (docs/model_switch_on_rule.md, amendment 2026-10-16).

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


def _recorded_atr(row: dict) -> float:
    ind = row.get("indicators") or {}
    v = (ind.get("primary") or {}).get("atr") if isinstance(ind, dict) else None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return float("nan")
    return v if np.isfinite(v) else float("nan")


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
    # The lab's engine skips an entry on a bar the REST candles flag as not
    # tradable; the label below does not (the bot saw the bar trade), but
    # takeability must, so the flag is kept.
    rest_tradable = bool(tr[0])
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
    # The engine's stop floor is max(0.5 x ATR, 10 ticks); the lab had the
    # primary ATR, so it is passed when the bot recorded it.
    atr = nan.copy()
    atr[0] = _recorded_atr(row)
    long_e, short_e = z.copy(), z.copy()
    (long_e if side > 0 else short_e)[0] = True
    stop_l, stop_s = nan.copy(), nan.copy()
    (stop_l if side > 0 else stop_s)[0] = stop
    sig = Signals(long_entry=long_e, short_entry=short_e, stop_long=stop_l, stop_short=stop_s,
                  supertrend=nan, direction=nan, atr=atr, wpr=nan, adx_1m=nan, adx_5m=nan,
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
    # WHEN THE COIN IS FREE AGAIN for the next takeable signal: run_portfolio
    # refuses an entry while (i - exit index) < cooldown_bars, counted in the
    # same resampled bars, so it is the time of bar exit+cooldown. Data that
    # does not reach that far falls back to the nominal bar spacing.
    i_exit = int(t.bars_held)
    cd = int(params.cooldown_bars or 0)
    times = P["time"].to_numpy("int64")
    exact = i_exit + cd < n
    free_from = int(times[i_exit + cd]) if exact else int(t.exit_time) + cd * minutes * 60
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
                "funding_rows": int(len(d["funding"])),
                "rest_tradable": rest_tradable, "cooldown_bars": cd,
                "free_from": free_from, "free_from_exact": bool(exact)})


async def label_signals(repo, client, costs_for, *, now: int | None = None,
                        limit: int = 500) -> dict:
    """Label what has resolved. `client` is a public DeltaClient-like object
    (candles(symbol, resolution, start, end)); `costs_for(symbol)` gives
    SymbolCosts. Returns counts for the summary line."""
    now = int(time.time()) if now is None else int(now)
    rows = await repo.signals_to_label(limit=limit)
    out = {"candidates": len(rows), "labelled": 0, "unlabellable": 0, "waiting": 0, "errors": 0}
    # ONE FETCH PER COIN PER RUN (2026-10-13). Live records ~2 signals per coin
    # per hour, so fetching 72h of 1m LTP/MARK/FUNDING per signal repeated the
    # same download dozens of times. The coin's window covers all its rows;
    # each row then gets exactly the slice a per-signal fetch would return.
    windows: dict[str, list[int]] = {}
    for row in rows:
        s = int(row["bar_open"])
        w = windows.setdefault(row["symbol"], [s, s])
        w[0], w[1] = min(w[0], s), max(w[1], s)
    cache: dict[str, tuple] = {}
    for row in rows:
        minutes = int(str(row.get("primary_timeframe") or "5m").rstrip("m") or 5)
        start = int(row["bar_open"])
        if now - (start + minutes * 60) < 300:
            out["waiting"] += 1
            continue
        end = min(now - 60, start + LOOKAHEAD)
        try:
            sym = row["symbol"]
            if sym not in cache:
                lo, hi = windows[sym]
                cache[sym] = await asyncio.to_thread(
                    _fetch, client, sym, lo, min(now - 60, hi + LOOKAHEAD))
            ltp, mark, fund = _slice(cache[sym], start, end)
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
    out.update(await mark_takeable(repo))
    return out


# =====================================================================
# TAKEABLE
# =====================================================================

def decide_takeable(rows: list[dict]) -> tuple[dict[str, bool], int]:
    """Takeability for ONE coin's labellable signals in (bar_open, id) order.

    Each row: idempotency_key, bar_open, labelled, r_net, takeable (None =
    undecided), exit_time and outcome_detail (free_from, rest_tradable,
    cooldown_bars). Walks forward keeping the bar_open from which the coin is
    free again; a decided row is taken as stored. Stops at the first signal
    with no outcome yet -- every later answer depends on it. Returns
    ({key: takeable} for rows newly decided, undecided rows left waiting).
    """
    free_from = None
    got: dict[str, bool] = {}
    for k, r in enumerate(rows):
        if not r["labelled"]:
            return got, sum(1 for x in rows[k:] if x["labelled"] and x["takeable"] is None)
        took = r["takeable"]
        if took is None:
            det = r.get("outcome_detail") or {}
            took = bool(r["r_net"] is not None
                        and det.get("rest_tradable", True)
                        and (free_from is None or int(r["bar_open"]) >= free_from))
            got[r["idempotency_key"]] = took
        if took:
            free_from = _free_from(r)
    return got, 0


def _free_from(r: dict) -> int:
    det = r.get("outcome_detail") or {}
    if det.get("free_from") is not None:
        return int(det["free_from"])
    # Labelled before `free_from` was stored: the exit bar plus the engine's
    # cooldown at the nominal 5m spacing.
    from deltabt.config import StrategyParams
    cd = int(det.get("cooldown_bars", StrategyParams().cooldown_bars) or 0)
    return int(r["exit_time"]) + cd * 300


async def mark_takeable(repo) -> dict:
    """Decide `takeable` for every labelled signal whose predecessors on its
    coin are all labelled. Idempotent: a decided row is never changed."""
    rows = await repo.takeability_rows()
    out = {"takeable": 0, "not_takeable": 0, "takeable_waiting": 0}
    by_coin: dict[str, list[dict]] = {}
    for r in rows:
        by_coin.setdefault(r["symbol"], []).append(r)
    for sym in sorted(by_coin):
        got, waiting = decide_takeable(by_coin[sym])
        out["takeable_waiting"] += waiting
        for key, took in got.items():
            if await repo.record_signal_takeable(key, took):
                out["takeable" if took else "not_takeable"] += 1
    return out


def _slice(frames, start, end):
    """What _fetch(client, symbol, start, end) would have returned, cut from a
    wider fetch: candles are inclusive [start, end]; funding from start-3600."""
    ltp, mark, fund = frames

    def cut(df, lo):
        if df is None or df.empty or "time" not in df:
            return df
        t = df["time"].to_numpy("int64")
        return df[(t >= lo) & (t <= end)].reset_index(drop=True)

    return cut(ltp, start), cut(mark, start), cut(fund, start - 3600)


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

def is_record_only(row: dict) -> bool:
    """A signal on a coin watched for the record only (2026-10-16): its
    readings say so, or -- if the readings were never written -- the bot's
    refusal does (app/risk/engine.RECORD_ONLY prefixes it)."""
    if ((row.get("features") or {}).get("record_only")):
        return True
    return str(row.get("rejection_reason") or "").startswith("record_only")


def live_frame(rows: list[dict]) -> pd.DataFrame:
    """Labelled TAKEABLE live signals in the lab's signals.parquet shape.

    The history holds only takeable signals; a live signal skipped because an
    earlier one on its coin was still open is the same move counted again, so
    it is left out (as is one not yet decided)."""
    recs = []
    for r in rows:
        if r.get("r_net") is None or r.get("takeable") is not True:
            continue
        lab = ((r.get("features") or {}).get("lab")) or {}
        rec = {"symbol": r["symbol"], "entry_time": int(r["bar_open"]),
               "side": float(r["direction"]), "R": float(r["r_net"]),
               "win": int(bool(r["win"])), "exit_reason": r.get("exit_reason"),
               "reach_1r": (float(r["mfe_r"] >= 1.0) if r.get("mfe_r") is not None else np.nan)}
        for k in SF.FEATURES:
            v = lab.get(k)
            rec[k] = float(v) if v is not None else np.nan
        # TRADE-UNIVERSE FLAG (2026-10-16): 0 for a coin watched for the record
        # only. Never a model input (grade() drops it with _live); it only
        # splits the switch-on rule's test. Rows from before then are all 1.
        rec["_trade"] = 0 if is_record_only(r) else 1
        recs.append(rec)
    cols = ["symbol", "entry_time", "side", "R", "win", "exit_reason", "reach_1r", *SF.FEATURES,
            "_trade"]
    return pd.DataFrame(recs, columns=cols)


def last_complete_week(now: int) -> int:
    """Monday 00:00 UTC of the most recent week that has fully ended."""
    monday = (now // DAY - ((now // DAY + 3) % 7)) * DAY     # this week's Monday
    return monday - WEEK


def grade(history: pd.DataFrame | None, live: pd.DataFrame, week_start: int) -> ModelGradeRecord:
    from sklearn.metrics import roc_auc_score
    week_end = week_start + WEEK
    cols = ["symbol", "entry_time", "side", "R", "win", "exit_reason", "reach_1r", *SF.FEATURES]
    if history is not None and not history.empty and "reach_1r" not in history:
        history = history.assign(reach_1r=np.nan)
    hist = history[cols] if history is not None and not history.empty else pd.DataFrame(columns=cols)
    if "_trade" not in live:
        live = live.assign(_trade=1)
    pool = pd.concat([hist.assign(_live=0, _trade=1), live.assign(_live=1)], ignore_index=True)
    pool = pool.astype({c: float for c in ["side", "R", "reach_1r", *SF.FEATURES]})
    pool["_trade"] = pool["_trade"].fillna(1).astype(int)
    pool["win"] = pool["win"].astype(int)
    train = pool[pool.entry_time < week_start - HOLD_HOURS * 3600]
    # THE GRADED WEEK IS LIVE SIGNALS ONLY (2026-10-15). The history file runs
    # to 2026-10-12, inside the first graded week, and history rows must never
    # be scored as if the bot had seen them.
    test = pool[(pool._live == 1) & (pool.entry_time >= week_start) & (pool.entry_time < week_end)]
    n_tr, n_tr_live, n_te = len(train), int(train._live.sum()), len(test)
    detail = {"purge_hours": HOLD_HOURS, "history_rows": int(len(hist)),
              "live_rows": int(len(live)), "test_live": int(test._live.sum()) if n_te else 0,
              "test_trade_universe": int(test._trade.sum()) if n_te else 0}
    auc = top = allr = p = None
    k = 0
    if n_tr >= 100 and train.win.nunique() == 2 and n_te >= 1:
        X = lambda df: SM.features(df.drop(columns=["_live", "_trade"]))
        mdl = SM.model().fit(X(train), train.win)
        s = mdl.predict_proba(X(test))[:, 1]
        allr = float(test.R.mean())
        top, p, k = SM.top_vs_random(test.R.to_numpy(float), s)
        top = float(top)
        # Per-signal scores, so the switch-on rule (docs/model_switch_on_rule.md)
        # can pool weeks later without re-training anything. The 5th element
        # is the trade-universe flag (1 = a coin the bot may trade, 0 =
        # record-only; amendment 2026-10-16).
        detail["scored"] = [[int(t), str(sym), round(float(sc), 6), round(float(r), 6), int(tu)]
                            for t, sym, sc, r, tu in zip(test.entry_time, test.symbol, s, test.R,
                                                         test._trade)]
        # SECOND TARGET (owner, 2026-10-15): "reaches +1R before the stop".
        # Same frozen model settings, trained on rows whose path is known.
        tr1, te1 = train[train.reach_1r.notna()], test[test.reach_1r.notna()]
        if len(tr1) >= 100 and tr1.reach_1r.nunique() == 2 and len(te1):
            m1 = SM.model().fit(X(tr1), tr1.reach_1r.astype(int))
            s1 = m1.predict_proba(X(te1))[:, 1]
            detail["scored_1r"] = [[int(t), str(sym), round(float(sc), 6), int(y), int(tu)]
                                   for t, sym, sc, y, tu in zip(te1.entry_time, te1.symbol, s1,
                                                                te1.reach_1r, te1._trade)]
        if test.win.nunique() == 2:
            auc = float(roc_auc_score(test.win, s))
    rec = ModelGradeRecord(week_start=week_start, week_end=week_end, n_train=n_tr,
                           n_train_live=n_tr_live, n_test=n_te, auc=auc, top_mean_r=top,
                           all_mean_r=allr, random_p=p, top_k=k, summary="", detail=detail)
    rec.summary = summary(rec)
    return rec


# =====================================================================
# SWITCH-ON RULE (docs/model_switch_on_rule.md, fixed 2026-10-15)
# =====================================================================

SWITCH_MIN_WEEKS = 4
SWITCH_MIN_SIGNALS = 30
SWITCH_TOP_FRAC = 0.3
SWITCH_P = 0.05
#: A 1R exit pays ~+0.97R and a stop costs ~-1.07R (fees, slippage), so the
#: share reaching +1R must beat this just to break even.
REACH_1R_BREAK_EVEN = 0.52


def _pooled(weeks: list[list], value_col: int, *, n_random: int = 2000, seed: int = 0):
    """Top SWITCH_TOP_FRAC by score in EACH week, pooled; against the same
    number of random picks in each week. Returns (top mean, random mean, p)."""
    rng = np.random.default_rng(seed)
    tops, per_week = [], []
    for rows in weeks:
        a = np.array([[r[2], r[value_col]] for r in rows], dtype=float)
        k = max(1, int(round(SWITCH_TOP_FRAC * len(a))))
        tops.append(a[np.argsort(-a[:, 0], kind="stable")[:k], 1])
        per_week.append((a[:, 1], k))
    top = float(np.concatenate(tops).mean())
    rand = np.array([np.concatenate([rng.choice(v, k, replace=False) for v, k in per_week]).mean()
                     for _ in range(n_random)])
    return top, float(rand.mean()), float((rand >= top).mean())


def _is_trade_universe(row) -> bool:
    """A scored row's trade-universe flag (5th element). Rows graded before
    the 2026-10-16 amendment have four elements; every coin was tradeable
    then, so they count as trade-universe signals."""
    return len(row) < 5 or bool(row[4])


def _rule(weeks: list[list], col: int, bar: float) -> dict:
    weeks = [w for w in weeks if len(w) >= SWITCH_MIN_SIGNALS]
    res = {"weeks": len(weeks), "needs_weeks": SWITCH_MIN_WEEKS}
    if len(weeks) >= SWITCH_MIN_WEEKS:
        top, rand, p = _pooled(weeks, col)
        res.update(top=top, random=rand, p=p, eligible=bool(p <= SWITCH_P and top > bar))
    else:
        res["eligible"] = False
    return res


def switch_check(grades: list[dict]) -> dict:
    """Apply the fixed switch-on rule to every graded week so far.

    Weeks count only with at least SWITCH_MIN_SIGNALS live test signals; ALL
    such weeks are pooled (none dropped). 3R target: pooled top-30% mean net R
    beats random picks at p <= SWITCH_P AND is above zero. 1R target: pooled
    top-30% share reaching +1R beats random at p <= SWITCH_P AND exceeds
    REACH_1R_BREAK_EVEN.

    AMENDMENT 2026-10-16 (owner; before the first grade on 2026-10-19). The
    bot now also records coins it never trades. A target is ELIGIBLE only if
    the rule passes on ALL recorded live signals AND, separately, on the
    trade-universe subset alone -- same test, same thresholds; a week
    qualifies for the subset with at least SWITCH_MIN_SIGNALS subset signals.
    Either target ELIGIBLE makes the model eligible; switching it on is the
    owner's decision, at 0.5% risk first.

    Returns {"3R": all signals, "1R": all signals, "trade_universe": {"3R",
    "1R"}}: the per-target dicts hold the all-signals figures, and their
    "eligible" is the VERDICT (both sets pass)."""
    out: dict = {"trade_universe": {}}
    for key, col, bar, name in (("scored", 3, 0.0, "3R"), ("scored_1r", 3, REACH_1R_BREAK_EVEN, "1R")):
        weeks = [(g.get("detail") or {}).get(key) or [] for g in grades]
        res = _rule(weeks, col, bar)
        sub = _rule([[r for r in w if _is_trade_universe(r)] for w in weeks], col, bar)
        res["eligible"] = bool(res["eligible"] and sub["eligible"])
        out[name] = res
        out["trade_universe"][name] = sub
    return out


def _passes(name: str, r: dict) -> bool:
    bar = 0.0 if name == "3R" else REACH_1R_BREAK_EVEN
    return "top" in r and r["p"] <= SWITCH_P and r["top"] > bar


def _rule_text(name: str, r: dict) -> str:
    if "top" not in r:
        return f"{r['weeks']} of {r['needs_weeks']} qualifying weeks so far"
    verdict = "passes" if _passes(name, r) else "fails"
    if name == "3R":
        return (f"top picks {r['top']:+.2f}R vs random {r['random']:+.2f}R "
                f"(p {r['p']:.2f}), {verdict}")
    return (f"top picks reached +1R {100 * r['top']:.0f}% vs random "
            f"{100 * r['random']:.0f}% (p {r['p']:.2f}; break-even "
            f"{100 * REACH_1R_BREAK_EVEN:.0f}%), {verdict}")


def switch_line(chk: dict) -> str:
    parts = []
    for name in ("3R", "1R"):
        r = chk[name]
        sub = (chk.get("trade_universe") or {}).get(name)
        text = f"{name} target: all signals {_rule_text(name, r)}"
        if sub is not None:
            text += f"; trade coins only {_rule_text(name, sub)}"
        text += f" -> {'ELIGIBLE' if r['eligible'] else 'not eligible'}"
        parts.append(text)
    return "Switch-on rule: " + "; ".join(parts) + "."


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
    lab = [r for r in rows if r.get("r_net") is not None]
    rec.detail.update({
        "live_labelled": len(lab),
        "live_not_takeable": sum(1 for r in lab if r.get("takeable") is False),
        "live_undecided": sum(1 for r in lab if r.get("takeable") is None),
        "live_record_only": sum(1 for r in lab if is_record_only(r))})
    await repo.record_model_grade(rec)
    chk = switch_check(await repo.model_grades())
    rec.detail["switch_check"] = chk
    rec.summary = summary(rec) + " " + switch_line(chk)
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
    lab = sub.add_parser("label-signals", help="label resolved signals as if taken, "
                                               "then mark which were takeable")
    lab.add_argument("--limit", type=int, default=2000)
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
                      f"{got['errors']} errors; {got['candidates']} looked at. "
                      f"Takeable: {got['takeable']} yes, {got['not_takeable']} no, "
                      f"{got['takeable_waiting']} waiting on an earlier signal.")
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

