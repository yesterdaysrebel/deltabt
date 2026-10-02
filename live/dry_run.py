"""DRY RUN: the live entry checks on the real venue, with no order ever sent.

WHY (2026-10-02, prod pilot, owner's choice: dry run first, real money later).
It answers what the paper bot cannot -- how the live code behaves against
Delta PROD's real books, products and balance -- without spending anything.
It is meant to run on a READ-ONLY venue key, so a defect here cannot trade:
this module never calls place_order, set_order_leverage or cancel_order, and
tests/live_exec/test_dry_run.py asserts that against a venue that fails the
test on any write.

WHAT IT DOES FOR EVERY APPROVED ENTRY, in the live broker's own order:
  1. the kill switch (refuses, exactly as live);
  2. the book guard on the REAL prod ticker -- entry-side deviation, spread,
     mark beyond the stop (LiveBroker._refuse_if_book_dislocated);
  3. the leverage the live broker would choose, read-only
     (LiveBroker._plan_leverage), against the configured equity -- the
     account behind a read-only key may hold nothing;
  4. records the order it WOULD have sent (DRY_RUN_ORDER), or why it would
     not (DRY_RUN_REFUSED), with the book it saw;
  5. hands the entry to the PAPER broker, which fills it from prod ticks and
     runs its exits, so positions, gates and the shadow-exit record behave as
     they would.

WHAT IT IS NOT. Fills are the paper model's, not the venue's; whether Delta
attaches a bracket, honours a trail, or fills a stop where the model says
cannot be learned without orders. The experiment registers the PAPER
execution profile, because that is what simulates the positions.
"""
from __future__ import annotations

import logging

from app.runtime.bot import TradingBot
from live.broker import OpeningRefused
from live.client import VenueError
from live.guards import kill_switch_engaged
from live.orders import STOP_LIMIT_CAP_R

log = logging.getLogger(__name__)


class DryRunBroker:
    """The paper broker, with the live broker's venue reads in front of entries.

    Everything except submit_order is the paper broker's, delegated, so the
    bot reads the paper execution surface (EXECUTION_FIELDS) and the paper
    fill model decides positions.
    """

    def __init__(self, paper, live_reader, *, equity: float,
                 kill_switch_path: str) -> None:
        self._paper = paper
        self._live = live_reader
        self._equity = float(equity)
        self._kill_switch_path = kill_switch_path
        self._records: list[dict] = []

    def __getattr__(self, name):
        return getattr(self._paper, name)

    def drain_dry_run_records(self) -> list[dict]:
        out, self._records = self._records, []
        return out

    def submit_order(self, intent, *, now: int | None = None,
                     order_uid: str | None = None):
        rec = {
            "order_uid": order_uid, "symbol": intent.symbol, "side": intent.side,
            "quantity": int(intent.quantity), "order_type": intent.order_type,
            "entry_reference": getattr(intent, "entry_reference", None),
            "stop_price": float(intent.stop_price),
            "target_price": float(intent.target_price),
            "risk_per_unit": float(intent.risk_per_unit),
        }
        try:
            if kill_switch_engaged(self._kill_switch_path):
                raise OpeningRefused(f"{intent.symbol}: kill switch engaged")
            book = self._live._refuse_if_book_dislocated(intent)
            plan = self._live._plan_leverage(intent, available=self._equity)
        except OpeningRefused as exc:
            self._records.append({**rec, "outcome": "DRY_RUN_REFUSED",
                                  "reason": str(exc)})
            raise
        side = intent.side
        stop, rpu = float(intent.stop_price), float(intent.risk_per_unit)
        venue_balance = None
        try:
            venue_balance = float(self._live.client.get_wallet_balance("USD")
                                  .get("balance") or 0)
        except VenueError as exc:                      # informational only
            log.warning("dry run: venue balance unreadable: %s", exc)
        self._records.append({
            **rec, "outcome": "DRY_RUN_ORDER",
            # What LiveBroker.submit_order would have sent: a market IOC with
            # the bracket attached and the 1.5R stop-limit cap.
            "would_send": {
                "side": "buy" if side > 0 else "sell", "size": int(intent.quantity),
                "order_type": "market_order", "time_in_force": "ioc",
                "bracket_stop_loss_price": stop,
                "bracket_stop_loss_limit_price": stop - side * float(STOP_LIMIT_CAP_R) * rpu,
                "bracket_take_profit_price": float(intent.target_price),
                "stop_trigger_method": "mark_price",
            },
            "book": book, "leverage": plan, "venue_usd_balance": venue_balance,
        })
        return self._paper.submit_order(intent, now=now, order_uid=order_uid)


class DryRunBot(TradingBot):
    """The paper bot, journaling what the dry-run broker would have sent."""

    async def drain_broker_events(self) -> None:
        drain = getattr(self.broker, "drain_dry_run_records", None)
        for rec in (drain() if drain else []):
            refused = rec["outcome"] == "DRY_RUN_REFUSED"
            await self._event("dry_run", rec["outcome"], symbol=rec["symbol"],
                              severity="INFO", payload=rec)
            if refused:
                log.info("dry run: entry would be refused: %s", rec.get("reason"))
        await super().drain_broker_events()


def build(settings, *, client, venue, symbols, products, strategy, costs,
          repo, lock, notifier, backfiller, kill_switch_path):
    """The dry-run bot: paper lifecycle, live venue checks, shadow exits on."""
    from app.execution.shadow_exits import ShadowExits
    from live.broker import LiveBroker
    from live.config import product_ids, tick_sizes

    bot = DryRunBot(settings, repo, costs, strategy=strategy, notifier=notifier,
                    backfiller=backfiller, lock=lock,
                    shadow_exits=ShadowExits(costs,
                                             slippage_bps=settings.risk.slippage_bps))
    reader = LiveBroker(client, product_ids=product_ids(products),
                        experiment_id="dry-run", tick_size=tick_sizes(products),
                        kill_switch_path=kill_switch_path)
    # The same entry-deviation limit the paper broker enforces after the fill,
    # so the book guard and the fill check agree on what "too far" means.
    reader.max_entry_deviation = bot.broker.max_entry_deviation
    bot.broker = DryRunBroker(bot.broker, reader,
                              equity=settings.risk.starting_equity,
                              kill_switch_path=kill_switch_path)
    return bot
