"""Gainer-fade paper runner (live/fade.py, owner 2026-10-28): the pure logic is the research rule."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from live import fade as F

DAY = F.DAY
D0 = 1_790_000_000 // DAY * DAY


def _daily(closes, opens=None, vol=1_000_000.0, start=D0 - 30 * DAY):
    n = len(closes)
    opens = closes if opens is None else opens
    t = start + DAY * np.arange(n)
    c = np.asarray(closes, float)
    o = np.asarray(opens, float)
    return pd.DataFrame({"time": t.astype("int64"), "open": o, "high": np.maximum(o, c) * 1.02,
                         "low": np.minimum(o, c) * 0.98, "close": c, "volume": vol})


def test_readings_and_picks_follow_the_research_definitions():
    day = D0
    base = np.full(30, 100.0)
    a = _daily(np.r_[base[:-1], 130.0], opens=np.r_[base[:-1], 100.0])      # +30% yesterday
    b = _daily(np.r_[base[:-1], 110.0], opens=np.r_[base[:-1], 100.0])      # +10%
    c = _daily(np.r_[base[:-1], 150.0], opens=np.r_[base[:-1], 100.0], vol=10.0)   # +50% but thin
    d = _daily(np.r_[base[:-1], 90.0], opens=np.r_[base[:-1], 100.0])       # fell
    e = _daily(np.r_[base[:-1], 140.0], opens=np.r_[base[:-1], 100.0])[-5:]  # +40%, no 14-day ATR
    r = F.readings({"A": a, "B": b, "C": c, "D": d, "E": e}, {s: 1.0 for s in "ABCDE"}, day)
    got = F.picks(r)
    assert list(got.symbol) == ["A", "B"], "thin, falling and too-new coins are not eligible"
    row = r.set_index("symbol").loc["A"]
    assert row.prev_change == pytest.approx(0.30)
    # ATR: simple mean of the last 14 true ranges, over D-1's close
    x = a[a.time < day]
    pc = x.close.shift(1)
    tr = np.maximum.reduce([x.high - x.low, (x.high - pc).abs(), (x.low - pc).abs()])
    assert row.atrp == pytest.approx(pd.Series(tr).rolling(14).mean().iloc[-1] / x.close.iloc[-1])
    assert row.prev_turnover == pytest.approx(1_000_000 * (x.high.iloc[-1] + x.low.iloc[-1] + x.close.iloc[-1]) / 3)


def test_a_coin_missing_yesterday_is_not_read():
    a = _daily(np.full(30, 100.0))
    r = F.readings({"A": a}, {"A": 1.0}, D0 + 5 * DAY)       # last candle is days before D-1
    assert r.empty


def test_size_is_2pct_risk_capped_at_5x():
    assert F.size(250.0, 100.0, 120.0, 1.0) == 0               # $5 risk / $20 per contract
    assert F.size(250.0, 1.0, 1.2, 1.0) == 25                  # $5 / $0.20
    assert F.size(250.0, 0.01, 0.0101, 1.0) == 50_000          # risk binds: $5 / $0.0001
    assert F.size(250.0, 0.01, 0.01001, 1.0) == 125_000        # 5x cap binds: $1,250 / $0.01
    assert F.size(250.0, 100.0, 99.0, 1.0) == 0                # a stop below a short's entry is refused


def _pos(target=None):
    return F.Position(account="A", symbol="X", day=D0, entry_time=D0, entry=100.0, stop=120.0, target=target,
                      contracts=10, cv=1.0, last_checked=D0)


def _bars(rows):
    return pd.DataFrame(rows, columns=["time", "high", "low", "close"])


def test_stop_fills_at_the_worse_of_stop_and_close_and_beats_the_target():
    p = _pos(target=96.0)
    bars = _bars([(D0 + 60, 101, 99, 100), (D0 + 120, 125, 95, 124)])   # touches both: STOP first
    assert F.walk(p, bars)
    assert (p.exit_reason, p.exit_price, p.exit_time) == ("STOP", 124.0, D0 + 180)


def test_target_and_time_exit_and_resume():
    p = _pos(target=96.0)
    assert not F.walk(p, _bars([(D0 + 60, 101, 99, 100)]))
    assert p.last_checked == D0 + 60
    # re-feeding the same bar after a restart changes nothing
    assert not F.walk(p, _bars([(D0 + 60, 130, 50, 100)]))
    assert F.walk(p, _bars([(D0 + 120, 100, 95.5, 97)]))
    assert (p.exit_reason, p.exit_price) == ("TARGET", 96.0)
    q = _pos()
    assert F.walk(q, _bars([(D0 + DAY - 120, 101, 99, 100), (D0 + DAY - 60, 101, 90, 91), (D0 + DAY, 50, 40, 45)]))
    assert (q.exit_reason, q.exit_price, q.exit_time) == ("TIME", 91.0, D0 + DAY), "the next day's bar is never used"


def test_settle_money_matches_the_research_R():
    p = _pos()
    p.exit_price, p.exit_reason, p.exit_time = 91.0, "TIME", D0 + DAY
    m = F.settle(p, funding_rates=[0.01, -0.02])
    assert m["gross"] == pytest.approx(90.0)
    assert m["fees"] == pytest.approx(0.0005 * 10 * (100 + 91))
    assert m["funding"] == pytest.approx(-0.0001 * 10 * 100)
    # research R (gainer_fade_stop_lab._outcomes): (short return - fees) / stop distance
    research_r = (-(91 / 100 - 1) - 0.0005 - 0.0005 * 0.91) / 0.20
    assert (m["pnl"] - m["funding"]) / m["risk"] == pytest.approx(research_r)


ROWS = Path("out/gainer_fade/rows_2025_2026.parquet")
DAYS = Path("out/gainer_fade/days_2025_2026.parquet")


@pytest.mark.skipif(not (ROWS.exists() and DAYS.exists()), reason="research data not present (gitignored)")
def test_picks_and_stops_reproduce_the_research_lab():
    import sys
    sys.path.insert(0, "scripts")
    import gainer_fade_2025_lab as L
    rows = pd.read_parquet(ROWS)
    days = pd.read_parquet(DAYS)
    cv = {}
    for s, g in days.groupby("symbol"):
        x = g.dropna(subset=["volume"])
        x = x[(x.volume > 0) & (x.turnover > 0)]
        if len(x):
            cv[s] = float((x.turnover / (x.volume * (x.high + x.low + x.close) / 3)).median())
    daily = {s: g[["time", "open", "high", "low", "close", "volume"]] for s, g in days.groupby("symbol") if s in cv}
    for day in sorted(rows[rows.window == "2026"].time.unique())[::37][:6]:
        ours = F.picks(F.readings(daily, cv, int(day)))
        theirs = L.select(rows[rows.time == day], None)
        assert list(ours.symbol) == list(theirs.symbol), pd.Timestamp(day, unit="s").date()
        assert np.allclose(ours.atrp.values, theirs.atrp.values)


def test_the_brief_report_renders_both_accounts_and_flags_a_stalled_runner():
    import datetime as dt
    import sys
    sys.path.insert(0, "scripts")
    import brief_report as B
    now = dt.datetime(2026, 10, 29, 4, 0, tzinfo=dt.timezone.utc)
    fade = {"accounts": {"A": {"closed": 4, "wins": 3, "pnl": 6.5, "r": 1.3, "equity": 256.5, "avg_win_r": 0.6,
                               "avg_loss_r": -0.5, "open": 1, "skipped": 0},
                         "QUICK": {"closed": 4, "wins": 4, "pnl": 3.0, "r": 0.8, "equity": 253.0, "avg_win_r": 0.2,
                                   "avg_loss_r": None, "open": 0, "skipped": 0}},
            "recent": [{"account": "QUICK", "symbol": "XUSD", "entry": "2026-10-29T00:00:00+00:00",
                        "exit": "2026-10-29T01:00:00+00:00", "reason": "TARGET", "r": 0.19, "pnl": 0.9}],
            "open": [{"account": "A", "symbol": "XUSD", "entry": "2026-10-29T00:00:00+00:00"}],
            "days": [{"day": "2026-10-29", "status": "ENTERED", "detail": {"picks": [{"symbol": "XUSD", "change": 0.5}]}}],
            "heartbeat_at": (now - dt.timedelta(minutes=2)).isoformat()}
    hz = '{"status":"healthy","ready":true,"checks":[{"name":"runner_loop_alive","ok":true}]}'
    txt, facts, problems = B.build({"HEALTHZ": hz}, {"fade": fade}, now, stack="bot", errors_24h=0,
                                   probe_problems=[], log_events=[], snapshots=False, expect_experiment=False)
    assert problems == [] and "All normal" in txt
    assert "A — 24 h hold" in txt and "Quick — 4% target" in txt and "75%" in txt and "100%" in txt
    assert "XUSD (+50% yesterday)" in txt and facts["closed"] == 8
    stalled = dict(fade, heartbeat_at=(now - dt.timedelta(hours=1)).isoformat(), days=[])
    _, _, problems = B.build({"HEALTHZ": hz}, {"fade": stalled}, now, stack="bot", errors_24h=0,
                             probe_problems=[], log_events=[], snapshots=False, expect_experiment=False)
    assert any("15 minutes" in p for p in problems) and any("not been made" in p for p in problems)


def test_health_has_the_shape_the_report_reads():
    import time
    r = F.FadeRunner("postgresql://unused")
    assert r.health()["status"] == "unhealthy"            # not started, no pass yet
    r.ready, r.last_loop_at = True, time.time()
    h = r.health()
    assert h["status"] == "healthy" and h["ready"] is True and all(c["ok"] for c in h["checks"])


def test_a_thin_coin_with_no_last_minute_exits_at_its_last_trade_once_the_day_is_over():
    p = _pos()
    bars = _bars([(D0 + 60, 101, 99, 100), (D0 + 3600, 101, 97, 98)])        # no 23:59 candle
    assert not F.walk(p, bars), "the day is not known to be over"
    q = _pos()
    assert F.walk(q, bars, day_complete=True)
    assert (q.exit_reason, q.exit_price, q.exit_time) == ("TIME", 98.0, D0 + DAY)


def test_learning_readings_from_daily_book_and_flow():
    from live import fade_learning as FL
    d = _daily(np.r_[np.full(39, 100.0), 130.0], opens=np.r_[np.full(39, 100.0), 100.0], start=D0 - 40 * DAY)
    d.loc[d.index[-1], "volume"] = 3_000_000.0
    x = FL.daily_extras(d, D0)
    last = d.iloc[-1]
    assert x["wick"] == pytest.approx((last.high - last.close) / (last.high - last.low))
    assert x["prev2_change"] == pytest.approx(0.0)
    assert x["abnormal_vol"] == pytest.approx(3.0 * (last.high + last.low + last.close) / 3 / d.iloc[-2][["high", "low", "close"]].mean(), rel=1e-6)
    book = {"buy": [{"price": "99.9", "size": 10}, {"price": "90", "size": 999}],
            "sell": [{"price": "100.1", "size": 30}]}
    b = FL.book_readings(book, cv=1.0)
    assert b["spread_bps"] == pytest.approx(20.0)
    assert b["book_imbalance"] == pytest.approx((999 - 3003) / (999 + 3003))     # the 90 bid is >1% away
    f = FL.flow_readings([{"size": 1, "price": "10", "buyer_role": "taker"},
                          {"size": 3, "price": "10", "buyer_role": "maker"}], cv=1.0)
    assert f["taker_buy_share"] == pytest.approx(0.25)
    assert FL._result({"success": True, "result": {"buy": []}}) == {"buy": []}


def test_the_weekly_grade_compares_model_rule_and_random_picks():
    from live import fade_learning as FL
    rng = np.random.default_rng(0)
    rows = []
    for d in range(42):
        day = D0 + d * DAY
        for rank in range(1, 16):
            sig = rng.normal()
            win = sig > 0
            rows.append({"day": day, "symbol": f"C{rank}", "rank": rank,
                         "r_a": 0.6 if win else -0.5, "r_quick": 0.2 if win else -0.6,
                         **{f: np.nan for f in FL.FEATURES if f != "rank"}, "prev_change": sig})
    df = pd.DataFrame(rows)
    res = FL.grade_week(df, D0 + 35 * DAY)
    assert res["n_test"] == 7 * 15 and res["days"] == 7
    assert res["model_a"] > res["rule_a"] and res["random_p_a"] < 0.05, "an informative reading is found"
    few = FL.grade_week(df[df.day < D0 + 5 * DAY], D0 + 3 * DAY)
    assert few["summary"].startswith("not graded")


def test_entry_is_the_first_traded_minute_within_15_minutes():
    class C:
        def __init__(self, rows): self.rows = rows
        def candles(self, s, res, a, b, drop_forming=True): return [x for x in self.rows if a <= x["time"] <= b]
    thin = C([{"time": D0 + 120, "close": "1.5"}, {"time": D0 + 180, "close": "1.6"}])
    assert F.first_trade(thin, "X", D0, D0 + 300) == (D0 + 120, 1.5), "00:00 had no trade: 00:02 is the entry"
    assert F.first_trade(C([]), "X", D0, D0 + 300) == "wait"
    assert F.first_trade(C([]), "X", D0, D0 + 20 * 60) is None, "no trade at all in 15 minutes: skipped"
    late = C([{"time": D0 + 16 * 60, "close": "2"}])
    assert F.first_trade(late, "X", D0, D0 + 20 * 60) is None
