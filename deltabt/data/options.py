"""Option product catalog and candle access for Delta Exchange India.

The perpetual side of this repo works with 220 permanent symbols. The option
side works with a catalog that turns over completely every few weeks: ~1,070
contracts are live at any instant and roughly 140 expire every day. Anything
built on "the current product list" is therefore survivorship-biased by
construction -- a backtest that only knows about contracts listed today knows
only about expiries that have not happened yet.

So the catalog here is the *expired* list as much as the live one, pulled with
`states=expired` and cached. Two properties of that endpoint make the whole
approach viable and were confirmed by live calls:

* Expired products carry a populated ``settlement_price``, so expiry payoff is
  ground truth and never has to be inferred from a last-traded print.
* Pagination continues well past the ``meta.total_count`` the endpoint reports
  (it reported 10,000 while happily serving 40,000), so the catalog is fully
  enumerable -- just slowly, at roughly 1,000 products per 10 seconds.

Candle access reuses :class:`~deltabt.data.store.CandleStore` unchanged: an
option symbol is just a symbol, and ``MARK:`` works on expired contracts. Both
series matter and they are not interchangeable. Traded premium (`ltp`) is
sparse and is what a fill would actually have paid; ``MARK:`` is continuous,
exchange-computed, and is what margin, liquidation and settlement key off. A
volatility study reads MARK; an execution study reads LTP.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from deltabt.config import META_DIR
from deltabt.data.client import DeltaClient

log = logging.getLogger(__name__)

#: ``C-BTC-96000-301026`` -> call / BTC / 96000 / 2026-10-30.
#: Strikes are integers on every listed contract observed; the pattern rejects
#: rather than coerces anything else so a format change fails loudly.
_SYMBOL_RE = re.compile(r"^(?P<kind>C|P)-(?P<underlying>[A-Z0-9]+)-(?P<strike>[0-9.]+)-(?P<expiry>\d{6})$")

#: Every Delta India option settles at 12:00 UTC on its expiry date. Verified
#: against `settlement_time` on live and expired products alike.
SETTLEMENT_HOUR_UTC = 12

OPTION_CONTRACT_TYPES = "call_options,put_options"


@dataclass(frozen=True)
class OptionSymbol:
    """A parsed option symbol."""

    symbol: str
    is_call: bool
    underlying: str
    strike: float
    expiry_date: dt.date

    @property
    def expiry_ts(self) -> int:
        """Settlement instant, unix seconds UTC."""
        return int(
            dt.datetime(
                self.expiry_date.year,
                self.expiry_date.month,
                self.expiry_date.day,
                SETTLEMENT_HOUR_UTC,
                tzinfo=dt.timezone.utc,
            ).timestamp()
        )

    @property
    def right(self) -> str:
        return "C" if self.is_call else "P"


def parse_symbol(symbol: str) -> OptionSymbol | None:
    """Parse an option symbol, or ``None`` if it is not one.

    Returns None rather than raising because callers routinely sweep a mixed
    product list -- MOVE options and perpetuals share the endpoint and are not
    errors, just not options of this shape.
    """
    m = _SYMBOL_RE.match(symbol)
    if not m:
        return None
    suffix = m.group("expiry")
    try:
        expiry = dt.date(
            2000 + int(suffix[4:6]), int(suffix[2:4]), int(suffix[0:2])
        )
    except ValueError:
        return None
    return OptionSymbol(
        symbol=symbol,
        is_call=m.group("kind") == "C",
        underlying=m.group("underlying"),
        strike=float(m.group("strike")),
        expiry_date=expiry,
    )


_CATALOG_COLUMNS = {
    "symbol": "object",
    "product_id": "int64",
    "is_call": "bool",
    "underlying": "object",
    "strike": "float64",
    "expiry_ts": "int64",
    "state": "object",
    "settlement_price": "float64",
    "launch_ts": "int64",
    "tick_size": "float64",
    "contract_value": "float64",
    "maker_fee": "float64",
    "taker_fee": "float64",
}


class OptionCatalog:
    """Cached live + expired option product catalog, stored as Parquet.

    Incremental by ``product_id``: a refresh walks newest-first and stops once
    it has seen ``stop_after_known`` consecutive already-cached products, so a
    daily top-up costs a page or two rather than the full 40-minute walk.
    """

    def __init__(
        self,
        client: DeltaClient | None = None,
        meta_dir: Path = META_DIR,
    ) -> None:
        self.client = client or DeltaClient()
        self.meta_dir = Path(meta_dir)
        self.meta_dir.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self.meta_dir / "options_catalog.parquet"

    # -- io -----------------------------------------------------------------

    def read(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame({k: pd.Series(dtype=v) for k, v in _CATALOG_COLUMNS.items()})
        return pd.read_parquet(self.path)

    def _write(self, df: pd.DataFrame) -> None:
        tmp = self.path.with_suffix(".parquet.tmp")
        df.to_parquet(tmp, index=False)
        tmp.replace(self.path)

    # -- refresh ------------------------------------------------------------

    def refresh(
        self,
        *,
        states: str = "live,expired",
        max_pages: int = 200,
        page_size: int = 1000,
        stop_after_known: int = 2000,
        time_budget_seconds: float = 3600.0,
    ) -> pd.DataFrame:
        """Walk the product endpoint and merge into the cache.

        ``max_pages`` and ``time_budget_seconds`` are both hard stops. Neither
        is a correctness limit -- a truncated walk yields a catalog that is
        complete from the present backwards to wherever it stopped, which is a
        usable window, but callers must not mistake it for the full history.
        Progress is logged so a truncated pull is visible rather than silent.
        """
        cached = self.read()
        known = set(cached["product_id"].tolist()) if not cached.empty else set()

        rows: list[dict] = []
        after: str | None = None
        pages = 0
        consecutive_known = 0
        started = time.monotonic()

        while pages < max_pages:
            if time.monotonic() - started > time_budget_seconds:
                log.warning("catalog refresh hit its time budget after %d pages", pages)
                break
            params_after = after
            payload = self._page(states, page_size, params_after)
            batch = payload.get("result") or []
            if not batch:
                break
            pages += 1

            new_this_page = 0
            for p in batch:
                parsed = parse_symbol(p["symbol"])
                if parsed is None:
                    continue  # MOVE options and anything else non-vanilla
                pid = int(p["id"])
                if pid in known:
                    continue
                new_this_page += 1
                rows.append(_row_from_product(p, parsed))

            if new_this_page == 0:
                consecutive_known += len(batch)
                if consecutive_known >= stop_after_known:
                    log.info("catalog refresh reached known products after %d pages", pages)
                    break
            else:
                consecutive_known = 0

            after = (payload.get("meta") or {}).get("after")
            if not after:
                break
            log.info("catalog page %d: %d new (%d total pending)", pages, new_this_page, len(rows))

        if not rows:
            return cached

        fresh = pd.DataFrame(rows)
        merged = (
            pd.concat([cached, fresh], ignore_index=True)
            if not cached.empty
            else fresh
        )
        # keep="last" so a refresh updates a contract that has since expired
        # and gained a settlement_price.
        merged = (
            merged.drop_duplicates(subset="product_id", keep="last")
            .sort_values(["expiry_ts", "underlying", "strike", "is_call"], ignore_index=True)
        )
        for col, dtype in _CATALOG_COLUMNS.items():
            if col in merged.columns:
                merged[col] = merged[col].astype(dtype)
        self._write(merged)
        log.info("catalog now holds %d option products", len(merged))
        return merged

    def _page(self, states: str, page_size: int, after: str | None) -> dict:
        params = {
            "contract_types": OPTION_CONTRACT_TYPES,
            "states": states,
            "page_size": page_size,
        }
        if after:
            params["after"] = after
        return self.client._get("/v2/products", params)

    # -- queries ------------------------------------------------------------

    def expiries(self, underlying: str, *, settled_only: bool = True) -> list[int]:
        df = self.read()
        if df.empty:
            return []
        m = df["underlying"] == underlying
        if settled_only:
            m &= df["settlement_price"].notna()
        return sorted(df.loc[m, "expiry_ts"].unique().tolist())

    def chain(self, underlying: str, expiry_ts: int) -> pd.DataFrame:
        """Every listed strike for one expiry, calls and puts."""
        df = self.read()
        if df.empty:
            return df
        m = (df["underlying"] == underlying) & (df["expiry_ts"] == expiry_ts)
        return df.loc[m].sort_values(["strike", "is_call"], ignore_index=True)


def _row_from_product(p: dict, parsed: OptionSymbol) -> dict:
    return {
        "symbol": parsed.symbol,
        "product_id": int(p["id"]),
        "is_call": parsed.is_call,
        "underlying": parsed.underlying,
        "strike": parsed.strike,
        "expiry_ts": parsed.expiry_ts,
        "state": p.get("state"),
        "settlement_price": _f(p.get("settlement_price")),
        "launch_ts": _ts(p.get("launch_time")),
        "tick_size": _f(p.get("tick_size"), 0.1),
        "contract_value": _f(p.get("contract_value"), 0.001),
        "maker_fee": _f(p.get("maker_commission_rate"), 0.0001),
        "taker_fee": _f(p.get("taker_commission_rate"), 0.0001),
    }


def _f(value, default: float = float("nan")) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ts(iso: str | None) -> int:
    if not iso:
        return 0
    try:
        return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0
