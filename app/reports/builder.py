"""Forward-test reporting.

AUDIT FINDING F7. There was no daily report and no final report, so a 30-day
run produced a database nobody would read.

All arithmetic lives here, in pure functions over rows the repository hands
back, so there is exactly ONE implementation of every figure and it is testable
without a database. The repository does fetching; this does counting.

THREE TIMESTAMPS, KEPT APART. Every event carries ``exchange_ts`` (when the
market produced it) and ``received_ts`` (when this process learned of it).
Processing time is their difference. Reporting one number for all three would
hide exactly the lag that tells an operator whether the bot is keeping up.

A NOTE ON WHAT THESE NUMBERS MEAN. The strategy being forward-tested was
classified NO ECONOMIC EDGE by the research programme. Ratios computed on a
handful of trades are noise, so every report states its own sample size and
``render_final`` refuses to present performance ratios below a floor. That is
not modesty, it is the difference between a report and a misleading one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

#: Below this many closed trades, performance ratios are not reported as
#: figures. Chosen before looking at any result.
MIN_TRADES_FOR_RATIOS = 30

IST = timezone(timedelta(hours=5, minutes=30))


def _ts(v) -> int | None:
    if v is None:
        return None
    return int(v.timestamp()) if hasattr(v, "timestamp") else int(v)


def _f(v, default=0.0) -> float:
    if v is None:
        return default
    try:
        out = float(v)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _ist(ts) -> str:
    t = _ts(ts)
    return (datetime.fromtimestamp(t, tz=IST).strftime("%Y-%m-%d %H:%M:%S IST")
            if t else "-")


@dataclass
class Section:
    title: str
    rows: list[tuple[str, object]] = field(default_factory=list)

    def add(self, label, value):
        self.rows.append((label, value))
        return self

    def render(self) -> str:
        if not self.rows:
            return f"{self.title}\n  (nothing)"
        w = max(len(str(k)) for k, _ in self.rows)
        body = "\n".join(f"  {str(k).ljust(w)}  {v}" for k, v in self.rows)
        return f"{self.title}\n{body}"


@dataclass
class Report:
    experiment_id: str | None
    experiment: dict | None
    day: str | None
    sections: list[Section] = field(default_factory=list)
    data: dict = field(default_factory=dict)

    def render(self, header: str) -> str:
        parts = [header, "=" * len(header), ""]
        parts += [s.render() + "\n" for s in self.sections]
        return "\n".join(parts)

    def render_status(self) -> str:
        return self.render(f"STATUS  {self.experiment_id}")

    def render_daily(self) -> str:
        return self.render(
            f"DAILY REPORT  {self.experiment_id}  {self.day or '(all)'}")

    def render_final(self) -> str:
        return self.render(f"FINAL REPORT  {self.experiment_id}")


def _identity(exp: dict | None) -> Section:
    s = Section("IDENTITY")
    if not exp:
        return s.add("experiment", "UNBOUND -- decisions carry no experiment id")
    for label, key in (("experiment_id", "experiment_id"), ("status", "status"),
                       ("strategy", "strategy_version"),
                       ("config_hash", "config_hash"),
                       ("strategy_hash", "strategy_hash"),
                       ("risk_hash", "risk_hash"),
                       ("execution_hash", "execution_hash"),
                       ("git_sha", "git_sha"), ("app_version", "app_version")):
        s.add(label, exp.get(key))
    s.add("git_dirty", exp.get("git_dirty"))
    s.add("symbols", ", ".join(exp.get("symbols") or []))
    s.add("started_at", _ist(exp.get("started_at")))
    snap = exp.get("snapshot") or {}
    strat = snap.get("strategy") or {}
    if strat:
        s.add("strategy params",
              f"ST({strat.get('supertrend', {}).get('atr_period')}, "
              f"{strat.get('supertrend', {}).get('multiplier')}) "
              f"ADX {strat.get('adx', {}).get('period')}/"
              f"DI {strat.get('adx', {}).get('di_period')} "
              f">= {strat.get('adx', {}).get('minimum')} "
              f"WPR {strat.get('williams_r', {}).get('period')} "
              f"target {strat.get('target_r')}R")
    return s


def _signals(rows: list[dict]) -> Section:
    s = Section("SIGNALS")
    by_outcome: dict[str, int] = {}
    by_symbol: dict[str, int] = {}
    by_dir = {"long": 0, "short": 0}
    reasons: dict[str, int] = {}
    for r in rows:
        o = r.get("outcome")
        by_outcome[o] = by_outcome.get(o, 0) + 1
        if o in ("APPROVED", "REJECTED"):
            by_symbol[r["symbol"]] = by_symbol.get(r["symbol"], 0) + 1
            d = r.get("direction")
            if d:
                by_dir["long" if d > 0 else "short"] += 1
        if o == "REJECTED" and r.get("rejection_reason"):
            key = str(r["rejection_reason"]).split(":")[0][:52]
            reasons[key] = reasons.get(key, 0) + 1
    s.add("evaluations", len(rows))
    for k in ("NO_SETUP", "SUPPRESSED", "REJECTED", "APPROVED"):
        s.add(f"  {k.lower()}", by_outcome.get(k, 0))
    s.add("by symbol", ", ".join(f"{k}={v}" for k, v in sorted(by_symbol.items()))
          or "-")
    s.add("by direction", f"long={by_dir['long']} short={by_dir['short']}")
    for reason, n in sorted(reasons.items(), key=lambda x: -x[1])[:8]:
        s.add(f"  reject: {reason}", n)
    return s


def _orders(orders: list[dict], fills: list[dict]) -> Section:
    s = Section("ORDERS AND EXECUTION")
    entry = [o for o in orders if o.get("purpose") == "entry"]
    exits = [o for o in orders if o.get("purpose") != "entry"]
    filled = [o for o in orders if o.get("status") == "FILLED"]
    expired = [o for o in orders if o.get("status") == "EXPIRED"]
    cancelled = [o for o in orders if o.get("status") == "CANCELLED"]
    s.add("entry orders", len(entry))
    s.add("exit orders", len(exits))
    s.add("filled", len(filled))
    s.add("expired", len(expired))
    s.add("cancelled", len(cancelled))
    if entry:
        s.add("entry fill rate",
              f"{100*sum(1 for o in entry if o.get('status')=='FILLED')/len(entry):.1f}%")

    delays = [d for d in (_delay(o) for o in filled) if d is not None]
    s.add("time to fill", f"n={len(delays)} mean={_mean(delays):.2f}s "
                          f"max={max(delays):.2f}s" if delays else "-")
    slips = [_f(f.get("slippage")) for f in fills if f.get("purpose") == "entry"]
    s.add("entry slippage", f"total ${sum(slips):.2f} mean ${_mean(slips):.4f}"
          if slips else "-")
    liq = {}
    for f in fills:
        liq[f.get("liquidity")] = liq.get(f.get("liquidity"), 0) + 1
    s.add("maker/taker", ", ".join(f"{k}={v}" for k, v in sorted(liq.items()))
          or "-")
    return s


def _delay(o: dict) -> float | None:
    a, b = _ts(o.get("created_exchange_ts")), _ts(o.get("filled_exchange_ts"))
    return None if (a is None or b is None) else max(0.0, b - a)


def _mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def _positions(rows: list[dict]) -> Section:
    s = Section("POSITIONS")
    closed = [p for p in rows if p.get("status") == "CLOSED"]
    open_ = [p for p in rows if p.get("status") != "CLOSED"]
    s.add("opened", len(rows))
    s.add("closed", len(closed))
    s.add("still open", len(open_))
    s.add("long / short",
          f"{sum(1 for p in rows if p['side'] > 0)} / "
          f"{sum(1 for p in rows if p['side'] < 0)}")
    holds = [h for h in (_hold(p) for p in closed) if h is not None]
    if holds:
        holds.sort()
        s.add("hold (hours)",
              f"median {holds[len(holds)//2]/3600:.2f} max {holds[-1]/3600:.2f}")
    if open_:
        ages = [_ts(p.get("opened_at")) for p in open_]
        s.add("oldest open", _ist(min(a for a in ages if a)))
    by_sym: dict[str, int] = {}
    for p in rows:
        by_sym[p["symbol"]] = by_sym.get(p["symbol"], 0) + 1
    s.add("by symbol", ", ".join(f"{k}={v}" for k, v in sorted(by_sym.items()))
          or "-")
    return s


def _hold(p: dict) -> float | None:
    a, b = _ts(p.get("opened_at")), _ts(p.get("closed_at"))
    return None if (a is None or b is None) else float(b - a)


def pnl_breakdown(positions: list[dict], funding: list[dict]) -> dict:
    """Gross, costs and net -- kept separate, as the research programme did.

    Reporting one net figure hides whether a strategy lost on signal or on
    cost, which is the whole distinction the research turned on.
    """
    closed = [p for p in positions if p.get("status") == "CLOSED"]
    fees = sum(_f(p.get("entry_fee")) + _f(p.get("exit_fee")) for p in closed)
    fund = sum(_f(f.get("funding_amount")) for f in funding)
    slip = sum(_f(p.get("entry_slippage")) + _f(p.get("exit_slippage"))
               for p in closed)
    net = sum(_f(p.get("realized_pnl")) for p in closed)
    rs = [_f(p.get("r_multiple")) for p in closed if p.get("r_multiple") is not None]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return {
        "trades": len(closed),
        "gross_pnl": net + fees + fund,
        "fees": fees,
        "funding": fund,
        "slippage": slip,
        "net_pnl": net,
        "r_values": rs,
        "expectancy_r": _mean(rs),
        "win_rate": (len(wins) / len(rs)) if rs else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "avg_winner_r": _mean(wins),
        "avg_loser_r": _mean(losses),
        "max_drawdown_r": _max_drawdown(rs),
    }


def _max_drawdown(rs: list[float]) -> float:
    peak = equity = worst = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def _pnl_section(b: dict, *, ratios: bool) -> Section:
    s = Section("P&L")
    s.add("closed trades", b["trades"])
    s.add("gross P&L", f"${b['gross_pnl']:+.2f}")
    s.add("  fees", f"${-b['fees']:+.2f}")
    s.add("  funding", f"${-b['funding']:+.2f}")
    s.add("  slippage", f"${-b['slippage']:+.2f} (already inside gross)")
    s.add("net P&L", f"${b['net_pnl']:+.2f}")
    if not b["trades"]:
        return s
    if ratios:
        s.add("expectancy", f"{b['expectancy_r']:+.3f}R")
        s.add("win rate", f"{100*b['win_rate']:.1f}%")
        s.add("profit factor",
              f"{b['profit_factor']:.2f}" if b["profit_factor"] else "n/a")
        s.add("avg winner", f"{b['avg_winner_r']:+.2f}R")
        s.add("avg loser", f"{b['avg_loser_r']:+.2f}R")
        s.add("max drawdown", f"{b['max_drawdown_r']:.2f}R")
    else:
        s.add("ratios", f"WITHHELD -- {b['trades']} closed trades is below the "
                        f"{MIN_TRADES_FOR_RATIOS}-trade floor")
        s.add("", "Insufficient sample size for profitability inference.")
    return s


def _data_quality(system_events: list[dict], quarantined: list[dict],
                  signals: list[dict]) -> Section:
    s = Section("DATA QUALITY AND RELIABILITY")
    counts: dict[str, int] = {}
    for e in system_events:
        counts[e["event_type"]] = counts.get(e["event_type"], 0) + 1
    for label, key in (("startups", "STARTUP"), ("ready", "READY"),
                       ("shutdowns", "SHUTDOWN"),
                       ("gaps repaired", "GAP_REPAIRED"),
                       ("gap repair failed", "GAP_REPAIR_FAILED"),
                       ("incomplete 5m", "INCOMPLETE_5M"),
                       ("halts", "POSITIONS_SUSPENDED"),
                       ("loop errors", "LOOP_ERROR"),
                       ("config drift refused", "CONFIG_DRIFT_REFUSED"),
                       ("duplicate positions refused", "DUPLICATE_POSITION_REFUSED")):
        s.add(label, counts.get(key, 0))
    s.add("fills quarantined", len(quarantined))
    suppressed = sum(1 for r in signals if r.get("outcome") == "SUPPRESSED")
    s.add("evaluations suppressed", suppressed)
    return s


def _timing(rows: list[dict]) -> Section:
    """exchange_ts vs received_ts. One number for both would hide the lag."""
    s = Section("TIMING")
    lags = []
    for r in rows:
        a, b = _ts(r.get("exchange_ts")), _ts(r.get("received_ts"))
        if a and b:
            lags.append(b - a)
    if not lags:
        return s.add("processing lag", "no paired timestamps yet")
    lags.sort()
    s.add("samples", len(lags))
    s.add("median lag", f"{lags[len(lags)//2]:.1f}s")
    s.add("p95 lag", f"{lags[int(len(lags)*0.95)]:.1f}s")
    s.add("max lag", f"{lags[-1]:.1f}s")
    s.add("note", "exchange_ts = market time; received_ts = wall time here")
    return s


def _risk(risk_events: list[dict]) -> Section:
    s = Section("RISK")
    by_limit: dict[str, int] = {}
    for e in risk_events:
        k = e.get("limit_name") or "(unnamed)"
        by_limit[k] = by_limit.get(k, 0) + 1
    s.add("risk-gate rejections", len(risk_events))
    for k, v in sorted(by_limit.items(), key=lambda x: -x[1]):
        s.add(f"  {k}", v)
    return s


async def build_report(repo, experiment_id: str | None, *,
                       day: str | None = None) -> Report:
    """Assemble a report. ``day`` is a UTC date, YYYY-MM-DD."""
    since = until = None
    if day:
        d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        since, until = int(d.timestamp()), int((d + timedelta(days=1)).timestamp())

    rows = await repo.report_rows(experiment_id, since, until)
    b = pnl_breakdown(rows["positions"], rows["funding"])
    rep = Report(experiment_id=experiment_id, experiment=rows.get("experiment"),
                 day=day, data={**rows, "pnl": b})
    rep.sections = [
        _identity(rows.get("experiment")),
        _signals(rows["signals"]),
        _orders(rows["orders"], rows["fills"]),
        _positions(rows["positions"]),
        _pnl_section(b, ratios=b["trades"] >= MIN_TRADES_FOR_RATIOS),
        _risk(rows["risk_events"]),
        _data_quality(rows["system_events"], rows["quarantined"],
                      rows["signals"]),
        _timing(rows["signals"]),
    ]
    return rep
