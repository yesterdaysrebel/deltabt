"""The real account's RUN 4, Spike Sell (live/spike_live.py; rules in docs/fade_live_spike_prereg.md): 10% margin at up
to 20x, no take-profit, the stop to break-even once the best point nets +$30.61 x scale, then a 5% trail.

The pure sizing always runs. The account's day -- two resting post-only sells, the first fill cancels the other, extra
isolated margin after the fill, the break-even move (moved, or at market), the 5% trail, the 24 h exit after the fill,
a stop at Delta logged against its trigger, the 2-stops-a-month hard stop, a resting order left at 24:00 -- runs
against PostgreSQL
(DELTABOT_TEST_DSN) and a fake Delta built on tests/live/test_fade_live.FakeDelta: a GTC sell RESTS until the test
fills it (fill_resting), and the bracket legs appear with the fill.
"""
from __future__ import annotations

import pytest

from live import spike_live as S
from tests.live.conftest import requires_pg
from tests.live.test_fade_live import D0, DAY, FakeDelta, _db, _rows, _state

LOCK_EVENTS = ("LOCK_MOVED", "LOCK_AT_MARKET", "LOCK_FAILED")


@pytest.fixture(autouse=True)
def _exit_mode(request, monkeypatch):
    """The tests in this file were written for exit C (break-even, then the trail) and keep that mode. The owner's
    2026-11-16 setting -- no break-even step, a plain 5% trail from the fill -- is the module default and is what the
    `test_plain_trail_*` tests run."""
    if request.node.name.startswith("test_rule_c"):
        return                                           # rule C (owner 2026-11-17) runs exactly as shipped
    monkeypatch.setattr(S, "BREAKEVEN_LOCK", not request.node.name.startswith("test_plain_trail"))
    # ... and with run 4's first numbers (20x, a 2.25% stop, a 5% trail from the fill), which these tests were built on
    monkeypatch.setattr(S, "MAX_LEVERAGE", 20)
    monkeypatch.setattr(S, "STOP_ABOVE", 0.0225)
    monkeypatch.setattr(S, "TRAIL", 0.05)
    monkeypatch.setattr(S, "TRAIL_ONLY_PAST_ENTRY", False)


# ============================================================ pure rules

def test_sizing_is_twenty_x_up_to_the_coin_limits():
    # the owner's SOPH screen: 50% of $1,534.98 at 20x = 83,682 contracts of 100 SOPH at 0.001792
    n, lev, notional = S.spike_size(1534.98, 0.001792, 100, 5.0, 50_000, 0.001, 202_000, share=0.50)
    assert lev == 20 and abs(n - 83_682) / 83_682 < 0.01
    # the run's own share: 20% of $3,184 at 20x on a plain coin (owner 2026-11-16; was 10%)
    assert S.TRADE_MARGIN == 0.20 and S.EXTRA_MARGIN == pytest.approx(0.20 * 25 / 49)   # owner 2026-11-16: 20%
    n, lev, notional = S.spike_size(3184, 1.0, 1, 5.0, 50_000, 0.001, None)
    assert lev == 20 and 12_300 < notional < 12_500
    # the first version's share: 49% of $3,184 at 20x
    n, lev, notional = S.spike_size(3184, 1.0, 1, 5.0, 50_000, 0.001, None, share=0.49)
    assert lev == 20 and 30_000 < notional < 31_000
    # PIEVERSE: 20x only up to $10,000 (scaling 0.005% per $ above): $1,560 of margin cannot carry 20x ...
    n, lev, notional = S.spike_size(3184, 2.0, 10, 5.0, 10_000, 0.005, None, share=0.49)
    assert lev < 20 and notional / lev <= 0.49 * 3184 + 1e-6
    # ... 10% ($318) can ...
    n, lev, notional = S.spike_size(3184, 2.0, 10, 5.0, 10_000, 0.005, None, share=0.10)
    assert lev == 20 and notional < 10_000
    # ... and the run's 20% ($637) is above the tier: less than 20x, never more margin than the share
    n, lev, notional = S.spike_size(3184, 2.0, 10, 5.0, 10_000, 0.005, None)
    assert lev < 20 and notional / lev <= S.TRADE_MARGIN * 3184 + 1e-6
    # the contract limit binds
    n, lev, _ = S.spike_size(3184, 0.001, 100, 5.0, 50_000, 0.001, 1_000)
    assert n == 1_000
    assert S.spike_size(0, 1.0, 1, 5.0, 50_000, 0.001, None) == (0, 0, 0.0)


def test_activation_and_break_even_prices_net_the_planned_dollars():
    e, notional = 1.0, S.TRADE_MARGIN * 3184 / (0.05 + S.FEE_RESERVE)        # the run's position on $3,184
    act, lk = S.activation_price(e, notional), S.lock_price(e, notional)
    assert S.BREAKEVEN_AFTER_USD == pytest.approx(150 * S.TRADE_MARGIN / 0.49) and S.LOCK_USD == 0 and S.TRAIL == 0.05
    assert abs(notional * (e - act) / e - notional * S.COST - S.BREAKEVEN_AFTER_USD) < 1e-6   # nets +$30.61
    assert abs(notional * (e - lk) / e - notional * S.COST) < 1e-9                            # nets $0: break-even
    assert act < lk < e and 0.005 < 1 - act / e < 0.007                                       # a ~0.6% fall


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

    def fill_resting(self, cid, n=None, price=None, at=None):
        """The resting sell fills (n contracts, default all) at its limit; the bracket legs appear. `at` (epoch s)
        stamps the fill as Delta does (`updated_at`)."""
        rest = self.resting[cid]
        row, p = rest["row"], rest["payload"]
        if at is not None:
            from tests.live.test_fade_live import _iso
            row["updated_at"] = _iso(at)
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
        if p.get("bracket_take_profit_price"):               # exit C sends none
            self._id += 1
            self.pending.append(dict(id=self._id, product_id=pid, reduce_only=True, stop_order_type="take_profit_order",
                                     stop_price=p["bracket_take_profit_price"], order_type="market_order"))

    def cancel_order(self, oid, pid):
        super().cancel_order(oid, pid)
        for rest in self.resting.values():
            if rest["row"]["id"] == oid and rest["row"]["state"] == "open":
                rest["row"]["state"] = "cancelled"

    def get_open_orders(self, pid=None):
        """Orders resting on the book (Delta: states=open) -- the entry sells; never the bracket legs."""
        return [dict(r["row"]) for r in self.resting.values() if r["row"]["state"] == "open" and pid in (None, r["row"]["product_id"])]

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
        assert "bracket_take_profit_price" not in x and rows["XUSD"]["target_price"] is None   # exit C: no target
        assert fake.lev[1] == 20 and rows["XUSD"]["leverage"] == 20
        notional = x["size"] * 1.05
        assert abs(notional - S.TRADE_MARGIN * 3184 / (0.05 + S.FEE_RESERVE)) <= 1.05   # margin / (5% + Delta's 0.12% fee reserve)
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
        assert fake.margin_added == [(1, round(S.EXTRA_MARGIN * 3184, 2))]
        assert rows["XUSD"]["extra_margin"] == round(S.EXTRA_MARGIN * 3184, 2)
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
async def test_the_stop_moves_to_break_even_once_the_best_point_nets_the_activation_profit():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    lows = {"low": 1.05}
    acct = _acct(fake, candles=lambda sym, a, b: [{"time": a + 60, "low": lows["low"], "close": lows["low"]}])
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        await acct.step(con, D0 + 600)
        assert not fake.edits                                           # no fall yet
        lows["low"] = 1.05 * 0.996                                      # a 0.4% fall nets under +$30.61: nothing moves
        fake.book["XUSD"] = (1.046, 1.047, 1.046)
        await acct.step(con, D0 + 750)
        assert not fake.edits and (await _rows(con))["XUSD"]["lock_price"] is None
        lows["low"] = 1.05 * 0.993                                      # a 0.7% fall: past the ~0.61% activation
        fake.book["XUSD"] = (1.043, 1.044, 1.043)                       # and the price is still below break-even
        await acct.step(con, D0 + 900)
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "OPEN" and x["lock_price"] is not None
        assert fake.edits and float(fake.edits[-1][2]) == x["lock_price"]
        assert abs(x["lock_price"] - 1.05 * (1 - S.COST)) < 0.0002      # break-even: the entry less the costs
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
        fake.book["XUSD"] = (1.049, 1.0495, 1.049)                      # bounced back above the break-even level
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
async def test_a_stop_is_logged_against_its_trigger_and_two_full_stops_do_not_end_the_month():
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
        assert [r["status"] for r in rows] == ["RESTING"]              # owner 2026-11-16: no monthly limit
        assert (await _state(con))["status"] == "RUNNING"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_the_month_off_rule_still_works_when_a_limit_is_set(monkeypatch):
    """MONTH_STOP_LIMIT = None is the owner's choice; setting a number must bring the old rule back unchanged."""
    import live.spike_live as SL
    monkeypatch.setattr(SL, "MONTH_STOP_LIMIT", 2)
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        from tests.live.test_fade_live import _paper_day
        for k in range(2):
            day = D0 + k * DAY
            if k:
                await _paper_day(con, (("XUSD", 1, 0.10, 1.0),), day)
            await acct.step(con, day + 120)
            fake.fill_resting(_cid(fake, 1) if k == 0 else list(fake.resting)[-1])
            await acct.step(con, day + 600)
            fake.fire_stop("XUSD", 1.0760, at=day + 700)
            await acct.step(con, day + 800)
            await acct.step(con, day + 800 + 301)
        assert await acct.month_stops(con, D0 + 2 * DAY) == 2
        await _paper_day(con, (("XUSD", 1, 0.10, 1.0),), D0 + 2 * DAY)
        await acct.step(con, D0 + 2 * DAY + 120)
        rows = await con.fetch("SELECT status, skip_reason FROM fade_live_positions WHERE day = $1",
                               __import__("datetime").datetime.fromtimestamp(D0 + 2 * DAY, __import__("datetime").timezone.utc))
        assert [(r["status"], r["skip_reason"][:16]) for r in rows] == [("SKIPPED", "2 full stops thi")]
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
        assert seen >= 1 and rows["XUSD"]["status"] == "OPEN" and rows["XUSD"]["extra_margin"] == round(S.EXTRA_MARGIN * 3184, 2)
        assert rows["YUSD"]["status"] == "SKIPPED"                      # the other pick cancelled at once too
        await acct.fast(con, D0 + 128)                                  # and again, and the next full pass:
        await acct.step(con, D0 + 150)
        assert fake.margin_added == [(1, round(S.EXTRA_MARGIN * 3184, 2))]       # ... the margin was added exactly once
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


def test_the_activation_scales_with_the_balance():
    e = 1.0
    full = S.activation_price(e, 6_200.0, 3184.0), S.lock_price(e, 6_200.0, 3184.0)
    half = S.activation_price(e, 3_100.0, 1592.0), S.lock_price(e, 3_100.0, 1592.0)
    assert full == pytest.approx(half)                                   # the same price fall at half the balance
    assert abs(3_100.0 * (e - half[0]) / e - 3_100.0 * S.COST - S.BREAKEVEN_AFTER_USD / 2) < 1e-6   # $15.31 at $1,592


@requires_pg
@pytest.mark.asyncio
async def test_the_trail_follows_the_price_down_after_the_lock_and_never_up():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    lows = {"low": 1.05 * 0.989}                                        # a 1.1% fall: the stop goes to break-even
    acct = _acct(fake, candles=lambda sym, a, b: [{"time": a + 60, "low": lows["low"], "close": lows["low"]}])
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        fake.book["XUSD"] = (1.035, 1.036, 1.035)
        await acct.step(con, D0 + 600)                                  # the break-even move
        x = (await _rows(con))["XUSD"]
        lock = x["lock_price"]
        assert lock is not None and x["trail_price"] is None
        await acct.step(con, D0 + 900)                                  # low 1.1%: 5% above it is looser than break-even
        assert (await _rows(con))["XUSD"]["trail_price"] is None
        lows["low"] = 1.05 * 0.94                                       # a 6% fall: 5% above it is tighter
        fake.book["XUSD"] = (0.99, 0.991, 0.99)
        await acct.step(con, D0 + 1200)
        x = (await _rows(con))["XUSD"]
        assert x["trail_price"] is not None and x["trail_price"] < lock
        assert abs(x["trail_price"] - 1.05 * 0.94 * 1.05) < 0.0002
        assert float(fake.edits[-1][2]) == x["trail_price"]
        n = len(fake.edits)
        lows["low"] = 1.05 * 0.94                                       # no new low: the stop does not move (never up)
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
        await acct.step(con, D0 + 600)                                  # the break-even move
        lows["low"] = 1.05 * 0.93                                       # fell 7% ...
        fake.book["XUSD"] = (1.03, 1.031, 1.03)                         # ... and is back up past the trailing level (1.0253)
        await acct.step(con, D0 + 900)
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["exit_reason"]) == ("CLOSED", "LOCK") and fake.pos[1] == 0
        assert (await _state(con))["status"] == "RUNNING"
    finally:
        await con.close()


# ============================================================ hardening after the code review (2026-11-15)

@requires_pg
@pytest.mark.asyncio
async def test_both_picks_filling_before_the_cancel_lands_keeps_one_and_buys_the_other_back():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0), ("YUSD", 2, 0.12, 2.0)))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))                                # both spike inside the same few seconds
        fake.fill_resting(_cid(fake, 2))
        await acct.step(con, D0 + 600)
        rows = await _rows(con)
        assert rows["XUSD"]["status"] == "OPEN"                         # the better-ranked fill is the trade
        assert (rows["YUSD"]["status"], rows["YUSD"]["exit_reason"]) == ("CLOSED", "DOUBLE_FILL")
        assert fake.pos[2] == 0 and fake.pos[1] < 0                     # one position at a time, at Delta too
        assert [e["kind"] for e in await _events(con, "DOUBLE_FILL")] == ["DOUBLE_FILL"]
        assert fake.margin_added == [(1, round(S.EXTRA_MARGIN * 3184, 2))]       # the trade still got its extra margin
        assert (await _state(con))["status"] == "RUNNING"               # planned: no halt
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_failed_read_before_the_top_up_does_not_cost_the_extra_margin():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    real, calls = fake.get_positions, {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 2:                                             # 1 = run 3's margin check; 2 = the top-up's own read
            raise ConnectionError("venue down")
        return real()
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        fake.get_positions = flaky
        await acct.fast(con, D0 + 125)                                  # the read fails: nothing sent, nothing recorded
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "OPEN" and x["extra_margin"] is None and "extra_margin_tried" not in (x["detail"] or "")
        assert fake.margin_added == []
        await acct.fast(con, D0 + 128)                                  # the next look adds it
        assert fake.margin_added == [(1, round(S.EXTRA_MARGIN * 3184, 2))]
        assert (await _rows(con))["XUSD"]["extra_margin"] == round(S.EXTRA_MARGIN * 3184, 2)
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_the_watcher_waits_for_the_margin_check_before_adding_margin():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        fake.fail["get_positions"] = 1                                  # run 3's isolated-margin check cannot read
        await acct.fast(con, D0 + 125)
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "OPEN" and "venue_position" not in (x["detail"] or "")
        assert fake.margin_added == []                                  # an unchecked position is NOT topped up
        await acct.fast(con, D0 + 128)                                  # the next look completes the check FIRST, then tops up
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "OPEN" and "venue_position" in x["detail"] and fake.margin_added == [(1, round(S.EXTRA_MARGIN * 3184, 2))]
        assert (await _state(con))["status"] == "RUNNING"               # not bought back as "MARGIN", not halted
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_break_even_move_that_fails_is_not_recorded_and_is_retried():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    lows = {"low": 1.05}
    acct = _acct(fake, candles=lambda sym, a, b: [{"time": a + 60, "low": lows["low"], "close": lows["low"]}])
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        await acct.step(con, D0 + 300)
        lows["low"] = 1.05 * 0.99
        fake.book["XUSD"] = (1.04, 1.041, 1.04)
        real, state = acct.stop_order, {"fail": True}

        def flaky(pid):
            if state["fail"]:
                state["fail"] = False
                raise ConnectionError("venue down")
            return real(pid)
        acct.stop_order = flaky
        await acct.step(con, D0 + 600)
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "OPEN" and x["lock_price"] is None and not fake.edits       # failed: NOT marked as moved
        assert [e["kind"] for e in await _events(con) if e["kind"] in LOCK_EVENTS] == ["LOCK_FAILED"]
        await acct.step(con, D0 + 900)
        x = (await _rows(con))["XUSD"]
        assert x["lock_price"] is not None and float(fake.edits[-1][2]) == x["lock_price"]
        assert [e["kind"] for e in await _events(con) if e["kind"] in LOCK_EVENTS] == ["LOCK_FAILED", "LOCK_MOVED"]
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_no_last_price_never_triggers_a_blind_buy_back():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake, candles=lambda sym, a, b: [{"time": a + 60, "low": 1.05 * 0.99, "close": 1.04}])
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        acct.last_price = lambda sym, ticker=None: 0.0                  # Delta's ticker has no price on these passes
        await acct.step(con, D0 + 150)
        await acct.step(con, D0 + 600)
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "OPEN" and fake.pos[1] < 0                # still short: nothing bought back blind ...
        assert x["lock_price"] is not None and float(fake.edits[-1][2]) == x["lock_price"]   # ... but the stop IS moved
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_the_entry_is_settled_from_deltas_reply_not_from_a_lookup_that_can_miss():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0), ("YUSD", 2, 0.12, 2.0)))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    real = fake.get_order_by_client_id
    try:
        fake.get_order_by_client_id = lambda cid: None                  # read-after-write lag: "not found"
        await acct.step(con, D0 + 120)
        rows = await _rows(con)
        assert {s: r["status"] for s, r in rows.items()} == {"XUSD": "RESTING", "YUSD": "RESTING"}   # not FAILED
        assert all(r["order_id"] is not None for r in rows.values())
        fake.get_order_by_client_id = real
        await acct.step(con, D0 + DAY + 5)                              # so both are still cancelled at 24:00
        assert all(r["row"]["state"] == "cancelled" for r in fake.resting.values())
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_an_entry_whose_reply_was_lost_is_found_among_the_open_orders_and_cancelled_while_halted():
    import live.client as C
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    real = fake.place_order

    def lost(order):
        real(order)                                                     # Delta took the order ...
        raise C.AmbiguousWrite("timeout")                               # ... and the reply never came
    try:
        fake.place_order = lost
        fake.get_order_by_client_id = lambda cid: None                  # and the lookup by client id misses it
        await acct.step(con, D0 + 120)
        assert (await _state(con))["status"] == "HALTED"                # the owner must look (run 3's rule, kept)
        assert (await _rows(con))["XUSD"]["status"] == "SENDING"
        await acct.step(con, D0 + 150)                                  # the bot finds it among Delta's OPEN orders ...
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "SKIPPED" and "HALTED" in x["skip_reason"]   # ... and cancels it: nothing rests unwatched
        assert all(r["row"]["state"] == "cancelled" for r in fake.resting.values()) and not fake.pos
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_an_entry_that_never_reached_delta_is_written_off_only_after_delta_has_had_time_to_show_it():
    import live.client as C
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)

    def never_arrives(order):
        raise C.AmbiguousWrite("connection reset")                      # nothing reached Delta
    try:
        fake.place_order = never_arrives
        await acct.step(con, D0 + 120)
        await acct.step(con, D0 + 150)                                  # 30 s later: too soon to conclude
        assert (await _rows(con))["XUSD"]["status"] == "SENDING"
        await acct.step(con, D0 + 300)                                  # 3 min later: by id, open orders, position -- nothing
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "FAILED" and "not at Delta" in x["skip_reason"]
        assert (await _state(con))["status"] == "HALTED"                # still the owner's call to resume
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_cancel_delta_refuses_because_the_order_is_already_gone_is_not_a_failure():
    import live.client as C
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        row = fake.resting[_cid(fake, 1)]["row"]

        def gone(oid, pid):
            row["state"] = "cancelled"                                  # somebody cancelled it at Delta by hand
            raise C.VenueRejected(404, "/v2/orders", {"error": {"code": "open_order_not_found"}})
        fake.cancel_order = gone
        await acct.step(con, D0 + DAY + 5)
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "SKIPPED" and (await _state(con))["status"] == "RUNNING"
        assert not [e for e in await _events(con, "ERROR")]             # the pass did not fail
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_second_position_whose_buy_back_failed_is_closed_on_the_next_pass_and_never_topped_up():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0), ("YUSD", 2, 0.12, 2.0)))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    real, state = acct.buy_back, {"fail": True}

    def flaky(symbol, cid_base):
        if state["fail"]:
            state["fail"] = False
            raise ConnectionError("venue down")
        return real(symbol, cid_base)
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        fake.fill_resting(_cid(fake, 2))
        acct.buy_back = flaky
        await acct.step(con, D0 + 150)                                  # YUSD's double-fill buy-back fails once ...
        await acct.fast(con, D0 + 153)
        assert fake.margin_added == [(1, round(S.EXTRA_MARGIN * 3184, 2))]       # only the trade is topped up, never the surplus one
        await acct.step(con, D0 + 180)                                  # ... and the exits close it (same pass or the next)
        rows = await _rows(con)
        assert (rows["YUSD"]["status"], rows["YUSD"]["exit_reason"]) == ("CLOSED", "DOUBLE_FILL") and fake.pos[2] == 0
        assert rows["XUSD"]["status"] == "OPEN" and (await _state(con))["status"] == "RUNNING"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_extra_margin_only_after_the_isolated_margin_check_has_passed():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0, margin="cross")                   # NOT isolated: the fill must be bought back
    acct = _acct(fake)
    real, state = acct.buy_back, {"fail": True}

    def flaky(symbol, cid_base):
        if state["fail"]:
            state["fail"] = False
            raise ConnectionError("venue down")
        return real(symbol, cid_base)
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        acct.buy_back = flaky
        await acct.fast(con, D0 + 125)                                  # the check FAILS, and its buy-back hits a venue blip
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "OPEN" and "venue_position" in x["detail"] and "margin_ok" not in x["detail"]
        await acct.fast(con, D0 + 128)                                  # no top-up for a position that failed the gate:
        assert fake.margin_added == []                                  # the check runs again and buys it back
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["exit_reason"]) == ("CLOSED", "MARGIN") and (await _state(con))["status"] == "HALTED"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_partial_fill_is_read_again_after_the_rest_is_cancelled():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        cid = _cid(fake, 1)
        row = fake.resting[cid]["row"]
        third = row["size"] // 3
        fake.fill_resting(cid, n=third)
        real = fake.cancel_order

        def more_fills_then_cancel(oid, pid):
            if oid == row["id"] and row["unfilled_size"] == row["size"] - third:
                row["unfilled_size"] = row["size"] - 2 * third          # another third fills before the cancel lands
                fake.pos[1] -= third
            real(oid, pid)
        fake.cancel_order = more_fills_then_cancel
        await acct.step(con, D0 + 600)
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "OPEN" and x["contracts"] == 2 * third == -fake.pos[1]      # what Delta really holds
        assert (await _state(con))["status"] == "RUNNING"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_resting_sells_are_cancelled_when_the_account_halts_or_the_kill_switch_is_on(tmp_path):
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0), ("YUSD", 2, 0.12, 2.0)))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        await acct.set_state(con, "HALTED", "the owner must look at something")
        await acct.step(con, D0 + 150)
        rows = await _rows(con)
        assert {s: (r["status"], r["skip_reason"][:36]) for s, r in rows.items()} == {
            "XUSD": ("SKIPPED", "resting sell cancelled: the account "), "YUSD": ("SKIPPED", "resting sell cancelled: the account ")}
        assert all(r["row"]["state"] == "cancelled" for r in fake.resting.values()) and not fake.pos
    finally:
        await con.close()
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        switch = tmp_path / "kill"
        switch.write_text("stop")
        acct.kill_switch_path = str(switch)
        await acct.step(con, D0 + 150)
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "SKIPPED" and "kill switch" in x["skip_reason"]
        assert all(r["row"]["state"] == "cancelled" for r in fake.resting.values())
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_fill_that_lands_while_halted_is_still_recorded_and_protected():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        await acct.set_state(con, "HALTED", "the owner must look at something")
        fake.fill_resting(_cid(fake, 1))                                # it fills before the bot could cancel it
        await acct.step(con, D0 + 150)
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "OPEN" and fake.pos[1] < 0                # ours, with its stop and its exits
        assert fake.margin_added == [(1, round(S.EXTRA_MARGIN * 3184, 2))]
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_run_3s_stops_do_not_count_toward_this_runs_month_and_its_rows_are_left_alone():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        import datetime as dt
        ts = dt.datetime.fromtimestamp(D0 + 500, dt.timezone.utc)
        await con.execute(                                              # run 3's stop this month: no run number in detail
            "INSERT INTO fade_live_positions (day, symbol, rank, status, exit_reason, exit_time, entry_time, detail) "
            "VALUES ($1, 'OLDUSD', 1, 'CLOSED', 'STOP_LOSS', $1, $1, '{\"ask\": 1.0}')", ts)
        assert await acct.month_stops(con, D0 + 3600) == 0
        assert acct._ours({"detail": '{"run": 4}'}) and not acct._ours({"detail": '{"ask": 1.0}'}) and not acct._ours({"detail": None})
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_the_capital_command_works_on_this_run():
    from live import fade_live as L
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)                                  # run 4 begins; one sell rests
        rc, msg = await L.record_capital(con, acct, D0 + 200)
        assert rc == 1 and "rest" in msg                                # not while an entry rests
        await acct.step(con, D0 + DAY + 5)                              # cancelled at 24:00: flat
        rc, msg = await L.record_capital(con, acct, D0 + DAY + 60)
        assert rc == 0, msg                                             # run 4's own row is accepted
        old = L.LiveFade(fake, risk_pct=0.10, start_usd=3184)
        rc, msg = await L.record_capital(con, old, D0 + DAY + 90)
        assert rc == 1 and "no run of this version" in msg              # which is why the command must build SpikeLive
        import inspect
        assert L.account_class() is S.SpikeLive and "account_class()" in inspect.getsource(L.capital)
        assert "account_class()" in inspect.getsource(L.from_env)       # one factory: the bot and the command agree
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_fill_bought_back_as_unprotected_also_cancels_the_other_resting_sell():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0), ("YUSD", 2, 0.12, 2.0)))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        fake.pending = [o for o in fake.pending if o["product_id"] != 1]    # Delta attached no stop to the fill
        await acct.step(con, D0 + 150)
        rows = await _rows(con)
        assert (rows["XUSD"]["status"], rows["XUSD"]["exit_reason"]) == ("CLOSED", "UNPROTECTED")
        assert (await _state(con))["status"] == "HALTED"
        assert rows["YUSD"]["status"] == "SKIPPED"                      # not left to fill on a halted account
        assert fake.resting[_cid(fake, 2)]["row"]["state"] == "cancelled" and not any(fake.pos.values())
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_one_resting_row_that_cannot_be_settled_does_not_stop_the_open_positions_checks():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0), ("YUSD", 2, 0.12, 2.0)))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        y_id = fake.resting[_cid(fake, 2)]["row"]["id"]
        real = fake.cancel_order

        def stuck(oid, pid):
            if oid == y_id:
                raise ConnectionError("venue down")                     # YUSD's cancel keeps failing
            real(oid, pid)
        fake.cancel_order = stuck
        fake.fill_resting(_cid(fake, 1))
        await acct.step(con, D0 + 150)
        assert (await _rows(con))["XUSD"]["status"] == "OPEN" and fake.margin_added   # the trade is whole regardless
        fake.fire_stop("XUSD", 1.0760, at=D0 + 200)
        await acct.step(con, D0 + 240)                                  # YUSD still fails -- XUSD's close is still read
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["exit_reason"]) == ("CLOSED", "STOP_LOSS")
        fake.cancel_order = real
        await acct.step(con, D0 + 270)                                  # and YUSD is cancelled once Delta answers
        assert fake.resting[_cid(fake, 2)]["row"]["state"] == "cancelled"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_stop_that_fires_during_the_break_even_buy_back_stays_a_full_stop():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake, candles=lambda sym, a, b: [{"time": a + 60, "low": 1.05 * 0.99, "close": 1.08}])
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        await acct.fast(con, D0 + 125)

        def spike_through_the_stop(sym, ticker=None):                   # between the pass's reconcile and the move:
            if fake.pos.get(1):
                fake.fire_stop("XUSD", 1.0760, at=D0 + 200)             # the ORIGINAL stop fires at Delta
            return 1.08
        acct.last_price = spike_through_the_stop
        await acct.step(con, D0 + 240)
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["exit_reason"]) == ("CLOSED", "STOP_LOSS") and x["lock_price"] is None
        assert await acct.month_stops(con, D0 + 3600) == 1              # counted toward the month's two
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_resting_sell_the_lookup_cannot_find_is_still_cancelled_at_midnight_by_its_order_id():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        assert (await _rows(con))["XUSD"]["order_id"] is not None
        fake.get_order_by_client_id = lambda cid: None                  # Delta's lookup by client id finds nothing
        await acct.step(con, D0 + DAY + 5)
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "SKIPPED" and "24:00" in x["skip_reason"]
        assert all(r["row"]["state"] == "cancelled" for r in fake.resting.values())   # cancelled by the id from the send
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_run_3s_unconfirmed_entry_keeps_run_3s_rule():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        import datetime as dt
        await con.execute("INSERT INTO fade_live_positions (day, symbol, rank, status, product_id, entry_cid, detail) "
                          "VALUES ($1, 'OLDUSD', 1, 'SENDING', 2, 'old-ioc-entry', '{\"ask\": 1.0}')",
                          dt.datetime.fromtimestamp(D0 - DAY, dt.timezone.utc))
        await acct.step(con, D0 + 150)
        old = await con.fetchrow("SELECT status, skip_reason FROM fade_live_positions WHERE symbol = 'OLDUSD'")
        assert (old["status"], old["skip_reason"]) == ("FAILED", "the entry never filled at Delta")   # IOC: gone
        assert (await _state(con))["status"] == "RUNNING"               # no "may be resting" halt for an IOC order
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_stop_that_fires_before_the_bot_has_seen_the_fill_is_still_recorded_as_that_stop():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1), at=D0 + 200)                   # filled at 00:03:20 ...
        fake.fire_stop("XUSD", 1.0760, at=D0 + 201)                     # ... and stopped a second later
        await acct.step(con, D0 + 230)                                  # the bot's first look comes after both
        await acct.step(con, D0 + 260)
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["exit_reason"]) == ("CLOSED", "STOP_LOSS"), (x["status"], x["exit_reason"])
        assert int(x["entry_time"].timestamp()) == D0 + 200             # Delta's fill time, not the bot's look
        assert await acct.month_stops(con, D0 + 3600) == 1 and (await _state(con))["status"] in ("RUNNING", "HALTED")
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_cancel_refused_for_another_reason_is_a_failure_not_already_gone():
    import live.client as C
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)

        def unauthorised(oid, pid):
            raise C.VenueRejected(401, "/v2/orders", {"error": {"code": "expired_signature"}})
        fake.cancel_order = unauthorised
        await acct.step(con, D0 + DAY + 5)
        assert (await _rows(con))["XUSD"]["status"] == "RESTING"        # NOT written off: the sell is still live
        assert [e["kind"] for e in await _events(con, "ERROR")] == ["ERROR"]   # and the pass is counted as failed
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_resting_sell_delta_stops_showing_is_given_two_minutes_then_cancelled_by_its_id():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake)
    try:
        await acct.step(con, D0 + 120)
        fake.get_order_by_client_id = lambda cid: None                  # Delta's lists stop showing the order
        fake.get_open_orders = lambda pid=None: []
        await acct.step(con, D0 + 150)
        await acct.step(con, D0 + 180)
        assert (await _rows(con))["XUSD"]["status"] == "RESTING"        # no conclusion on a first (or a quick second) miss
        assert all(r["row"]["state"] == "open" for r in fake.resting.values())
        await acct.step(con, D0 + 300)                                  # 2 minutes on: cancelled by the id from the send
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "SKIPPED" and "cancelled by its id" in x["skip_reason"]
        assert all(r["row"]["state"] == "cancelled" for r in fake.resting.values())   # nothing left resting unwatched
    finally:
        await con.close()


# ------------------------------------------------------------------ owner 2026-11-16: no break-even step, a plain trail
def test_plain_trail_is_the_default_and_the_month_has_no_stop_limit():
    import importlib.util
    spec = importlib.util.spec_from_file_location("spike_live_as_shipped", S.__file__)   # a private copy: the fixture
    fresh = importlib.util.module_from_spec(spec)                                        # above patches the shared one
    spec.loader.exec_module(fresh)
    assert fresh.BREAKEVEN_LOCK is False and fresh.MONTH_STOP_LIMIT is None


@requires_pg
@pytest.mark.asyncio
async def test_plain_trail_no_break_even_move_and_the_stop_trails_from_the_fill():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    lows = {"low": 1.05 * 0.993}                                        # a 0.7% fall: exit C would go to break-even here
    acct = _acct(fake, candles=lambda sym, a, b: [{"time": a + 60, "low": lows["low"], "close": lows["low"]}])
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        fake.book["XUSD"] = (1.043, 1.044, 1.043)
        await acct.step(con, D0 + 600)
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "OPEN" and x["lock_price"] is None and x["trail_price"] is None and not fake.edits
        lows["low"] = 1.05 * 0.98                                       # a 2% fall: 5% above it is still looser than the stop
        fake.book["XUSD"] = (1.03, 1.031, 1.03)
        await acct.step(con, D0 + 900)
        assert (await _rows(con))["XUSD"]["trail_price"] is None and not fake.edits
        lows["low"] = 1.05 * 0.96                                       # a 4% fall: 1.0584 is under the 1.0736 stop
        fake.book["XUSD"] = (1.01, 1.011, 1.01)
        await acct.step(con, D0 + 1200)
        x = (await _rows(con))["XUSD"]
        assert x["lock_price"] is None and abs(x["trail_price"] - 1.05 * 0.96 * 1.05) < 0.0002
        assert x["trail_price"] < x["stop_price"] and float(fake.edits[-1][2]) == x["trail_price"]
        n = len(fake.edits)
        await acct.step(con, D0 + 1500)                                 # no new low: the stop does not move (never up)
        assert len(fake.edits) == n
        lows["low"] = 1.05 * 0.90
        fake.book["XUSD"] = (0.95, 0.951, 0.95)
        await acct.step(con, D0 + 1800)
        assert abs((await _rows(con))["XUSD"]["trail_price"] - 1.05 * 0.90 * 1.05) < 0.0002
        kinds = [e["kind"] for e in await _events(con)]
        assert [k for k in kinds if k.startswith("TRAIL")] == ["TRAIL_MOVED", "TRAIL_MOVED"]
        assert not [k for k in kinds if k in LOCK_EVENTS]
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_plain_trail_buys_back_at_market_when_the_price_is_already_back_above_the_level():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    lows = {"low": 1.05 * 0.93}                                         # fell 7%: the trailing level is 1.0253 ...
    acct = _acct(fake, candles=lambda sym, a, b: [{"time": a + 60, "low": lows["low"], "close": lows["low"]}])
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        fake.book["XUSD"] = (1.03, 1.031, 1.03)                         # ... and the price is already back above it
        await acct.step(con, D0 + 600)
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["exit_reason"]) == ("CLOSED", "LOCK") and fake.pos[1] == 0
        assert [e["kind"] for e in await _events(con) if e["kind"].startswith("TRAIL")] == ["TRAIL_AT_MARKET"]
        assert (await _state(con))["status"] == "RUNNING"
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_plain_trail_a_stop_after_the_trail_moved_is_not_a_full_stop_and_is_logged_as_trailed():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    lows = {"low": 1.05 * 0.96}
    acct = _acct(fake, candles=lambda sym, a, b: [{"time": a + 60, "low": lows["low"], "close": lows["low"]}])
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        fake.book["XUSD"] = (1.01, 1.011, 1.01)
        await acct.step(con, D0 + 600)                                  # the stop is moved to 1.0584
        trail = (await _rows(con))["XUSD"]["trail_price"]
        assert trail is not None
        fake.fire_stop("XUSD", 1.0600, at=D0 + 700)
        await acct.step(con, D0 + 800)
        await acct.step(con, D0 + 800 + 301)
        x = (await _rows(con))["XUSD"]
        assert (x["status"], x["exit_reason"]) == ("CLOSED", "STOP_LOSS")
        assert await acct.month_stops(con, D0 + 3600) == 0              # not the original stop: not a full stop
        fills = await _events(con, "STOP_FILL")
        assert len(fills) == 1 and '"trailed": true' in fills[0]["detail"] and '"locked": false' in fills[0]["detail"]
    finally:
        await con.close()



# ------------------------------------------------------------------ owner 2026-11-17: rule C ("approved on C")
def test_rule_c_is_what_ships():
    import importlib.util
    spec = importlib.util.spec_from_file_location("spike_live_rule_c", S.__file__)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    assert (fresh.MAX_LEVERAGE, fresh.STOP_ABOVE, fresh.TRAIL, fresh.TRAIL_ONLY_PAST_ENTRY) == (5, 0.10, 0.10, True)
    assert fresh.TRADE_MARGIN == 0.20 and fresh.BREAKEVEN_LOCK is False and fresh.MONTH_STOP_LIMIT is None


def test_rule_c_sizing_is_five_x_so_the_position_is_about_the_balance():
    n, lev, notional = S.spike_size(3184, 1.0, 1, 5.0, 50_000, 0.001, None)
    assert lev == 5 and 3_150 < notional < 3_180                       # 20% / (20% + 0.12% fee reserve) of $3,184
    assert notional * S.STOP_ABOVE / 3184 == pytest.approx(0.10, abs=0.002)   # a full stop ~10% of the balance
    liq = 1 + 1 / 5 - 0.025                                               # isolated, before the extra margin
    assert liq - 1 > S.STOP_ABOVE + 0.05                                  # liquidation well past the stop
    n, lev, _ = S.spike_size(3184, 1.0, 1, 5.0, 50_000, 0.001, None, max_lev=20)
    assert lev == 20                                                      # an explicit cap still wins


@requires_pg
@pytest.mark.asyncio
async def test_rule_c_stop_ten_percent_and_the_trail_only_once_past_the_entry():
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    lows = {"low": 1.05}
    acct = _acct(fake, candles=lambda sym, a, b: [{"time": a + 60, "low": lows["low"], "close": lows["low"]}])
    try:
        await acct.step(con, D0 + 120)
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "RESTING" and x["leverage"] == 5
        assert abs(x["limit_price"] - 1.05) < 1e-9 and abs(x["stop_price"] - 1.155) < 0.0002   # +10% from the limit
        fake.fill_resting(_cid(fake, 1))
        fake.book["XUSD"] = (1.0, 1.001, 1.0)
        lows["low"] = 1.05 * 0.95                     # a 5% fall: 10% above it is 1.097 -- tighter than the stop but a loss
        await acct.step(con, D0 + 600)
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "OPEN" and x["trail_price"] is None and not fake.edits
        lows["low"] = 1.05 * 0.90                     # a 10% fall: 10% above it is 1.0395, under the 1.05 entry
        fake.book["XUSD"] = (0.95, 0.951, 0.95)
        await acct.step(con, D0 + 900)
        x = (await _rows(con))["XUSD"]
        assert abs(x["trail_price"] - 1.05 * 0.90 * 1.10) < 0.0002 and x["trail_price"] <= x["entry_price"]
        assert float(fake.edits[-1][2]) == x["trail_price"]
        lows["low"] = 1.05 * 0.80
        fake.book["XUSD"] = (0.85, 0.851, 0.85)
        await acct.step(con, D0 + 1200)
        assert abs((await _rows(con))["XUSD"]["trail_price"] - 1.05 * 0.80 * 1.10) < 0.0002
        assert [e["kind"] for e in await _events(con) if e["kind"].startswith("TRAIL")] == ["TRAIL_MOVED", "TRAIL_MOVED"]
        assert not [e for e in await _events(con) if e["kind"] in LOCK_EVENTS]
    finally:
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_rule_c_a_spike_through_two_percent_does_not_stop_it():
    """Today's CROSS (2026-11-17): filled, then +2.4% above the fill within the minute. Under rule C still open."""
    con = await _db(picks=(("XUSD", 1, 0.10, 1.0),))
    fake = SpikeDelta(balance=3184.0)
    acct = _acct(fake, candles=lambda sym, a, b: [{"time": a + 60, "low": 1.05, "close": 1.075}])
    try:
        await acct.step(con, D0 + 120)
        fake.fill_resting(_cid(fake, 1))
        fake.book["XUSD"] = (1.075, 1.076, 1.075)
        await acct.step(con, D0 + 600)
        x = (await _rows(con))["XUSD"]
        assert x["status"] == "OPEN" and x["trail_price"] is None and x["stop_price"] > 1.075 * 1.07
    finally:
        await con.close()
