# H-MAKER-1 — STATISTICAL REPORT

## Inference

Ratified H-NULL-1 hierarchy, used unchanged:

    PRIMARY               cluster
    SECONDARY DIAGNOSTIC  moving-block bootstrap
    DIAGNOSTIC            iid

    cluster unit = (symbol, 5-minute bucket)
    MDE = 2.8 * SE_cluster

Declared before collection. Orders 30 s apart on one symbol overlap in
their markout windows and see the same order flow; an iid standard error
would understate uncertainty exactly as it did in H-REL-1.

`hnull1.inference()` is called unchanged and `se_cluster` is read
explicitly — that function predates the ratification and still defaults
`se` to the block estimator.

## Cluster versus iid, at the primary horizon

| bound | fills | clusters | SE cluster | SE iid | understatement |
|---|---:|---:|---:|---:|---:|
| conservative | 190 | 73 | 0.231 | 0.247 | 0.93× |
| optimistic | 190 | 73 | 0.231 | 0.247 | 0.93× |

## Decision rule (frozen)

    PASS          CI upper < 5.54 bps AND fill model supported
                  AND sample targets met
    FAIL          CI lower >= 5.54 bps
    INCONCLUSIVE  CI straddles 5.54, OR targets not met,
                  OR the two queue bounds imply different verdicts

    conservative bound -> PASS
    optimistic bound   -> PASS
    sample targets met -> False
    FINAL              -> INCONCLUSIVE
