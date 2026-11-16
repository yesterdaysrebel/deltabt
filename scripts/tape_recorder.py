"""Records what the price bars do not show, so the owner's hand trades can be compared with it later
(2026-11-16, owner "Try it"): every trade print with its aggressor side, and the order book every 10 seconds, for the
coins he is likely to trade -- the day's biggest movers and the most traded perps. Delta keeps no history of either,
so this has to run WHILE he trades. PUBLIC DATA ONLY: no key, no account, no orders.

  trades:  websocket `all_trades` (format in app/market_data/trade_flow.py) -> out/manual/tape/trades_YYYYMMDD.csv
           columns: ts_us, symbol, price, size (contracts), aggressor (B = taker bought, S = taker sold, ? = unknown)
  book:    REST /v2/l2orderbook/{symbol}?depth=20 every 10 s            -> out/manual/tape/book_YYYYMMDD.csv
           columns: ts, symbol, bid, ask, bid_size, ask_size, and contracts resting within 0.2 / 0.5 / 1.0% of the mid
           on each side
  symbols: re-chosen every 15 minutes: the 12 crypto perps with the largest 24 h move (turnover >= $100k) plus the
           8 with the highest turnover -> out/manual/tape/symbols.log

    PYTHONPATH=. python scripts/tape_recorder.py           # runs until stopped; safe to restart (files are appended)
"""
import asyncio
import json
import time
from pathlib import Path

import requests
import websockets

OUT = Path("out/manual/tape")
API = "https://api.india.delta.exchange/v2"
WS = "wss://socket.india.delta.exchange"
RESELECT, BOOK_EVERY, SILENCE = 900, 10, 90


def choose():
    t = requests.get(f"{API}/tickers", params={"contract_types": "perpetual_futures"}, timeout=30).json()["result"]
    c = [x for x in t if x.get("top_tag") == "crypto" and x["symbol"].endswith("USD") and float(x.get("turnover_usd") or 0) >= 100_000]
    movers = sorted(c, key=lambda x: -abs(float(x.get("ltp_change_24h") or 0)))[:12]
    liquid = sorted(c, key=lambda x: -float(x.get("turnover_usd") or 0))[:8]
    return sorted({x["symbol"] for x in movers + liquid})


def day():
    return time.strftime("%Y%m%d", time.gmtime())


def append(name, lines):
    if lines:
        with open(OUT / f"{name}_{day()}.csv", "a") as f:
            f.write("".join(lines))


async def trades(symbols, stop):
    """One websocket session for a fixed symbol list; returns when `stop` is set or the feed goes silent."""
    async with websockets.connect(WS, ping_interval=20, max_size=2 ** 22) as ws:
        await ws.send(json.dumps({"type": "subscribe", "payload": {"channels": [{"name": "all_trades", "symbols": symbols}]}}))
        seen = {}
        while not stop.is_set():
            msg = json.loads(await asyncio.wait_for(ws.recv(), SILENCE))
            kind = msg.get("type")
            rows = [msg] if kind == "all_trades" else (list(reversed(msg.get("trades") or [])) if kind == "all_trades_snapshot" else [])
            out, sym = [], msg.get("symbol")
            for r in rows:
                try:
                    ts = int(r["timestamp"])
                    if ts <= seen.get(sym, 0) and kind == "all_trades_snapshot":
                        continue                                       # a snapshot after a reconnect repeats old prints
                    seen[sym] = max(seen.get(sym, 0), ts)
                    side = "B" if r.get("buyer_role") == "taker" else ("S" if r.get("seller_role") == "taker" else "?")
                    out.append(f"{ts},{sym},{r['price']},{r['size']},{side}\n")
                except (KeyError, TypeError, ValueError):
                    pass
            append("trades", out)


async def book(symbols, stop):
    while not stop.is_set():
        t0 = time.time()
        for sym in symbols:
            try:
                b = (await asyncio.to_thread(requests.get, f"{API}/l2orderbook/{sym}", params={"depth": 20}, timeout=10)).json().get("result") or {}
                buy = [(float(x["price"]), float(x["size"])) for x in b.get("buy") or []]
                sell = [(float(x["price"]), float(x["size"])) for x in b.get("sell") or []]
                if not buy or not sell:
                    continue
                bid, ask = max(buy), min(sell)
                mid = (bid[0] + ask[0]) / 2
                depth = [f"{sum(s for p, s in buy if p >= mid * (1 - w)):.0f},{sum(s for p, s in sell if p <= mid * (1 + w)):.0f}" for w in (0.002, 0.005, 0.01)]
                append("book", [f"{time.time():.1f},{sym},{bid[0]},{ask[0]},{bid[1]:.0f},{ask[1]:.0f},{','.join(depth)}\n"])
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.15)
        await asyncio.sleep(max(0.0, BOOK_EVERY - (time.time() - t0)))


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            symbols = await asyncio.to_thread(choose)
        except Exception as exc:  # noqa: BLE001
            print("choose failed", repr(exc)[:200], flush=True)
            await asyncio.sleep(30)
            continue
        with open(OUT / "symbols.log", "a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {' '.join(symbols)}\n")
        stop = asyncio.Event()
        tasks = [asyncio.create_task(trades(symbols, stop)), asyncio.create_task(book(symbols, stop))]
        done, _ = await asyncio.wait(tasks, timeout=RESELECT, return_when=asyncio.FIRST_COMPLETED)
        stop.set()
        for t in tasks:
            t.cancel()
        res = await asyncio.gather(*tasks, return_exceptions=True)
        for r in res:
            if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                print(time.strftime("%H:%M:%S"), "restart after", repr(r)[:200], flush=True)
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
