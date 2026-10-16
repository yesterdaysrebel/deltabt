"""Record ~40 readings for every signal the bot sees, taken or skipped.

WHY (owner, 2026-10-13). The bot should learn from every signal, not only the
two a day it takes. Every evaluated signal that has a direction -- approved,
or refused by the daily cap, the open position, the per-coin pause, zero
contracts, a venue check, a halt -- gets the readings the signal lab used
(deltabt/signal_features.py, identical definitions, so live rows pool with the
5,273 historical ones) plus a few the history cannot have: the ticker and the
L2 order book at that moment. `python -m live label-signals` later labels each
one as if it had been taken; `python -m live grade-model` grades a model on
them weekly.

IT NEVER TOUCHES A TRADE. It runs after the decision and after the order, as
a background task with a timeout; the CPU work and the public REST calls run
in a worker thread so the event loop never waits on them. Any failure is
logged and recorded as NaNs with the error. No result flows back into the
bot: the only output is an UPDATE of `strategy_signals.features`.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Mapping

import pandas as pd

from deltabt import signal_features as SF
from deltabt.strategy import resample_complete

log = logging.getLogger(__name__)

#: Version of the JSON written to strategy_signals.features.
FEATURES_VERSION = 1
#: Primary-bar history the readings need (24h of 5m for ret_24h / 24h high,
#: plus warm-up for the 240m Supertrend): 8 days of 1m.
HISTORY_MINUTES = 8 * 1440

#: How a refusal is named in the daily "Learning" line. First match wins;
#: limit names come from the risk engine, text from _place / the broker.
SKIP_RULES = (
    # A coin watched for the record only (2026-10-16): never tradeable, so
    # this wins over everything else.
    ("record_only", ("record_only",)),
    ("halt", ("market state is", "market is halted")),
    ("daily_cap", ("max_trades_per_day",)),
    ("open_position", ("max_open_positions", "already holding", "already reserved")),
    ("coin_pause", ("pause_symbol_after_loss", "paused after a loss")),
    ("zero_contracts", ("min_contract_size", "zero contracts")),
    ("cooldown", ("cooldown",)),
    ("loss_limits", ("max_daily_loss", "max_drawdown", "max_consecutive_losses", "halted")),
    ("venue_check", ("broker:", "kill switch", "unknown outcome")),
)


def skip_reason(outcome: str | None, reason: str | None, limit_name: str | None) -> str:
    """'taken', or the category of why the signal was skipped."""
    if outcome == "APPROVED":
        return "taken"
    text = f"{limit_name or ''} {reason or ''}".lower()
    for name, needles in SKIP_RULES:
        if any(n in text for n in needles):
            return name
    return "other"


def _frame(rows: list[dict], value: str = "close") -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df.get(col), errors="coerce").astype("float64") if col in df else 0.0
    df["time"] = df["time"].astype("int64")
    return df.sort_values("time", ignore_index=True)


class SignalRecorder:
    """Builds the features JSON for one signal. Construct once per process.

    `picks`: {symbol: {"change": %, "rank": 1.., "turnover": USD}} for the
    coins picked as today's top gainers, and `pick_day` the UTC day (unix s)
    they were picked for. Plain data, injected by live/__main__.py, so app/
    never imports live/. `launches`: {symbol: unix s}. `contract_values`:
    {symbol: underlying per contract}, for order-book depth in USD.

    `record_only` (2026-10-16): coins watched for the record only, never
    traded. Their rows carry ``"record_only": true`` and ``skip ==
    "record_only"`` whatever the bot's refusal said; every row carries
    ``record_only`` and the pick ``rank`` (None for a watchlist coin outside
    the ranks, or a held coin off the list). A watchlist coin's pick holds
    its 24h change and turnover with rank None, so day_gain_pct and
    log_turnover are filled and day_rank is NaN.
    """

    def __init__(self, spec, *, client=None, picks: Mapping | None = None,
                 pick_day: int | None = None, launches: Mapping | None = None,
                 tags: Mapping | None = None, contract_values: Mapping | None = None,
                 timeout: float = 60.0, record_only=()) -> None:
        self.spec = spec
        self._client = client
        self.picks = dict(picks or {})
        self.record_only = frozenset(record_only or ()) | frozenset(
            s for s, i in self.picks.items() if (i or {}).get("record_only"))
        #: BTC context is the same for every signal on one bar; with ~3x more
        #: coins watched it is fetched once per bar, not once per signal.
        self._btc_cache: dict[tuple[str, int], pd.DataFrame] = {}
        self._btc_lock = threading.Lock()
        self.pick_day = pick_day
        self.launches = dict(launches or {})
        self.tags = dict(tags or {})
        self.contract_values = dict(contract_values or {})
        self.timeout = timeout
        self._tasks: set[asyncio.Task] = set()

    @property
    def client(self):
        if self._client is None:
            from deltabt.data.client import DeltaClient
            # public endpoints only; short timeouts, few retries -- a slow
            # venue costs a NaN, never a delay
            self._client = DeltaClient(timeout=8.0, max_retries=2, per_second=4.0)
        return self._client

    # -- scheduling (called from the bot; must never raise) -----------------

    def schedule(self, repo, *, key: str, symbol: str, bars: list, bar_open: int,
                 side: int, entry: float, stop: float, outcome: str | None,
                 reason: str | None, limit_name: str | None,
                 flow: dict | None = None) -> asyncio.Task | None:
        try:
            task = asyncio.get_running_loop().create_task(self.record(
                repo, key=key, symbol=symbol, bars=bars, bar_open=bar_open, side=side,
                entry=entry, stop=stop, outcome=outcome, reason=reason,
                limit_name=limit_name, flow=flow), name=f"signal-features-{symbol}")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return task
        except Exception:                                  # noqa: BLE001
            log.exception("could not schedule signal features")
            return None

    async def drain(self, timeout: float = 5.0) -> None:
        """Give in-flight recordings a moment at shutdown, then drop them."""
        if not self._tasks:
            return
        _done, pending = await asyncio.wait(list(self._tasks), timeout=timeout)
        for t in pending:
            t.cancel()

    async def record(self, repo, *, key, symbol, bars, bar_open, side, entry, stop,
                     outcome, reason, limit_name, flow: dict | None = None) -> dict:
        t0 = time.monotonic()
        try:
            payload = await asyncio.wait_for(asyncio.to_thread(
                self.compute, symbol=symbol, bars=bars, bar_open=bar_open, side=side,
                entry=entry, stop=stop), timeout=self.timeout)
        except Exception as exc:                            # noqa: BLE001
            log.warning("signal features failed for %s: %s", symbol, exc)
            payload = {"lab": {k: None for k in SF.FEATURES}, "live": {}, "pre_24h": None,
                       "errors": [f"{type(exc).__name__}: {exc}"]}
        ro = symbol in self.record_only
        payload.update(version=FEATURES_VERSION, computed_at=int(time.time()),
                       elapsed_s=round(time.monotonic() - t0, 3),
                       final_outcome=outcome, final_reason=reason, limit_name=limit_name,
                       skip="record_only" if ro else skip_reason(outcome, reason, limit_name),
                       # watched for the record only, never tradeable (2026-10-16)
                       record_only=ro, rank=(self.picks.get(symbol) or {}).get("rank"),
                       # order flow at the decision (app/market_data/trade_flow.py);
                       # live-only, like the order book under "live"
                       flow=flow)
        try:
            await repo.record_signal_features(key, payload)
        except Exception:                                   # noqa: BLE001
            log.exception("could not store signal features for %s", symbol)
        return payload

    # -- the work (worker thread) -------------------------------------------

    def _btc(self, res: str, at: int, fetch) -> pd.DataFrame:
        """BTC candles for the bar ending at `at`: the same request for every
        signal on that bar, so fetched once. A copy is returned; a failed
        fetch is not cached (it raises, and the next signal tries again)."""
        key = (res, int(at))
        with self._btc_lock:
            hit = self._btc_cache.get(key)
        if hit is None:
            hit = fetch()
            with self._btc_lock:
                if len(self._btc_cache) >= 8:
                    for k in sorted(self._btc_cache, key=lambda k: k[1])[:4]:
                        self._btc_cache.pop(k, None)
                self._btc_cache[key] = hit
        return hit.copy()

    def compute(self, *, symbol: str, bars: list, bar_open: int, side: int,
                entry: float, stop: float) -> dict:
        errors: list[str] = []
        at = int(bar_open) + 300

        def get(what, fn):
            try:
                return fn()
            except Exception as exc:                        # noqa: BLE001
                errors.append(f"{what}: {type(exc).__name__}: {exc}")
                return None

        cl = self.client
        ticker = get("ticker", lambda: cl._get(f"/v2/tickers/{symbol}").get("result"))
        book = get("l2orderbook", lambda: cl._get(f"/v2/l2orderbook/{symbol}").get("result"))
        btc_1m = get("btc_1m", lambda: self._btc(
            "1m", at, lambda: _frame(cl.candles("BTCUSD", "1m", at - 25 * 3600, at - 60))))
        btc_60m = get("btc_60m", lambda: self._btc(
            "1h", at, lambda: _frame(cl.candles("BTCUSD", "1h", at - 14 * 86400, at - 3600))))
        oi = get("oi", lambda: _frame(cl.candles("OI:" + symbol, "1h", at - 27 * 3600, at - 3600))
                 .rename(columns={"close": "oi"}))
        funding = get("funding", lambda: _frame(cl.candles("FUNDING:" + symbol, "1h",
                                                           at - 12 * 3600, at - 3600)))

        one = pd.DataFrame([b.as_row() for b in bars[-HISTORY_MINUTES:]]) if bars else None
        p5 = h1 = h4 = None
        if one is not None and not one.empty and getattr(self.spec, "primary_minutes", 5) == 5:
            p5, h1, h4 = (resample_complete(one, 5), resample_complete(one, 60),
                          resample_complete(one, 240))
        elif one is not None:
            errors.append("readings are defined for a 5m primary only")
        tag = SF.tag_of((ticker or {}).get("tags")) if ticker and ticker.get("tags") is not None \
            else self.tags.get(symbol)
        picks = ({self.pick_day: self.picks[symbol]}
                 if self.pick_day is not None and symbol in self.picks else None)
        lab, pre = SF.signal_features(
            p5=p5, h1=h1, h4=h4, bar_open=bar_open, side=side, entry_price=entry,
            stop_price=stop, spec=self.spec, btc_1m=btc_1m, btc_60m=btc_60m,
            funding=funding, oi=oi if oi is not None and not oi.empty else None,
            picks=picks, launch=self.launches.get(symbol), tag=tag)
        errors += lab.pop("feature_errors", [])
        live = SF.book_readings(ticker, book, self.contract_values.get(symbol))
        return {"lab": lab, "live": live, "pre_24h": pre, "tag": tag,
                "picked": symbol in self.picks, "errors": errors}
