"""The gainer-fade REAL-MONEY account, run 3 (live/fade_live.py; rules in docs/fade_live_ladder_prereg.md).

The pure rules always run. The account's day -- entries with a market stop resting at Delta, the ladder's
sizing, the 24 h exit, a stop that fires at Delta, the $1,000 stop and the owner's refill, the capital check,
a restart, exits-only, the error breaker -- runs against PostgreSQL (DELTABOT_TEST_DSN) and a fake Delta that
answers in the venue's shapes and keeps a wallet: bracket legs rest as PENDING reduce-only `stop_loss_order`
rows, positions are signed sizes, a close shows in order history as a filled reduce-only buy, and the wallet
moves by fees and realised P&L exactly as Delta's `balance` does.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from live import fade_live as L
from tests.live.conftest import TEST_DSN, requires_pg

D0 = 1793232000                                   # 2026-10-29 00:00 UTC
DAY = 86400
ON = {"DELTABOT_DRY_RUN": "0", "DELTA_ENV": "prod", "DELTA_API_KEY": "k", "DELTA_API_SECRET": "s",
      "DELTABOT_RISK_PER_TRADE": "0.10", "DELTABOT_EQUITY": "1500"}


# ============================================================ pure rules

def test_the_switch_is_strict():
    assert L.enabled(ON) == (True, "on")
    for k, v in (("DELTABOT_DRY_RUN", "1"), ("DELTABOT_DRY_RUN", ""), ("DELTA_ENV", "testnet"),
                 ("DELTA_API_SECRET", ""), ("DELTABOT_RISK_PER_TRADE", "0.11"), ("DELTABOT_EQUITY", "1000"),
                 ("DELTABOT_EQUITY", "176"), ("DELTABOT_EQUITY", "x")):   # version 1's sizing must not switch run 3 on
        assert not L.enabled(dict(ON, **{k: v}))[0], (k, v)
    unset = dict(ON)
    del unset["DELTABOT_DRY_RUN"]                 # unset is NOT "0" here, unlike live/__main__
    assert not L.enabled(unset)[0]


def test_prod_keys_alone_are_enough_to_close_what_is_held():
    assert L.can_close(dict(ON, DELTABOT_DRY_RUN="1"))
    assert L.can_close(dict(ON, DELTABOT_EQUITY="176"))
    assert not L.can_close(dict(ON, DELTA_ENV="testnet")) and not L.can_close(dict(ON, DELTA_API_KEY=""))


def test_from_env_makes_no_venue_call_and_is_exits_only_without_the_switch(monkeypatch):
    import live.config as C
    made = []

    class NoVenue:
        def __getattr__(self, name):
            raise AssertionError(f"from_env called the venue: {name}")
    monkeypatch.setattr(C, "client_from_env", lambda src: made.append(1) or NoVenue())
    acct, why = L.from_env(ON)
    assert why == "on" and acct.entries and acct.risk_pct == 0.10 and acct.start_usd == 1500
    acct, why = L.from_env(dict(ON, DELTABOT_DRY_RUN="1"))
    assert acct is not None and not acct.entries and why.startswith("exits only")
    assert L.from_env(dict(ON, DELTA_ENV="testnet")) == (None, "DELTA_ENV is not prod")
    assert len(made) == 2


def test_sizing_cap_leverage_and_ticks():
    from live.fade import lever_plan
    # 10% risk on a 20% stop wants 50% notional: 2x on 25% margin would do, but at 2x liquidation (1/2 - 2.5%
    # = 47.5%) is not 2 x 20% = 40% away... it is: 47.5% >= 40% -> 2x, notional 50%, risk the full 10%.
    assert lever_plan(0.10, 0.20, 2.5) == (0.5, 2)
    # a 15% stop wants 67%: capped at 60% notional, 2x (margin 30%)
    assert lever_plan(0.10, 0.15, 2.5) == (0.6, 2)
    # a 25% stop: 2x would liquidate 47.5% away < 50% -> 1x, 30% notional (margin cap), risk 7.5%
    assert lever_plan(0.10, 0.25, 2.5) == (0.3, 1)
    # a 30% stop at 3% risk wants 10% notional: 1x
    assert lever_plan(0.03, 0.30, 2.5) == (0.1, 1)
    # a 5% stop: 3x allowed (1/3 - 2.5% = 30.8% >= 10%), but 60% notional needs only 2x
    assert lever_plan(0.10, 0.05, 2.5) == (0.6, 2)
    # past 48.75% even 1x cannot keep liquidation 2x away
    assert lever_plan(0.10, 0.49, 2.5) == (0.0, 0)
    # contracts: $1,500, bid 1.00, stop 1.20 -> 50% notional = 750 contracts at 2x
    assert L.contracts_for(1500, 0.10, 1.0, 1.2, 1.0, 2.5) == (750, 2)
    assert L.contracts_for(1500, 0.03, 1.0, 1.2, 1.0, 2.5) == (225, 1)        # 3%: 15% notional, 1x
    assert L.contracts_for(1500, 0.10, 0.2, 0.25, 1000, 2.5) == (2, 1)       # 25% stop: 1x, 30% = $450 -> 2 of $200
    assert L.contracts_for(1500, 0.10, 0.2, 0.3, 1000, 2.5) == (0, 0)        # 50% stop: liquidation cannot be 2x away
    assert L.contracts_for(0, 0.10, 1.0, 1.2, 1.0, 2.5) == (0, 0)
    assert L.contracts_for(1500, 0.10, 1.2, 1.0, 1.0, 2.5) == (0, 0)
    assert L.to_tick(1.23456, Decimal("0.001"), up=True) == Decimal("1.235")
    assert L.to_tick(1.23456, Decimal("0.001"), up=False) == Decimal("1.234")
    pnl, r = L.money(1.0, 0.9, 26, 1.0, 1.2, 0.05)
    assert abs(pnl - 2.55) < 1e-9 and abs(r - 2.55 / 5.2) < 1e-9


def test_the_ladder_steps_on_the_high_water_mark():
    from live.fade import ladder_risk
    assert ladder_risk(1500, 1500) == 0.10 and ladder_risk(2000, 1500) == 0.10   # above the mark: top step
    assert ladder_risk(1601, 2000) == 0.10 and ladder_risk(1600, 2000) == 0.05   # exactly 20% under: 5%
    assert ladder_risk(1301, 2000) == 0.05 and ladder_risk(1300, 2000) == 0.03   # exactly 35% under: 3%
    assert ladder_risk(100, 2000) == 0.03 and ladder_risk(1500, 0) == 0.10


def test_capital_rules():
    assert not L.capital_suspect(L.capital_gap(1500, 1509, 0), 1500)          # under $10 and 2%: funding etc.
    assert not L.capital_suspect(L.capital_gap(1500, 1529, 0), 1500)          # 2% of $1,500 = $30
    assert L.capital_suspect(L.capital_gap(1500, 1531, 0), 1500)
    assert not L.capital_suspect(L.capital_gap(1500, 1650, 150), 1500)        # a winning close explains it
    assert L.capital_suspect(L.capital_gap(1500, 1000, 0), 1500)              # a withdrawal
    assert L.high_water_after("STOPPED", 1500, 2600, 510) == 1500             # a refill restarts the ladder
    assert L.high_water_after("RUNNING", 2100, 2000, 500) == 2500             # a deposit keeps the $ drawdown
    assert L.high_water_after("RUNNING", 1000, 2000, -500) == 1500            # a withdrawal likewise
    assert L.high_water_after("HALTED", 1000, 1200, -500) == 1000             # never under the balance
    assert L.high_water_after("RUNNING", 1000, None, 0) == 1000


# ============================================================ a fake Delta

class FakeDelta:
    """Just enough of live.client.LiveClient, answering in Delta's shapes, with a wallet that moves by fees
    and realised P&L the way Delta's `balance` does."""

    FEE = 0.01

    def __init__(self, balance=1500.0, attach_stop=True, fill=None, stop_size=None):
        self.bal = balance
        self.attach_stop = attach_stop
        self.fill = fill                              # contracts an entry fills (None = all)
        self.stop_size = stop_size                    # the size the stop leg states (None = no size field)
        self.products = {"XUSD": dict(id=1, tick_size="0.0001", contract_value="1", maintenance_margin="2.5"),
                         "YUSD": dict(id=2, tick_size="0.0001", contract_value="1", maintenance_margin="2.5")}
        self.book = {"XUSD": (1.0000, 1.0010, 1.0005), "YUSD": (2.0000, 2.0020, 2.0010)}
        self.pos: dict[int, int] = {}
        self.entry: dict[int, float] = {}
        self.pending: list[dict] = []
        self.history: list[dict] = []
        self.lev: dict[int, int] = {}
        self.sent: list[dict] = []
        self.cancelled: list[int] = []
        self.by_cid: dict[str, dict] = {}
        self.fail: dict[str, int] = {}               # method -> how many more calls raise
        self.no_balance = False
        self._id = 100

    def _sym(self, pid):
        return next(s for s, p in self.products.items() if p["id"] == pid)

    def _maybe_fail(self, name):
        if self.fail.get(name):
            self.fail[name] -= 1
            raise ConnectionError(f"{name}: venue down")

    def get_product(self, s): return self.products[s]
    def get_ticker(self, s):
        b, a, m = self.book[s]
        return {"mark_price": str(m), "quotes": {"best_bid": str(b), "best_ask": str(a)}}
    def get_balance(self):
        self._maybe_fail("get_balance")
        return [] if self.no_balance else [{"asset_symbol": "USD", "balance": str(round(self.bal, 6))}]
    def get_position(self, pid):
        return {"product_id": pid, "size": self.pos[pid]} if self.pos.get(pid) else None
    def get_positions(self):
        self._maybe_fail("get_positions")
        return [{"product_id": pid, "product_symbol": self._sym(pid) if pid in (1, 2) else "ZUSD", "size": n}
                for pid, n in self.pos.items() if n]
    def get_pending_orders(self, pid=None): return [o for o in self.pending if pid in (None, o["product_id"])]
    def get_open_orders(self, pid=None): return []
    def order_history(self, pid=None, page_size=20): return [o for o in self.history if pid in (None, o["product_id"])]
    def set_order_leverage(self, pid, lev): self.lev[pid] = lev
    def get_order_leverage(self, pid): return float(self.lev.get(pid, 20))
    def get_order_by_client_id(self, cid): return self.by_cid.get(cid)
    def cancel_order(self, oid, pid):
        self.cancelled.append(oid)
        self.pending = [o for o in self.pending if o["id"] != oid]

    def _close(self, pid, n, price, fee):
        self.bal += (self.entry[pid] - price) * n - fee

    def place_order(self, order):
        p = order.to_payload()
        self.sent.append(p)
        pid, size, sym = p["product_id"], p["size"], self._sym(p["product_id"])
        bid, ask, _ = self.book[sym]
        self._id += 1
        if p["side"] == "sell" and not p.get("reduce_only"):
            n = size if self.fill is None else min(size, self.fill)
            self.pos[pid] = self.pos.get(pid, 0) - n
            self.entry[pid] = bid
            self.bal -= self.FEE
            row = dict(id=self._id, product_id=pid, side="sell", size=size, unfilled_size=size - n,
                       average_fill_price=str(bid), paid_commission=str(self.FEE), state="closed",
                       client_order_id=p["client_order_id"])
            if self.attach_stop and "bracket_stop_loss_price" in p:
                self._id += 1
                leg = dict(id=self._id, product_id=pid, reduce_only=True, stop_order_type="stop_loss_order",
                           stop_price=p["bracket_stop_loss_price"],
                           order_type="limit_order" if "bracket_stop_loss_limit_price" in p else "market_order")
                if self.stop_size is not None:
                    leg["size"] = self.stop_size
                self.pending.append(leg)
        else:
            n = min(size, -self.pos.get(pid, 0)) if p.get("reduce_only") else size
            self.pos[pid] = self.pos.get(pid, 0) + n
            row = dict(id=self._id, product_id=pid, side="buy", size=size, unfilled_size=size - n,
                       average_fill_price=str(ask) if n else None, paid_commission=str(self.FEE) if n else "0",
                       state="closed", reduce_only=True, client_order_id=p["client_order_id"])
            if n:
                self._close(pid, n, ask, self.FEE)
                self.history.insert(0, row)
        self.by_cid[p["client_order_id"]] = row
        return row

    def fire_stop(self, sym, price, kind="stop_loss_order"):
        """A bracket leg fires at Delta: the position closes at `price` and the wallet takes the result."""
        pid = self.products[sym]["id"]
        n = -self.pos.get(pid, 0)
        self.pos[pid] = 0
        self.pending = [o for o in self.pending if o["product_id"] != pid]
        self._close(pid, n, price, 0.02)
        self._id += 1
        self.history.insert(0, dict(id=self._id, product_id=pid, side="buy", size=n, unfilled_size=0,
                                    average_fill_price=str(price), paid_commission="0.02", reduce_only=True,
                                    stop_order_type=kind, state="closed"))


# ============================================================ the account's day (PostgreSQL)

async def _db(picks=(("XUSD", 1, 0.10, 1.0), ("YUSD", 2, 0.12, 2.0)), day=D0):
    import asyncpg

    from live import fade
    con = await asyncpg.connect(TEST_DSN)
    await con.execute("DROP TABLE IF EXISTS fade_live_positions, fade_live_state, fade_live_events, "
                      "fade_positions, fade_days, fade_heartbeat, fade_paper_capital")
    await con.execute(fade.SCHEMA)
    await L.ensure_schema(con)
    if picks:
        await _paper_day(con, picks, day)
    return con


async def _paper_day(con, picks, day):
    ts = dt.datetime.fromtimestamp(day, dt.timezone.utc)
    await con.execute("INSERT INTO fade_days (day, status) VALUES ($1, 'ENTERED')", ts)
    for sym, rank, atrp, entry in picks:
        await con.execute(
            "INSERT INTO fade_positions (account, day, symbol, rank, prev_change, prev_turnover, atrp, entry_time, "
            "entry_price, stop_price, contracts, contract_value, equity_before, status) "
            "VALUES ('A', $1, $2, $3, 0.3, 1e6, $4, $1, $5, $6, 5, 1, 250, 'OPEN')",
            ts, sym, rank, atrp, entry, entry * (1 + 2 * atrp))


def _acct(fake, **kw):
    return L.LiveFade(fake, risk_pct=0.10, start_usd=1500, kill_switch_path="/nonexistent/kill",
                      protect_wait=0.0, sleep=lambda s: None, **kw)


async def _rows(con):
    return {r["symbol"]: r for r in await con.fetch("SELECT * FROM fade_live_positions")}


async def _state(con):
    return await con.fetchrow("SELECT * FROM fade_live_state WHERE id = 1")


@requires_pg
@pytest.mark.asyncio
async def test_a_full_day_sell_with_a_market_stop_then_buy_back_after_24_hours():
    con = await _db()
    fake = FakeDelta()
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        st = await _state(con)
        assert (st["status"], st["run_version"], st["high_water"], st["added_usd"]) == ("RUNNING", 3, 1500, 0)
        rows = await _rows(con)
        assert {s: r["status"] for s, r in rows.items()} == {"XUSD": "OPEN", "YUSD": "OPEN"}
        sells = [p for p in fake.sent if p["side"] == "sell"]
        assert len(sells) == 2 and all(p["time_in_force"] == "ioc" and "bracket_stop_loss_price" in p
                                       and p["stop_trigger_method"] == "mark_price" for p in sells)
        assert not any("bracket_stop_loss_limit_price" in p or "bracket_take_profit_price" in p for p in sells), \
            "a MARKET stop and no take-profit"
        x = next(p for p in sells if p["product_id"] == 1)
        assert x["limit_price"] == "0.99" and x["bracket_stop_loss_price"] == "1.2"   # bid 1.00, 2 x 10% ATR
        # $150 at risk on a 20% stop: 750 contracts = 50% of the wallet as notional, carried at 2x (margin 25%)
        assert x["size"] == 750 and fake.lev == {1: 2, 2: 1}   # YUSD's 24% stop: 2x would liquidate 47.5% away
        assert rows["XUSD"]["leverage"] == 2
        assert rows["XUSD"]["risk_pct"] == 0.10 and rows["XUSD"]["target_price"] is None
        assert '"spread_bps": 10.0' in rows["XUSD"]["detail"]
        # the second pick is sized off the wallet after the first's entry fee
        assert rows["YUSD"]["balance_before"] == pytest.approx(1500 - 0.01)
        await acct.step(con, D0 + 3600)                                          # nothing to do mid-day
        assert len(fake.sent) == 2
        await acct.step(con, D0 + DAY + 10)                                      # 24 h later
        rows = await _rows(con)
        assert {s: (r["status"], r["exit_reason"]) for s, r in rows.items()} == \
            {"XUSD": ("CLOSED", "TIME"), "YUSD": ("CLOSED", "TIME")}
        buys = [p for p in fake.sent if p["side"] == "buy"]
        assert len(buys) == 2 and all(p["reduce_only"] for p in buys) and fake.pos == {1: 0, 2: 0}
        assert not fake.pending and len(fake.cancelled) == 2                     # the stops, once flat
        assert abs(rows["XUSD"]["pnl"] - ((1.0 - 1.001) * 750 - 0.02)) < 1e-9
        st = await _state(con)
        assert st["status"] == "RUNNING" and st["balance"] == pytest.approx(fake.bal)   # the closes explain it
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_stop_that_fires_at_delta_is_recorded_and_its_loss_is_not_a_capital_change():
    con = await _db()
    fake = FakeDelta()
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        fake.fire_stop("XUSD", 1.25)                                             # gapped past the 1.2 stop
        for k in range(1, 4):
            await acct.step(con, D0 + 120 + k * 400)                            # three snapshots
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["exit_reason"], x["exit_price"]) == ("CLOSED", "STOP_LOSS", 1.25)
        assert x["r"] < -1.0                                                    # slipped past the stop
        st = await _state(con)
        assert st["status"] == "RUNNING" and st["balance"] == pytest.approx(fake.bal)
        n = (await _rows(con))["XUSD"]["contracts"]
        assert n == 750 and fake.bal == pytest.approx(1500 - 0.01 - 0.01 - 0.25 * n - 0.02)
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_fill_without_a_resting_stop_is_bought_back_and_the_account_halts():
    con = await _db()
    fake = FakeDelta(attach_stop=False)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        rows = await _rows(con)
        assert (rows["XUSD"]["status"], rows["XUSD"]["exit_reason"]) == ("CLOSED", "UNPROTECTED")
        assert (rows["YUSD"]["status"], rows["YUSD"]["skip_reason"]) == ("SKIPPED", "run halted"), \
            "a halted account opens nothing more, and says so"
        assert fake.pos == {1: 0} and (await _state(con))["status"] == "HALTED"
        await acct.step(con, D0 + 300)
        assert len([p for p in fake.sent if p["side"] == "sell"]) == 1
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_stop_smaller_than_the_position_does_not_count_as_protection():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = FakeDelta(stop_size=100)                  # the leg says 100 contracts; 750 were sold
    try:
        await _acct(fake).step(con, D0 + 120)
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["exit_reason"]) == ("CLOSED", "UNPROTECTED") and fake.pos[1] == 0
        assert (await _state(con))["status"] == "HALTED"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_partial_fill_with_a_stop_covering_it_is_kept():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = FakeDelta(fill=200, stop_size=200)
    try:
        await _acct(fake).step(con, D0 + 120)
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["contracts"]) == ("OPEN", 200) and fake.pos[1] == -200
        assert (await _state(con))["status"] == "RUNNING"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_the_ladder_sizes_off_the_stored_high_water_mark():
    con = await _db(picks=(("XUSD", 1, 0.15, 1.0),))      # a 30% stop: risk, not the cap, sets the size
    fake = FakeDelta(balance=1500)
    acct = _acct(fake)
    try:
        await acct.state(con, D0 + 60)                                           # the run begins at $1,500
        await con.execute("UPDATE fade_live_state SET high_water = 2000")       # it had reached $2,000
        await acct.step(con, D0 + 120)                                           # 25% under: 5%
        x = (await _rows(con))["XUSD"]
        assert x["risk_pct"] == 0.05 and x["contracts"] == int(0.05 * 1500 / 0.3)
    finally:
        await con.close()
    con = await _db(picks=(("XUSD", 1, 0.15, 1.0),))
    fake = FakeDelta(balance=1250)
    acct = _acct(fake)
    try:
        await acct.state(con, D0 + 60)
        await con.execute("UPDATE fade_live_state SET high_water = 2000")       # 37.5% under: 3%
        await acct.step(con, D0 + 120)
        x = (await _rows(con))["XUSD"]
        assert x["risk_pct"] == 0.03 and x["contracts"] == int(0.03 * 1250 / 0.3)
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_under_1000_the_run_stops_and_only_a_refill_within_budget_restarts_it():
    con = await _db(picks=None)
    fake = FakeDelta(balance=1500)
    acct = _acct(fake)
    try:
        await acct.state(con, D0 + 60)
        await con.execute("UPDATE fade_live_state SET high_water = 2600")
        fake.bal = 990.0                                 # losses (the capital base follows them)
        await con.execute("UPDATE fade_live_state SET balance = 990")
        await _paper_day(con, (("XUSD", 1, 0.10, 1.0), ("YUSD", 2, 0.12, 2.0)), D0)
        await acct.step(con, D0 + 120)
        assert not fake.sent and (await _state(con))["status"] == "STOPPED"
        assert (await _rows(con))["XUSD"]["skip_reason"].startswith("run stopped")
        # resume does not end a STOP; a refill short of $1,400 is refused
        fake.bal = 1300.0
        rc, msg = await L.record_capital(con, acct, D0 + 3600)
        assert rc == 1 and "under the $1400" in msg and (await _state(con))["status"] == "STOPPED"
        fake.bal = 1500.0                                # +$510
        rc, msg = await L.record_capital(con, acct, D0 + 3700)
        st = await _state(con)
        assert rc == 0 and st["status"] == "RUNNING" and st["high_water"] == 1500 and st["added_usd"] == 510
        await _paper_day(con, (("XUSD", 1, 0.10, 1.0),), D0 + DAY)
        await acct.step(con, D0 + DAY + 120)                                     # back at 10%
        assert [r["risk_pct"] for r in await con.fetch(
            "SELECT risk_pct FROM fade_live_positions WHERE status = 'OPEN'")] == [0.10]
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_refills_past_the_3000_budget_end_the_run():
    con = await _db(picks=None)
    fake = FakeDelta(balance=1500)
    acct = _acct(fake)
    try:
        await acct.state(con, D0 + 60)
        await con.execute("UPDATE fade_live_state SET status = 'STOPPED', balance = 950, added_usd = 2600")
        fake.bal = 1500.0                                # +$550 would make $3,150
        rc, msg = await L.record_capital(con, acct, D0 + 3600)
        assert rc == 1 and "over the $3,000 budget" in msg and (await _state(con))["status"] == "STOPPED"
        assert await con.fetchval("SELECT count(*) FROM fade_live_events WHERE kind = 'REFILL_REFUSED'") == 1
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_capital_is_recorded_only_when_flat():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = FakeDelta()
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        rc, msg = await L.record_capital(con, acct, D0 + 3600)
        assert rc == 1 and "positions are open" in msg
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_deposit_no_trade_explains_halts_on_the_second_look_and_the_owner_records_it():
    con = await _db(picks=None)
    fake = FakeDelta(balance=1500)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 60)
        await con.execute("UPDATE fade_live_state SET high_water = 2000")
        fake.bal += 500                                  # money arrives with nothing open
        await acct.step(con, D0 + 400)
        assert (await _state(con))["status"] == "RUNNING", "one look is not enough (a close may be in flight)"
        await acct.step(con, D0 + 800)
        st = await _state(con)
        assert st["status"] == "HALTED" and st["reason"].startswith("wallet moved $+500.00")
        assert st["balance"] == 1500 and st["high_water"] == 2000, "the base and the mark are not moved by it"
        rc, msg = await L.record_capital(con, acct, D0 + 900)
        st = await _state(con)
        assert rc == 0 and st["status"] == "RUNNING" and st["balance"] == 2000
        assert st["high_water"] == 2500 and st["added_usd"] == 500             # the $ drawdown is kept
        await acct.step(con, D0 + 1300)
        assert (await _state(con))["status"] == "RUNNING"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_withdrawal_halts_and_does_not_fake_a_drawdown_once_recorded():
    con = await _db(picks=None)
    fake = FakeDelta(balance=1500)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 60)
        fake.bal -= 400
        await acct.step(con, D0 + 400)
        await acct.step(con, D0 + 800)
        assert (await _state(con))["status"] == "HALTED"
        rc, _ = await L.record_capital(con, acct, D0 + 900)
        st = await _state(con)
        assert rc == 0 and st["balance"] == 1100 and st["high_water"] == 1100 and st["added_usd"] == 0
        from live.fade import ladder_risk
        assert ladder_risk(st["balance"], st["high_water"]) == 0.10
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_new_run_needs_the_funded_sub_account_but_growth_never_halts_it():
    con = await _db()
    fake = FakeDelta(balance=2000.0)                     # > 1.25 x $1,500 at the start: not the sub-account
    try:
        await _acct(fake).step(con, D0 + 120)
        st = await _state(con)
        assert not fake.sent and st["status"] == "HALTED" and "cannot start" in st["reason"]
    finally:
        await con.close()
    con = await _db()
    fake = FakeDelta(balance=1500.0)
    acct = _acct(fake)
    try:
        await acct.state(con, D0 + 60)
        fake.bal = 3000.0                                # it doubled through trading
        await con.execute("UPDATE fade_live_state SET balance = 3000, high_water = 3000")
        await acct.step(con, D0 + 120)
        assert (await _state(con))["status"] == "RUNNING"
        assert {r["status"] for r in (await _rows(con)).values()} == {"OPEN"}
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_version_1s_row_begins_a_new_run_and_its_open_position_still_exits():
    con = await _db(picks=None)
    fake = FakeDelta(balance=1500)
    try:
        await con.execute("INSERT INTO fade_live_state (id, status, reason, balance, high_water) "
                          "VALUES (1, 'RUNNING', 'started', 176, 180)")
        fake.pos[1], fake.entry[1] = -26, 1.0
        fake.pending.append(dict(id=7, product_id=1, reduce_only=True, stop_order_type="stop_loss_order"))
        await con.execute(
            "INSERT INTO fade_live_positions (day, symbol, rank, status, product_id, contract_value, contracts, "
            "stop_price, entry_price, entry_fee, entry_time) VALUES ($1, 'XUSD', 1, 'OPEN', 1, 1, 26, 1.2, 1.0, 0.01, $1)",
            dt.datetime.fromtimestamp(D0 - DAY, dt.timezone.utc))
        await _acct(fake).step(con, D0 + 10)
        st = await _state(con)
        assert (st["status"], st["run_version"], st["high_water"]) == ("RUNNING", 3, 1500)
        assert st["balance"] == pytest.approx(fake.bal) and fake.bal == pytest.approx(1500 - 0.026 - 0.01)
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["exit_reason"]) == ("CLOSED", "TIME") and fake.pos[1] == 0
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_restart_and_exits_only_still_buy_back_at_24_hours():
    con = await _db()
    fake = FakeDelta()
    try:
        await _acct(fake).step(con, D0 + 120)
        assert len([p for p in fake.sent if p["side"] == "sell"]) == 2
        await _paper_day(con, (("XUSD", 1, 0.10, 1.0),), D0 + DAY)
        off = _acct(fake, entries=False)                # restarted with the switch off: exits only
        await off.step(con, D0 + DAY + 120)
        rows = {(r["day"].timestamp(), r["symbol"]): r["status"]
                for r in await con.fetch("SELECT * FROM fade_live_positions")}
        assert rows == {(D0, "XUSD"): "CLOSED", (D0, "YUSD"): "CLOSED"}, "bought back; nothing new sold"
        assert len([p for p in fake.sent if p["side"] == "sell"]) == 2 and fake.pos == {1: 0, 2: 0}
        await L.record_off(con, "exits only: test")
        assert (await _state(con))["status"] == "OFF"
        on = _acct(fake)                                 # the switch back on: same run, ledger kept
        await on.step(con, D0 + DAY + 200)
        st = await _state(con)
        assert st["status"] == "RUNNING" and st["run_version"] == 3
        assert len([p for p in fake.sent if p["side"] == "sell"]) == 3
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_failure_in_the_middle_of_the_24_hour_buy_back_is_recorded_as_time_not_a_halt():
    # Found by scripts/fade_live_sim.py --faults: the buy filled, then the next read failed before the row was
    # written; the next pass found the coin flat with a plain reduce-only buy and called it MANUAL_CLOSE (HALT).
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = FakeDelta()
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        real, failed = fake.get_position, []

        def flaky(pid):
            if any(p["side"] == "buy" for p in fake.sent) and not failed:   # the first read after the buy filled
                failed.append(pid)
                raise ConnectionError("venue down")
            return real(pid)
        fake.get_position = flaky
        await acct.step(con, D0 + DAY + 10)
        assert (await _rows(con))["XUSD"]["status"] == "OPEN" and fake.pos[1] == 0
        fake.get_position = real
        await acct.step(con, D0 + DAY + 40)
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["exit_reason"], x["exit_price"]) == ("CLOSED", "TIME", 1.001)
        assert (await _state(con))["status"] == "RUNNING"
        assert len([p for p in fake.sent if p["side"] == "buy"]) == 1, "never bought twice"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_ten_failed_passes_in_a_row_halt_entries_but_nine_do_not():
    con = await _db(picks=None)
    fake = FakeDelta()
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 60)
        fake.fail["get_positions"] = 9
        for k in range(10):
            await acct.step(con, D0 + 90 + 30 * k)
        assert (await _state(con))["status"] == "RUNNING"
        fake.fail["get_positions"] = 10
        for k in range(10):
            await acct.step(con, D0 + 500 + 30 * k)
        st = await _state(con)
        assert st["status"] == "HALTED" and st["reason"].startswith("10 failed passes in a row")
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_missing_balance_is_an_error_not_a_stop():
    con = await _db()
    fake = FakeDelta()
    acct = _acct(fake)
    try:
        await acct.state(con, D0 + 60)
        fake.no_balance = True
        await acct.step(con, D0 + 120)
        assert not fake.sent and (await _state(con))["status"] == "RUNNING"
        assert await con.fetchval("SELECT count(*) FROM fade_live_events WHERE kind = 'ERROR'") == 1
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_wide_spread_or_a_passed_window_is_skipped_and_recorded():
    con = await _db()
    fake = FakeDelta()
    fake.book["XUSD"] = (1.0, 1.02, 1.01)               # 2% spread
    try:
        await _acct(fake).step(con, D0 + 120)
        rows = await _rows(con)
        assert rows["XUSD"]["status"] == "SKIPPED" and rows["XUSD"]["skip_reason"].startswith("spread 2.00%")
        assert rows["YUSD"]["status"] == "OPEN"
    finally:
        await con.close()
    con = await _db()
    fake = FakeDelta()
    try:
        await _acct(fake).step(con, D0 + 31 * 60)
        rows = await _rows(con)
        assert not fake.sent and {r["status"] for r in rows.values()} == {"SKIPPED"}
        assert rows["XUSD"]["skip_reason"].startswith("entry window")
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_position_the_bot_did_not_open_halts_it_and_is_not_touched():
    con = await _db()
    fake = FakeDelta()
    fake.pos[99] = -5
    try:
        await _acct(fake).step(con, D0 + 120)
        assert not fake.sent and fake.pos[99] == -5
        assert "no record" in (await _state(con))["reason"]
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_an_order_sent_before_a_crash_is_found_by_its_client_order_id():
    con = await _db()
    fake = FakeDelta()
    acct = _acct(fake)
    try:
        from live.orders import client_order_id
        await acct.state(con, D0 + 60)
        cid = client_order_id("fade-live", str(D0), "XUSD", "entry")
        fake.pos[1], fake.entry[1] = -26, 1.0
        fake.pending.append(dict(id=7, product_id=1, reduce_only=True, stop_order_type="stop_loss_order"))
        fake.by_cid[cid] = dict(size=26, unfilled_size=0, average_fill_price="1.0", paid_commission="0.01")
        await con.execute(
            "INSERT INTO fade_live_positions (day, symbol, rank, status, product_id, contract_value, contracts, "
            "stop_price, entry_cid) VALUES ($1, 'XUSD', 1, 'SENDING', 1, 1, 26, 1.2, $2)",
            dt.datetime.fromtimestamp(D0, dt.timezone.utc), cid)
        await acct.step(con, D0 + 120)
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["contracts"], x["entry_price"]) == ("OPEN", 26, 1.0)
        assert not [p for p in fake.sent if p["product_id"] == 1], "never sent twice"
        assert (await _state(con))["status"] == "RUNNING"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_coin_picked_two_days_running_is_bought_back_before_it_is_sold_again():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = FakeDelta()
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        await _paper_day(con, (("XUSD", 1, 0.10, 1.0),), D0 + DAY)
        await acct.step(con, D0 + DAY + 120)
        sides = [p["side"] for p in fake.sent]
        assert sides == ["sell", "buy", "sell"]
        st = [r["status"] for r in await con.fetch("SELECT status FROM fade_live_positions ORDER BY day")]
        assert st == ["CLOSED", "OPEN"] and fake.pos[1] < 0
    finally:
        await con.close()


# ============================================================ found by the independent reviews (2026-11-09)

@requires_pg
@pytest.mark.asyncio
async def test_a_close_with_no_pnl_does_not_freeze_the_capital_check_or_refills():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = FakeDelta()
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        fake.pos[1] = 0                                  # flat at Delta, no closing order anywhere
        fake.pending.clear()
        fake.bal -= 80
        for k in range(1, 4):                            # flat_since, then 300 s later UNKNOWN_EXIT
            await acct.step(con, D0 + 120 + k * 310)
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["exit_reason"], x["pnl"]) == ("CLOSED", "UNKNOWN_EXIT", None)
        st = await _state(con)
        assert st["status"] == "HALTED" and st["balance"] == pytest.approx(fake.bal), "the base moved on"
        assert await con.fetchval("SELECT count(*) FROM fade_live_events WHERE kind = 'BASE_RESET'") >= 1
        fake.bal += 500                                  # a later deposit is still caught and recordable
        await acct.step(con, D0 + 3000)
        await acct.step(con, D0 + 3400)
        rc, msg = await L.record_capital(con, acct, D0 + 3500)
        assert rc == 0 and "$+500.00" in msg
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_yesterdays_buy_back_on_the_same_coin_is_never_taken_for_todays_stop():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = FakeDelta()
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        await _paper_day(con, (("XUSD", 1, 0.10, 1.0),), D0 + DAY)
        await acct.step(con, D0 + DAY + 120)             # bought back, sold again
        pid = 1
        n = -fake.pos[pid]
        fake.pos[pid] = 0                                # today's stop fired; history does not show it yet
        fake.pending.clear()
        fake._close(pid, n, 1.25, 0.02)
        await acct.step(con, D0 + DAY + 3600)
        today = await con.fetchrow("SELECT * FROM fade_live_positions WHERE day = $1",
                                   dt.datetime.fromtimestamp(D0 + DAY, dt.timezone.utc))
        assert today["status"] == "OPEN" and today["flat_since"] is not None, "waits; does not grab yesterday's buy"
        fake.history.insert(0, dict(id=999, product_id=pid, side="buy", size=n, unfilled_size=0,
                                    average_fill_price="1.25", paid_commission="0.02", reduce_only=True,
                                    stop_order_type="stop_loss_order", state="closed"))
        await acct.step(con, D0 + DAY + 3700)
        today = await con.fetchrow("SELECT * FROM fade_live_positions WHERE day = $1",
                                   dt.datetime.fromtimestamp(D0 + DAY, dt.timezone.utc))
        assert (today["status"], today["exit_reason"], today["exit_price"]) == ("CLOSED", "STOP_LOSS", 1.25)
        assert (await _state(con))["status"] == "RUNNING"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_run_that_could_not_start_begins_by_itself_once_topped_up():
    con = await _db()
    fake = FakeDelta(balance=176.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        assert (await _state(con))["status"] == "HALTED" and not fake.sent
        await acct.step(con, D0 + 150)
        assert await con.fetchval("SELECT count(*) FROM fade_live_events WHERE kind = 'NEW_RUN'") == 1, "no spam"
        fake.bal = 1500.0                                # the owner tops up
        await acct.step(con, D0 + 200)
        st = await _state(con)
        assert (st["status"], st["balance"], st["high_water"], st["added_usd"]) == ("RUNNING", 1500, 1500, 0), \
            "the funding is the start, not a refill"
        assert len([p for p in fake.sent if p["side"] == "sell"]) == 2
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_halt_while_stopped_is_kept_and_blocks_the_refill_until_cleared():
    con = await _db(picks=None)
    fake = FakeDelta(balance=1500)
    acct = _acct(fake)
    try:
        await acct.state(con, D0 + 60)
        await con.execute("UPDATE fade_live_state SET status = 'STOPPED', reason = 'balance under the floor', "
                          "balance = 950")
        fake.pos[99] = -5                                # something appears at Delta while stopped
        await acct.step(con, D0 + 120)
        st = await _state(con)
        assert st["status"] == "STOPPED" and L.HALT_NOTE in st["reason"] and "no record" in st["reason"]
        del fake.pos[99]
        fake.bal = 1500.0
        rc, msg = await L.record_capital(con, acct, D0 + 3600)
        assert rc == 1 and "look first" in msg
        await con.execute("UPDATE fade_live_state SET reason = 'balance under the floor'")   # what resume does
        rc, msg = await L.record_capital(con, acct, D0 + 3700)
        assert rc == 0 and (await _state(con))["status"] == "RUNNING"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_two_leg_buy_back_is_priced_on_both_legs_and_a_leftover_halts():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = FakeDelta()
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        real = fake.place_order

        def two_legs(order):                             # the limit leg fills 150 of 750; market fills the rest at 1.01
            p = order.to_payload()
            if p["side"] == "buy" and p["order_type"] == "limit_order":
                fake.book["XUSD"] = (1.0, 1.001, 1.0)
                row = real(type(order)(**{**order.__dict__, "size": 150}))
                fake.book["XUSD"] = (1.0, 1.01, 1.0)
                return row
            return real(order)
        fake.place_order = two_legs
        await acct.step(con, D0 + DAY + 10)
        x = (await _rows(con))["XUSD"]
        px = (150 * 1.001 + 600 * 1.01) / 750
        assert x["status"] == "CLOSED" and x["exit_price"] == pytest.approx(px)
        assert x["pnl"] == pytest.approx((1.0 - px) * 750 - 0.03)          # three fees of $0.01
    finally:
        await con.close()
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = FakeDelta()
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        real = fake.place_order
        fake.place_order = lambda order: real(order) if order.to_payload()["side"] == "sell" else \
            real(type(order)(**{**order.__dict__, "size": 1}))            # each leg fills one contract only
        await acct.step(con, D0 + DAY + 10)
        assert (await _rows(con))["XUSD"]["status"] == "OPEN" and fake.pos[1] == -748
        st = await _state(con)
        assert st["status"] == "HALTED" and "left 748 contracts open" in st["reason"]
        assert await con.fetchval("SELECT count(*) FROM fade_live_events WHERE kind = 'EXIT_INCOMPLETE'") == 1
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_reconcile_failure_does_not_stop_the_24_hour_exit():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = FakeDelta()
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        fake.fail["get_positions"] = 1
        await acct.step(con, D0 + DAY + 10)
        assert (await _rows(con))["XUSD"]["status"] == "CLOSED" and fake.pos[1] == 0
        assert await con.fetchval("SELECT count(*) FROM fade_live_events WHERE kind = 'ERROR'") == 1
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_each_exit_attempt_has_new_order_ids():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = FakeDelta()
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        fake.book["XUSD"] = (1.0, 0.0, 1.0)              # no ask: the limit leg is not sent; market only
        real = fake.place_order
        fake.place_order = lambda o: (_ for _ in ()).throw(ConnectionError("down")) \
            if o.to_payload()["side"] == "buy" else real(o)
        await acct.step(con, D0 + DAY + 10)
        fake.place_order = real
        fake.book["XUSD"] = (1.0, 1.001, 1.0)
        await acct.step(con, D0 + DAY + 40)
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "CLOSED" and len(x["exit_cids"]) == 4 and len(set(x["exit_cids"])) == 4
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_the_snapshot_raises_the_high_water_mark_on_a_new_high():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = FakeDelta()
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        fake.fire_stop("XUSD", 0.80)                     # a stop at a profit is still a close the bot explains
        await acct.step(con, D0 + 600)
        st = await _state(con)
        assert st["status"] == "RUNNING" and st["high_water"] == pytest.approx(fake.bal) and fake.bal > 1500
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_entry_refusals_and_skips():
    import live.client as C
    cases = [
        ("rejected", lambda f: setattr(f, "place_order", lambda o: (_ for _ in ()).throw(C.VenueRejected(400, "/v2/orders", {"error": {"code": "no"}}))),
         ("FAILED", "Delta refused"), "RUNNING"),
        ("ambiguous", lambda f: setattr(f, "place_order", lambda o: (_ for _ in ()).throw(C.AmbiguousWrite("?"))),
         ("SENDING", None), "HALTED"),
        ("lev", lambda f: setattr(f, "get_order_leverage", lambda pid: 5.0), ("SKIPPED", "leverage reads 5x"), "RUNNING"),
        ("mark", lambda f: f.book.update(XUSD=(1.0, 1.001, 1.3)), ("SKIPPED", "book unusable"), "RUNNING"),
        ("tiny", lambda f: f.products["XUSD"].update(contract_value="1000"), ("SKIPPED", "below one contract"), "RUNNING"),
    ]
    for name, breaks, (status, why), state in cases:
        con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
        fake = FakeDelta()
        breaks(fake)
        try:
            await _acct(fake).step(con, D0 + 120)
            x = (await _rows(con))["XUSD"]
            assert x["status"] == status, name
            assert why is None or (x["skip_reason"] or "").startswith(why), (name, x["skip_reason"])
            assert (await _state(con))["status"] == state, name
        finally:
            await con.close()
    con = await _db(picks=(("XUSD", 1, 0.20, 1.0),))     # a 40% stop is too far for 1x
    fake = FakeDelta()
    try:
        await _acct(fake).step(con, D0 + 120)
        assert (await _rows(con))["XUSD"]["skip_reason"].startswith("stop 40% away")
    finally:
        await con.close()
    con = await _db()
    fake = FakeDelta()
    import tempfile
    with tempfile.NamedTemporaryFile() as kill:
        try:
            await L.LiveFade(fake, risk_pct=0.10, start_usd=1500, kill_switch_path=kill.name, protect_wait=0.0,
                             sleep=lambda s: None).step(con, D0 + 120)
            assert not fake.sent and {r["skip_reason"] for r in (await _rows(con)).values()} == {"kill switch engaged"}
        finally:
            await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_exactly_1000_trades_and_a_cent_under_stops():
    for bal, status in ((1000.0, "RUNNING"), (999.99, "STOPPED")):
        con = await _db(picks=None)
        fake = FakeDelta(balance=1500)
        acct = _acct(fake)
        try:
            await acct.state(con, D0 + 60)
            fake.bal = bal
            await con.execute("UPDATE fade_live_state SET balance = $1", bal)
            await _paper_day(con, (("XUSD", 1, 0.10, 1.0),), D0)
            await acct.step(con, D0 + 120)
            assert (await _state(con))["status"] == status, bal
        finally:
            await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_close_at_delta_that_is_not_the_stop_or_our_buy_back_halts():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = FakeDelta()
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        n = -fake.pos[1]
        fake.pos[1] = 0
        fake.pending.clear()
        fake._close(1, n, 0.95, 0.01)
        fake.history.insert(0, dict(id=555, product_id=1, side="buy", size=n, unfilled_size=0, reduce_only=True,
                                    average_fill_price="0.95", paid_commission="0.01", client_order_id="byhand"))
        await acct.step(con, D0 + 3600)
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["exit_reason"]) == ("CLOSED", "MANUAL_CLOSE")
        assert (await _state(con))["status"] == "HALTED"
    finally:
        await con.close()


# ============================================================ the daily report

@requires_pg
@pytest.mark.asyncio
async def test_the_report_shows_real_trades_beside_paper_and_flags_a_halt():
    import sys

    from deploy.aws import db_probe as P
    sys.path.insert(0, "scripts")
    import brief_report as B
    con = await _db()
    fake = FakeDelta()
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        fake.fire_stop("XUSD", 1.21)
        await acct.step(con, D0 + 3600)
        lv = await P.collect_live(con, now=dt.datetime.fromtimestamp(D0 + 3600, dt.timezone.utc))
        assert lv["closed"] == 1 and lv["wins"] == 0 and lv["state"]["status"] == "RUNNING"
        now = dt.datetime.fromtimestamp(D0 + 3 * 3600, dt.timezone.utc)
        lv["state"]["balance_at"] = now.isoformat()
        assert B.live_is_on(lv) and B.live_problems(lv, now) == []
        text = "\n".join(B.live_lines(lv) + B.accounts_lines({"accounts": {}}, None, lv))
        assert "REAL trades on Delta" in text and "Open now (1)" in text and "Closed (1)" in text
        assert "stop loss" in text and "REAL Delta (rule A ladder)" in text and "under its high of $1,500.00" in text
        halted = dict(lv, state=dict(lv["state"], status="HALTED", reason="test"))
        assert "HALTED" in B.live_problems(halted, now)[0]
        assert not B.live_is_on({"state": {"status": "OFF", "reason": 'dry_run is not "0"'}, "journal": []})
    finally:
        await con.close()


def test_a_real_sale_far_below_paper_is_flagged():
    import sys
    sys.path.insert(0, "scripts")
    import brief_report as B
    now = dt.datetime.fromtimestamp(D0 + 3600, dt.timezone.utc)
    f = list(__import__("deploy.aws.db_probe", fromlist=["x"]).LIVE_JOURNAL_FIELDS)
    row = dict(day=D0, symbol="XUSD", status="OPEN", skip_reason=None, contracts=26, paper_entry=1.0,
               entry_price=0.94, stop_price=1.2, opened=D0 + 120, closed=None, exit_price=None, exit_reason=None,
               pnl=None, r=None)
    lv = {"state": {"status": "RUNNING", "balance": 176.0, "balance_at": now.isoformat()},
          "journal_fields": f, "journal": [[row[k] for k in f]], "closed": 0, "wins": 0, "pnl": 0, "r": 0}
    probs = B.live_problems(lv, now)
    assert len(probs) == 1 and "0.30R worse than paper" in probs[0]
