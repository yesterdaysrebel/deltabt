# H-MAKER-1 — COLLECTION REPORT

Pre-registration sha256 `c079c7a5244ba5a2665fcd6ad4d35f7f0fbcac06e1e6fb32ebaee78c4a5358c8`.
Read-only recording. **No order was ever placed.**

## What was captured

| symbol | L2 snapshots | trade prints |
|---|---:|---:|
| BTCUSD | 6,293 | 11,949 |
| ETHUSD | 6,292 | 7,561 |
| SOLUSD | 6,269 | 1,451 |
| XRPUSD | 6,260 | 618 |

- feed files: feed_1787037882.jsonl.gz
- gaps longer than 5s: **0**
- paper orders generated: **840**

## Submission policy (frozen, signal-free)

    one order per symbol every 30s
    side alternates by sequence position alone
    limit = best bid (BUY) / best ask (SELL), joining the back of the queue
    size 1 contract, lifetime 60s

The side depends on the order's index and nothing else. No price, no
volatility, no book state and no clock feature enters the decision. That
is what keeps this an execution measurement rather than a strategy.

## Sample targets

| target | required | achieved | met |
|---|---:|---:|---|
| resting orders | 600 | 840 | YES |
| credible fills | 400 | 192 | **NO** |

The targets were frozen before collection. Collection was not stopped
early because the estimate looked favourable, and not extended because it
looked unfavourable.
