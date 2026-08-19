"""HTTP surface: health/readiness status codes, dashboard, and no leakage."""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.api.app import create_app, to_ist, to_utc
from app.config.settings import RiskConfig
from tests.live.test_recovery import make_bot, open_a_position

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def bot():
    b = make_bot({})
    await b.start()
    return b


@pytest_asyncio.fixture
async def client(bot):
    return TestClient(create_app(bot)), bot


class TestEndpoints:
    async def test_healthz_is_503_when_the_feed_is_dead(self, client):
        """The stub feed never connects, so this must NOT be 200."""
        c, _ = client
        r = c.get("/healthz")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "unhealthy"
        assert any(x["name"] == "websocket_fresh" and not x["ok"]
                   for x in body["checks"])

    async def test_health_names_every_failing_check(self, client):
        c, _ = client
        checks = c.get("/healthz").json()["checks"]
        names = {x["name"] for x in checks}
        assert names == {"websocket_fresh", "candles_fresh", "no_recent_gaps",
                         "database_writable", "strategy_running",
                         # A flag is not evidence: the loop can die while every
                         # other signal stays green, so health asks the loop
                         # itself when it last ran.
                         "evaluation_loop_alive"}

    async def test_readyz_reports_why_it_is_not_ready(self, client):
        c, _ = client
        body = c.get("/readyz").json()
        assert "checks" in body and body["checks"]

    async def test_metrics_is_prometheus_text(self, client):
        c, _ = client
        r = c.get("/metrics")
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]
        for m in ("deltabot_signals_detected_total", "deltabot_open_positions",
                  "deltabot_websocket_reconnects_total", "deltabot_system_health"):
            assert m in r.text

    async def test_status_exposes_the_config_hash(self, client):
        c, bot = client
        s = c.get("/api/status").json()
        assert s["strategy_config_hash"] == bot.strategy.config_hash
        assert s["symbols"] == list(bot.symbols)
        assert s["strategy_version"].startswith("H-WPR-1-VariantA@")

    async def test_market_endpoint_reports_indicators(self, client):
        c, _ = client
        rows = c.get("/api/market").json()
        assert len(rows) == 1 and rows[0]["symbol"] == "BTCUSD"
        assert rows[0]["state"] == "LIVE"
        assert "adx" in rows[0] and "williams_r" in rows[0]

    async def test_risk_endpoint_reports_the_live_limits(self, client):
        c, _ = client
        r = c.get("/api/risk").json()
        assert r["risk_per_trade_pct"] == pytest.approx(0.5)
        assert r["minimum_rr"] == 2.0
        assert r["max_trades_per_day"] == RiskConfig().max_trades_per_day
        assert "daily_loss_remaining" in r

    async def test_positions_and_trades_reflect_an_open_position(self, bot):
        await open_a_position(bot)
        c = TestClient(create_app(bot))
        pos = c.get("/api/positions").json()
        assert len(pos) == 1
        assert pos[0]["side"] == "LONG"
        assert pos[0]["stop"] < pos[0]["entry"] < pos[0]["target"]
        assert pos[0]["opened_ist"].endswith("IST")
        assert c.get("/api/trades").json()[0]["symbol"] == "BTCUSD"

    async def test_signals_endpoint_returns_rejection_reasons(self, bot):
        from app.strategy.explanation import Explanation, Outcome
        from app.runtime.bot import idempotency_key
        e = Explanation(symbol="BTCUSD", bar_open=1786560300,
                        primary_timeframe="5m", confirmation_timeframe="1m",
                        strategy_version=bot.strategy.version,
                        strategy_config_hash=bot.strategy.config_hash,
                        outcome=Outcome.REJECTED, direction=1)
        e.rejection_reason = "reward/risk 1.40 is below minimum_rr 2.00"
        await bot._record_signal(
            e, idempotency_key("BTCUSD", 1786560300, 1, bot.strategy.config_hash))
        c = TestClient(create_app(bot))
        rows = c.get("/api/signals").json()
        assert rows[0]["outcome"] == "REJECTED"
        assert "minimum_rr" in rows[0]["rejection_reason"]

    async def test_dashboard_renders_and_is_self_contained(self, client):
        c, _ = client
        r = c.get("/")
        assert r.status_code == 200
        html = r.text
        assert "PAPER ONLY" in html
        assert "cannot place a real order" in html
        # No external fetches: everything must work behind a tunnel with no
        # egress to a CDN.
        for token in ("http://cdn", "https://cdn", "unpkg.com", "jsdelivr",
                      "googleapis.com"):
            assert token not in html


class TestNoLeakage:
    async def test_no_endpoint_exposes_a_credential_field(self, client):
        c, _ = client
        for path in ("/api/status", "/api/risk", "/api/market",
                     "/api/positions", "/api/trades", "/healthz", "/readyz"):
            body = c.get(path).text.lower()
            for bad in ("secret", "api_key", "apikey", "password", "token",
                        "signature"):
                assert bad not in body, f"{path} leaks {bad}"

    async def test_database_url_is_never_returned(self, client):
        c, bot = client
        assert "postgresql://" not in c.get("/api/status").text

    async def test_openapi_docs_are_disabled(self, client):
        c, _ = client
        assert c.get("/docs").status_code == 404


class TestTimezones:
    async def test_storage_is_utc_and_display_is_ist(self):
        ts = 1786560000                       # 2026-08-12T18:40:00Z
        assert to_utc(ts).startswith("2026-08-12T18:40:00")
        assert to_ist(ts) == "2026-08-13 00:10:00 IST"

    async def test_null_timestamps_survive(self):
        assert to_ist(None) is None and to_utc(None) is None


class TestHeartbeatLogIsValidJson:
    """The heartbeat line is what the bot-silent alarm counts.

    Before the first websocket message, `seconds_since_ws_message` is +inf.
    `json.dumps` writes that as a bare `Infinity`, which Python round-trips but
    which is not RFC-8259 JSON -- and the CloudWatch metric filter that watches
    this log is not Python. Shipped once and caught on the live host.
    """

    async def test_a_non_finite_field_never_reaches_the_log(self):
        import json
        from app.monitoring.logging import JsonFormatter
        import logging as _logging
        from app.monitoring.health import json_safe

        silence = json_safe(float("inf"))
        assert silence is None, "json_safe must flatten +inf before it is logged"

        record = _logging.LogRecord("app.runtime.bot", _logging.INFO, __file__, 1,
                                    "heartbeat", (), None)
        record.seconds_since_ws_message = (
            round(silence, 1) if silence is not None else None)
        line = JsonFormatter().format(record)
        assert "Infinity" not in line and "NaN" not in line, line
        parsed = json.loads(line)
        assert parsed["seconds_since_ws_message"] is None

    async def test_the_naive_guard_that_failed_is_documented(self):
        """`x or -1` does not catch inf, because inf is truthy."""
        assert (float("inf") or -1) == float("inf")
        assert round(float("inf"), 1) == float("inf")
