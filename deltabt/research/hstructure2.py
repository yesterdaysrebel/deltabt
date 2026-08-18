"""H-STRUCTURE-2 -- do HH/HL and LH/LL transitions predict forward PRICE returns?

PRE-REGISTERED. Frozen before any event was counted:
``out/hstructure2/hstructure2_preregistration.md``
sha256 7338dddb3159fc0a1443ac8f12ab6cf0c366b42be2d6eb670d4749bd7b41689d

STAGE A MEASURES PRICE, NOT TRADES
    No stop, no target, no R, no fee, no slippage, no funding appears in this
    module, and none may be added to it. H-Structure-1 already tested this
    family jointly with a 2R/structural-stop geometry and returned NO SIGNAL;
    H-COST-1 and H-NULL-1 then showed that geometry can destroy a real effect
    AND manufacture a fake one. Stage A exists to ask the prior question -- is
    there information at all -- with the geometry removed entirely.

WHAT IS REUSED (loaded, not copied, not edited)
    out/hstructure/code/hstructure.py :: _swing_flags, _structure_state
        The H-Structure-1 swing detector, which passed that experiment's
        anti-lookahead audit: structure state reproduced exactly at 48
        truncation points, 0 violations across 36,732 trades. Loading the
        archived file by path rather than re-typing it is deliberate -- a copy
        would be a new implementation wearing an audited implementation's
        reputation.
    deltabt.strategy.resample_ohlcv    UTC-aligned 1m -> 15m aggregation
    deltabt.data.quality.tradable_mask synthetic-bar and halt exclusion
    deltabt.research.hnull1.inference  the ratified estimator, called AS-IS

INFERENCE
    ``hnull1.inference`` predates the ratification that made cluster PRIMARY and
    still returns ``se = se_block`` when a block length is supplied. It is NOT
    modified here -- its sha256 is recorded in out/hnull1/inference_frozen.json
    and editing it would invalidate that record. Every headline number in this
    module reads ``se_cluster`` explicitly instead.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from deltabt.config import OUT_DIR
from deltabt.data.quality import tradable_mask
from deltabt.research.hnull1 import inference
from deltabt.strategy import resample_ohlcv

# --------------------------------------------------------------- frozen inputs

#: §2.2 -- ONE arm. Not swept, not re-picked after the census.
STRUCT_TF_MIN = 15
SWING_N = 3
TRIGGER = "oneshot"

#: §3.2 -- all six reported; §6 -- +1h is the gate.
HORIZONS_MIN = (5, 15, 30, 60, 240, 1440)
PRIMARY_HORIZON_MIN = 60

SYMBOLS = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")

#: §3.3 -- pinned exactly as H-Structure-1 / H-COST-1, so TEST cannot drift.
STUDY = int(pd.Timestamp("2025-01-01", tz="UTC").timestamp())
DATA_END = 1786531980                          # 2026-08-12 10:53Z
_SPAN = DATA_END - STUDY
TRAIN = (STUDY, STUDY + int(_SPAN * 0.6))      # -> 2025-12-20
VALID = (TRAIN[1], STUDY + int(_SPAN * 0.8))   # -> 2026-04-16
TEST_START = VALID[1]                          # LOCKED. Never read.

#: §5 -- control seed, frozen in the pre-registration.
CONTROL_SEED = 20260818
CONTROL_PERMUTATIONS = 1000

#: §4.2 -- 2.8 ~= z_0.975 + z_0.80, the identical constant H-NULL-1 used.
MDE_K = 2.8

EVENTS = ("CONT_LONG", "CONT_SHORT", "FAIL_LONG", "FAIL_SHORT")
FAMILIES = {"S2-CONT": ("CONT_LONG", "CONT_SHORT"),
            "S2-FAIL": ("FAIL_LONG", "FAIL_SHORT")}
DIRECTION = {"CONT_LONG": 1, "CONT_SHORT": -1, "FAIL_LONG": 1, "FAIL_SHORT": -1}

_ARCHIVE = Path(__file__).resolve().parents[2] / "out/hstructure/code/hstructure.py"


#: Module name the archive is registered under. It must be in ``sys.modules``
#: BEFORE execution, and it must be STABLE across processes. The archived
#: detector is numba ``cache=True``, and numba pickles the defining module's
#: NAME into its on-disk cache entry. Loaded anonymously that name is
#: ``<dynamic>``, and the next process to read the cache raises
#: ModuleNotFoundError from inside pickle -- a failure that looks like a numba
#: bug and is really a module-naming one.
_ARCHIVE_MODNAME = "_hstructure1_archive"


def _load_archived_detector():
    """Import the H-Structure-1 swing detector from the frozen archive.

    By path, because that archive is a results directory and not a package. The
    alternative -- pasting the two functions in here -- would silently fork the
    audited implementation, and the fork would inherit the audit's credibility
    without having earned it.

    Loaded once and memoised. Re-executing it per call is not merely wasteful:
    each execution recompiles the numba kernels against a module object the
    cache can no longer find.
    """
    if _ARCHIVE_MODNAME in sys.modules:
        return sys.modules[_ARCHIVE_MODNAME]
    if not _ARCHIVE.exists():
        raise FileNotFoundError(
            f"the H-Structure-1 swing detector is missing from {_ARCHIVE}. It is "
            f"reused unchanged by H-STRUCTURE-2 and is not reimplemented here.")
    spec = importlib.util.spec_from_file_location(_ARCHIVE_MODNAME, _ARCHIVE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_ARCHIVE_MODNAME] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        del sys.modules[_ARCHIVE_MODNAME]
        raise
    return mod


# ------------------------------------------------------------------- structure


def structure_state(df1m: pd.DataFrame, tf_min: int = STRUCT_TF_MIN,
                    n_str: int = SWING_N) -> dict:
    """Confirmed-swing structure on the ``tf_min`` grid, valid as of each close.

    Every array is indexed by STRUCTURE bar and is knowable only from the
    instant ``time[t] + tf_min*60``.
    """
    if tf_min < 1:
        raise ValueError(f"tf_min must be >= 1, got {tf_min}")
    if n_str < 1:
        raise ValueError(f"n_str must be >= 1, got {n_str}")

    hs1 = _load_archived_detector()
    d = resample_ohlcv(df1m, tf_min) if tf_min > 1 else df1m.reset_index(drop=True)
    h = d["high"].to_numpy("float64")
    lo = d["low"].to_numpy("float64")
    c = d["close"].to_numpy("float64")

    sh, sl = hs1._swing_flags(h, lo, n_str)
    (last_h_px, _prev_h_px, last_l_px, _prev_l_px, _lhi, _phi, _lli, _pli,
     is_hh, is_lh, is_hl, is_ll, _new_hi, _new_lo) = hs1._structure_state(
        sh, sl, h, lo, c, n_str)

    return dict(time=d["time"].to_numpy("int64"), close=c,
                last_h_px=last_h_px, last_l_px=last_l_px,
                is_hh=is_hh, is_lh=is_lh, is_hl=is_hl, is_ll=is_ll,
                #: 3N bars for three confirmed swings, +1 so a comparison exists
                warmup=3 * n_str + 1)


def _oneshot(x: np.ndarray) -> np.ndarray:
    """FALSE -> TRUE transitions only (§2.2). A level trigger would emit the
    same standing condition on consecutive bars as if they were new events."""
    return x & ~np.concatenate(([False], x[:-1]))


def event_flags(S: dict) -> dict:
    """The four frozen events of §2.3, on the structure grid.

    Closed and symmetric: from bull structure a break up is continuation and a
    break down is failure; from bear structure, the mirror. Bars satisfying a
    continuation AND a failure condition are reported here and dropped by
    ``events`` -- they arise when the last confirmed low sits above the last
    confirmed high, which is possible in a fast leg.
    """
    H, L, c = S["last_h_px"], S["last_l_px"], S["close"]
    bull = S["is_hh"] & S["is_hl"]
    bear = S["is_ll"] & S["is_lh"]
    with np.errstate(invalid="ignore"):
        raw = {
            "CONT_LONG": bull & np.isfinite(H) & (c > H),
            "FAIL_SHORT": bull & np.isfinite(L) & (c < L),
            "CONT_SHORT": bear & np.isfinite(L) & (c < L),
            "FAIL_LONG": bear & np.isfinite(H) & (c > H),
        }
    out = {k: _oneshot(v) for k, v in raw.items()}
    warm = S["warmup"]
    for v in out.values():
        v[:warm] = False
    out["_conflict"] = ((out["CONT_LONG"] & out["FAIL_SHORT"])
                        | (out["CONT_SHORT"] & out["FAIL_LONG"]))
    return out


# -------------------------------------------------------------- forward returns


def _locate(times: np.ndarray, target: np.ndarray, tol_s: int = 60) -> np.ndarray:
    """Index of the bar at ``target``, or -1. By TIMESTAMP, never by arithmetic.

    The 1m cache has gaps. ``j + h`` would step over one and silently measure a
    shorter horizon than the one it is labelled with, which is the kind of error
    that produces a real-looking effect out of nothing.
    """
    j = np.searchsorted(times, target, side="left")
    ok = j < times.size
    j_safe = np.where(ok, np.minimum(j, times.size - 1), 0)
    ok &= np.abs(times[j_safe] - target) <= tol_s
    return np.where(ok, j_safe, -1)


def events(df1m: pd.DataFrame, symbol: str, *, tf_min: int = STRUCT_TF_MIN,
           n_str: int = SWING_N) -> pd.DataFrame:
    """One row per event, with forward signed returns at every frozen horizon.

    ``t0`` is the open of the first 1m bar at or after the structure bar's close
    (§3.1) -- the first instant the event is knowable. Never the structure
    bar's own close, never backdated.
    """
    S = structure_state(df1m, tf_min, n_str)
    F = event_flags(S)

    t1 = df1m["time"].to_numpy("int64")
    o1 = df1m["open"].to_numpy("float64")
    c1 = df1m["close"].to_numpy("float64")
    trad = np.asarray(tradable_mask(df1m), "bool")

    rows = []
    for name in EVENTS:
        idx = np.flatnonzero(F[name] & ~F["_conflict"])
        if not idx.size:
            continue
        # the structure bar closes at time[t] + tf; that is when it is knowable
        knowable = S["time"][idx] + tf_min * 60
        j0 = _locate(t1, knowable)
        keep = (j0 >= 0) & trad[np.maximum(j0, 0)]
        idx, j0 = idx[keep], j0[keep]
        if not idx.size:
            continue
        rows.append(pd.DataFrame(dict(
            symbol=symbol, event=name, direction=DIRECTION[name],
            struct_i=idx, t0=t1[j0], p0=o1[j0])))

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


def in_split(ev: pd.DataFrame, split: tuple[int, int], horizon_min: int) -> pd.DataFrame:
    """Events measurable ENTIRELY inside ``split`` at this horizon (§3.3).

    ``t0 + h <= split_end`` is load-bearing, not tidiness. Without it a +1d
    event near the end of TRAIN reads VALID prices, and one near the end of
    VALID reads TEST prices -- which would break the TEST lock outright.
    """
    if horizon_min not in HORIZONS_MIN:
        raise ValueError(f"horizon {horizon_min} is not pre-declared; "
                         f"{HORIZONS_MIN} are")
    lo, hi = split
    if hi > TEST_START:
        raise ValueError(
            f"split ends at {hi} which is at or past the locked TEST boundary "
            f"{TEST_START}. TEST is never computed.")
    t0 = ev["t0"].to_numpy("int64")
    m = (t0 >= lo) & (t0 + horizon_min * 60 <= hi) & np.isfinite(ev[f"y_{horizon_min}"])
    return ev.loc[m].reset_index(drop=True)


# ------------------------------------------------------------------- estimation


def day_cluster(t0: np.ndarray) -> np.ndarray:
    """§4.1 -- calendar UTC day, POOLED ACROSS SYMBOLS.

    Absorbs both dependence structures H-NULL-1's 50-bet episodes cannot see:
    events hours apart share almost their whole +1d return window, and four
    events at one timestamp on four correlated symbols are close to one
    observation rather than four.
    """
    return np.asarray(t0, "int64") // 86400


def estimate(y: np.ndarray, t0: np.ndarray) -> dict:
    """Effect, cluster SE, t, 95% CI and MDE. Cluster is PRIMARY (§4)."""
    y = np.asarray(y, "float64")
    if y.size == 0:
        return dict(n=0, effect=np.nan, se=np.nan, t=np.nan,
                    ci_low=np.nan, ci_high=np.nan, mde=np.nan)
    r = inference(y, cluster_id=day_cluster(t0))
    se = r["se_cluster"]          # explicit: inference() defaults to block
    eff = r["mean"]
    return dict(n=int(y.size), effect=eff, se=se,
                t=eff / se if se and np.isfinite(se) else np.nan,
                ci_low=eff - 1.96 * se, ci_high=eff + 1.96 * se,
                mde=MDE_K * se, n_clusters=int(np.unique(day_cluster(t0)).size),
                se_iid=r["se_iid"], win_rate=float((y > 0).mean()),
                median=float(np.median(y)))


def control(y: np.ndarray, direction: np.ndarray, symbol: np.ndarray, *,
            n_perm: int = CONTROL_PERMUTATIONS, seed: int = CONTROL_SEED) -> dict:
    """§5 -- timestamp-matched direction permutation, within symbol.

    Preserves symbol, timestamp and the exact multiset of directions; randomizes
    only which event gets which direction. A fair coin would NOT reproduce a
    direction imbalance, so with any drift in the window the drift would leak
    into the signal as though it were structure. Permuting the observed labels
    reproduces the imbalance exactly, and the drift cancels.
    """
    y = np.asarray(y, "float64")
    d = np.asarray(direction, "float64")
    if y.size == 0:
        return dict(mean=np.nan, ci_low=np.nan, ci_high=np.nan, p_value=np.nan)
    raw = y * d                                    # unsigned return per event
    rng = np.random.default_rng(seed)
    sym = np.asarray(symbol)
    groups = [np.flatnonzero(sym == s) for s in np.unique(sym)]

    obs = float(y.mean())
    means = np.empty(n_perm)
    for i in range(n_perm):
        perm = d.copy()
        for g in groups:
            perm[g] = rng.permutation(d[g])
        means[i] = float((raw * perm).mean())
    return dict(mean=float(means.mean()),
                ci_low=float(np.quantile(means, 0.025)),
                ci_high=float(np.quantile(means, 0.975)),
                p_value=float((np.abs(means) >= abs(obs)).mean()),
                n_perm=n_perm, seed=seed)


def family_frame(ev: pd.DataFrame, family: str) -> pd.DataFrame:
    if family not in FAMILIES:
        raise ValueError(f"family must be one of {tuple(FAMILIES)}, got {family!r}")
    return ev[ev["event"].isin(FAMILIES[family])].reset_index(drop=True)


OUT = OUT_DIR / "hstructure2"
