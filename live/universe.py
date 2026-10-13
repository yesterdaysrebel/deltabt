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
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping

log = logging.getLogger(__name__)

ENV_UNIVERSE = "DELTABOT_UNIVERSE"
ENV_TOP_N = "DELTABOT_TOP_N"
ENV_MIN_TURNOVER = "DELTABOT_MIN_TURNOVER_USD"

DEFAULT_TOP_N = 10
#: About BEATUSD's turnover on 2026-10-13 ($283k); 59 of 196 crypto perps cleared it.
DEFAULT_MIN_TURNOVER_USD = 250_000.0


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
    if n < 1:
        raise ValueError(f"top_n must be >= 1, got {n}")
    picked: list[tuple[float, str]] = []
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
        picked.append((change, sym))
    picked.sort(key=lambda t: (-t[0], t[1]))
    return [s for _, s in picked[:n]]


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
    n, floor = settings_from_env(env)
    gainers = top_gainers(tickers, n=n, min_turnover_usd=floor)
    universe = merge_held(gainers, held)
    if not universe:
        raise ValueError(
            f"no coin qualifies as a top gainer (top_n={n}, "
            f"min_turnover_usd={floor:.0f}) and none is held")
    log.info("universe: top gainers %s; held %s", gainers,
             [s for s in universe if s not in gainers])
    return universe
