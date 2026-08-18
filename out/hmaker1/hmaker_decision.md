# H-MAKER-DECISION-1 — final economic viability of maker execution

Execution decision on evidence already collected. **No signal was tested, no
hypothesis searched, no parameter optimised, no new data collected.**

---

# Executive decision

## B — MAKER ECONOMICALLY INVIABLE

Under every execution policy that can actually be run, the break-even hurdle
stays above the previously declared feasibility boundary of **7.47 bps**.

The finding does not rest on the unmeasured fill rate. **Even granting a
perfect 100% entry fill rate — a more generous assumption than any measurement
could support — the hurdle is 8.55 bps.** The decision is therefore identifiable
from current evidence, which is why this is B and not C.

---

# What H-MAKER-1 actually established

The two questions must be kept apart, because they came out very differently.

### Q2 — adverse selection: answered, and it is small

    adverse selection @ +1m   +0.221 bps
    95% CI                    [-0.231, +0.673]
    MDE                        0.646 bps
    kill threshold             5.54 bps
    CI upper is 8.2x BELOW the threshold

190 fills across 73 clusters, MDE under a basis point. **Adverse selection is
not the binding constraint and never was.** Nothing below is driven by it — and
the conclusion survives even if adverse selection is set to its CI upper bound
(see Sensitivity).

### Q1 — fill probability: the constraint, but not the decisive one

    touch rate                84.6%
    simulated fill rate       22.9%   (60 s, at the touch)
    gap                       61.7 points

Two queue models disagreed on depth for 241 of 840 orders and on the fill
outcome for **zero**; unfilled orders sat a median 3,882 contracts short of
clearing. So 22.9% is robust as an order of magnitude, though it is a
*simulated* rate, not a production one.

It turns out not to matter, for the reason in the next section.

---

# Execution economics

Per-leg costs, from the frozen model:

    maker leg   f_m + a  =  2.36 + 0.221  =  2.581 bps
    taker leg   f_t + s  =  5.90 + 2.00   =  7.900 bps

| policy | fill probability | base fees | adverse selection | fallback | effective hurdle | verdict |
|---|---|---:|---:|---|---:|---|
| **P0** pure taker (status quo) | n/a | 11.80 | — | n/a | **15.80** | FAIL |
| **P1** idealised passive/passive | n/a | 4.72 | 2×0.221 | *abandon both legs* | **5.16** | PASS — **but unexecutable** |
| **P2** maker entry (abandon) + taker exit | any | 8.26 | 0.221 | none needed | **10.48** | FAIL |
| **P3** both legs passive, cross on miss | 22.9% | blended | blended | taker | **13.36** | FAIL |
| **P4** maker entry (abandon) + exit mix w=36.4% | any | blended | blended | taker on stops | **8.55** | FAIL |

## The structural fact that decides this

**The entry leg can abandon. The exit leg cannot.**

If a passive entry does not fill, you simply hold no position — no fee, no
obligation, nothing lost but the opportunity. If a passive *exit* does not fill,
you are still holding the position. You must eventually close it, and closing it
means crossing. The alternative — a resting limit that may never fill — is an
unbounded loss, not an execution policy.

That is why **P1 passes and cannot be used.** It is the only policy under the
boundary, and it requires an option that does not exist on the exit side.

Every executable policy therefore pays a taker leg on exit with probability
`1 − w`, where `w` is the fraction of exits that are passively executable (a
target being reached, rather than a stop being hit).

---

# Break-even equations

Let

    p  = probability a passive order fills within the observed window
    w  = fraction of exits that execute passively
    M  = f_m + a  = 2.581 bps   (a leg that executes as maker)
    T  = f_t + s  = 7.900 bps   (a leg that crosses)
    G  = expected gross directional edge, bps, before execution

### Abandon-on-miss: fill probability cancels out

Expected net **per signal**:

    E[net] = p · (G − C_maker)

This is positive iff `G > C_maker`. **`p` multiplies both terms and therefore
cannot change the sign.** Fill probability governs *throughput*, not viability —
at 22.9% you need 4.4× as many signals for the same statistical power, which
matters for MDE and capital efficiency, but not for whether the trade is
profitable.

This is the single most important algebraic point, and it is why "22.9% × 4.72
bps = cheap execution" is the wrong calculation in both directions.

### P3 — cross on miss, both legs

    RT(p) = 2 · [ p·M + (1−p)·T ]

    RT(p) ≤ 7.47   ⇒   p ≥ (2T − 7.47) / (2(T − M))   =   0.783

**A 78.3% fill rate is required. Observed is 22.9% — a factor of 3.4.**

### P4 — maker entry with abandon, exit mix w

    RT(w) = M + [ w·M + (1−w)·T ]

    RT(w) ≤ 7.47   ⇒   w ≥ (M + T − 7.47) / (T − M)   =   0.566

**56.6% of exits must be passively executable.**

### Why that requirement is self-defeating

For a system exiting at a 2R target or a 1R stop, `w` *is* the win rate. At
w = 0.566 the gross edge is

    3w − 1 = +0.698 R

At the live median 1R of roughly 100 bps, that is ≈70 bps of edge — **about 19×
the largest effect ever measured in this program (3.74 bps), and that effect was
never established as genuine.**

So maker execution becomes viable only for a strategy so profitable it would not
need the cost saving. The condition defeats itself.

---

# Sensitivity

Evaluated at the required points. **These are sensitivity probes, not
parameters being selected.**

### Fill probability, policy P3 (cross on miss)

| p | round-trip hurdle | vs 7.47 |
|---:|---:|---|
| 10% | 14.74 bps | FAIL |
| **22.9% (observed)** | **13.36 bps** | FAIL |
| 30% | 12.61 bps | FAIL |
| 50% | 10.48 bps | FAIL |
| 100% | 5.16 bps | PASS (degenerates to pure maker) |

### Exit mix, policy P4 (entry abandons — p is irrelevant here)

| w | round-trip hurdle | vs 7.47 |
|---:|---:|---|
| 10% | 9.95 bps | FAIL |
| **36.4% (live win rate)** | **8.55 bps** | FAIL |
| 50% | 7.82 bps | FAIL |
| 56.6% | 7.47 bps | boundary |
| 100% | 5.16 bps | PASS (unexecutable — no stops) |

### The test that settles identifiability

**Grant a perfect entry fill rate, p = 1.00:**

    w = 0.364  ->  8.55 bps    FAIL
    w = 0.500  ->  7.82 bps    FAIL

Even with an entry that always fills as maker, the hurdle stays above the
boundary. **The missing production-fill measurement is therefore not material to
the decision.**

### Robustness to adverse selection

Setting adverse selection to its 95% CI **upper** bound (0.673 bps instead of
0.221) moves P4 at w = 0.364 from 8.55 to **9.16 bps** — worse, still FAIL. The
conclusion is insensitive to the one quantity H-MAKER-1 measured precisely.

---

# Comparison with existing evidence

Minimum hurdle across executable policies: **8.55 bps** (P4 at the observed exit
mix). The nearest executable alternative, P3 at the observed fill rate, is 13.36.

| reference | value | required edge vs reference |
|---|---:|---|
| 0 bps | 0 | hurdle is 8.55 bps above |
| **3.74 bps** — largest measured effect, *never established as genuine* | 3.74 | hurdle is **2.3×** larger |
| **7.47 bps** — declared feasibility boundary | 7.47 | hurdle is **1.14×** above |

The margin against the boundary is 14%, not an order of magnitude. That is
stated plainly rather than dressed up: maker execution is **modestly and
consistently** inviable, not catastrophically so. But it is above the boundary
under every executable policy, at every sensitivity point except the two that
require an option that does not exist (abandoning an exit, or never being
stopped out).

The boundary itself is unchanged at 7.47 bps and was not revisited after seeing
H-MAKER-1.

---

# What is and is not proven

### Proven

- **Adverse selection on filled passive orders is small** — +0.221 bps, CI upper
  0.673, against a 5.54 kill threshold. Measured, not assumed.
- **Passive orders at the touch fill about 22.9% of the time in 60 seconds**, and
  that figure is robust to the queue ambiguity the feed could not resolve.
- **Maker execution does lower the cost of a filled round trip**, from 15.80 to
  5.16 bps in the idealised case.
- **It does not lower the hurdle far enough**, because the exit leg cannot
  abandon and therefore pays taker on every stop.

### Not proven, and not claimed

- **No production fill rate.** No real order was placed. 22.9% is a
  reconstruction from public data, bounded rather than exact.
- **No strategy is profitable.** Nothing here establishes any directional edge.
  The 3.74 bps figure is an empirical scale reference and was never a validated
  signal.
- **Adverse selection under a signal is unmeasured.** H-MAKER-1's submission rule
  was signal-free by construction. Fills correlated with a real signal could
  behave differently. This does not affect the decision, because the decision
  survives at the CI upper bound anyway.

### The distinction the instruction asks for

**Execution viability** — whether the regime lowers the hurdle into a range where
an independently discovered edge could plausibly survive: **NO.** The hurdle
lands at 8.55–13.36 bps against a 7.47 boundary.

**Strategy profitability** — whether any signal produces that edge: **not
addressed, and deliberately so.**

---

# Final gate

## MAKER ECONOMICALLY INVIABLE

---

# Next authorization

**STOP the Delta directional-research program.**

No H-MAKER-2. No new indicator family. No new market-structure family. No
timeframe sweep. No parameter sweep. No hypothesis invented because the margin
was 14% rather than 200%.

The three feasibility paths are now all closed:

    A  longer horizon    NOT MEASURABLE — MDE grows linearly in h while the
                         effect grows as sqrt(h); blind before profitable
    C  better data       DEAD on the cost identity — requires >91% of the
                         typical move at microstructure horizons
    B  maker execution   INVIABLE — the exit leg cannot abandon, so the hurdle
                         stays above the boundary even at a perfect fill rate

Nineteen market experiments produced zero positive economic verdicts. The
feasibility phase found one surviving path. That path has now been measured and
does not clear its own pre-declared boundary.

What remains is the operator's decision, unchanged from the strategic diagnosis:
change market, change venue, change instrument class, or stop trading research.
None of those is a research task and none is started here.

---

# Final anti-loop check

| # | question | answer |
|---|---|---|
| 1 | Did you test a new trading signal? | **NO** |
| 2 | Did you search for a new hypothesis? | **NO** |
| 3 | Did you optimize any parameter? | **NO** — p and w were evaluated at prescribed sensitivity points, never selected |
| 4 | Did you use future TEST data? | **NO** — no market data was read at all |
| 5 | Did you change the feasibility threshold? | **NO** — 7.47 bps unchanged, and 5.54 bps unchanged |
| 6 | Did you create a new strategy family? | **NO** |
| 7 | Did you recommend another research cycle merely because the answer was inconclusive? | **NO** — the answer is not inconclusive, and the recommendation is to stop |

All seven NO. No violation to report.
