"""H-VOL-1 -- does expansion out of volatility compression predict price?

PRE-REGISTERED. Frozen before any event was counted:
``out/hvol1/hvol1_preregistration.md``
sha256 624d0b2848bbc58555f347d4f1e33027dbc46f9590846b7cfad6f3be851b36a5

THIS MODULE DEFINES EVENTS AND NOTHING ELSE
    Reference price, horizons, split admission, cluster inference, MDE, control
    and gate are all imported unchanged from ``hstructure2``. That module is
    hash-frozen in its own manifest and is NOT edited here -- a second
    hypothesis reusing the machinery must not be able to alter it, or the first
    hypothesis's frozen result stops being reproducible.

THE COMPRESSION STATE IS INHERITED, NOT CHOSEN NOW
    ``hcompress._rolling_quantile_causal`` and ``hcompress._compression_zones``
    are imported unchanged, with H-Compress-1's frozen constants. Picking a
    fresh percentile, window and duration today would be three new numbers
    chosen by me after two related experiments had already failed, and no
    reader could tell whether they were picked to work. Numbers frozen in a
    prior pre-registration cannot have been.

    What is NOT inherited is everything execution-specific -- the retest entry,
    the 3-bar order lifetime, the volume multiple, the body-size filter. Stage A
    has no execution and may not inherit execution parameters.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from deltabt import indicators as ind
from deltabt.config import OUT_DIR
from deltabt.data.quality import tradable_mask
from deltabt.research.hcompress import (_compression_zones,
                                        _rolling_quantile_causal)
# machinery, imported unchanged -- see module docstring
from deltabt.research.hstructure2 import (CONTROL_PERMUTATIONS, CONTROL_SEED,
                                          HORIZONS_MIN, MDE_K,
                                          PRIMARY_HORIZON_MIN, STUDY, SYMBOLS,
                                          TEST_START, TRAIN, VALID, DATA_END,
                                          _locate, control, day_cluster,
                                          estimate, in_split)
from deltabt.strategy import resample_ohlcv

__all__ = ["TF_MIN", "ATR_PERIOD", "PCT_LOOKBACK", "PERCENTILE", "MIN_DURATION",
           "RANGE_MAX", "EVENTS", "FAMILIES", "compression", "event_flags",
           "events", "estimate", "control", "in_split", "day_cluster",
           "HORIZONS_MIN", "PRIMARY_HORIZON_MIN", "SYMBOLS", "TRAIN", "VALID",
           "TEST_START", "STUDY", "DATA_END", "MDE_K", "CONTROL_SEED",
           "CONTROL_PERMUTATIONS", "OUT"]

#: All six inherited verbatim from H-Compress-1's frozen pre-registration.
TF_MIN = 15
ATR_PERIOD = 14
PCT_LOOKBACK = 960          # 10 days of 15m bars
PERCENTILE = 0.20
MIN_DURATION = 4
RANGE_MAX = 1.5

EVENTS = ("EXP_UP", "EXP_DOWN")
FAMILIES = {"V1-EXP": EVENTS}
DIRECTION = {"EXP_UP": 1, "EXP_DOWN": -1}

OUT = OUT_DIR / "hvol1"


def compression(df1m: pd.DataFrame) -> dict:
    """Compression state on the 15m grid, valid as of each bar's CLOSE.

    The percentile window ends at t-1 and excludes t; the zone extremes use
    bars up to and including t. Both properties come from the imported
    H-Compress-1 helpers and are not re-derived here.
    """
    d = resample_ohlcv(df1m, TF_MIN).reset_index(drop=True)
    h = d["high"].to_numpy("float64")
    lo = d["low"].to_numpy("float64")
    c = d["close"].to_numpy("float64")

    atr = ind.atr(h, lo, c, ATR_PERIOD)
    with np.errstate(invalid="ignore", divide="ignore"):
        atr_pct = atr / c
    thr = _rolling_quantile_causal(atr_pct, PCT_LOOKBACK, PERCENTILE)
    compressed = np.isfinite(thr) & (atr_pct < thr)
    zh, zl, nb, ok = _compression_zones(h, lo, atr, compressed,
                                        MIN_DURATION, RANGE_MAX)
    return dict(time=d["time"].to_numpy("int64"), close=c, high=h, low=lo,
                atr=atr, atr_pct=atr_pct, threshold=thr, compressed=compressed,
                zone_high=zh, zone_low=zl, zone_bars=nb, zone_ok=ok,
                warmup=PCT_LOOKBACK + ATR_PERIOD)


def event_flags(C: dict) -> dict:
    """The two frozen events of §2, on the 15m grid.

    ``ok``, ``zone_high`` and ``zone_low`` are read at t-1; only ``close`` is
    read at t. The event is knowable at the close of bar t and not before.
    """
    c = C["close"]
    n = c.size
    prev = np.concatenate(([False], C["zone_ok"][:-1]))
    zh = np.concatenate(([np.nan], C["zone_high"][:-1]))
    zl = np.concatenate(([np.nan], C["zone_low"][:-1]))

    with np.errstate(invalid="ignore"):
        up = prev & np.isfinite(zh) & (c > zh)
        dn = prev & np.isfinite(zl) & (c < zl)

    if (up & dn).any():
        raise AssertionError(
            "a bar broke both boundaries, which requires zone_low > zone_high. "
            "The pre-registration states these are mutually exclusive; that "
            "claim is now false and the definition must be revisited, not "
            "silently resolved.")

    def oneshot(x):
        return x & ~np.concatenate(([False], x[:-1]))

    out = {"EXP_UP": oneshot(up), "EXP_DOWN": oneshot(dn)}
    warm = min(C["warmup"], n)
    for v in out.values():
        v[:warm] = False
    return out


def events(df1m: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """One row per event with forward signed returns at every frozen horizon.

    Same schema and same timing rules as ``hstructure2.events``: the reference
    price is the OPEN of the first 1m bar at or after the 15m bar's close, and
    every horizon is located by TIMESTAMP so a gap in the cache drops the event
    instead of quietly measuring a shorter window than its label claims.
    """
    C = compression(df1m)
    F = event_flags(C)

    t1 = df1m["time"].to_numpy("int64")
    o1 = df1m["open"].to_numpy("float64")
    c1 = df1m["close"].to_numpy("float64")
    trad = np.asarray(tradable_mask(df1m), "bool")

    rows = []
    for name in EVENTS:
        idx = np.flatnonzero(F[name])
        if not idx.size:
            continue
        j0 = _locate(t1, C["time"][idx] + TF_MIN * 60)
        keep = (j0 >= 0) & trad[np.maximum(j0, 0)]
        idx, j0 = idx[keep], j0[keep]
        if not idx.size:
            continue
        rows.append(pd.DataFrame(dict(
            symbol=symbol, event=name, direction=DIRECTION[name],
            bar_i=idx, t0=t1[j0], p0=o1[j0],
            zone_bars=C["zone_bars"][idx - 1])))

    if not rows:
        return pd.DataFrame(columns=["symbol", "event", "direction", "t0", "p0"])
    ev = pd.concat(rows, ignore_index=True).sort_values("t0").reset_index(drop=True)

    p0 = ev["p0"].to_numpy("float64")
    sign = ev["direction"].to_numpy("float64")
    for h in HORIZONS_MIN:
        jh = _locate(t1, ev["t0"].to_numpy("int64") + (h - 1) * 60)
        ph = np.where(jh >= 0, c1[np.maximum(jh, 0)], np.nan)
        ev[f"y_{h}"] = sign * (ph / p0 - 1.0)
        ev[f"t_{h}"] = np.where(jh >= 0, t1[np.maximum(jh, 0)], -1)
    return ev


def family_frame(ev: pd.DataFrame, family: str) -> pd.DataFrame:
    if family not in FAMILIES:
        raise ValueError(f"family must be one of {tuple(FAMILIES)}, got {family!r}")
    return ev[ev["event"].isin(FAMILIES[family])].reset_index(drop=True)
