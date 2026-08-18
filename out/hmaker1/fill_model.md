# H-MAKER-1 — FILL MODEL  (Q1)

> Can a realistic resting limit order actually fill?

## The three quantities, kept separate

| quantity | value | what it means |
|---|---:|---|
| **1. touch rate** | 84.6% | price reached our limit. **This is not a fill rate.** |
| **2. simulated fill rate** | 22.9% – 22.9% | reconstructed book/trade sequence implies a fill. A BOUND. |
| **3. actual fill rate** | **not measurable** | no real order was placed. Not approximated. |

**The gap between (1) and (2) is 61.8 percentage points.** That gap is
the entire reason this experiment was run. The feasibility phase could only
see the touch rate from OHLC and said so; here the two are measured side by
side against real book and trade data.

## Fill statistics

| | conservative | optimistic |
|---|---:|---:|
| orders | 840 | 840 |
| fills | 192 | 192 |
| fill rate | 22.9% | 22.9% |
| partial fills | 0 | 0 |
| partial-fill rate | 0.0% | 0.0% |
| median time to fill | 22.7s | 22.7s |
| p90 time to fill | 51.5s | 51.5s |
| median queue ahead | 4,852 | 1,644 |
| median spread | 0.53 bps | — |

Partial fills are structurally impossible with a 1-contract order, and the
measured rate confirms it rather than being interpreted.

## Per symbol (conservative)

| symbol | orders | touch | fill | median time to fill |
|---|---:|---:|---:|---:|
| BTCUSD | 210 | 96.2% | 24.8% | 21.8s |
| ETHUSD | 210 | 96.2% | 31.4% | 20.8s |
| SOLUSD | 210 | 82.4% | 25.2% | 24.8s |
| XRPUSD | 210 | 63.8% | 10.0% | 15.1s |

## Queue position — the limitation, stated plainly

**Exact queue position cannot be reconstructed from Delta's public feed.**
Three measured reasons, recorded in the pre-registration before collection:

1. **No order count.** Levels carry aggregate size only. We know how much
   rests at a price, never how many orders. Queue position is expressible
   in size ahead, never in orders ahead.
2. **Coalescing.** `l2_orderbook` arrives at ~1 Hz with sequence deltas of
   2–4, so two to three book updates are skipped between snapshots. Event
   ordering inside a one-second window is unobservable.
3. **Cancellations are not attributable.** A fall in aggregate size means
   someone cancelled, but not whether they were ahead of us (which helps)
   or behind us (which does not) — and the net figure also absorbs new
   orders joining behind us.

Hence a bound rather than an estimate. The conservative model assumes no
cancellation ever helps us; the optimistic model assumes every one does.
**No midpoint is reported, because the feed does not contain one.**
