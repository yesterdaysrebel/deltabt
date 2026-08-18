"""H-MAKER-1 -- offline paper-order simulation and adverse-selection measurement.

PRE-REGISTERED. Frozen before any order was simulated:
``out/hmaker1/preregistration.md``
sha256 c079c7a5244ba5a2665fcd6ad4d35f7f0fbcac06e1e6fb32ebaee78c4a5358c8

NO ORDERS ARE PLACED. This module reads a recorded feed from disk. It contains
no exchange connection, no authentication and no send path of any kind.

WHAT THIS MODULE CANNOT DO, STATED UP FRONT
    It cannot compute an actual fill rate, because no real order was ever
    placed. It cannot compute an exact simulated fill rate either, for three
    measured reasons recorded in the pre-registration: the feed carries
    aggregate size with NO ORDER COUNT, snapshots are coalesced at ~1 Hz with
    2-3 book updates skipped between them, and a fall in aggregate size cannot
    be attributed to a cancellation ahead of us rather than behind us.

    It therefore BOUNDS the fill rate between a conservative model (only trades
    consume our queue) and an optimistic one (every observed cancellation is
    ahead of us). Both are computed and neither is preferred. Reporting a
    midpoint would be manufacturing precision the feed does not contain.

THE SUBMISSION RULE IS SIGNAL-FREE BY CONSTRUCTION
    One order per symbol every 30 s, side alternating by sequence position
    alone. It consults no price, no volatility, no book state and no clock
    feature. If it ever does, this has become a strategy experiment.
"""

from __future__ import annotations

import gzip
import json
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from deltabt.config import OUT_DIR

OUT = OUT_DIR / "hmaker1"

# --------------------------------------------------------------- frozen inputs

SYMBOLS = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")
SUBMIT_EVERY_S = 30.0
ORDER_LIFETIME_S = 60.0
ORDER_SIZE = 1
MARKOUT_MIN = (1, 5, 15)
PRIMARY_MARKOUT_MIN = 1
KILL_THRESHOLD_BPS = 5.54
CLUSTER_BUCKET_S = 300
MDE_K = 2.8

#: §4.4 -- a feed gap longer than this voids every order live at the time.
MAX_GAP_S = 5.0

MODES = ("conservative", "optimistic")


# ------------------------------------------------------------------ feed load


@dataclass
class Book:
    ts: list = field(default_factory=list)        # recv seconds, float
    bid: list = field(default_factory=list)       # best bid price
    ask: list = field(default_factory=list)
    levels: list = field(default_factory=list)    # dict price -> size, our side view


@dataclass
class Feed:
    books: dict = field(default_factory=dict)     # symbol -> Book
    trades: dict = field(default_factory=dict)    # symbol -> list of (ts, px, sz, aggressor)
    gaps: list = field(default_factory=list)
    session: dict = field(default_factory=dict)
    truncated: list = field(default_factory=list)


def _aggressor(buyer_role, seller_role) -> int:
    """+1 an aggressive BUY lifted the offer, -1 an aggressive SELL hit the bid.

    Derived from the venue's own maker/taker labels, never guessed from an
    uptick rule.
    """
    if seller_role == "taker":
        return -1
    if buyer_role == "taker":
        return 1
    return 0


def load_feed(paths) -> Feed:
    f = Feed()
    for s in SYMBOLS:
        f.books[s] = Book()
        f.trades[s] = []
    seen_snapshot = set()
    last_recv = None

    for p in sorted(Path(x) for x in paths):
        # A file still being written has no end-of-stream marker and its last
        # block may be a partial line. Both are MISSING DATA: the tail is
        # dropped, never reconstructed, and the truncation is recorded.
        try:
            with gzip.open(p, "rt") as fh:
                lines = list(fh)
        except EOFError:
            with gzip.open(p, "rt") as fh:
                lines = []
                try:
                    for ln in fh:
                        lines.append(ln)
                except EOFError:
                    pass
            f.truncated.append(str(p.name))
        for line in lines:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                f.truncated.append(f"{p.name}:partial-line")
                continue
            if True:
                t = r.get("type")
                if t == "session":
                    f.session = r
                    continue
                recv = r.get("recv")
                if recv is None:
                    continue
                if last_recv is not None and recv - last_recv > MAX_GAP_S:
                    f.gaps.append((last_recv, recv))
                last_recv = recv
                sym = r.get("symbol")
                if sym not in f.books:
                    continue
                if t == "l2":
                    buy = [(float(px), float(sz)) for px, sz in r["buy"]]
                    sell = [(float(px), float(sz)) for px, sz in r["sell"]]
                    if not buy or not sell:
                        continue
                    b = f.books[sym]
                    b.ts.append(recv)
                    b.bid.append(buy[0][0])
                    b.ask.append(sell[0][0])
                    b.levels.append({**{px: sz for px, sz in buy},
                                     **{px: sz for px, sz in sell}})
                elif t == "trades":
                    # the snapshot on subscribe is history, not live flow
                    if r.get("snapshot"):
                        if sym in seen_snapshot:
                            continue
                        seen_snapshot.add(sym)
                        continue
                    for px, sz, br, sr, ts in r["trades"]:
                        if px is None or sz is None:
                            continue
                        f.trades[sym].append((float(ts) / 1e6, float(px),
                                              float(sz), _aggressor(br, sr)))
    for s in SYMBOLS:
        f.trades[s].sort(key=lambda x: x[0])
    return f


# ------------------------------------------------------------------- orders


@dataclass
class Order:
    symbol: str
    seq: int
    side: int                # +1 passive BUY, -1 passive SELL
    submit_ts: float
    limit_px: float
    size_ahead0: float
    best_bid: float
    best_ask: float
    expiry_ts: float


def generate_orders(feed: Feed, *, every_s: float = SUBMIT_EVERY_S,
                    lifetime_s: float = ORDER_LIFETIME_S) -> list[Order]:
    """One order per symbol every ``every_s``, side alternating by count.

    The alternation depends on the sequence index and nothing else. No market
    variable is consulted anywhere in this function -- that is what makes the
    sample signal-free rather than a strategy.
    """
    if every_s <= 0 or lifetime_s <= 0:
        raise ValueError("every_s and lifetime_s must be positive")
    orders = []
    for sym in SYMBOLS:
        b = feed.books[sym]
        if len(b.ts) < 2:
            continue
        t0, tend = b.ts[0], b.ts[-1]
        seq = 0
        t = t0
        while t <= tend:
            i = bisect_right(b.ts, t) - 1
            if i < 0:
                t += every_s
                seq += 1
                continue
            side = 1 if seq % 2 == 0 else -1
            px = b.bid[i] if side == 1 else b.ask[i]
            lv = b.levels[i]
            orders.append(Order(symbol=sym, seq=seq, side=side, submit_ts=b.ts[i],
                                limit_px=px, size_ahead0=float(lv.get(px, 0.0)),
                                best_bid=b.bid[i], best_ask=b.ask[i],
                                expiry_ts=b.ts[i] + lifetime_s))
            seq += 1
            t += every_s
    orders.sort(key=lambda o: (o.submit_ts, o.symbol))
    return orders


# --------------------------------------------------------------- simulation


def _trades_between(tr: list, lo: float, hi: float) -> list:
    lo_i = bisect_left(tr, (lo,))
    hi_i = bisect_right(tr, (hi, float("inf"), float("inf"), 9))
    return tr[lo_i:hi_i]


def _cancels_at_level(book: Book, px: float, lo: float, hi: float,
                      traded: float) -> float:
    """Size that left level ``px`` without a trade explaining it.

    NOT attributable to a position in the queue. A net decrease could be an
    order ahead of us cancelling (which helps) or behind us (which does not),
    and a net figure also nets off new orders joining behind us. This is why it
    feeds ONLY the optimistic bound.
    """
    i0 = bisect_left(book.ts, lo)
    i1 = bisect_right(book.ts, hi) - 1
    if i0 >= len(book.ts) or i1 < 0 or i1 <= i0:
        return 0.0
    s0 = book.levels[i0].get(px, 0.0)
    s1 = book.levels[i1].get(px, 0.0)
    return max(0.0, -( (s1 - s0) + traded ))


def simulate(order: Order, feed: Feed, mode: str) -> dict:
    """Resolve one paper order under one queue bound.

    ``conservative`` -- only opposing-aggressor trades at or through our price
    consume the queue ahead of us. Cancellations are assumed to be behind us.
    A LOWER bound on the fill rate.

    ``optimistic`` -- every observed cancellation at our level is assumed to be
    ahead of us as well. An UPPER bound.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    sym = order.symbol
    tr = _trades_between(feed.trades[sym], order.submit_ts, order.expiry_ts)
    px = order.limit_px
    want = -order.side          # a resting BUY is filled by an aggressive SELL

    # a trade "at or through" our price, on the opposing aggressor side
    hits = [t for t in tr if t[3] == want and
            ((order.side == 1 and t[1] <= px) or (order.side == -1 and t[1] >= px))]
    touched = bool(hits)

    ahead = order.size_ahead0
    if mode == "optimistic":
        traded_total = sum(t[2] for t in hits)
        ahead = max(0.0, ahead - _cancels_at_level(
            feed.books[sym], px, order.submit_ts, order.expiry_ts, traded_total))

    cum = 0.0
    for ts, tpx, tsz, _ in hits:
        cum += tsz
        if cum > ahead:
            qty = min(float(ORDER_SIZE), cum - ahead)
            return dict(filled=True, fill_ts=ts, fill_qty=qty,
                        partial=qty < ORDER_SIZE, touched=touched,
                        queue_ahead=ahead, consumed=cum,
                        time_to_fill=ts - order.submit_ts)
    return dict(filled=False, fill_ts=None, fill_qty=0.0, partial=False,
                touched=touched, queue_ahead=ahead, consumed=cum,
                time_to_fill=None)


# ---------------------------------------------------------------- markout


def mid_at(book: Book, ts: float, *, tol_s: float = 5.0) -> float:
    """Mid from the nearest snapshot AT OR AFTER ``ts``. By timestamp only."""
    i = bisect_left(book.ts, ts)
    if i >= len(book.ts) or book.ts[i] - ts > tol_s:
        return float("nan")
    return 0.5 * (book.bid[i] + book.ask[i])


def markouts(order: Order, feed: Feed, fill_ts: float) -> dict:
    """Signed markout and adverse selection, in bps, against the MID.

    Measuring against our own fill price would credit us the half-spread we
    earned by resting, which the 4.72 bps fee arithmetic already counts. Against
    the mid, the two stay separate.
    """
    b = feed.books[order.symbol]
    m0 = mid_at(b, fill_ts)
    out = {"mid_at_fill": m0}
    for h in MARKOUT_MIN:
        mh = mid_at(b, fill_ts + h * 60.0)
        signed = (order.side * (mh - m0) / m0 * 1e4
                  if np.isfinite(m0) and np.isfinite(mh) and m0 > 0 else np.nan)
        out[f"signed_markout_{h}m"] = signed
        out[f"adverse_{h}m"] = -signed
    return out


def voided_by_gap(order: Order, feed: Feed) -> bool:
    """§4.4 -- a feed gap over 5 s voids every order live at the time."""
    return any(not (order.expiry_ts < lo or order.submit_ts > hi)
               for lo, hi in feed.gaps)


def run_all(feed: Feed, orders: list[Order]) -> dict:
    """Every order under both queue bounds. No mode is preferred."""
    rows = {m: [] for m in MODES}
    for o in orders:
        if voided_by_gap(o, feed):
            continue
        for m in MODES:
            r = simulate(o, feed, m)
            rec = dict(symbol=o.symbol, seq=o.seq, side=o.side,
                       submit_ts=o.submit_ts, limit_px=o.limit_px,
                       size_ahead0=o.size_ahead0,
                       spread_bps=1e4 * (o.best_ask - o.best_bid)
                       / (0.5 * (o.best_ask + o.best_bid)), **r)
            if r["filled"]:
                rec.update(markouts(o, feed, r["fill_ts"]))
            rows[m].append(rec)
    return rows


# ---------------------------------------------------------------- inference


def cluster_ids(symbols, submit_ts) -> np.ndarray:
    """§6 -- (symbol, 5-minute bucket). Declared before collection.

    Orders 30 s apart on one symbol overlap in their markout windows and see the
    same order flow; treating them as independent understates uncertainty
    exactly as it did in H-REL-1.
    """
    sym = np.asarray(symbols)
    bucket = (np.asarray(submit_ts, "float64") // CLUSTER_BUCKET_S).astype("int64")
    uniq = {s: i for i, s in enumerate(sorted(set(sym.tolist())))}
    return np.array([uniq[s] for s in sym], "int64") * 10**12 + bucket


def estimate(values, symbols, submit_ts) -> dict:
    """Cluster-primary inference, reusing the H-NULL-1 estimator unchanged."""
    from deltabt.research.hnull1 import inference

    v = np.asarray(values, "float64")
    keep = np.isfinite(v)
    v = v[keep]
    if v.size == 0:
        # every key the populated branch returns, so downstream reporting
        # cannot KeyError on an empty horizon
        return dict(n=0, mean=np.nan, se=np.nan, t=np.nan, ci_low=np.nan,
                    ci_high=np.nan, mde=np.nan, se_iid=np.nan, n_clusters=0)
    cid = cluster_ids(np.asarray(symbols)[keep], np.asarray(submit_ts)[keep])
    r = inference(v, cluster_id=cid)
    se = r["se_cluster"]                    # explicit: inference() defaults to block
    m = r["mean"]
    return dict(n=int(v.size), mean=m, se=se,
                t=m / se if se and np.isfinite(se) else np.nan,
                ci_low=m - 1.96 * se, ci_high=m + 1.96 * se,
                mde=MDE_K * se, se_iid=r["se_iid"],
                n_clusters=int(np.unique(cid).size))


def verdict(ci_low: float, ci_high: float, *, threshold: float = KILL_THRESHOLD_BPS,
            sample_ok: bool = True, bounds_agree: bool = True) -> str:
    """§7 -- frozen. The threshold is a parameter only so tests can exercise it."""
    if not np.isfinite(ci_low) or not np.isfinite(ci_high):
        return "INCONCLUSIVE"
    if not sample_ok or not bounds_agree:
        return "INCONCLUSIVE"
    if ci_high < threshold:
        return "PASS"
    if ci_low >= threshold:
        return "FAIL"
    return "INCONCLUSIVE"
