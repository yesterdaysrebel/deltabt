"""Which coins the bot watches today: Delta's top gainers (owner, 2026-10-13).

The owner trades by opening the "top gainers" page in Delta's app and picking
coins from it. The bot does the same once a day, at start-up: it reads the
public tickers, keeps crypto perpetuals that are trading normally and have
enough turnover to fill a stop, sorts them by 24h last-price change, and takes
the top N. A daily restart (run_live.sh) re-picks the list.

COINS STILL HELD ARE ALWAYS KEPT. recover() refuses to start when an open
position's coin is not in the universe, so a coin that drops off the gainers
list while the bot holds it must stay watched until the position closes.

Settings (env, written by run_live.sh from the stack's sizing parameter):

    DELTABOT_UNIVERSE          "fixed" (default: live/config.VENUE_SYMBOLS) or "top_gainers"
    DELTABOT_TOP_N             how many gainers to watch (default 10)
    DELTABOT_MIN_TURNOVER_USD  24h turnover floor in USD (default 250000)

RECORD-ONLY COINS (owner, 2026-10-16). To grow the learning data ~3x the bot
also WATCHES coins it never trades, and only records their signals:

    gainers ranked top_n+1 .. record_top_n   same rules as the trade list
    a fixed watchlist                        every day, whatever its rank;
                                             a coin not listed is skipped

The defaults live here, not in run_live.sh (its user_data is at the 16 KiB
cap). Optional env overrides, which run_live.sh does not forward:

    DELTABOT_RECORD_TOP_N      rank cut-off for recording (default 30;
                               <= DELTABOT_TOP_N turns ranked recording off)
    DELTABOT_RECORD_EXTRA      comma list (default BTCUSD,ETHUSD,BEATUSD,AKEUSD;
                               set to "" for no watchlist)

A coin in today's top N, or still held, is TRADEABLE exactly as before; every
other watched coin is RECORD-ONLY and can never be traded: app/risk/engine.py
refuses it first, and the bot's _place and every broker refuse it again.
Fixed mode is unchanged (no record-only coins).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping

log = logging.getLogger(__name__)

ENV_UNIVERSE = "DELTABOT_UNIVERSE"
ENV_TOP_N = "DELTABOT_TOP_N"
ENV_MIN_TURNOVER = "DELTABOT_MIN_TURNOVER_USD"
ENV_RECORD_TOP_N = "DELTABOT_RECORD_TOP_N"
ENV_RECORD_EXTRA = "DELTABOT_RECORD_EXTRA"

DEFAULT_TOP_N = 10
#: About BEATUSD's turnover on 2026-10-13 ($283k); 59 of 196 crypto perps cleared it.
DEFAULT_MIN_TURNOVER_USD = 250_000.0
#: Gainers ranked up to here are watched; those past the top N are record-only.
DEFAULT_RECORD_TOP_N = 30
#: Recorded every day whatever their rank (owner, 2026-10-16).
DEFAULT_RECORD_EXTRA = ("BTCUSD", "ETHUSD", "BEATUSD", "AKEUSD")


def _num(row: Mapping, key: str) -> float | None:
    try:
        return float(row.get(key))
    except (TypeError, ValueError):
        return None


def top_gainers(rows: Iterable[Mapping], *, n: int = DEFAULT_TOP_N,
                min_turnover_usd: float = DEFAULT_MIN_TURNOVER_USD) -> list[str]:
    """The n crypto perpetuals with the biggest 24h rise, highest first.

    A row qualifies when it is a perpetual, tagged crypto (not an xStock or
    commodity token), trading normally, has turnover at or above the floor,
    and actually rose over 24h. Rows missing a field do not qualify.
    """
    return [r["symbol"] for r in ranked_gainers(rows, n=n, min_turnover_usd=min_turnover_usd)]


def ranked_gainers(rows: Iterable[Mapping], *, n: int = DEFAULT_TOP_N,
                   min_turnover_usd: float = DEFAULT_MIN_TURNOVER_USD) -> list[dict]:
    """top_gainers with what the pick saw: symbol, rank (1 = biggest rise),
    change (24h %, as Delta's ltp_change_24h), turnover (24h USD), tags.

    The signal recorder stores these with every signal (day_gain_pct,
    day_rank, log_turnover in deltabt/signal_features.py)."""
    if n < 1:
        raise ValueError(f"top_n must be >= 1, got {n}")
    picked: list[tuple[float, str, float, object]] = []
    for r in rows:
        if r.get("contract_type") != "perpetual_futures":
            continue
        if r.get("top_tag") != "crypto":
            continue
        if r.get("product_trading_status") != "operational":
            continue
        change = _num(r, "ltp_change_24h")
        turnover = _num(r, "turnover_usd")
        sym = r.get("symbol")
        if not sym or change is None or turnover is None:
            continue
        if change <= 0 or turnover < min_turnover_usd:
            continue
        picked.append((change, sym, turnover, r.get("tags")))
    picked.sort(key=lambda t: (-t[0], t[1]))
    return [{"symbol": sym, "rank": k, "change": change, "turnover": turnover, "tags": tags}
            for k, (change, sym, turnover, tags) in enumerate(picked[:n], 1)]


def merge_held(gainers: list[str], held: Iterable[str]) -> tuple[str, ...]:
    """Gainers first, then any held coin not among them."""
    out = list(gainers)
    for s in held:
        if s not in out:
            out.append(s)
    return tuple(out)


def mode(env: Mapping[str, str] | None = None) -> str:
    src = os.environ if env is None else env
    m = (src.get(ENV_UNIVERSE) or "fixed").strip().lower()
    if m not in ("fixed", "top_gainers"):
        raise ValueError(f"{ENV_UNIVERSE}={m!r} is not 'fixed' or 'top_gainers'")
    return m


def settings_from_env(env: Mapping[str, str] | None = None) -> tuple[int, float]:
    src = os.environ if env is None else env
    n = int(src.get(ENV_TOP_N) or DEFAULT_TOP_N)
    floor = float(src.get(ENV_MIN_TURNOVER) or DEFAULT_MIN_TURNOVER_USD)
    if n < 1 or floor < 0:
        raise ValueError(f"bad universe settings: top_n={n} min_turnover_usd={floor}")
    return n, floor


def pick(tickers: Iterable[Mapping], held: Iterable[str],
         env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Today's universe from a tickers snapshot and the coins still held."""
    return pick_with_info(tickers, held, env)[0]


def pick_with_info(tickers: Iterable[Mapping], held: Iterable[str],
                   env: Mapping[str, str] | None = None) -> tuple[tuple[str, ...], dict]:
    """(universe, {symbol: {"rank", "change", "turnover", "tags"}}) -- the
    second for the gainers only; a coin kept because it is held has none."""
    n, floor = settings_from_env(env)
    ranked = ranked_gainers(tickers, n=n, min_turnover_usd=floor)
    gainers = [r["symbol"] for r in ranked]
    info = {r["symbol"]: {k: r[k] for k in ("rank", "change", "turnover", "tags")}
            for r in ranked}
    universe = merge_held(gainers, held)
    if not universe:
        raise ValueError(
            f"no coin qualifies as a top gainer (top_n={n}, "
            f"min_turnover_usd={floor:.0f}) and none is held")
    log.info("universe: top gainers %s; held %s", gainers,
             [s for s in universe if s not in gainers])
    return universe, info


def record_settings_from_env(env: Mapping[str, str] | None = None
                             ) -> tuple[int, tuple[str, ...]]:
    """(record_top_n, watchlist). Unset means the defaults;
    DELTABOT_RECORD_EXTRA="" means no watchlist."""
    src = os.environ if env is None else env
    raw_n = src.get(ENV_RECORD_TOP_N)
    n = DEFAULT_RECORD_TOP_N if raw_n is None or not raw_n.strip() else int(raw_n)
    if n < 0:
        raise ValueError(f"bad {ENV_RECORD_TOP_N}={n}")
    raw = src.get(ENV_RECORD_EXTRA)
    extra = DEFAULT_RECORD_EXTRA if raw is None else tuple(
        s.strip().upper() for s in raw.split(",") if s.strip())
    return n, tuple(dict.fromkeys(extra))


def _info(r: Mapping, record_only: bool) -> dict:
    return {**{k: r[k] for k in ("rank", "change", "turnover", "tags")},
            "record_only": record_only}


def pick_wide(tickers: Iterable[Mapping], held: Iterable[str],
              env: Mapping[str, str] | None = None
              ) -> tuple[tuple[str, ...], tuple[str, ...], dict]:
    """(trade universe, record-only coins, pick info).

    The TRADE universe is exactly pick_with_info's: today's top N gainers plus
    every coin still held. RECORD-ONLY = the gainers ranked N+1..record_top_n,
    then the watchlist, minus anything tradeable; a watchlist coin missing from
    the tickers (not listed) is skipped with a log line. The two never
    overlap. Pick info covers every watched gainer and watchlist coin:
    {"rank", "change", "turnover", "tags", "record_only"} -- rank None for a
    watchlist coin outside the ranks, whose 24h change and turnover come from
    its own ticker row. A held coin off the lists still has none.
    """
    rows = list(tickers)
    held = list(held)
    trade, info = pick_with_info(rows, held, env)
    info = {s: {**i, "record_only": False} for s, i in info.items()}
    n, floor = settings_from_env(env)
    record_n, extra = record_settings_from_env(env)
    record: list[str] = []
    if record_n > n:
        for r in ranked_gainers(rows, n=record_n, min_turnover_usd=floor)[n:]:
            sym = r["symbol"]
            if sym in trade:              # held: tradeable, as before
                info.setdefault(sym, _info(r, False))
                continue
            record.append(sym)
            info[sym] = _info(r, True)
    listed = {str(r.get("symbol") or "").upper(): r for r in rows
              if r.get("contract_type", "perpetual_futures") == "perpetual_futures"}
    for sym in extra:
        if sym in trade or sym in record:
            continue
        row = listed.get(sym)
        if row is None:
            log.warning("record-only watchlist: %s is not listed on the venue; skipped", sym)
            continue
        record.append(sym)
        info[sym] = {"rank": None, "change": _num(row, "ltp_change_24h"),
                     "turnover": _num(row, "turnover_usd"), "tags": row.get("tags"),
                     "record_only": True}
    log.info("record-only today (watched, never traded): %s", record)
    return trade, tuple(record), info
