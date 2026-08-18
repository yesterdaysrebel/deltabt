# H-MAKER-1 — PRE-REGISTRATION

**Passive maker execution: can a resting limit order fill, and what does being
filled cost?**

This is an EXECUTION MEASUREMENT experiment. It is not a market-prediction
experiment and must not become one. No indicator, no signal, no direction
forecast, no entry/exit/stop/target optimisation, no VALID, no TEST.

Authorised by the feasibility phase
(`out/phase_discovery/feasibility_report.md`), which found Path B the only
surviving path and made it conditional on this measurement.

Frozen before any order was simulated and before any fill was recorded.

---

## 1. THE ONE QUESTION

> Can passive execution produce a sufficiently low and measurable trading cost?

Two sub-questions, and nothing else:

    Q1  Can a realistic resting limit order actually fill?
    Q2  What is the economic adverse selection of those actual fills?

---

## 2. FROZEN CONTEXT

    19 recorded market experiments, 0 positive economic verdicts
    largest measured directional effect            3.74 bps
    taker round trip                              15.80 bps
    maker both legs                                4.72 bps
    maker + OHLC adverse-selection allowance       7.17 bps
    maker saving vs taker                         11.08 bps
    PRE-DECLARED KILL THRESHOLD                    5.54 bps per maker leg

5.54 bps is exactly half the 11.08 bps saving: adverse selection at that level
on both legs erases the entire advantage. **This number does not move after
seeing data.**

---

## 3. WHAT THE VENUE FEED ACTUALLY PROVIDES — MEASURED, NOT ASSUMED

Measured live from `wss://socket.india.delta.exchange` on 2026-08-18 over a
75-second probe, before this document was written. These facts determine what
can and cannot be established, so they are recorded here rather than discovered
later.

### 3.1 `l2_orderbook`

- A **full snapshot**, not an incremental delta stream. 2,713 bid and 1,851 ask
  levels on BTCUSD.
- Delivered at **~1 Hz**: 77 snapshots in 75 s; inter-snapshot gap median
  1000.7 ms, p90 1013.1 ms, max 1038.4 ms.
- Carries `last_sequence_no`. Sequence deltas between consecutive snapshots:
  median **3**, max **4**. The feed is therefore **COALESCED** — two to three
  book updates are skipped between every snapshot we receive.
- Level schema: `{"limit_price", "size", "depth"}`. `size` is the **aggregate**
  quantity at that price. **There is no order count and no per-order
  breakdown.**

### 3.2 `all_trades`

- Individual prints with **microsecond** timestamps.
- Carries `buyer_role` / `seller_role` ∈ {maker, taker}, so **the aggressor side
  is known**. `buyer_role=maker, seller_role=taker` is an aggressive SELL that
  hit the bid.
- Event-driven, not coalesced.

### 3.3 What follows, and it is a limitation not a nuisance

**Exact queue position cannot be reconstructed from this feed.** Three distinct
reasons, none of which can be engineered away:

1. **No order count.** We know 2,360 contracts rest at a price; we do not know
   whether that is 2 orders or 200. Queue position can therefore be expressed
   in SIZE AHEAD but never in ORDERS AHEAD.
2. **Coalescing.** With 2–3 updates skipped per snapshot, the ordering of
   events inside a 1-second window is unobservable. A cancel and a trade in the
   same window cannot be sequenced.
3. **Cancellations are not identifiable by position.** If aggregate size at our
   level falls by 100 with no trade, someone cancelled — but we cannot tell
   whether they were ahead of us (which helps) or behind us (which does not),
   and a net change also mixes in new orders joining behind us.

**This experiment therefore does not estimate a fill rate. It BOUNDS one.** See
§6. Manufacturing a point estimate here would be manufacturing precision, and
the pre-registration forbids it in advance so that a tempting mid-point cannot
be adopted later.

---

## 4. COLLECTION

### 4.1 What is recorded

Per symbol, continuously, for the frozen collection window:

    L2 snapshot     timestamp, last_sequence_no, best bid, best ask, spread,
                    and the top 25 levels each side (limit_price, size, depth)
    trade print     timestamp, price, size, buyer_role, seller_role
                    -> aggressor side derived, never guessed

Raw feed messages are written to disk before any processing, so the analysis can
be re-run without re-collecting.

### 4.2 Paper orders — NO REAL ORDERS ARE PLACED

There is no order-placement code anywhere in this repository and none is added.
`tests/live/test_no_live_trading.py` enforces that against the shipped source
and must continue to pass.

Submission policy, frozen, and deliberately **signal-free**:

    every 30 s per symbol, alternating side (BUY, SELL, BUY, ...)
    limit price = the current best bid (BUY) / best ask (SELL)
    size        = 1 contract   (the minimum; size effects are NOT studied)
    lifetime    = 60 s, then cancelled
    the alternation is by sequence position only -- it does not depend on price,
    volatility, book state, time of day or anything else

Alternating strictly by count guarantees a balanced BUY/SELL sample without any
predictive input. **If the submission rule ever consults a market variable to
decide side, price or timing, this has become a strategy experiment and must
stop.**

Posting at the touch — joining the back of the existing queue at the best
price — is the canonical passive order and is exactly what the 4.72 bps maker
economics in the feasibility report assume.

### 4.3 Sample targets, frozen

    ~600 resting orders   for fill-rate precision
    ~400 credible fills   for adverse-selection precision

Collection stops when the order target is met **and** every live order has
passed its markout horizon. Collection is **not** stopped early because the
estimate looks favourable, and **not** extended because it looks unfavourable.

If the fill target is not reached at the order target, that is reported as a low
fill rate — it is a result, not a reason to keep collecting.

### 4.4 Missing data

- A gap in the L2 stream longer than 5 s voids every order live at the time;
  they are excluded and counted, never repaired by interpolation.
- An order whose full markout horizon is not observed is excluded **at that
  horizon only**, and the exclusion count is reported per horizon.
- Reconnections are logged. Orders spanning a reconnection are voided.

---

## 5. DEFINITIONS — FIXED BEFORE COLLECTION

### 5.1 The three quantities that must never be conflated

    1. TOUCH RATE          the best price reached or crossed our limit price.
                           This is NOT a fill rate and is reported only to
                           expose how misleading it is.

    2. SIMULATED FILL RATE the reconstructed book/trade sequence implies the
                           order would have filled. Reported as a BOUND.

    3. ACTUAL FILL RATE    a real order was really filled. NOT MEASURABLE here.
                           No real orders are placed. This is reported as
                           unavailable, not approximated.

### 5.2 Queue model

At submission of an order at price `p`:

    size_ahead(0) = aggregate size at level p in the most recent L2 snapshot

We join the back of that queue. Thereafter, per elapsed interval:

    consumed_by_trades = total traded size at price p with the opposing
                         aggressor (for a resting BUY at the bid: aggressive
                         SELL prints at p or below)

    net_book_change    = size at p now - size at p before + consumed_by_trades
                         (negative => cancellations occurred at this level)

Two bounds, both computed, neither preferred:

    CONSERVATIVE  size_ahead shrinks ONLY by consumed_by_trades.
                  Cancellations are assumed to be behind us, so they never
                  help. This is a LOWER bound on the fill rate.

    OPTIMISTIC    size_ahead shrinks by consumed_by_trades AND by every
                  observed cancellation, all assumed to be ahead of us.
                  This is an UPPER bound on the fill rate.

A fill occurs the first time `size_ahead <= 0` and a trade occurs at our price
(queue priority alone does not fill an order; a counterparty must trade).

**Partial fills.** Fill quantity is `min(order_size, traded_size_after_queue_
cleared)`. With a 1-contract order, a partial fill is possible only if the
clearing trade is smaller than 1 contract, which the contract specification does
not permit — so the partial-fill rate is expected to be structurally 0 and is
reported to confirm that, not to be interpreted.

**Price improvement is not modelled.** An order resting at the bid does not fill
at a better price. Any real price improvement would make maker execution look
better than reported, so this is the conservative direction.

### 5.3 Adverse selection — THE PRIMARY METRIC

Per filled maker leg, in basis points, using **signed markout against the mid**:

    m(0)  = mid price at the moment of fill
    m(h)  = mid price h minutes after the fill

    signed_markout(h) = side * (m(h) - m(0)) / m(0) * 10_000
                        side = +1 for a passive BUY, -1 for a passive SELL

    adverse_selection(h) = -signed_markout(h)

So a passive BUY followed by a price **decline** yields positive adverse
selection, and a passive SELL followed by a **rise** likewise. Positive means
the fill hurt us. This is the sign convention the kill threshold is stated in.

Markout horizons, all reported: **+1m, +5m, +15m**.

    PRIMARY HORIZON = +1m

Declared now, before collection, so that "one of three horizons was fine" cannot
become the result. +1m is chosen because adverse selection is a property of the
fill event, and at +5m and +15m the measurement is increasingly dominated by
ordinary price variance rather than by the information in the fill. The other
two horizons are reported as supporting evidence and are subject to Bonferroni
×3 if ever cited as the basis for a verdict.

Mid price is `(best_bid + best_ask) / 2` from the nearest L2 snapshot at or
after the target instant, located by **timestamp**, never by index arithmetic.

### 5.4 Why markout against the MID and not against the fill price

Measuring against our own fill price would credit us the half-spread we earned
by resting, and would flatter passive execution. The half-spread is already
counted in the 4.72 bps fee arithmetic. Measuring the fill's information content
against the mid keeps the two separate and is the conservative choice.

---

## 6. INFERENCE

Ratified production hierarchy from H-NULL-1
(`out/hnull1/inference_promotion.json`), used unchanged:

    PRIMARY               cluster
    SECONDARY DIAGNOSTIC  moving-block bootstrap
    DIAGNOSTIC            iid

**An iid standard error is not used as the headline merely because it is
convenient.** Orders 30 seconds apart on the same symbol overlap in their
markout windows and respond to the same order flow; treating them as independent
would understate uncertainty exactly as it did in H-REL-1.

    PRIMARY cluster unit = (symbol, 5-minute bucket)

Declared in advance. A 5-minute bucket at a 30-second submission cadence groups
~10 consecutive orders per symbol, which is the scale over which book state and
order flow persist. `hnull1.inference()` is called unchanged and `se_cluster` is
read explicitly, because that function predates the ratification and still
defaults `se` to the block estimator.

    MDE = 2.8 * SE_cluster

reported beside every null claim, as H-NULL-1's Gate 5 requires.

Reported for the primary metric: point estimate, 95% CI, number of fills, number
of orders, fill rate, partial-fill rate — separately under the CONSERVATIVE and
OPTIMISTIC queue bounds.

---

## 7. THE DECISION RULE — FROZEN

Let `AS_hi` be the upper bound of the 95% CI of adverse selection at +1m, taken
under the **conservative** queue model (fewer fills, and the fills that do occur
are the ones the queue actually cleared).

    PASS          AS_hi < 5.54 bps
                  AND the fill model is supported: the conservative and
                  optimistic fill rates are both > 0 and the sample meets
                  the frozen targets

    FAIL          the lower bound of the 95% CI is >= 5.54 bps

    INCONCLUSIVE  the CI straddles 5.54 bps, OR the sample targets are not
                  met, OR the conservative and optimistic bounds are so far
                  apart that they imply different verdicts

**INCONCLUSIVE is not converted to PASS by choosing the optimistic bound. It is
not converted to FAIL because the result is inconvenient.** If the two queue
bounds disagree on the verdict, the verdict is INCONCLUSIVE and the reason is
named.

---

## 8. FORBIDDEN

EMA, WPR, ADX, Supertrend, market structure, momentum, mean reversion,
order-flow alpha, signal discovery, predictive feature engineering, parameter
sweeps, strategy backtests, LONG-vs-SHORT optimisation, entry/exit/stop/target
optimisation, VALID, TEST, new hypothesis families, H-MAKER-2, and "let's try
another execution assumption" after seeing the result.

If queue position turns out not to be estimable within the declared bounds, the
required action is to **stop and report that fact**, not to invent a new
estimator.

---

## 9. WHAT WOULD MAKE THIS EXPERIMENT INVALID

Stated in advance:

- If the submission rule is changed after collection begins.
- If the kill threshold is moved.
- If the primary horizon or the primary queue bound is reselected after seeing
  results.
- If a real order is ever placed.
- If the cluster definition is changed to obtain a narrower interval.

---

## 10. OUTPUT

    out/hmaker1/preregistration.md        this document
    out/hmaker1/preregistration.sha256    its hash
    out/hmaker1/collection_report.md      what was actually captured
    out/hmaker1/fill_model.md             Q1, with its limitations stated
    out/hmaker1/adverse_selection.md      Q2
    out/hmaker1/statistical_report.md     inference, MDE, bounds
    out/hmaker1/final_verdict.md          exactly one of PASS / FAIL / INCONCLUSIVE

One append-only registry record. No historical entry is modified.
