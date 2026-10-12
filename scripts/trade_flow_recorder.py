"""Record Delta's public trade prints, with the taker side, for the screen coins (idea 5, 2026-10-13).

    PYTHONPATH=. .venv/bin/python scripts/trade_flow_recorder.py [OUT_DIR]

WHY. Trade flow (who is buying vs selling) is the one information source no
entry test has had: Delta serves no trade history (the REST endpoint returns
the last 50 prints), and the bot's all_trades feed drops the side and saves
nothing. Nothing can be tested until a few months of this exist.

WHAT. Subscribes to the public all_trades channel for the 32 symbol-screen
coins (the same reconnect-forever client the bot uses, app/market_data/delta_ws.py),
and appends every print as a row -- ts_us, symbol, price, size, buyer_role,
seller_role -- to OUT_DIR/<UTC date>.csv.gz, flushed every 60 s as a new gzip
member (pandas reads multi-member gzip). A heartbeat with counts goes to
OUT_DIR/recorder.log each minute. Public endpoint, no keys, nothing sent.

WHERE IT RUNS IS THE WEAK POINT: in a container it dies with the container.
"""
import asyncio, csv, datetime as dt, glob, gzip, io, logging, pathlib, signal, sys, time
sys.path.insert(0, ".")
from app.market_data.delta_ws import DeltaWS

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "data/trade_flow"); OUT.mkdir(parents=True, exist_ok=True)
SYMS = sorted(pathlib.Path(f).stem for f in glob.glob("out/sweep/five_min_arm_lab/symbol_screen/trades/*.csv"))
logging.basicConfig(filename=OUT / "recorder.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("recorder")
buf, counts, unknown = [], {"rows": 0, "snapshots": 0, "flushes": 0}, set()


def on_message(msg):
    kind = msg.get("type")
    if kind == "all_trades":
        rows = [msg]
    elif kind == "all_trades_snapshot":
        rows = msg.get("trades") or []; counts["snapshots"] += 1
        for r in rows:
            r.setdefault("symbol", msg.get("symbol"))
    else:
        if kind not in unknown and kind not in ("subscriptions", "subscribed"):
            unknown.add(kind); log.info("ignoring message type %r", kind)
        return
    for r in rows:
        if "price" in r and "symbol" in r:
            buf.append((int(r.get("timestamp") or 0), r["symbol"], r["price"], r.get("size"), r.get("buyer_role"), r.get("seller_role")))
    counts["rows"] += len(rows)


def flush():
    if not buf:
        return
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    s = io.StringIO(); w = csv.writer(s); w.writerows(buf); buf.clear()
    with gzip.open(OUT / f"{day}.csv.gz", "ab") as f:
        f.write(s.getvalue().encode())
    counts["flushes"] += 1


async def main():
    ws = DeltaWS(SYMS, on_message, channels=("all_trades",))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, ws.stop)
    task = asyncio.create_task(ws.run()); last = time.time()
    while not task.done():
        await asyncio.sleep(5)
        if time.time() - last >= 60:
            flush(); last = time.time()
            log.info("heartbeat rows=%d snapshots=%d flushes=%d ws_messages=%d silence=%.0fs", counts["rows"], counts["snapshots"],
                     counts["flushes"], ws.stats.messages, time.time() - (ws.stats.last_message_at or time.time()))
    flush(); await task


if __name__ == "__main__":
    log.info("starting: %d symbols -> %s", len(SYMS), OUT)
    asyncio.run(main())
