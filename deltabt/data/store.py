"""Parquet cache for candle series.

Layout: ``{CACHE_DIR}/{symbol}/{series}_{resolution}.parquet`` where ``series``
is one of ``ltp``, ``mark``, ``funding``. Fetches are incremental -- an
existing file is extended at both ends rather than refetched, so repeat runs
are offline and instant.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pandas as pd

from deltabt.config import (
    CACHE_DIR,
    META_DIR,
    RESOLUTION_FUNDING,
    SERIES_FUNDING,
    SERIES_LTP,
    SERIES_MARK,
)
from deltabt.data.client import DeltaClient, resolution_seconds

log = logging.getLogger(__name__)

SERIES_PREFIX = {
    "ltp": SERIES_LTP,
    "mark": SERIES_MARK,
    "funding": SERIES_FUNDING,
}

#: Delta's 1m history exists from late 2023 but is too sparse to use until
#: early 2024. Probing earlier just wastes requests.
DEFAULT_HISTORY_START = 1704067200  # 2024-01-01T00:00:00Z

_SCHEMA = {
    "time": "int64",
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "volume": "float64",
}


class CandleStore:
    """Fetch-through cache over :class:`DeltaClient`."""

    def __init__(
        self,
        client: DeltaClient | None = None,
        cache_dir: Path = CACHE_DIR,
    ) -> None:
        self.client = client or DeltaClient()
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- paths --------------------------------------------------------------

    def path_for(self, symbol: str, series: str, resolution: str) -> Path:
        if series not in SERIES_PREFIX:
            raise ValueError(f"unknown series {series!r}; expected {sorted(SERIES_PREFIX)}")
        return self.cache_dir / symbol / f"{series}_{resolution}.parquet"

    # -- io -----------------------------------------------------------------

    def read(self, symbol: str, series: str, resolution: str) -> pd.DataFrame:
        path = self.path_for(symbol, series, resolution)
        if not path.exists():
            return _empty_frame()
        return pd.read_parquet(path)

    def _write(self, symbol: str, series: str, resolution: str, df: pd.DataFrame) -> None:
        path = self.path_for(symbol, series, resolution)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".parquet.tmp")
        df.to_parquet(tmp, index=False)
        tmp.replace(path)

    # -- fetch --------------------------------------------------------------

    def load(
        self,
        symbol: str,
        series: str,
        resolution: str,
        start: int,
        end: int,
        *,
        refresh: bool = True,
    ) -> pd.DataFrame:
        """Return candles for ``[start, end]``, fetching only what's missing."""
        cached = self.read(symbol, series, resolution)
        needed: list[tuple[int, int]] = []
        step = resolution_seconds(resolution)

        if cached.empty:
            needed.append((start, end))
        else:
            have_lo = int(cached["time"].iloc[0])
            have_hi = int(cached["time"].iloc[-1])
            if start < have_lo:
                needed.append((start, have_lo - step))
            if refresh and end > have_hi:
                needed.append((have_hi + step, end))

        if needed:
            api_symbol = f"{SERIES_PREFIX[series]}{symbol}"
            fetched: list[pd.DataFrame] = [cached] if not cached.empty else []
            for lo, hi in needed:
                if lo > hi:
                    continue
                log.info(
                    "fetching %s %s %s %s..%s",
                    symbol, series, resolution, _iso(lo), _iso(hi),
                )
                rows = self.client.candles(api_symbol, resolution, lo, hi)
                if rows:
                    fetched.append(_to_frame(rows))
            if fetched:
                merged = (
                    pd.concat(fetched, ignore_index=True)
                    .drop_duplicates(subset="time", keep="last")
                    .sort_values("time", ignore_index=True)
                )
                self._write(symbol, series, resolution, merged)
                cached = merged

        if cached.empty:
            return cached
        mask = (cached["time"] >= start) & (cached["time"] <= end)
        return cached.loc[mask].reset_index(drop=True)

    def load_all_series(
        self,
        symbol: str,
        resolution: str,
        start: int,
        end: int,
        *,
        refresh: bool = True,
    ) -> dict[str, pd.DataFrame]:
        """Load the three series the engine needs for one symbol.

        LTP prices fills, MARK triggers stops, FUNDING supplies the periodic
        charge. They are kept separate rather than joined because MARK has no
        synthetic bars while LTP does, so their indexes legitimately differ.

        Funding is fetched at a coarser resolution on purpose -- see
        ``RESOLUTION_FUNDING``.
        """
        out = {
            name: self.load(symbol, name, resolution, start, end, refresh=refresh)
            for name in ("ltp", "mark")
        }
        out["funding"] = self.load(
            symbol, "funding", RESOLUTION_FUNDING, start, end, refresh=refresh
        )
        return out


# --- product metadata -------------------------------------------------------


class ProductCatalog:
    """Cached per-symbol contract specs.

    Fees, tick size, lot size, leverage and funding interval all vary by
    symbol on Delta India, so nothing here may be hardcoded globally.
    """

    def __init__(
        self,
        client: DeltaClient | None = None,
        meta_dir: Path = META_DIR,
        *,
        max_age_seconds: int = 86400,
    ) -> None:
        self.client = client or DeltaClient()
        self.meta_dir = Path(meta_dir)
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        self.max_age_seconds = max_age_seconds
        self._cache: dict[str, dict] | None = None

    @property
    def _path(self) -> Path:
        return self.meta_dir / "products.json"

    def _fresh_enough(self) -> bool:
        p = self._path
        return p.exists() and (time.time() - p.stat().st_mtime) < self.max_age_seconds

    def all(self, *, refresh: bool = False) -> dict[str, dict]:
        if self._cache is not None and not refresh:
            return self._cache

        if not refresh and self._fresh_enough():
            self._cache = json.loads(self._path.read_text())
            return self._cache

        log.info("refreshing product catalog")
        products = self.client.products()
        catalog = {p["symbol"]: _spec_from_product(p) for p in products}
        self._path.write_text(json.dumps(catalog, indent=2, sort_keys=True))
        self._cache = catalog
        return catalog

    def get(self, symbol: str) -> dict:
        catalog = self.all()
        if symbol not in catalog:
            raise KeyError(
                f"{symbol} is not a live perpetual on Delta India. "
                f"It may be delisted or renamed -- note several symbols were "
                f"redenominated (e.g. PEPEUSD -> 1000PEPEUSD)."
            )
        return catalog[symbol]


def _spec_from_product(p: dict) -> dict:
    """Extract the fields the engine needs, with API semantics decoded.

    ``initial_margin``/``maintenance_margin`` arrive as percentages expressed
    as plain numbers ("0.5" means 0.5%), so max leverage is 100 / that value.
    """
    specs = p.get("product_specs") or {}
    initial_margin = _f(p.get("initial_margin"), 1.0)
    return {
        "symbol": p["symbol"],
        "product_id": p.get("id"),
        "tick_size": _f(p.get("tick_size"), 0.5),
        "contract_value": _f(p.get("contract_value"), 1.0),
        "maker_fee": _f(p.get("maker_commission_rate"), 0.0002),
        "taker_fee": _f(p.get("taker_commission_rate"), 0.0005),
        "initial_margin_pct": initial_margin,
        "maintenance_margin_pct": _f(p.get("maintenance_margin"), 0.25),
        "max_leverage": (100.0 / initial_margin) if initial_margin > 0 else 1.0,
        "position_size_limit": _f(p.get("position_size_limit"), float("inf")),
        # Funding cadence is a per-product field and differs across symbols
        # (8h for ~80, 4h for ~140). Never assume 8h.
        "funding_interval_seconds": int(
            _f(specs.get("rate_exchange_interval"), 28800.0)
        ),
        "settling_asset": (p.get("settling_asset") or {}).get("symbol"),
        "launch_time": p.get("launch_time"),
    }


def _f(value, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame({k: pd.Series(dtype=v) for k, v in _SCHEMA.items()})


def _to_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for col, dtype in _SCHEMA.items():
        if col not in df.columns:
            # FUNDING and MARK series return volume: null.
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(dtype)
    return df[list(_SCHEMA)].sort_values("time", ignore_index=True)


def _iso(ts: int) -> str:
    return pd.Timestamp(ts, unit="s", tz="UTC").strftime("%Y-%m-%d %H:%M")
