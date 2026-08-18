"""H-MAKER-1 raw feed recorder. READ-ONLY. NO ORDERS ARE PLACED.

    PYTHONPATH=. python3 -m deltabt.research.record_hmaker1_feed --minutes 100

Records `l2_orderbook` and `all_trades` to gzipped JSONL, exactly as received.
Nothing is simulated here and nothing is analysed here: the paper-order
simulation runs OFFLINE against these files, so it can be re-run, tested and
audited without re-collecting a feed that cannot be replayed.

SAFETY
    This module imports the read-only websocket client and adds no send path
    beyond subscribe. There is no authentication, no signing, no credential and
    no order-placement call anywhere in this repository, and
    tests/live/test_no_live_trading.py enforces that against the shipped source.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
import time
from pathlib import Path

import websockets

from app.config.settings import WS_URL

SYMBOLS = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")
CHANNELS = ("l2_orderbook", "all_trades")

#: §4.1 -- the frozen depth kept per side. Deeper levels are irrelevant to an
#: order resting at the touch and would multiply the file size for nothing.
KEEP_LEVELS = 25


def trim(msg: dict) -> dict:
    """Keep the frozen schema. The full book is 2,700 levels a side."""
    if msg.get("type") == "l2_orderbook":
        return dict(
            type="l2", symbol=msg.get("symbol"),
            ts=msg.get("timestamp"), seq=msg.get("last_sequence_no"),
            updated=msg.get("last_updated_at"),
            spread=msg.get("spread"),
            buy=[[lv["limit_price"], lv["size"]] for lv in (msg.get("buy") or [])[:KEEP_LEVELS]],
            sell=[[lv["limit_price"], lv["size"]] for lv in (msg.get("sell") or [])[:KEEP_LEVELS]],
        )
    if msg.get("type") in ("all_trades", "all_trades_snapshot"):
        return dict(
            type="trades", symbol=msg.get("symbol"),
            snapshot=msg.get("type") == "all_trades_snapshot",
            trades=[[t["price"], t["size"], t.get("buyer_role"),
                     t.get("seller_role"), t["timestamp"]]
                    for t in (msg.get("trades") or [])] or
                   [[msg.get("price"), msg.get("size"), msg.get("buyer_role"),
                     msg.get("seller_role"), msg.get("timestamp")]],
        )
    return dict(type="other", raw=msg)


async def run(out_dir: Path, minutes: float) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    started = int(time.time())
    path = out_dir / f"feed_{started}.jsonl.gz"
    meta = out_dir / f"feed_{started}.meta.json"
    deadline = time.time() + minutes * 60
    counts = {"l2": 0, "trades": 0, "other": 0}
    reconnects, gaps = 0, []
    last_msg = time.time()

    fh = gzip.open(path, "wt")
    fh.write(json.dumps(dict(type="session", started=started, symbols=list(SYMBOLS),
                             channels=list(CHANNELS), keep_levels=KEEP_LEVELS,
                             ws_url=WS_URL)) + "\n")
    try:
        while time.time() < deadline:
            try:
                async with websockets.connect(WS_URL, open_timeout=15,
                                              max_size=None, ping_interval=20) as ws:
                    await ws.send(json.dumps({"type": "subscribe", "payload": {
                        "channels": [{"name": c, "symbols": list(SYMBOLS)}
                                     for c in CHANNELS]}}))
                    while time.time() < deadline:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        now = time.time()
                        if now - last_msg > 5.0:
                            gaps.append([last_msg, now])
                        last_msg = now
                        rec = trim(json.loads(raw))
                        rec["recv"] = now
                        counts[rec["type"] if rec["type"] in counts else "other"] += 1
                        fh.write(json.dumps(rec) + "\n")
                        if counts["l2"] % 500 == 0:
                            fh.flush()
            except asyncio.CancelledError:
                raise
            except Exception as exc:                      # noqa: BLE001
                reconnects += 1
                gaps.append([last_msg, time.time()])
                print(f"reconnect {reconnects}: {type(exc).__name__}: {exc}",
                      flush=True)
                await asyncio.sleep(min(2 ** min(reconnects, 5), 30))
    finally:
        fh.close()
        meta.write_text(json.dumps(dict(
            started=started, ended=int(time.time()), minutes=minutes,
            symbols=list(SYMBOLS), counts=counts, reconnects=reconnects,
            gaps_over_5s=gaps, file=path.name), indent=2) + "\n")
        print(f"done: {counts} reconnects={reconnects} gaps>5s={len(gaps)} -> {path}",
              flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=100.0)
    ap.add_argument("--out", default="out/hmaker1/feed")
    a = ap.parse_args(argv)
    if a.minutes <= 0:
        raise SystemExit("--minutes must be positive")
    return asyncio.run(run(Path(a.out), a.minutes))


if __name__ == "__main__":
    sys.exit(main())
