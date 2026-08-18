"""H-EMA-2 reporting layer. Observability only -- see preregistration S 15.

Nothing here changes a candidate, a signal, a stop, a cost, a control or a gate.
Every number is derived from arrays and trade frames the frozen pipeline already
produced. If this module were deleted the experiment's results would be
identical; only the ability to read them would be lost.

The funnel is reconstructed rather than instrumented. The frozen simulator
reports `signals`, `skipped_stop` and `skipped_size` and nothing else, so
`rejected_position_open` is taken as the residual -- exact, because the
simulator has no other path that consumes a signal without producing a trade.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from deltabt.research import hema2

TOL = 1e-9

TRADE_COLUMNS = [
    "symbol", "exec_tf", "mechanism", "candidate_id", "direction",
    "signal_time", "entry_time", "entry_price", "stop_price", "target_price",
    "stop_distance_pct", "stop_distance_bps", "risk_R",
    "exit_time", "exit_price", "exit_reason", "duration_min",
    "gross_R", "fee_R", "slippage_R", "funding_R", "net_R",
    "contracts", "notional", "ambiguous",
]


def slug(candidate_id: str) -> str:
    return candidate_id.replace("|", "__").replace("/", "-")


def funnel(raw_lo, raw_sh, F, sym, window, warmup: int, res, n_trades: int) -> dict:
    """Setup lifecycle for one arm on one symbol. S 15.1."""
    raw_lo = np.asarray(raw_lo, bool)
    raw_sh = np.asarray(raw_sh, bool)
    raw = raw_lo | raw_sh
    detected = int(raw.sum())

    t1 = sym["t1"]
    n1 = len(t1)
    e = hema2.entry_index(F["time"], F["tf"], t1)
    valid_stop = hema2.valid_stop_mask(F)

    in_warm = raw.copy()
    in_warm[warmup:] = False
    rej_warm = int(in_warm.sum())

    live = raw.copy()
    live[:warmup] = False
    rej_stop_invalid = int((live & ~valid_stop).sum())
    live = live & valid_stop

    has_bar = (e > 0) & (e < n1)
    rej_no_bar = int((live & ~has_bar).sum())
    live = live & has_bar

    ent_t = np.zeros(len(live), dtype="int64")
    ent_t[has_bar] = t1[e[has_bar]]
    inwin = (ent_t >= window[0]) & (ent_t < window[1])
    rej_outside = int((live & ~inwin).sum())
    eligible = int((live & inwin).sum())

    other = eligible - res.skipped_stop - res.skipped_size - n_trades
    return dict(
        setups_detected=detected,
        rejected_warmup=rej_warm,
        rejected_stop_invalid=rej_stop_invalid,
        rejected_no_entry_bar=rej_no_bar,
        rejected_outside_split=rej_outside,
        eligible_setups=eligible,
        skipped_stop=int(res.skipped_stop),
        skipped_size=int(res.skipped_size),
        rejected_position_open=int(max(other, 0)),
        trades_entered=int(n_trades),
        funnel_residual=int(other),        # must be >= 0; negative means a bug
    )


def to_journal(frame: pd.DataFrame, arm: dict) -> pd.DataFrame:
    """Trade frame -> the human/machine journal schema. S 3."""
    if frame.empty:
        return pd.DataFrame(columns=TRADE_COLUMNS)
    out = pd.DataFrame({
        "symbol": frame.symbol,
        "exec_tf": arm["exec_tf"],
        "mechanism": arm["mechanism"],
        "candidate_id": arm["arm_id"],
        "direction": np.where(frame.side > 0, "LONG", "SHORT"),
        "signal_time": frame.signal_time,
        "entry_time": frame.entry_time,
        "entry_price": frame.entry_price,
        "stop_price": frame.stop_price,
        "target_price": frame.target_price,
        "stop_distance_pct": frame.stop_pct * 100.0,
        "stop_distance_bps": frame.stop_pct * 10_000.0,
        "risk_R": frame.r_price,
        "exit_time": frame.exit_time,
        "exit_price": frame.exit_price,
        "exit_reason": frame.exit_reason,
        "duration_min": frame.bars_held,
        "gross_R": frame.r_gross,
        "fee_R": -frame.fee_r,
        "slippage_R": -frame.slip_r,
        "funding_R": -frame.funding_r,
        "net_R": frame.r_net,
        "contracts": frame.contracts,
        "notional": frame.notional,
        "ambiguous": frame.ambiguous,
    })
    return out[TRADE_COLUMNS]


def reconcile(frame: pd.DataFrame, funnel_row: dict) -> dict:
    """S 15.2. Returns per-check booleans; the caller invalidates on failure."""
    if frame.empty:
        return dict(ok=True, checks={}, n=0)
    g = frame.r_gross.to_numpy("float64")
    n = frame.r_net.to_numpy("float64")
    fee = frame.fee_r.to_numpy("float64")
    slip = frame.slip_r.to_numpy("float64")
    fund = frame.funding_r.to_numpy("float64")
    cost = frame.cost_r.to_numpy("float64")

    def close(a, b):
        return bool(np.isclose(a, b, rtol=TOL, atol=1e-9))

    checks = {
        "net_equals_gross_minus_cost": close(float(n.sum()),
                                             float(g.sum() - cost.sum())),
        "cost_equals_components": close(float(cost.sum()),
                                        float(fee.sum() + slip.sum() + fund.sum())),
        "per_trade_net": bool(np.allclose(n, g - cost, rtol=0, atol=1e-9)),
        "trades_le_eligible": funnel_row["trades_entered"] <= funnel_row["eligible_setups"],
        "funnel_residual_non_negative": funnel_row["funnel_residual"] >= 0,
        "no_stop_over_cap": bool((frame.stop_pct <= hema2.MAX_STOP_PCT + 1e-12).all()),
        "stop_side_correct": bool(
            ((frame.side > 0) == (frame.stop_price < frame.entry_price)).all()),
    }
    return dict(ok=all(checks.values()), checks=checks, n=int(len(frame)))


def economics(frame: pd.DataFrame) -> dict:
    """Per-trade expectancy decomposition. S 14."""
    if frame.empty:
        return dict(trades=0)
    n = len(frame)
    return dict(
        trades=n,
        gross_expectancy=float(frame.r_gross.mean()),
        fee_drag=-float(frame.fee_r.mean()),
        slippage_drag=-float(frame.slip_r.mean()),
        funding_drag=-float(frame.funding_r.mean()),
        net_expectancy=float(frame.r_net.mean()),
        gross_total_R=float(frame.r_gross.sum()),
        net_total_R=float(frame.r_net.sum()),
        win_rate=float((frame.r_net > 0).mean()),
        wins=int((frame.r_net > 0).sum()),
        losses=int((frame.r_net <= 0).sum()),
        longs=int((frame.side > 0).sum()),
        shorts=int((frame.side < 0).sum()),
        median_net_R=float(frame.r_net.median()),
        avg_stop_pct=float(frame.stop_pct.mean() * 100),
        median_stop_pct=float(frame.stop_pct.median() * 100),
        median_stop_bps=float(frame.stop_pct.median() * 10_000),
        cost_per_R=float(frame.cost_r.mean()),
        pct_target=float((frame.exit_reason == "target").mean() * 100),
        pct_stop=float((frame.exit_reason == "stop").mean() * 100),
        unresolved_at_boundary=int((frame.exit_reason == "end").sum()),
    )


def representative(journal: pd.DataFrame) -> dict:
    """Deterministic best / worst / typical. S 11. Never hand-picked."""
    if journal.empty:
        return dict(best=journal, worst=journal, typical=journal)
    s = journal.sort_values("net_R", ascending=False)
    med = journal.net_R.median()
    typical = journal.iloc[(journal.net_R - med).abs().argsort()[:3]]
    return dict(best=s.head(5), worst=s.tail(5).iloc[::-1], typical=typical)


def _ts(v) -> str:
    return pd.Timestamp(int(v), unit="s").strftime("%Y-%m-%d %H:%M")


def format_trade(r) -> str:
    """The S 4 block, from real simulator values only."""
    sp = (r.stop_price / r.entry_price - 1.0) * 100.0
    tp = (r.target_price / r.entry_price - 1.0) * 100.0
    return "\n".join([
        f"{r.symbol} | {r.exec_tf}m | {r.mechanism} | {r.candidate_id} | {r.direction}",
        f"Signal:   {_ts(r.signal_time)}",
        f"Entry:    {_ts(r.entry_time)} @ {r.entry_price:,.4g}",
        f"Stop:     {r.stop_price:,.4g} ({sp:+.2f}%)",
        f"Target:   {r.target_price:,.4g} ({tp:+.2f}%)",
        f"Exit:     {_ts(r.exit_time)} @ {r.exit_price:,.4g}",
        f"Reason:   {str(r.exit_reason).upper()}",
        f"Duration: {int(r.duration_min)} min",
        "",
        f"Gross:    {r.gross_R:+.3f}R",
        f"Fees:     {r.fee_R:+.3f}R",
        f"Slip:     {r.slippage_R:+.3f}R",
        f"Funding:  {r.funding_R:+.3f}R",
        f"NET:      {r.net_R:+.3f}R",
    ])


def funnel_block(f: dict) -> str:
    """The S 13 funnel, as text."""
    L = [f"Setups detected        {f['setups_detected']:>10,}"]
    for k, lab in (("rejected_warmup", "warmup"),
                   ("rejected_stop_invalid", "stop invalid"),
                   ("rejected_no_entry_bar", "no entry bar"),
                   ("rejected_outside_split", "outside split")):
        if f[k]:
            L.append(f"   - {lab:<20} {f[k]:>10,}")
    L.append(f"Eligible setups        {f['eligible_setups']:>10,}")
    for k, lab in (("skipped_stop", "stop >5% or <=0"),
                   ("skipped_size", "size rounds to 0"),
                   ("rejected_position_open", "position already open")):
        if f[k]:
            L.append(f"   - {lab:<20} {f[k]:>10,}")
    L.append(f"Trades entered         {f['trades_entered']:>10,}")
    return "\n".join(L)


def md_table(d: pd.DataFrame) -> str:
    if d is None or len(d) == 0:
        return "_(none)_"

    def cell(v):
        if v is None or (isinstance(v, float) and not np.isfinite(v)):
            return ""
        if isinstance(v, (float, np.floating)):
            return f"{v:,.4f}".rstrip("0").rstrip(".") if abs(v) < 1e4 else f"{v:,.1f}"
        return str(v)

    cols = list(d.columns)
    return "\n".join(
        ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
        + ["| " + " | ".join(cell(v) for v in row) + " |"
           for row in d.itertuples(index=False, name=None)])
