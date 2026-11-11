"""The funding-collection paper account (live/funding_paper.py): the backtest's rule, real fills, never an order."""
from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

from live import funding_paper as FP
from tests.live.conftest import requires_pg

DAY, HOUR = FP.DAY, FP.HOUR
D0 = 1794268800 - (1794268800 // DAY) % 3 * DAY                     # a rebalance day (day index divisible by 3)


def test_rebalance_every_third_utc_day():
    assert FP.is_rebalance_day(D0)
    assert not FP.is_rebalance_day(D0 + DAY) and not FP.is_rebalance_day(D0 + 2 * DAY)
    assert FP.is_rebalance_day(D0 + 3 * DAY)


def test_daily_funding_sums_each_days_settlements_and_needs_two_days():
    rows = [dict(time=t, close=0.01) for t in range(D0 - 3 * DAY, D0, HOUR)]          # 0.01% at every hour
    by = FP.daily_funding(rows, 8 * HOUR, [D0 - 3 * DAY, D0 - 2 * DAY, D0 - DAY])
    assert by == pytest.approx({D0 - 3 * DAY: 0.0003, D0 - 2 * DAY: 0.0003, D0 - DAY: 0.0003})   # 3 stamps a day
    by4 = FP.daily_funding(rows, 4 * HOUR, [D0 - DAY])
    assert by4[D0 - DAY] == pytest.approx(0.0006)                                      # 6 stamps at 4 h
    assert FP.signal({1: 0.001}) is None and FP.signal({1: 0.001, 2: 0.003}) == pytest.approx(0.002)
    stale = [dict(time=D0 - 10 * DAY, close=0.5)]                                     # a row far before: not used
    assert FP.daily_funding(stale, 8 * HOUR, [D0 - DAY]) == {}


def test_choose_longs_the_lowest_and_shorts_the_highest_fifth():
    sig = {f"C{i:02d}USD": (i - 20) / 1e4 for i in range(40)}                       # 40 coins
    longs, shorts = FP.choose(sig)
    assert len(longs) == len(shorts) == 8                                             # a fifth
    assert longs == [f"C{i:02d}USD" for i in range(8)]                                # most negative funding
    assert shorts == [f"C{i:02d}USD" for i in range(32, 40)]                          # most positive funding
    assert FP.choose({f"X{i}": i for i in range(FP.MIN_COINS - 1)}) == ([], [])       # too few coins: no book
    assert len(FP.choose({f"X{i}": i for i in range(20)})[0]) == 4                    # int(20 * 0.2)


def test_backtest_parity_of_the_selection():
    """scripts/backtest_survivors.portfolio ranks -F3 and longs the TOP k = lowest funding; k = max(int(n*0.2), 3)."""
    import numpy as np
    rng = np.random.default_rng(1)
    sig = {f"S{i}": float(x) for i, x in enumerate(rng.normal(0, 1e-3, 57))}
    s = pd.Series(sig)
    a = (-s).sort_values()
    k = max(int(len(a) * 0.2), 3)
    longs, shorts = FP.choose(sig)
    assert set(longs) == set(a.index[-k:]) and set(shorts) == set(a.index[:k])


def test_funding_sign_contracts_and_pnl():
    # a long pays a positive rate, a short receives it; 0.1% twice on 10 units at $2 = $0.04
    assert FP.funding_usd(+1, 10, 2.0, [0.1, 0.1]) == pytest.approx(-0.04)
    assert FP.funding_usd(-1, 10, 2.0, [0.1, 0.1]) == pytest.approx(+0.04)
    assert FP.funding_usd(+1, 10, 2.0, [-0.1]) == pytest.approx(+0.02)
    assert FP.contracts_for(100.0, 2.0, 1.0) == 50 and FP.contracts_for(1.0, 2.0, 1.0) == 0
    m = FP.close_pnl(-1, 10, 2.0, 1.8, 0.04)                                          # short gained $2
    assert m["gross"] == pytest.approx(2.0) and m["fees"] == pytest.approx(FP.FEE * 10 * 3.8)
    assert m["pnl"] == pytest.approx(2.0 - FP.FEE * 38 + 0.04)
    rows = [dict(time=t, close=0.05) for t in range(D0, D0 + DAY, HOUR)]
    assert FP.settlement_rates(rows, 8 * HOUR, D0 + 60, D0 + DAY) == [0.05, 0.05]    # stamps 08:00 and 16:00


def test_no_order_path_and_no_keys():
    src = Path(FP.__file__).read_text()
    tree = ast.parse(src)
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"place_order", "cancel_order", "set_leverage", "buy_back", "sell"}
    assert "api_key" not in src.lower() and "secret" not in src.lower()


# ============================================================ the account on a database

class FakeClient:
    """tickers (quotes), 1d candles and FUNDING 1h rows, in Delta's shapes."""

    def __init__(self, rates: dict[str, float], price: dict[str, float]):
        self.rates, self.price = rates, price
        self.later, self.switch_at = {}, None                     # rates from `switch_at` on (history stays)

    def tickers(self, kind):
        return [dict(symbol=s, top_tag="crypto", contract_value="1", mark_price=str(p),
                     quotes=dict(best_bid=str(p * 0.999), best_ask=str(p * 1.001))) for s, p in self.price.items()]

    def candles(self, sym, res, a, b, drop_forming=True):
        if sym.startswith("FUNDING:"):
            c = sym[8:]
            return [dict(time=t, close=self.later[c] if self.switch_at is not None and t >= self.switch_at else self.rates[c])
                    for t in range(a // HOUR * HOUR, b + 1, HOUR)]
        p = self.price[sym]
        return [dict(time=t, open=p, high=p, low=p, close=p, volume=1e6 / p) for t in range(a, b + 1, DAY)]


class Pool:
    def __init__(self, con):
        self.con = con

    def acquire(self):
        con = self.con

        class Ctx:
            async def __aenter__(s): return con
            async def __aexit__(s, *a): return False
        return Ctx()


class Runner:
    def __init__(self, con):
        self._pool = Pool(con)
        self.catalog = {}
        self._day_cache = None


@requires_pg
@pytest.mark.asyncio
async def test_two_rebalances_open_keep_close_and_settle_funding(monkeypatch):
    import asyncpg

    from tests.live.conftest import TEST_DSN
    con = await asyncpg.connect(TEST_DSN)
    try:
        await con.execute("DROP TABLE IF EXISTS funding_positions, funding_periods")
        await FP.ensure_schema(con)
        coins = [f"C{i:02d}USD" for i in range(25)]
        rates = {s: (i - 12) * 0.01 for i, s in enumerate(coins)}                      # % per settlement
        price = {s: 10.0 for s in coins}
        fake = FakeClient(rates, price)
        monkeypatch.setattr(FP._Rebalance, "__init__", lambda self, runner, day, now: (
            setattr(self, "runner", runner), setattr(self, "day", day), setattr(self, "now", now),
            setattr(self, "client", fake)) and None)
        r = Runner(con)
        await FP._Rebalance(r, D0, D0 + 1500).run()
        rows = await con.fetch("SELECT symbol, side, contracts, entry_price FROM funding_positions ORDER BY symbol")
        assert {x["symbol"] for x in rows if x["side"] > 0} == set(coins[:5])        # 25 coins -> k = 5
        assert {x["symbol"] for x in rows if x["side"] < 0} == set(coins[-5:])
        longs = [x for x in rows if x["side"] > 0]
        assert all(x["entry_price"] == pytest.approx(10.01) for x in longs)           # longs buy at the ask
        assert all(x["contracts"] == 14 for x in longs)                               # floor($150 / $10.01 ask)
        assert all(x["contracts"] == 15 for x in rows if x["side"] < 0)               # floor($150 / $9.99 bid)
        p = await con.fetchrow("SELECT status, equity FROM funding_periods")
        assert p["status"] == "ENTERED" and p["equity"] == pytest.approx(FP.START_EQUITY)

        # funding flips one day after the open, so 2 of the 3 signal days carry the new rates and the books
        # change; prices unchanged. Held from D0 00:25: stamps 08:00 and 16:00 at the OLD rate, then 7 at the new.
        fake.later = {s: (12 - i) * 0.01 for i, s in enumerate(coins)}
        fake.switch_at = D0 + DAY
        await FP._Rebalance(r, D0 + 3 * DAY, D0 + 3 * DAY + 1500).run()
        closed = await con.fetch("SELECT * FROM funding_positions WHERE status = 'CLOSED'")
        assert len(closed) == 10                                                      # every old position was flipped
        c = next(x for x in closed if x["symbol"] == coins[0])                        # was LONG at -0.12%: paid to longs
        assert c["settlements"] == 9                                                  # 3 days x 3 stamps (8 h default)
        # long 14 at $10.01: received 0.12% x 2 stamps, then paid 0.12% x 7 -> net -0.60% of $140.14
        assert c["funding"] == pytest.approx(-0.0060 * 14 * 10.01) and c["gross"] < 0  # and lost the spread
        opened = await con.fetch("SELECT symbol, side FROM funding_positions WHERE status = 'OPEN'")
        assert {x["symbol"] for x in opened if x["side"] > 0} == set(coins[-5:])
        n = await con.fetchval("SELECT count(*) FROM funding_periods")
        await FP._Rebalance(r, D0 + 3 * DAY, D0 + 3 * DAY + 1600).run()               # a second pass: no double trade
        assert await con.fetchval("SELECT count(*) FROM funding_periods") == n
        assert await con.fetchval("SELECT count(*) FROM funding_positions") == 20
        from deploy.aws import db_probe as P                                          # the daily report's reader
        f = await P.collect_funding(con)
        assert f["periods"] == 2 and f["closed"] == 10 and f["longs"] == 5 and f["shorts"] == 5
        assert f["pnl"] == pytest.approx(f["price"] + f["funding"] - f["fees"], abs=0.02)
        assert f["rows"][-1]["change"] is not None and f["last"]["status"] == "ENTERED"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_step_waits_for_its_time_and_records_a_missed_rebalance():
    import asyncpg

    from tests.live.conftest import TEST_DSN
    con = await asyncpg.connect(TEST_DSN)
    try:
        await con.execute("DROP TABLE IF EXISTS funding_positions, funding_periods")
        await FP.ensure_schema(con)
        r = Runner(con)
        await FP.step(r, con, D0 + DAY + 3600)                                        # not a rebalance day
        await FP.step(r, con, D0 + 600)                                               # too early
        assert getattr(r, "_funding_task", None) is None
        await FP.step(r, con, D0 + 4 * HOUR)                                          # past 03:00: missed
        assert await con.fetchval("SELECT status FROM funding_periods") == "MISSED"
    finally:
        await con.close()


def test_the_report_shows_the_account_and_flags_a_missed_or_stale_rebalance():
    import datetime as dt
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import brief_report as B
    f = {"periods": 3, "equity": 1562.5, "closed": 10, "wins": 6, "pnl": 62.5, "funding": 80.0, "price": -10.0,
         "fees": 7.5, "longs": 9, "shorts": 9,
         "rows": [{"period": "2026-11-12", "equity": 1500.0, "change": None},
                  {"period": "2026-11-15", "equity": 1530.0, "change": 0.02},
                  {"period": "2026-11-18", "equity": 1562.5, "change": 0.0212}],
         "last": {"period": "2026-11-18", "status": "ENTERED", "coins": 48, "why": None}}
    text = "\n".join(B.funding_lines(f))
    assert "$1,562.50" in text and "+4.2%" in text and "9 longs and 9 shorts" in text and "+2.1%" in text
    assert text.index("2026-11-18") < text.index("2026-11-15"), "newest rebalance first"
    now = dt.datetime(2026, 11, 19, 2, tzinfo=dt.timezone.utc)
    assert B.funding_problems(f, now) == []
    assert "no funding-collection rebalance since" in B.funding_problems(f, now + dt.timedelta(days=4))[0]
    missed = dict(f, last={"period": "2026-11-18", "status": "MISSED"})
    assert "MISSED" in B.funding_problems(missed, now)[0]
    assert B.funding_problems(None, now) == [] and "Not started" in "\n".join(B.funding_lines(None))
    assert "could not be read" in B.funding_problems({"error": "x"}, now)[0]
