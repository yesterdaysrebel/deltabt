# H-MAKER-1 — FINAL VERDICT

> Can passive execution produce a sufficiently low and measurable trading
> cost?

Pre-registration sha256 `c079c7a5244ba5a2665fcd6ad4d35f7f0fbcac06e1e6fb32ebaee78c4a5358c8`, frozen before any
order was simulated. Kill threshold **5.54 bps**, primary horizon **+1m**, both frozen.

---

## What was measured

| | conservative | optimistic |
|---|---:|---:|
| resting orders | 840 | 840 |
| touch rate | 84.6% | 84.6% |
| **simulated fill rate** | **22.9%** | **22.9%** |
| fills | 192 | 192 |
| partial-fill rate | 0.0% | 0.0% |
| median time to fill | 22.7s | 22.7s |
| adverse selection @ +1m | +0.221 bps | +0.221 bps |
| 95% CI | [-0.231, +0.673] | [-0.231, +0.673] |
| clusters | 73 | 73 |
| MDE | 0.646 bps | 0.646 bps |

## The frozen decision

    sample targets   orders 840/600  fills 192/400  -> NOT MET
    conservative     PASS
    optimistic       PASS
    bounds agree     True

## The one number this experiment existed to correct

    touch rate               84.6%
    simulated fill rate      22.9%  (conservative)
                             22.9%  (optimistic)
    gap                      61.8%

The feasibility phase could only see the touch rate from OHLC, said so, and
declined to lean on it. That caution was warranted: a touch is not a fill,
and the difference is not a rounding detail.

---

## PATH B — INCONCLUSIVE

The available execution data cannot establish the economics reliably.

### The precise measurement limitation

- **Sample targets not met.** 840 orders against a frozen target of 600, and 192 fills against 400. The targets were frozen before collection and are not lowered now to manufacture a verdict.


### What the data DOES establish, precisely

The verdict is INCONCLUSIVE because of Q1, not Q2. It would be wrong to
read it as "adverse selection could not be measured":

    adverse selection @ +1m   +0.221 bps
    95% CI                     [-0.231, +0.673]
    MDE                         0.646 bps
    kill threshold              5.54 bps
    CI upper is                 8.2x BELOW the threshold

That is a tight measurement on 190 fills across 73 clusters —
the MDE is under a basis point. Adverse selection on filled passive
orders is small, and nothing in this data suggests otherwise.

**The binding limitation is the fill rate.** At 22.9%, 840 orders
produced only 192 fills against a frozen target of 400.

### The queue ambiguity turned out not to matter

The pre-registration warned that cancellations cannot be attributed to a
queue position, and bounded the fill rate rather than estimating it. On
this data the bound is real but non-binding: the two models disagree on
queue depth for **241 of 840 orders**, and on the fill outcome for
**zero**. Unfilled orders sit a median **3,882 contracts** short of
clearing — a gap no plausible cancellation attribution closes.

So the one thing the feed could not resolve does not affect the answer.

### What is NOT concluded

INCONCLUSIVE is not converted to PASS by adopting the optimistic bound,
and not converted to FAIL because the result is inconvenient. Both were
forbidden in advance.

### No rescue cycle

Per the governing instruction, the limitation is identified and **no
further research cycle is created to rescue the hypothesis.** No
H-MAKER-2, no alternative execution assumption, no relaxed threshold.
Any continuation requires explicit operator authorisation.
