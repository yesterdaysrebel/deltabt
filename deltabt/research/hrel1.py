"""H-REL-1 -- does a BTC shock predict an under-responding follower's price?

PRE-REGISTERED. Frozen before any event was counted:
``out/hrel1/hrel1_preregistration.md``
sha256 0711bb59aa7e07779080e6c618a16b1ece0aa206ecc8e23c65ca8ec8d10fd586

Hypothesis 3 of 3 -- the last in the phase.

THE FORMULATION, CHOSEN IN ADVANCE
    The protocol offers four candidate formulations and requires exactly one be
    selected before TRAIN. This is the LEAD-LAG one: BTC makes an unusually
    large 15m move, a follower does not move as far in the same direction, and
    the question is whether the follower closes the gap.

    It is directional by construction -- the prediction's sign is the leader's
    sign, so nothing about direction is fitted -- and it needs ONE new
    threshold. Relative-strength divergence and continuation would each need a
    strength measure, a lookback and a divergence threshold, which is the
    boundary between a hypothesis and a parameter search.

    BTC is designated leader a priori: largest asset, venue reference. It is
    NOT chosen by trying all four and keeping whichever leads best -- that
    would be a four-arm search reported as one hypothesis.

THE ONE NEW NUMBER
    The 95th percentile defining a shock. Conventional tail definition, and the
    mirror of the 20th percentile H-Compress-1 froze for the low tail. Not
    swept. The estimator computing it is imported unchanged, and its defining
    property is that the window ends at t-1 and excludes t, so a bar can never
    help decide whether it is itself unusual.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from deltabt.config import OUT_DIR
from deltabt.data.quality import tradable_mask
from deltabt.research.hcompress import _rolling_quantile_causal
# machinery, imported unchanged -- hstructure2 is hash-frozen and not edited
from deltabt.research.hstructure2 import (CONTROL_PERMUTATIONS, CONTROL_SEED,
                                          DATA_END, HORIZONS_MIN, MDE_K,
                                          PRIMARY_HORIZON_MIN, STUDY,
                                          TEST_START, TRAIN, VALID, _locate,
                                          control, day_cluster, estimate,
                                          in_split)
from deltabt.strategy import resample_ohlcv

TF_MIN = 15
PCT_LOOKBACK = 960
SHOCK_PERCENTILE = 0.95

LEADER = "BTCUSD"
FOLLOWERS = ("ETHUSD", "SOLUSD", "XRPUSD")
SYMBOLS = (LEADER,) + FOLLOWERS

EVENTS = ("LAG_UP", "LAG_DOWN")
FAMILIES = {"R1-LAG": EVENTS}
DIRECTION = {"LAG_UP": 1, "LAG_DOWN": -1}

#: §3.1 -- the event universe has three symbols because the leader cannot lag
#: itself, so the protocol's 3-of-4 is unreachable. Declared before TRAIN.
SYMBOLS_REQUIRED_A5 = 2

OUT = OUT_DIR / "hrel1"


def bars15(df1m: pd.DataFrame) -> pd.DataFrame:
    d = resample_ohlcv(df1m, TF_MIN).reset_index(drop=True)
    c = d["close"].to_numpy("float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.concatenate(([np.nan], np.log(c[1:] / c[:-1])))
    return pd.DataFrame({"time": d["time"].to_numpy("int64"), "close": c, "r": r})


def leader_shock(lead15: pd.DataFrame) -> pd.DataFrame:
    """|r_BTC| against its own causal trailing 95th percentile."""
    a = np.abs(lead15["r"].to_numpy("float64"))
    thr = _rolling_quantile_causal(a, PCT_LOOKBACK, SHOCK_PERCENTILE)
    out = lead15.copy()
    out["abs_r"] = a
    out["threshold"] = thr
    out["shock"] = np.isfinite(thr) & (a >= thr)
    return out


def event_flags(lead: pd.DataFrame, foll15: pd.DataFrame) -> pd.DataFrame:
    """Events for one follower, on the timestamps both series actually have.

    Inner join, never forward-fill: a filled follower bar would carry a price
    from before the shock and would look like a follower that failed to move.
    """
    m = lead.merge(foll15, on="time", how="inner", suffixes=("_l", "_f"))
    rl = m["r_l"].to_numpy("float64")
    rf = m["r_f"].to_numpy("float64")
    sign = np.sign(rl)

    with np.errstate(invalid="ignore"):
        under = sign * (rl - rf) > 0
    live = m["shock"].to_numpy("bool") & under & np.isfinite(rf) & (sign != 0)

    up = live & (sign > 0)
    dn = live & (sign < 0)

    def oneshot(x):
        return x & ~np.concatenate(([False], x[:-1]))

    warm = min(PCT_LOOKBACK, len(m))
    up, dn = oneshot(up), oneshot(dn)
    up[:warm] = False
    dn[:warm] = False
    return pd.DataFrame({"time": m["time"].to_numpy("int64"),
                         "LAG_UP": up, "LAG_DOWN": dn,
                         "r_leader": rl, "r_follower": rf,
                         "gap": sign * (rl - rf)})


def events(foll_1m: pd.DataFrame, follower: str, lead: pd.DataFrame) -> pd.DataFrame:
    """One row per event for ``follower``, with forward signed returns.

    ``lead`` is the output of ``leader_shock`` and is computed once for BTC, not
    per follower -- recomputing it would leave three chances for the leader's
    own definition to drift apart.
    """
    if follower == LEADER:
        raise ValueError(f"{LEADER} is the leader and cannot be a follower; "
                         f"it cannot lag itself")
    F = event_flags(lead, bars15(foll_1m))

    t1 = foll_1m["time"].to_numpy("int64")
    o1 = foll_1m["open"].to_numpy("float64")
    c1 = foll_1m["close"].to_numpy("float64")
    trad = np.asarray(tradable_mask(foll_1m), "bool")

    rows = []
    for name in EVENTS:
        idx = np.flatnonzero(F[name].to_numpy("bool"))
        if not idx.size:
            continue
        j0 = _locate(t1, F["time"].to_numpy("int64")[idx] + TF_MIN * 60)
        keep = (j0 >= 0) & trad[np.maximum(j0, 0)]
        idx, j0 = idx[keep], j0[keep]
        if not idx.size:
            continue
        rows.append(pd.DataFrame(dict(
            symbol=follower, event=name, direction=DIRECTION[name],
            t0=t1[j0], p0=o1[j0],
            r_leader=F["r_leader"].to_numpy()[idx],
            r_follower=F["r_follower"].to_numpy()[idx],
            gap=F["gap"].to_numpy()[idx])))

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
