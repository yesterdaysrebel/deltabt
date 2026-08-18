"""H-MAKER-1 execution simulator guards.

The verdict rests on three things being right: that a touch is never counted as
a fill, that the two queue bounds really bound, and that the adverse-selection
sign convention matches the one the kill threshold is stated in. Everything
here exists to protect one of those.
"""

from __future__ import annotations

import numpy as np
import pytest

from deltabt.research import hmaker1 as h


def book(sym="BTCUSD", *, t0=1000.0, n=200, bid=100.0, ask=100.1,
         level_size=10.0, step=1.0):
    b = h.Book()
    for i in range(n):
        b.ts.append(t0 + i * step)
        b.bid.append(bid)
        b.ask.append(ask)
        b.levels.append({bid: level_size, ask: level_size})
    return b


def feed(trades=(), sym="BTCUSD", **kw):
    f = h.Feed()
    for s in h.SYMBOLS:
        f.books[s] = book(s, **kw) if s == sym else h.Book()
        f.trades[s] = []
    f.trades[sym] = sorted(trades)
    return f


def order(sym="BTCUSD", side=1, px=100.0, ahead=10.0, t=1000.0, life=60.0):
    return h.Order(symbol=sym, seq=0, side=side, submit_ts=t, limit_px=px,
                   size_ahead0=ahead, best_bid=100.0, best_ask=100.1,
                   expiry_ts=t + life)


# ------------------------------------------------------------- aggressor side

def test_aggressor_comes_from_venue_labels_not_an_uptick_rule():
    assert h._aggressor("maker", "taker") == -1     # seller crossed: hit the bid
    assert h._aggressor("taker", "maker") == +1     # buyer crossed: lifted offer
    assert h._aggressor("maker", "maker") == 0


# ------------------------------------------------------------- touch vs fill

def test_a_touch_is_not_a_fill():
    """The whole feasibility case turned on this distinction."""
    f = feed([(1005.0, 100.0, 3.0, -1)])
    r = h.simulate(order(ahead=10.0), f, "conservative")
    assert r["touched"] is True
    assert r["filled"] is False          # only 3 of 10 ahead consumed


def test_fill_requires_the_queue_to_clear_and_a_trade_to_occur():
    f = feed([(1005.0, 100.0, 12.0, -1)])
    r = h.simulate(order(ahead=10.0), f, "conservative")
    assert r["filled"] is True
    assert r["fill_ts"] == 1005.0
    assert r["fill_qty"] == pytest.approx(1.0)
    assert r["time_to_fill"] == pytest.approx(5.0)


def test_queue_must_be_strictly_exceeded_not_merely_matched():
    """Exactly clearing the queue puts us at the front, it does not fill us."""
    f = feed([(1005.0, 100.0, 10.0, -1)])
    assert h.simulate(order(ahead=10.0), f, "conservative")["filled"] is False


def test_same_side_aggressor_does_not_fill_a_resting_order():
    """A resting BUY is filled by an aggressive SELL, never by another buyer."""
    f = feed([(1005.0, 100.0, 50.0, +1)])
    r = h.simulate(order(side=1, ahead=1.0), f, "conservative")
    assert r["filled"] is False and r["touched"] is False


def test_trades_through_the_price_count_for_a_resting_buy():
    f = feed([(1005.0, 99.5, 12.0, -1)])
    assert h.simulate(order(side=1, px=100.0, ahead=10.0), f,
                      "conservative")["filled"] is True


def test_trades_above_our_bid_do_not_count():
    f = feed([(1005.0, 100.05, 99.0, -1)])
    assert h.simulate(order(side=1, px=100.0, ahead=1.0), f,
                      "conservative")["filled"] is False


def test_sell_side_mirrors_exactly():
    f = feed([(1005.0, 100.1, 12.0, +1)])
    r = h.simulate(order(side=-1, px=100.1, ahead=10.0), f, "conservative")
    assert r["filled"] is True


# ------------------------------------------------------------- the two bounds

def test_optimistic_never_fills_less_than_conservative():
    """If the bounds ever cross, they are not bounds."""
    rng = np.random.default_rng(0)
    for seed in range(30):
        rng = np.random.default_rng(seed)
        tr = [(1000.0 + float(rng.uniform(1, 55)), 100.0,
               float(rng.uniform(0.5, 6)), -1) for _ in range(8)]
        f = feed(sorted(tr))
        # shrink the level over time so cancellations exist to be inferred
        for i, lv in enumerate(f.books["BTCUSD"].levels):
            lv[100.0] = max(0.0, 20.0 - 0.1 * i)
        o = order(ahead=20.0)
        c = h.simulate(o, f, "conservative")
        p = h.simulate(o, f, "optimistic")
        assert p["queue_ahead"] <= c["queue_ahead"] + 1e-9
        assert not (c["filled"] and not p["filled"])


def test_optimistic_credits_cancellations_conservative_does_not():
    f = feed([(1005.0, 100.0, 3.0, -1)])
    for i, lv in enumerate(f.books["BTCUSD"].levels):
        lv[100.0] = 10.0 if i < 10 else 2.0        # 5 cancelled beyond the trade
    o = order(ahead=10.0)
    assert h.simulate(o, f, "conservative")["filled"] is False
    assert h.simulate(o, f, "optimistic")["queue_ahead"] < 10.0


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError, match="mode must be one of"):
        h.simulate(order(), feed(), "midpoint")


# ------------------------------------------------------------- markout signs

def test_a_passive_buy_followed_by_a_decline_is_adverse():
    f = feed(bid=100.0, ask=100.1, n=2000, step=1.0)
    b = f.books["BTCUSD"]
    for i, ts in enumerate(b.ts):
        if ts >= 1060.0:                            # price falls after the fill
            b.bid[i], b.ask[i] = 99.0, 99.1
    m = h.markouts(order(side=1), f, fill_ts=1000.0)
    assert m["adverse_1m"] > 0
    assert m["signed_markout_1m"] < 0


def test_a_passive_sell_followed_by_a_rise_is_adverse():
    f = feed(bid=100.0, ask=100.1, n=2000, step=1.0)
    b = f.books["BTCUSD"]
    for i, ts in enumerate(b.ts):
        if ts >= 1060.0:
            b.bid[i], b.ask[i] = 101.0, 101.1
    m = h.markouts(order(side=-1), f, fill_ts=1000.0)
    assert m["adverse_1m"] > 0


def test_a_favourable_fill_gives_negative_adverse_selection():
    f = feed(bid=100.0, ask=100.1, n=2000, step=1.0)
    b = f.books["BTCUSD"]
    for i, ts in enumerate(b.ts):
        if ts >= 1060.0:
            b.bid[i], b.ask[i] = 101.0, 101.1
    assert h.markouts(order(side=1), f, fill_ts=1000.0)["adverse_1m"] < 0


def test_markout_is_located_by_timestamp_and_returns_nan_past_the_feed():
    f = feed(n=30, step=1.0)
    m = h.markouts(order(), f, fill_ts=1000.0)
    assert np.isnan(m["adverse_15m"])               # feed ends long before +15m


def test_mid_at_never_reaches_backwards():
    b = book(n=10, step=1.0)
    assert np.isnan(h.mid_at(b, 1000.0 + 100))
    assert h.mid_at(b, 1003.4) == pytest.approx(100.05)


# ------------------------------------------------------ submission is signal-free

def test_side_alternates_by_sequence_and_ignores_the_market():
    f = feed(n=600, step=1.0)
    os_ = [o for o in h.generate_orders(f) if o.symbol == "BTCUSD"]
    assert len(os_) > 10
    assert [o.side for o in os_[:6]] == [1, -1, 1, -1, 1, -1]
    assert all(o.side == (1 if o.seq % 2 == 0 else -1) for o in os_)


def test_a_buy_rests_at_the_bid_and_a_sell_at_the_ask():
    f = feed(n=600, step=1.0)
    for o in h.generate_orders(f):
        assert o.limit_px == (o.best_bid if o.side == 1 else o.best_ask)


def test_generate_orders_rejects_a_nonpositive_cadence():
    with pytest.raises(ValueError, match="must be positive"):
        h.generate_orders(feed(), every_s=0)


# ------------------------------------------------------------- missing data

def test_a_feed_gap_voids_orders_live_at_the_time():
    f = feed(n=600, step=1.0)
    f.gaps = [(1010.0, 1030.0)]
    assert h.voided_by_gap(order(t=1000.0, life=60.0), f) is True
    assert h.voided_by_gap(order(t=1100.0, life=60.0), f) is False


def test_voided_orders_are_dropped_not_repaired():
    f = feed([(1005.0, 100.0, 99.0, -1)], n=600, step=1.0)
    f.gaps = [(1000.0, 1050.0)]
    rows = h.run_all(f, [order(t=1000.0)])
    assert rows["conservative"] == [] and rows["optimistic"] == []


# ------------------------------------------------------------- inference

def test_cluster_is_symbol_and_five_minute_bucket():
    c = h.cluster_ids(["BTCUSD", "BTCUSD", "BTCUSD", "ETHUSD"],
                      [0.0, 60.0, 400.0, 0.0])
    assert c[0] == c[1]          # same symbol, same 5-min bucket
    assert c[0] != c[2]          # later bucket
    assert c[0] != c[3]          # different symbol


def test_estimate_reads_the_cluster_se_not_the_block_default():
    from deltabt.research.hnull1 import cluster_se
    rng = np.random.default_rng(1)
    v = rng.normal(0, 5, 400)
    sym = ["BTCUSD"] * 400
    ts = np.arange(400) * 30.0
    got = h.estimate(v, sym, ts)
    assert got["se"] == pytest.approx(cluster_se(v, h.cluster_ids(sym, ts)))
    assert got["mde"] == pytest.approx(2.8 * got["se"])


def test_estimate_survives_an_empty_sample():
    r = h.estimate([], [], [])
    assert r["n"] == 0 and np.isnan(r["mean"])


def test_nan_markouts_are_excluded_not_zero_filled():
    v = [1.0, np.nan, 3.0]
    r = h.estimate(v, ["BTCUSD"] * 3, [0.0, 30.0, 60.0])
    assert r["n"] == 2 and r["mean"] == pytest.approx(2.0)


# ------------------------------------------------------------- the frozen gate

def test_the_kill_threshold_is_the_frozen_number():
    assert h.KILL_THRESHOLD_BPS == 5.54


def test_verdict_rules_are_the_pre_declared_ones():
    assert h.verdict(0.1, 5.53) == "PASS"
    assert h.verdict(5.54, 9.0) == "FAIL"
    assert h.verdict(4.0, 7.0) == "INCONCLUSIVE"     # straddles
    assert h.verdict(np.nan, np.nan) == "INCONCLUSIVE"


def test_an_undersized_sample_cannot_pass():
    assert h.verdict(0.1, 1.0, sample_ok=False) == "INCONCLUSIVE"


def test_disagreeing_bounds_cannot_pass():
    assert h.verdict(0.1, 1.0, bounds_agree=False) == "INCONCLUSIVE"
