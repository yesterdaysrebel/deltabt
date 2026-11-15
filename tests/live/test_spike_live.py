"""The real account's RUN 4, "sell the spike" at 20x (live/spike_live.py; rules in docs/fade_live_spike_prereg.md).

The pure sizing always runs. The account's day -- two resting post-only sells, the first fill cancels the other, extra
isolated margin after the fill, the +$200 lock (moved, or at market), the 24 h exit after the fill, a stop at Delta
logged against its trigger, the 2-stops-a-month hard stop, a resting order left at 24:00 -- runs against PostgreSQL
(DELTABOT_TEST_DSN) and a fake Delta built on tests/live/test_fade_live.FakeDelta: a GTC sell RESTS until the test
fills it (fill_resting), and the bracket legs appear with the fill.
"""
from __future__ import annotations

import pytest

from live import spike_live as S
from tests.live.conftest import requires_pg
from tests.live.test_fade_live import D0, DAY, FakeDelta, _db, _rows, _state

LOCK_EVENTS = ("LOCK_MOVED", "LOCK_AT_MARKET", "LOCK_FAILED")


# ============================================================ pure rules

def test_sizing_is_twenty_x_up_to_the_coin_limits():
    # the owner's SOPH screen: 50% of $1,534.98 at 20x = 83,682 contracts of 100 SOPH at 0.001792
    n, lev, notional = S.spike_size(1534.98, 0.001792, 100, 5.0, 50_000, 0.001, 202_000, share=0.50)
    assert lev == 20 and abs(n - 83_682) / 83_682 < 0.01
    # the run's own share: 49% of $3,184 at 20x on a plain coin
    n, lev, notional = S.spike_size(3184, 1.0, 1, 5.0, 50_000, 0.001, None)
    assert lev == 20 and 30_000 < notional < 31_000
    # PIEVERSE: 20x only up to $10,000 (scaling 0.005% per $ above): $1,560 of margin cannot carry 20x
    n, lev, notional = S.spike_size(3184, 2.0, 10, 5.0, 10_000, 0.005, None)
    assert lev < 20 and notional / lev <= 0.49 * 3184 + 1e-6
    # the contract limit binds
    n, lev, _ = S.spike_size(3184, 0.001, 100, 5.0, 50_000, 0.001, 1_000)
    assert n == 1_000
    assert S.spike_size(0, 1.0, 1, 5.0, 50_000, 0.001, None) == (0, 0, 0.0)


def test_target_and_lock_prices_net_the_planned_dollars():
    e, notional = 1.0, 31_000.0
    tp, lk = S.target_price(e, notional), S.lock_price(e, notional)
    assert abs(notional * (e - tp) / e - notional * S.COST - S.TARGET_USD) < 1e-6
    assert abs(notional * (e - lk) / e - notional * S.COST - S.LOCK_USD) < 1e-6
    assert tp < lk < e


# ============================================================ a fake Delta with resting orders

class SpikeDelta(FakeDelta):
    def __init__(self, **kw):
        super().__init__(**kw)
        for p in self.products.values():
            p.update(initial_margin="5", max_leverage_notional="50000", initial_margin_scaling_factor="0.001",
                     position_size_limit="10000000")
        self.resting: dict[str, dict] = {}
        self.edits: list[tuple] = []
        self.margin_added: list[tuple] = []

    def get_ticker(self, s):
        t = super().get_ticker(s)
        t["close"] = str(self.book[s][0])                 # last traded = bid in this fake
        return t

    def place_order(self, order):
        p = order.to_payload()
        if p.get("time_in_force") == "gtc" and p["side"] == "sell" and not p.get("reduce_only"):
            self.sent.append(p)
            self._id += 1
            row = dict(id=self._id, product_id=p["product_id"], side="sell", size=p["size"], unfilled_size=p["size"],
                       average_fill_price=None, paid_commission="0", state="open", client_order_id=p["client_order_id"])
            self.resting[p["client_order_id"]] = {"row": row, "payload": p}
            self.by_cid[p["client_order_id"]] = row
            return row
        return super().place_order(order)

    def fill_resting(self, cid, n=None, price=None):
        """The resting sell fills (n contracts, default all) at its limit; the bracket legs appear."""
        rest = self.resting[cid]
        row, p = rest["row"], rest["payload"]
        n = row["size"] if n is None else n
        pid = p["product_id"]
        px = float(price or p["limit_price"])
        self.pos[pid] = self.pos.get(pid, 0) - n
        self.entry[pid] = px
        self.bal -= self.FEE
        row.update(unfilled_size=row["size"] - n, average_fill_price=str(px), paid_commission=str(self.FEE),
                   state="closed" if n == row["size"] else "open")
        self._id += 1
        self.pending.append(dict(id=self._id, product_id=pid, reduce_only=True, stop_order_type="stop_loss_order",
                                 stop_price=p["bracket_stop_loss_price"], order_type="market_order"))
        self._id += 1
        self.pending.append(dict(id=self._id, product_id=pid, reduce_only=True, stop_order_type="take_profit_order",
                                 stop_price=p["bracket_take_profit_price"], order_type="market_order"))

    def cancel_order(self, oid, pid):
        super().cancel_order(oid, pid)
        for rest in self.resting.values():
            if rest["row"]["id"] == oid and rest["row"]["state"] == "open":
                rest["row"]["state"] = "cancelled"

    def edit_stop_price(self, oid, pid, stop_price):
        self.edits.append((oid, pid, str(stop_price)))
        for o in self.pending:
            if o["id"] == oid:
                o["stop_price"] = str(stop_price)
        return {"id": oid}

    def add_position_margin(self, pid, delta):
        self.margin_added.append((pid, delta))
        return {"margin": str(delta), "liquidation_price": "1.05", "margin_mode": "isolated"}

    def _margined(self, pid):
        row = super()._margined(pid)
        if pid in (1, 2) and self.margin != "silent":
            lev = self.lev.get(pid, 1)
            row["liquidation_price"] = str(self.entry.get(pid, 1.0) * (1 + 1 / lev - 0.025))
        return row


def _acct(fake, candles=None):
    bars = candles if candles is not None else (lambda sym, a, b: [])
    return S.SpikeLive(fake, risk_pct=0.10, start_usd=3184, kill_switch_path="/nonexistent/kill",
                       protect_wait=0.0, sleep=lambda s: None, candles=bars)


async def _events(con, kind=None):
    rows = await con.fetch("SELECT kind, symbol, detail FROM fade_live_events ORDER BY id")
    return [r for r in rows if kind is None or r["kind"] == kind]


def _cid(fake, pid):
    return next(c for c, r in fake.resting.items() if r["payload"]["product_id"] == pid)


# ============================================================ the account's day (PostgreSQL)

@requires_pg
@pytest.mark.asyncio
async def test_two_resting_sells_with_a_last_price_stop_and_both_cancelled_at_midnight():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0), ("YUSD", 2, 0.12, 2.0)))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        st = await _state(con)
        assert (st["status"], st["run_version"]) == ("RUNNING", 4)
        rows = await _rows(con)
        assert {s: r["status"] for s, r in rows.items()} == {"XUSD": "RESTING", "YUSD": "RESTING"}
        x = fake.resting[_cid(fake, 1)]["payload"]
        assert x["time_in_force"] == "gtc" and x["post_only"] is True and x["side"] == "sell"
        assert x["limit_price"] == "1.05"                              # 5% above the last price 1.00
        assert x["bracket_stop_loss_price"] == "1.0736"                # +2.25% from the limit, on the tick
        assert x["stop_trigger_method"] == x["bracket_stop_trigger_method"] == "last_traded_price"
        assert fake.lev[1] == 20 and rows["XUSD"]["leverage"] == 20
        notional = x["size"] * 1.05
        assert abs(notional - 0.49 * 3184 / (0.05 + S.FEE_RESERVE)) <= 1.05   # margin / (5% + Delta's 0.12% fee reserve)
        assert not fake.pos                                             # nothing sold yet
        await acct.step(con, D0 + DAY + 5)                              # 24:00 UTC: no spike came
        rows = await _rows(con)
        assert {s: (r["status"], r["skip_reason"][:12]) for s, r in rows.items() if s in ("XUSD", "YUSD")} == \
            {"XUSD": ("SKIPPED", "no 5% spike "), "YUSD": ("SKIPPED", "no 5% spike ")}
        assert all(r["row"]["state"] == "cancelled" for r in fake.resting.values())
        assert (await _state(con))["status"] == "RUNNING"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_the_first_fill_cancels_the_other_and_adds_extra_margin():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0), ("YUSD", 2, 0.12, 2.0)))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))                                # XUSD spikes to 1.05 and fills
        await acct.step(con, D0 + 3600)
        rows = await _rows(con)
        assert rows["XUSD"]["status"] == "OPEN" and rows["XUSD"]["entry_price"] == 1.05
        assert rows["YUSD"]["status"] == "SKIPPED" and "filled first" in rows["YUSD"]["skip_reason"]
        assert fake.resting[_cid(fake, 2)]["row"]["state"] == "cancelled"
        assert fake.margin_added == [(1, round(0.25 * 3184, 2))]
        assert rows["XUSD"]["extra_margin"] == round(0.25 * 3184, 2)
        assert [e["kind"] for e in await _events(con, "EXTRA_MARGIN")] == ["EXTRA_MARGIN"]
        assert (await _state(con))["status"] == "RUNNING"               # a resting fill is not a stranger's position
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_partial_fill_keeps_what_filled_and_cancels_the_rest():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        cid = _cid(fake, 1)
        size = fake.resting[cid]["row"]["size"]
        fake.fill_resting(cid, n=size // 3)
        await acct.step(con, D0 + 600)
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "OPEN" and x["contracts"] == size // 3
        assert fake.resting[cid]["row"]["id"] in fake.cancelled         # the unfilled rest
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_the_lock_moves_the_stop_after_a_one_percent_fall():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    lows = {"low": 1.05}
    acct = _acct(fake, candles=lambda sym, a, b: [{"time": a + 60, "low": lows["low"], "close": lows["low"]}])
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        await acct.step(con, D0 + 600)
        assert not fake.edits                                           # no 1% fall yet
        lows["low"] = 1.05 * 0.989                                      # the low reaches -1.1%
        fake.book["XUSD"] = (1.035, 1.036, 1.035)                       # and the price is still below the lock level
        await acct.step(con, D0 + 900)
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "OPEN" and x["lock_price"] is not None
        assert fake.edits and float(fake.edits[-1][2]) == x["lock_price"]
        notional = x["contracts"] * 1.05
        assert abs(notional * (1.05 - x["lock_price"]) / 1.05 - notional * S.COST - S.LOCK_USD) < notional * 0.0001 + 1
        assert [e["kind"] for e in await _events(con) if e["kind"] in LOCK_EVENTS] == ["LOCK_MOVED"]
        n_edits = len(fake.edits)
        await acct.step(con, D0 + 1200)                                 # moved once only
        assert len(fake.edits) == n_edits
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_the_lock_buys_back_at_market_when_the_price_is_already_above_it_and_does_not_halt():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake, candles=lambda sym, a, b: [{"time": a + 60, "low": 1.05 * 0.985, "close": 1.049}])
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        fake.book["XUSD"] = (1.049, 1.0495, 1.049)                      # bounced back above the +$200 level
        await acct.step(con, D0 + 600)
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["exit_reason"]) == ("CLOSED", "LOCK")
        assert fake.pos[1] == 0 and not fake.edits
        assert (await _state(con))["status"] == "RUNNING"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_twenty_four_hours_after_the_fill_not_after_midnight():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        await acct.step(con, D0 + 10 * 3600)                            # filled at 10:00
        await acct.step(con, D0 + DAY + 60)                             # next midnight: still open
        assert (await _rows(con))["XUSD"]["status"] == "OPEN"
        await acct.step(con, D0 + DAY + 10 * 3600 + 60)                 # 24 h after the fill
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["exit_reason"]) == ("CLOSED", "TIME")
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_stop_is_logged_against_its_trigger_and_two_full_stops_end_the_month():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        for k in range(2):
            day = D0 + k * DAY
            if k:
                from tests.live.test_fade_live import _paper_day
                await _paper_day(con, (("XUSD", 1, 0.10, 1.0),), day)
            await acct.step(con, day + 120)
            fake.fill_resting(_cid(fake, 1) if k == 0 else list(fake.resting)[-1])
            await acct.step(con, day + 600)
            fake.fire_stop("XUSD", 1.0760, at=day + 700)                # trigger 1.0736, filled 0.22% worse
            await acct.step(con, day + 800)
            await acct.step(con, day + 800 + 301)
        fills = await _events(con, "STOP_FILL")
        assert len(fills) == 2 and '"slip_pct": 0.2235' in fills[0]["detail"]
        assert await acct.month_stops(con, D0 + 2 * DAY) == 2
        from tests.live.test_fade_live import _paper_day
        await _paper_day(con, (("XUSD", 1, 0.10, 1.0),), D0 + 2 * DAY)
        await acct.step(con, D0 + 2 * DAY + 120)
        rows = await con.fetch("SELECT status, skip_reason FROM fade_live_positions WHERE day = $1",
                               __import__("datetime").datetime.fromtimestamp(D0 + 2 * DAY, __import__("datetime").timezone.utc))
        assert [(r["status"], r["skip_reason"][:16]) for r in rows] == [("SKIPPED", "2 full stops thi")]
        assert (await _state(con))["status"] == "RUNNING"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_no_new_resting_orders_while_a_position_is_open():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 20 * 3600)                            # late: the window is 00:00-00:30
        assert (await _rows(con))["XUSD"]["status"] == "SKIPPED"
    finally:
        await con.close()
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        await acct.step(con, D0 + 23 * 3600)                            # filled late on day 1, still open at midnight
        from tests.live.test_fade_live import _paper_day
        await _paper_day(con, (("YUSD", 1, 0.12, 2.0),), D0 + DAY)
        await acct.step(con, D0 + DAY + 120)
        y = await con.fetchrow("SELECT status, skip_reason FROM fade_live_positions WHERE symbol = 'YUSD'")
        assert (y["status"], y["skip_reason"]) == ("SKIPPED", "a position is open: one at a time")
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_an_extra_margin_reply_that_is_lost_is_read_back_not_resent():
    import live.client as C
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    calls = []

    def lost(pid, delta):
        calls.append(delta)
        raise C.AmbiguousWrite("timeout")
    fake.add_position_margin = lost
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        await acct.step(con, D0 + 600)
        assert len(calls) == 1                                          # never sent twice
        assert [e["kind"] for e in await _events(con, "EXTRA_MARGIN_UNCLEAR")] == ["EXTRA_MARGIN_UNCLEAR"]
        assert (await _rows(con))["XUSD"]["status"] == "OPEN"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_run_4_begins_on_the_funded_3184_wallet_although_the_host_still_says_1500():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.13)
    acct = S.SpikeLive(fake, risk_pct=0.10, start_usd=1500, kill_switch_path="/nonexistent/kill",
                       protect_wait=0.0, sleep=lambda s: None, candles=lambda *a: [])
    try:
        await acct.step(con, D0 + 120)
        st = await _state(con)
        assert (st["status"], st["run_version"]) == ("RUNNING", 4) and st["balance"] == pytest.approx(3184.13)
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_liquidation_counts_as_a_full_stop_and_does_not_halt():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        await acct.step(con, D0 + 600)
        fake.fire_stop("XUSD", 1.10, kind="liquidation_order", at=D0 + 700)
        await acct.step(con, D0 + 800)
        await acct.step(con, D0 + 800 + 301)
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["exit_reason"]) == ("CLOSED", "LIQUIDATION")
        assert (await _state(con))["status"] == "RUNNING" and await acct.month_stops(con, D0 + DAY) == 1
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_option_a_the_fast_watcher_adds_the_extra_margin_without_waiting_for_the_pass():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0), ("YUSD", 2, 0.12, 2.0)))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        seen = await acct.fast(con, D0 + 125)                           # 5 s later, between two 30 s passes
        rows = await _rows(con)
        assert seen >= 1 and rows["XUSD"]["status"] == "OPEN" and rows["XUSD"]["extra_margin"] == round(0.25 * 3184, 2)
        assert rows["YUSD"]["status"] == "SKIPPED"                      # the other pick cancelled at once too
        await acct.fast(con, D0 + 128)                                  # and again, and the next full pass:
        await acct.step(con, D0 + 150)
        assert fake.margin_added == [(1, round(0.25 * 3184, 2))]        # ... the margin was added exactly once
        assert (await _state(con))["status"] == "RUNNING"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_option_a_a_refused_top_up_is_logged_and_not_retried_every_poll():
    import live.client as C
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    calls = []

    def refused(pid, delta):
        calls.append(delta)
        raise C.VenueRejected(400, "/v2/positions/change_margin", {"error": {"code": "insufficient_margin"}})
    fake.add_position_margin = refused
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        for t in (125, 128, 131, 150):
            await acct.fast(con, D0 + t)
        await acct.step(con, D0 + 180)
        assert len(calls) == 1
        assert [e["kind"] for e in await _events(con, "EXTRA_MARGIN_REFUSED")] == ["EXTRA_MARGIN_REFUSED"]
    finally:
        await con.close()


def test_the_target_and_lock_scale_with_the_balance():
    e = 1.0
    full = S.target_price(e, 31_000.0, 3184.0), S.lock_price(e, 31_000.0, 3184.0)
    half = S.target_price(e, 15_500.0, 1592.0), S.lock_price(e, 15_500.0, 1592.0)
    assert full == pytest.approx(half)                                   # the same price fall at half the balance
    assert abs(15_500.0 * (e - half[0]) / e - 15_500.0 * S.COST - 250.0) < 1e-6   # $250 target at $1,592
    assert abs(15_500.0 * (e - half[1]) / e - 15_500.0 * S.COST - 100.0) < 1e-6   # $100 lock at $1,592


@requires_pg
@pytest.mark.asyncio
async def test_the_trail_follows_the_price_down_after_the_lock_and_never_up():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    lows = {"low": 1.05 * 0.989}                                        # a 1.1% fall: the lock goes in
    acct = _acct(fake, candles=lambda sym, a, b: [{"time": a + 60, "low": lows["low"], "close": lows["low"]}])
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        fake.book["XUSD"] = (1.035, 1.036, 1.035)
        await acct.step(con, D0 + 600)                                  # the lock
        x = (await _rows(con))["XUSD"]
        lock = x["lock_price"]
        assert lock is not None and x["trail_price"] is None
        await acct.step(con, D0 + 900)                                  # low 1.1%: 0.8% above it is looser than the lock
        assert (await _rows(con))["XUSD"]["trail_price"] is None
        lows["low"] = 1.05 * 0.984                                      # a 1.6% fall: 0.8% above it is tighter
        fake.book["XUSD"] = (1.034, 1.035, 1.034)
        await acct.step(con, D0 + 1200)
        x = (await _rows(con))["XUSD"]
        assert x["trail_price"] is not None and x["trail_price"] < lock
        assert abs(x["trail_price"] - 1.05 * 0.984 * 1.008) < 0.0002
        assert float(fake.edits[-1][2]) == x["trail_price"]
        n = len(fake.edits)
        lows["low"] = 1.05 * 0.984                                      # no new low: the stop does not move (never up)
        await acct.step(con, D0 + 1500)
        assert len(fake.edits) == n
        assert [e["kind"] for e in await _events(con) if e["kind"].startswith("TRAIL")] == ["TRAIL_MOVED"]
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_the_trail_buys_back_at_market_when_the_price_already_bounced_above_it():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    lows = {"low": 1.05 * 0.989}
    acct = _acct(fake, candles=lambda sym, a, b: [{"time": a + 60, "low": lows["low"], "close": lows["low"]}])
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        fake.book["XUSD"] = (1.035, 1.036, 1.035)
        await acct.step(con, D0 + 600)                                  # the lock
        lows["low"] = 1.05 * 0.983                                      # fell 1.7% ...
        fake.book["XUSD"] = (1.045, 1.046, 1.045)                       # ... and is back up past the trailing level
        await acct.step(con, D0 + 900)
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["exit_reason"]) == ("CLOSED", "LOCK") and fake.pos[1] == 0
        assert (await _state(con))["status"] == "RUNNING"
    finally:
        await con.close()
