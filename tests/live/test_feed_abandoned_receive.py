"""Shutting the feed down must not log a false ERROR.

Seen on the prod dry run 2026-10-02 10:38Z: shutdown cancelled the feed while
`_recv` waited, the pending `ws.recv()` task was orphaned, it ended with
ConnectionClosedOK when the socket closed, and asyncio logged "Task exception
was never retrieved" at ERROR. The daily report counts ERROR lines, so every
restart read as a fault for 24 hours.
"""
from __future__ import annotations

import asyncio
import gc

from app.market_data.delta_ws import DeltaMarketFeed


class ClosingSocket:
    """recv() pends until the socket closes, then fails the way websockets does."""

    def __init__(self) -> None:
        self.closed = asyncio.Event()

    async def recv(self):
        await self.closed.wait()
        raise ConnectionError("sent 1000 (OK); then received 1000 (OK)")


def test_cancelling_the_feed_mid_receive_logs_nothing():
    reported = []

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _l, ctx: reported.append(ctx["message"]))
        feed = DeltaMarketFeed.__new__(DeltaMarketFeed)   # only _recv is exercised
        feed.recv_timeout = 60
        ws = ClosingSocket()
        stop_task = asyncio.ensure_future(asyncio.Event().wait())
        receiving = asyncio.ensure_future(feed._recv(ws, stop_task))
        await asyncio.sleep(0.01)
        receiving.cancel()                     # shutdown
        try:
            await receiving
        except asyncio.CancelledError:
            pass
        ws.closed.set()                        # then the socket closes
        await asyncio.sleep(0.01)
        stop_task.cancel()
        await asyncio.sleep(0)
        del receiving, ws
        gc.collect()
        await asyncio.sleep(0)

    asyncio.run(scenario())
    gc.collect()
    assert reported == [], f"asyncio logged: {reported}"
