"""HTTP surface: /healthz, /readyz, /metrics, /api/*, and the dashboard.

Nothing here exposes a credential, because the process holds none. Nothing here
can place an order, because no such method exists in the process. The dashboard
is read-only by construction rather than by permission check.

Storage is UTC throughout; IST appears only in rendered output, per section 15.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from app.api.dashboard import render_dashboard
from app.monitoring.health import (
    evaluate_health,
    evaluate_readiness,
    json_safe,
)
from app.monitoring.metrics import render_prometheus

IST = timezone(timedelta(hours=5, minutes=30))


def to_ist(ts) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(float(ts), tz=IST).strftime("%Y-%m-%d %H:%M:%S IST")


def to_utc(ts) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def create_app(bot) -> FastAPI:
    app = FastAPI(title="Delta India paper-trading bot", docs_url=None,
                  redoc_url=None)

    async def _health():
        writable = await bot.repo.is_writable()
        return evaluate_health(bot.health_snapshot(), db_writable=writable)

    @app.get("/healthz")
    async def healthz() -> Response:
        report = await _health()
        return JSONResponse(report.to_dict(), status_code=report.status_code)

    @app.get("/readyz")
    async def readyz() -> Response:
        snap = bot.health_snapshot()
        warm = all(len(b.frame_5m()) >= 145 for b in bot.builder.builders.values())
        report = evaluate_readiness(
            snap,
            db_connected=await bot.repo.is_writable(),
            lock_held=(bot.lock is None or getattr(bot.lock, "held", False)),
            backfill_complete=bot.ready,
            indicators_warm=warm,
            execution_ready=bot.broker is not None,
        )
        return JSONResponse(report.to_dict(), status_code=report.status_code)

    @app.get("/metrics")
    async def metrics() -> Response:
        report = await _health()
        snap = dict(report.snapshot)
        snap.update(healthy=report.healthy, daily_pnl=bot.state.daily_pnl,
                    cumulative_pnl=bot.state.realized_pnl,
                    drawdown_pct=bot.state.drawdown_pct)
        return PlainTextResponse(
            render_prometheus(bot.metrics, bot.feed.stats.as_dict(), snap),
            media_type="text/plain; version=0.0.4")

    # -- json API --------------------------------------------------------

    @app.get("/api/status")
    async def status() -> dict:
        report = await _health()
        snap = report.snapshot
        return json_safe({
            "strategy_version": bot.strategy.version,
            "strategy_config_hash": bot.strategy.config_hash,
            "symbols": list(bot.symbols),
            "ready": bot.ready,
            "healthy": report.healthy,
            "failing_checks": report.failures,
            "ws_connected": snap["ws_connected"],
            "seconds_since_ws_message": snap["seconds_since_ws_message"],
            "last_closed_1m_utc": to_utc(snap["last_closed_1m"]),
            "last_closed_1m_ist": to_ist(snap["last_closed_1m"]),
            "uptime_seconds": int(snap["uptime_seconds"]),
            "recent_gaps": snap["recent_gaps"],
            "recovery_error": snap["recovery_error"],
            "instance_uid": bot.instance_uid,
            "metrics": bot.metrics.as_dict(),
            "feed": bot.feed.stats.as_dict(),
        })

    @app.get("/api/market")
    async def market() -> list[dict]:
        out = []
        for sym in bot.symbols:
            b = bot.builder[sym]
            last = b.last_closed_1m
            row = {
                "symbol": sym,
                "state": bot.halts[sym].state.value,
                "last_price": last.close if last else None,
                "last_closed_1m_ist": to_ist(last.start if last else None),
                "bars_1m": len(b.bars),
                "gaps": b.stats.gaps,
            }
            five = b.frame_5m(limit=bot.strategy.window_bars)
            one = b.frame(limit=bot.strategy.window_bars)
            if len(five) >= 145 and len(one) >= 145:
                from app.strategy.rules import IndicatorSnapshot
                snap = IndicatorSnapshot(five, bot.strategy).at(-1)
                row.update(supertrend=round(snap["supertrend"], 4),
                           trend="up" if snap["direction"] < 0 else "down",
                           adx=round(snap["adx"], 2),
                           plus_di=round(snap["plus_di"], 2),
                           minus_di=round(snap["minus_di"], 2),
                           williams_r=round(snap["wpr"], 2))
            out.append(row)
        return out

    @app.get("/api/positions")
    async def positions() -> list[dict]:
        rows = []
        for p in bot.broker.get_positions():
            cv = bot.costs[p.symbol].contract_value
            px = p.last_price or p.entry_price
            rows.append({
                "position_uid": p.position_uid, "symbol": p.symbol,
                "side": "LONG" if p.side > 0 else "SHORT", "status": p.status,
                "quantity": p.quantity, "entry": p.entry_price,
                "stop": p.stop_price, "target": p.target_price,
                "current_price": px,
                "unrealized_pnl": round(p.unrealized(px, cv), 2),
                "r": round(p.r_at(px, cv), 3),
                "opened_ist": to_ist(p.opened_at),
            })
        return rows

    @app.get("/api/risk")
    async def risk() -> dict:
        s, cfg = bot.state, bot.settings.risk
        now = int(time.time())
        return {
            "equity": round(s.equity, 2),
            "peak_equity": round(s.peak_equity, 2),
            "daily_pnl": round(s.daily_pnl, 2),
            "daily_loss_pct": round(100 * s.daily_loss_pct, 3),
            "daily_loss_remaining": round(
                max(0.0, cfg.max_daily_loss_pct - s.daily_loss_pct) *
                (s.day_start_equity or s.equity), 2),
            "drawdown_pct": round(100 * s.drawdown_pct, 3),
            "trades_today": s.trades_today,
            "max_trades_per_day": cfg.max_trades_per_day,
            "consecutive_losses": s.consecutive_losses,
            "max_consecutive_losses": cfg.max_consecutive_losses,
            "risk_per_trade_pct": 100 * cfg.risk_per_trade,
            "minimum_rr": cfg.minimum_rr,
            "cooldown_trade_remaining": max(
                0, cfg.cooldown_after_trade_seconds - (now - s.last_trade_at))
                if s.last_trade_at else 0,
            "cooldown_loss_remaining": max(
                0, cfg.cooldown_after_loss_seconds - (now - s.last_loss_at))
                if s.last_loss_at else 0,
            "wins": s.wins, "losses": s.losses,
            "day_utc": s.day,
        }

    @app.get("/api/signals")
    async def signals(limit: int = 50) -> list[dict]:
        rows = await bot.repo.recent_signals(limit)
        for r in rows:
            bo = r.get("bar_open")
            r["bar_open_ist"] = to_ist(
                bo.timestamp() if hasattr(bo, "timestamp") else bo)
        return rows

    @app.get("/api/trades")
    async def trades(limit: int = 50) -> list[dict]:
        rows = await bot.repo.load_recent_positions(limit)
        return [{
            "symbol": p.symbol, "side": "LONG" if p.side > 0 else "SHORT",
            "status": p.status, "entry": p.entry_price, "stop": p.stop_price,
            "target": p.target_price, "exit": p.exit_price,
            "quantity": p.quantity, "pnl": p.realized_pnl,
            "r": p.r_multiple, "reason": p.exit_reason,
            "opened_ist": to_ist(p.opened_at), "closed_ist": to_ist(p.closed_at),
        } for p in rows]

    @app.get("/api/events")
    async def events(limit: int = 50) -> list[dict]:
        return await bot.repo.recent_system_events(limit)

    # -- dashboard -------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> str:
        return render_dashboard()

    return app
