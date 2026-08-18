# H-EMA-3 FINAL VERDICT

Pre-registration sha256 `00ffd0bcf97834dc0ae0bc7eaf6e5b97…` (computed after the document was final, verified against it).
Supersedes H-EMA-2. TEST never computed.

## Family

EMA-derived mechanisms (crossover, slope, volatility expansion, pullback/re-entry, higher-timeframe regime) on 5m / 15m / 1h, measured by a paired mirror-direction barrier test.

## The result

| k | TRAIN excess R | TRAIN t | VALID excess R | VALID t | VALID 95% CI | cost floor R | edge as % of cost |
|---|---|---|---|---|---|---|---|
| 0.5 | +0.0315 | +15.53 | **+0.0181** | **+5.40** | [+0.0115, +0.0246] | 0.297 | +6% |
| 1.0 | +0.0194 | +6.11 | **+0.0007** | **+0.13** | [-0.0096, +0.0110] | 0.297 | +0% |
| 2.0 | +0.0036 | +0.75 | **-0.0137** | **-1.77** | [-0.0288, +0.0015] | 0.297 | -5% |
| 4.0 | -0.0144 | -2.02 | **-0.0305** | **-2.80** | [-0.0520, -0.0091] | 0.297 | -10% |

## Gross edge: **YES, and it replicates — at k = 0.5 only**

TRAIN +0.0315 R (t = 15.5) → VALID **+0.0181 R (t = 5.40)**, CI excluding zero. Positive in all four symbols on VALID (BTC +0.0253, ETH +0.0078, SOL +0.0221, XRP +0.0173), in both halves of VALID, in four of five mechanisms, and independently in the supplementary BEATUSD (+0.0379, t = 3.56) which has no TRAIN data at all. This is a real directional signal.

## Cost survivable: **NO — by a factor of sixteen**

```
VALID edge at k=0.5      +0.0181 R
round-trip cost floor     0.2970 R   (15.8 bps / 0.53% median stop)
                         --------
edge as a fraction              6%
```

The edge would need to be **16x larger** to pay the spread. It is not close, and no exit redesign reaches it, because the information is gone before the trade can travel far enough to matter.

## The decay curve is the finding

Information is concentrated at very short horizons and **reverses** at long ones. On VALID: +0.0181 at k=0.5, +0.0007 at k=1, −0.0137 at k=2, −0.0305 at k=4 (t = −2.80, CI excluding zero).

This kills the trend-following rescue on evidence rather than assumption. A wider target, a trailing stop or a longer hold would all have made the result **worse**. H-EMA-2's fixed 2R exit was measuring at exactly the horizon where the signal is worthless — it could not have found anything, which is why its 'no edge' conclusion was right for the wrong reason.

## Cross-symbol robustness: **YES for the signal, irrelevant for the economics**

All four symbols positive at k=0.5 on VALID, plus BEATUSD blind. But k=1 does NOT replicate (VALID +0.0007, t = 0.13 against TRAIN +0.0194, t = 6.11), so the surviving effect is confined to the shortest horizon tested.

## The two effects work against each other

The edge is a **5m phenomenon** — VALID k=0.5 by timeframe: 5m +0.0288 (t 7.6), 15m −0.0030 (t −0.5), 1h −0.0303 (t −1.9). But 5m has the *worst* cost/R (0.305 vs 0.077 at 1h). Where the signal lives, costs are highest; where costs are lowest, the signal is absent or negative. That is the whole family in one sentence.

## Classification: **DEAD**

Pre-registration §11: *"A statistically significant edge smaller than the round trip is DEAD as a trading hypothesis, however large its t-statistic."* Gate 1 passes (positive, CI excludes zero, both splits). Gate 3 and 4 pass. **Gate 2 fails by 16x.** Registry verdict: NO ECONOMIC EDGE — the mechanism exists, the economics do not.

## Most important finding

**EMA signals do predict direction — for about half a risk unit — and that is roughly one sixteenth of what it costs to act on it.** Three experiments now converge on the same structural conclusion from different angles: the binding constraint in this program is not signal discovery, it is the ratio of cost to risk unit. H-EMA-1 found it as a confound, H-Structure-1 found it as a dead family, and H-EMA-3 now measures it directly.

A methodological result worth keeping: H-EMA-2 concluded 'no edge' with a design whose minimum detectable effect was 7–24x the effect it was ruling out. The same data, measured with a paired estimator, shows a 15-sigma signal. **A null result is only as strong as the instrument's power, and that power must be reported alongside it.**

## Recommended next experiment

Not EMA, and not another signal family. The cross-sectional observation from BEATUSD is the lead: its cost floor is **0.076 R against the majors' 0.297 R**, because its structural stops are ~2.1% wide rather than ~0.53%. Its edge/cost ratio is 50% versus the majors' 6% — still short of 1, but nearly an order of magnitude better, and driven entirely by risk-unit geometry.

> **H-COST-1 — is there an instrument or volatility regime where cost/R falls below the available edge?** Sweep cost/R cross-sectionally across instruments and volatility states rather than sweeping signals. Pre-declare the eligibility screen on synthetic-bar share, and model slippage as a function of liquidity rather than a flat 2 bps, since a flat assumption flatters exactly the thin instruments this would favour.

**Explicitly not recommended:** more EMA variants, more timeframes, different exits, or adding indicators. The barrier sweep shows the information is real, short-lived, and an order of magnitude below the cost floor. Nothing in that sentence is fixed by a better entry signal.

