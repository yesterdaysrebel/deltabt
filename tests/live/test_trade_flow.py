"""Order flow from Delta's public all_trades feed (app/market_data/trade_flow.py)."""
from app.market_data.trade_flow import TradeFlow

T0 = 1_792_090_000


def trade(ts, price, size, buyer="taker", sym="XRPUSD"):
    return {"type": "all_trades", "symbol": sym, "price": str(price), "size": size,
            "buyer_role": buyer, "seller_role": "maker" if buyer == "taker" else "taker",
            "timestamp": ts * 1_000_000}


def test_buys_sells_and_imbalance_per_window():
    f = TradeFlow({"XRPUSD": 1.0})
    f.on_message(trade(T0 - 600, 1.0, 100))                  # buy $100, 10 min ago
    f.on_message(trade(T0 - 120, 1.0, 300, buyer="maker"))   # sell $300, 2 min ago
    f.on_message(trade(T0 - 30, 1.0, 50))                    # buy $50, 30 s ago
    s = f.snapshot("XRPUSD", T0, side=1)
    assert (s["buy_usd_1m"], s["sell_usd_1m"], s["trades_1m"]) == (50, 0, 1)
    assert (s["buy_usd_5m"], s["sell_usd_5m"]) == (50, 300)
    assert (s["buy_usd_15m"], s["sell_usd_15m"], s["trades_15m"]) == (150, 300, 3)
    assert s["imbalance_15m"] == (150 - 300) / 450
    assert f.snapshot("XRPUSD", T0, side=-1)["flow_with_trade_15m"] == -s["imbalance_15m"]
    assert s["largest_trade_usd_15m"] == 300


def test_contract_value_converts_contracts_to_usd():
    f = TradeFlow({"BTCUSD": 0.001})
    f.on_message(trade(T0 - 10, 82300.0, 17, sym="BTCUSD"))
    assert round(f.snapshot("BTCUSD", T0, 1)["buy_usd_1m"], 2) == round(17 * 0.001 * 82300.0, 2)


def test_snapshot_message_is_ingested_oldest_first_and_not_double_counted():
    f = TradeFlow()
    snap = {"type": "all_trades_snapshot", "symbol": "XRPUSD",
            "trades": [trade(T0 - 5, 1.0, 2), trade(T0 - 50, 1.0, 1)]}    # newest first
    f.on_message(snap)
    f.on_message(trade(T0 - 2, 1.0, 4))
    f.on_message(snap)                    # a reconnect re-sends older prints
    s = f.snapshot("XRPUSD", T0, 1)
    assert s["trades_1m"] == 3 and s["buy_usd_1m"] == 7


def test_old_prints_are_forgotten_and_garbage_is_dropped():
    f = TradeFlow()
    f.on_message(trade(T0 - 2000, 1.0, 1))
    f.on_message(trade(T0, 1.0, 1))
    assert f.snapshot("XRPUSD", T0, 1)["prints_seen"] == 1
    for bad in ({"type": "all_trades"}, {"type": "all_trades", "symbol": "X", "price": "x",
                                         "size": 1, "timestamp": 1}, None):
        f.on_message(bad) if bad is not None else None
    assert f.dropped >= 2
    assert f.snapshot("NOPE", T0, 1)["trades_15m"] == 0 and f.snapshot("NOPE", T0, 1)["imbalance_5m"] is None
