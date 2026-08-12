"""Counters, exported as Prometheus text without requiring the client library.

Written as a plain dataclass rather than prometheus_client Counters so the
whole bot is importable and testable without the optional `live` extra
installed. The exposition format is simple enough to emit directly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Metrics:
    ticks: int = 0
    candles_1m: int = 0
    candles_5m: int = 0
    incomplete_5m: int = 0
    bad_messages: int = 0
    signals_detected: int = 0
    signals_rejected: int = 0
    duplicate_signals: int = 0
    orders: int = 0
    orders_expired: int = 0
    fills: int = 0
    fills_quarantined: int = 0
    closed_positions: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


#: name -> (metric type, help text). Gauges come from live state rather than
#: from the counter dataclass.
_COUNTERS = {
    "ticks": "market ticks processed",
    "candles_1m": "closed 1m candles built",
    "candles_5m": "closed 5m candles derived",
    "incomplete_5m": "5m buckets missing at least one minute",
    "bad_messages": "unusable websocket messages dropped",
    "signals_detected": "setups detected by the strategy",
    "signals_rejected": "setups rejected by the risk engine",
    "duplicate_signals": "evaluations already recorded (idempotency hits)",
    "orders": "paper orders created",
    "orders_expired": "entry orders expired or cancelled unfilled",
    "fills": "paper fills booked",
    "fills_quarantined": "fills that could not be matched to a position",
    "closed_positions": "paper positions closed",
}


def render_prometheus(metrics: Metrics, feed_stats: dict, health: dict,
                      *, prefix: str = "deltabot") -> str:
    """Prometheus text exposition. No secrets are ever emitted."""
    lines: list[str] = []

    def counter(name: str, value, help_text: str) -> None:
        lines.append(f"# HELP {prefix}_{name} {help_text}")
        lines.append(f"# TYPE {prefix}_{name} counter")
        lines.append(f"{prefix}_{name} {value}")

    def gauge(name: str, value, help_text: str) -> None:
        lines.append(f"# HELP {prefix}_{name} {help_text}")
        lines.append(f"# TYPE {prefix}_{name} gauge")
        lines.append(f"{prefix}_{name} {value}")

    d = metrics.as_dict()
    for name, help_text in _COUNTERS.items():
        counter(f"{name}_total", d.get(name, 0), help_text)

    counter("websocket_messages_total", feed_stats.get("websocket_messages", 0),
            "websocket messages received")
    counter("websocket_reconnects_total", feed_stats.get("websocket_reconnects", 0),
            "websocket reconnections")
    counter("stale_feed_events_total", feed_stats.get("stale_feed_events", 0),
            "times the feed went silent with the socket open")

    gauge("open_positions", health.get("open_positions", 0), "open paper positions")
    gauge("equity", health.get("equity", 0.0), "paper account equity")
    gauge("daily_pnl", health.get("daily_pnl", 0.0), "realised PnL today")
    gauge("cumulative_pnl", health.get("cumulative_pnl", 0.0), "realised PnL total")
    gauge("max_drawdown_pct", health.get("drawdown_pct", 0.0),
          "drawdown from peak equity")
    gauge("data_gap_total", health.get("recent_gaps", 0),
          "candle gaps in the recent window")
    gauge("last_market_event_timestamp",
          feed_stats.get("last_message_at", 0) or 0,
          "unix time of the last websocket message")
    gauge("system_health", 1 if health.get("healthy") else 0,
          "1 when every health condition holds")
    gauge("ready", 1 if health.get("ready") else 0, "1 when warm-up is complete")
    gauge("uptime_seconds", int(health.get("uptime_seconds", 0)), "process uptime")
    return "\n".join(lines) + "\n"
