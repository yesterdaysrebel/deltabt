"""The gainer-fade REAL-MONEY account (live/fade_live.py; rules in docs/fade_live_quick_prereg.md).

The pure rules always run. The account's day -- entries, the exchange-held stop and take-profit, the
24 h exit, a leg that fires at Delta, the floor, a halt -- runs against PostgreSQL (DELTABOT_TEST_DSN)
and a fake Delta that answers in the venue's shapes: bracket legs rest as PENDING reduce-only
`stop_loss_order` / `take_profit_order` rows, positions are signed sizes, a close shows in order history
as a filled reduce-only buy.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from live import fade_live as L
from tests.live.conftest import TEST_DSN, requires_pg

D0 = 1793232000                                   # 2026-10-29 00:00 UTC
DAY = 86400


# ============================================================ pure rules

def test_the_switch_is_strict():
    on = {"DELTABOT_DRY_RUN": "0", "DELTA_ENV": "prod", "DELTA_API_KEY": "k", "DELTA_API_SECRET": "s",
          "DELTABOT_RISK_PER_TRADE": "0.05", "DELTABOT_EQUITY": "1200"}
    assert L.enabled(on) == (True, "on")
    for k, v in (("DELTABOT_DRY_RUN", "1"), ("DELTABOT_DRY_RUN", ""), ("DELTA_ENV", "testnet"),
                 ("DELTA_API_SECRET", ""), ("DELTABOT_RISK_PER_TRADE", "0.06"), ("DELTABOT_EQUITY", "960"),
                 ("DELTABOT_EQUITY", "176")):          # version 1's sizing must not switch version 2 on
        assert not L.enabled(dict(on, **{k: v}))[0], (k, v)
    unset = dict(on)
    del unset["DELTABOT_DRY_RUN"]                 # unset is NOT "0" here, unlike live/__main__
    assert not L.enabled(unset)[0]


def test_sizing_leverage_and_ticks():
    # 5% of $1,200 = $60 between a bid of 1.00 and a stop 20% above, 1 coin per contract: 300 contracts;
    # at most 45% of the balance as notional ($540) would allow 540, so risk binds.
    assert L.contracts_for(1200, 0.05, 1.0, 1.2, 1.0) == 300
    # a 3% stop would allow 2,000 contracts by risk; the 45% notional cap binds at 540
    assert L.contracts_for(1200, 0.05, 1.0, 1.03, 1.0) == 540
    assert L.contracts_for(1200, 0.05, 0.2, 0.3, 1000) == 0        # one contract risks $100
    # rule QUICK: the take-profit is 0.2 x the stop distance under the entry, rounded away from it
    assert L.target_for(1.0, 1.2, Decimal("0.0001")) == Decimal("0.96")
    assert L.target_for(2.0, 2.48, Decimal("0.0001")) == Decimal("1.904")
    assert L.target_for(1.0, 1.2, Decimal("0.05")) == Decimal("0.95")   # 0.96 is not on a 0.05 grid: down
    assert L.leverage_ok(0.30, 2.5) and not L.leverage_ok(0.33, 2.5)  # 1x: 0.975 >= 3 x dist
    assert L.to_tick(1.23456, Decimal("0.001"), up=True) == Decimal("1.235")
    assert L.to_tick(1.23456, Decimal("0.001"), up=False) == Decimal("1.234")
    pnl, r = L.money(1.0, 0.9, 26, 1.0, 1.2, 0.05)
    assert abs(pnl - 2.55) < 1e-9 and abs(r - 2.55 / 5.2) < 1e-9


# ============================================================ a fake Delta

class FakeDelta:
    """Just enough of live.client.LiveClient, answering in Delta's shapes."""

    def __init__(self, balance=1200.0, attach_stop=True, attach_target=True):
        self.bal = balance
        self.attach_stop = attach_stop
        self.attach_target = attach_target
        self.products = {"XUSD": dict(id=1, tick_size="0.0001", contract_value="1", maintenance_margin="2.5"),
                         "YUSD": dict(id=2, tick_size="0.0001", contract_value="1", maintenance_margin="2.5")}
        self.book = {"XUSD": (1.0000, 1.0010, 1.0005), "YUSD": (2.0000, 2.0020, 2.0010)}
        self.pos: dict[int, int] = {}
        self.pending: list[dict] = []
        self.history: list[dict] = []
        self.lev: dict[int, int] = {}
        self.sent: list[dict] = []
        self.cancelled: list[int] = []
        self.by_cid: dict[str, dict] = {}
        self._id = 100

    def _sym(self, pid):
        return next(s for s, p in self.products.items() if p["id"] == pid)

    def get_product(self, s): return self.products[s]
    def get_ticker(self, s):
        b, a, m = self.book[s]
        return {"mark_price": str(m), "quotes": {"best_bid": str(b), "best_ask": str(a)}}
    def get_balance(self): return [{"asset_symbol": "USD", "balance": str(self.bal)}]
    def get_position(self, pid):
        return {"product_id": pid, "size": self.pos[pid]} if self.pos.get(pid) else None
    def get_positions(self):
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

    def place_order(self, order):
        p = order.to_payload()
        self.sent.append(p)
        pid, size, sym = p["product_id"], p["size"], self._sym(p["product_id"])
        bid, ask, _ = self.book[sym]
        self._id += 1
        if p["side"] == "sell" and not p.get("reduce_only"):
            self.pos[pid] = self.pos.get(pid, 0) - size
            row = dict(id=self._id, product_id=pid, side="sell", size=size, unfilled_size=0,
                       average_fill_price=str(bid), paid_commission="0.01", state="closed",
                       client_order_id=p["client_order_id"])
            if self.attach_stop and "bracket_stop_loss_price" in p:
                self._id += 1
                self.pending.append(dict(id=self._id, product_id=pid, reduce_only=True,
                                         stop_order_type="stop_loss_order", stop_price=p["bracket_stop_loss_price"]))
            if self.attach_target and "bracket_take_profit_price" in p:
                self._id += 1
                self.pending.append(dict(id=self._id, product_id=pid, reduce_only=True,
                                         stop_order_type="take_profit_order",
                                         stop_price=p["bracket_take_profit_price"]))
        else:
            n = min(size, -self.pos.get(pid, 0)) if p.get("reduce_only") else size
            self.pos[pid] = self.pos.get(pid, 0) + n
            row = dict(id=self._id, product_id=pid, side="buy", size=size, unfilled_size=size - n,
                       average_fill_price=str(ask) if n else None, paid_commission="0.01" if n else "0",
                       state="closed", reduce_only=True, client_order_id=p["client_order_id"])
            if n:
                self.history.insert(0, row)
        self.by_cid[p["client_order_id"]] = row
        return row

    def fire_stop(self, sym, price, kind="stop_loss_order"):
        """A bracket leg fires at Delta: the position closes and Delta cancels the other leg."""
        pid = self.products[sym]["id"]
        n = -self.pos.get(pid, 0)
        self.pos[pid] = 0
        self.pending = [o for o in self.pending if o["product_id"] != pid]
        self._id += 1
        self.history.insert(0, dict(id=self._id, product_id=pid, side="buy", size=n, unfilled_size=0,
                                    average_fill_price=str(price), paid_commission="0.02", reduce_only=True,
                                    stop_order_type=kind, state="closed"))

    def fire_target(self, sym, price):
        self.fire_stop(sym, price, kind="take_profit_order")


# ============================================================ the account's day (PostgreSQL)

async def _db(picks=(("XUSD", 1, 0.10, 1.0), ("YUSD", 2, 0.12, 2.0)), day=D0):
    import asyncpg

    from live import fade
    con = await asyncpg.connect(TEST_DSN)
    await con.execute("DROP TABLE IF EXISTS fade_live_positions, fade_live_state, fade_live_events, "
                      "fade_positions, fade_days, fade_heartbeat")
    await con.execute(fade.SCHEMA)
    await L.ensure_schema(con)
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
    return L.LiveFade(fake, risk_pct=0.05, start_usd=1200, kill_switch_path="/nonexistent/kill",
                      protect_wait=0.0, sleep=lambda s: None, **kw)


async def _rows(con):
    return {r["symbol"]: r for r in await con.fetch("SELECT * FROM fade_live_positions")}


@requires_pg
@pytest.mark.asyncio
async def test_a_full_day_sell_with_a_resting_stop_then_buy_back_after_24_hours():
    con = await _db()
    fake = FakeDelta()
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        rows = await _rows(con)
        assert {s: r["status"] for s, r in rows.items()} == {"XUSD": "OPEN", "YUSD": "OPEN"}
        sells = [p for p in fake.sent if p["side"] == "sell"]
        assert len(sells) == 2 and all(p["time_in_force"] == "ioc" and "bracket_stop_loss_price" in p
                                       and p["stop_trigger_method"] == "mark_price" for p in sells)
        x = next(p for p in sells if p["product_id"] == 1)
        assert x["limit_price"] == "0.99" and x["bracket_stop_loss_price"] == "1.2"   # bid 1.00, 2 x 10% ATR
        assert x["bracket_take_profit_price"] == "0.96"                          # 0.2 x 0.20 under the bid
        assert x["size"] == 300 and fake.lev == {1: 1, 2: 1}                      # $60 / $0.20 a contract
        assert rows["XUSD"]["paper_entry"] == 1.0 and rows["XUSD"]["entry_price"] == 1.0
        assert rows["XUSD"]["target_price"] == 0.96 and rows["YUSD"]["target_price"] == 1.904
        assert not await con.fetchval("SELECT count(*) FROM fade_live_events WHERE kind = 'TAKE_PROFIT_MISSING'")
        await acct.step(con, D0 + 3600)                                          # nothing to do mid-day
        assert len(fake.sent) == 2
        await acct.step(con, D0 + DAY + 10)                                      # 24 h later
        rows = await _rows(con)
        assert {s: (r["status"], r["exit_reason"]) for s, r in rows.items()} == \
            {"XUSD": ("CLOSED", "TIME"), "YUSD": ("CLOSED", "TIME")}
        buys = [p for p in fake.sent if p["side"] == "buy"]
        assert len(buys) == 2 and all(p["reduce_only"] for p in buys) and fake.pos == {1: 0, 2: 0}
        assert not fake.pending and len(fake.cancelled) == 4                     # both legs cancelled once flat
        assert abs(rows["XUSD"]["pnl"] - ((1.0 - 1.001) * 300 - 0.02)) < 1e-9
        assert await con.fetchval("SELECT status FROM fade_live_state") == "RUNNING"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_stop_that_fires_at_delta_is_recorded_from_order_history():
    con = await _db()
    fake = FakeDelta()
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        fake.fire_stop("XUSD", 1.21)
        await acct.step(con, D0 + 5 * 3600)
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["exit_reason"], x["exit_price"]) == ("CLOSED", "STOP_LOSS", 1.21)
        assert x["r"] < -1.0                                                    # slipped past the stop
        assert await con.fetchval("SELECT status FROM fade_live_state") == "RUNNING"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_take_profit_that_fires_at_delta_is_a_normal_close_not_a_halt():
    con = await _db()
    fake = FakeDelta()
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        fake.fire_target("XUSD", 0.96)
        await acct.step(con, D0 + 2 * 3600)
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["exit_reason"], x["exit_price"]) == ("CLOSED", "TAKE_PROFIT", 0.96)
        assert abs(x["r"] - (0.04 * 300 - 0.03) / 60) < 1e-9                    # +0.2R less fees
        assert await con.fetchval("SELECT status FROM fade_live_state") == "RUNNING"
        await acct.step(con, D0 + DAY + 10)                                      # 24 h: only YUSD left to buy back
        assert [p["product_id"] for p in fake.sent if p["side"] == "buy"] == [2]
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_missing_take_profit_is_reported_but_the_position_is_kept():
    con = await _db()
    fake = FakeDelta(attach_target=False)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        rows = await _rows(con)
        assert {s: r["status"] for s, r in rows.items()} == {"XUSD": "OPEN", "YUSD": "OPEN"}
        assert await con.fetchval("SELECT count(*) FROM fade_live_events WHERE kind = 'TAKE_PROFIT_MISSING'") == 2
        assert await con.fetchval("SELECT status FROM fade_live_state") == "RUNNING"
        assert not [p for p in fake.sent if p["side"] == "buy"], "the stop still protects it: nothing bought back"
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
        assert "YUSD" not in rows, "a halted account opens nothing more"
        assert fake.pos == {1: 0} and await con.fetchval("SELECT status FROM fade_live_state") == "HALTED"
        await acct.step(con, D0 + 300)
        assert len([p for p in fake.sent if p["side"] == "sell"]) == 1
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_under_the_floor_the_run_stops_and_sends_nothing():
    con = await _db()
    fake = FakeDelta(balance=959.0)
    try:
        await _acct(fake).step(con, D0 + 120)
        assert not fake.sent
        assert await con.fetchval("SELECT status FROM fade_live_state") == "STOPPED"
        assert (await _rows(con))["XUSD"]["skip_reason"].startswith("run stopped")
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_wallet_that_is_not_the_sub_account_halts_before_any_order():
    con = await _db()
    fake = FakeDelta(balance=1600.0)
    try:
        await _acct(fake).step(con, D0 + 120)
        assert not fake.sent and await con.fetchval("SELECT status FROM fade_live_state") == "HALTED"
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
        assert "no record" in await con.fetchval("SELECT reason FROM fade_live_state")
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
        cid = client_order_id("fade-live", str(D0), "XUSD", "entry")
        fake.pos[1] = -26
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
        assert await con.fetchval("SELECT status FROM fade_live_state") == "RUNNING"
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
        assert st == ["CLOSED", "OPEN"] and fake.pos[1] == -300
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
        assert "stop loss" in text and "REAL Delta (Quick, 5%)" in text and "$1,200.00" in text
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
