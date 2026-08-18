# H-STRUCTURE-2 — FINAL REPORT

Hypothesis 1 of 3 in the MARKET PHENOMENON DISCOVERY phase.

> HH/HL and LH/LL structural transitions contain directional information
> about subsequent price movement.

Pre-registration `out/hstructure2/hstructure2_preregistration.md`, sha256
`7338dddb3159fc0a1443ac8f12ab6cf0c366b42be2d6eb670d4749bd7b41689d`, frozen
before any event was counted. Manifest `out/hstructure2/manifest.json`.

---

## Journal

```
S2-CONT   (CONT_LONG + CONT_SHORT)

    Events:                 3,811
    Long:                   1,995
    Short:                  1,816
    Day clusters:             353

    TRAIN  (effect = mean signed forward return)
    ------------------------------------------
    +5m         +0.0075%   t  +0.93
    +15m        -0.0070%   t  -0.70
    +30m        -0.0148%   t  -1.17
    +60m        -0.0130%   t  -0.73   <-- PRIMARY
    +240m       -0.0063%   t  -0.16
    +1440m      -0.0485%   t  -0.39

    MDE:                  +0.0498%
    Effect/MDE:             -0.26x
    Control:              +0.0007%   p = 0.3830
    Symbols positive:           1/4
    Agreeing (A5):              3/4
    Per symbol:        BTCUSD -0.0006%  ETHUSD +0.0079%  SOLUSD -0.0275%  XRPUSD -0.0308%
    Halves:            H1 -0.0306%   H2 +0.0057%

    VALID              NOT COMPUTED — Stage-A gate not passed on TRAIN

    Stage A:           INSUFFICIENT POWER

    FINAL:             INSUFFICIENT POWER

S2-FAIL   (FAIL_LONG + FAIL_SHORT)

    Events:                 3,346
    Long:                   1,652
    Short:                  1,694
    Day clusters:             353

    TRAIN  (effect = mean signed forward return)
    ------------------------------------------
    +5m         +0.0035%   t  +0.60
    +15m        -0.0026%   t  -0.28
    +30m        +0.0006%   t  +0.04
    +60m        +0.0024%   t  +0.11   <-- PRIMARY
    +240m       -0.0356%   t  -0.93
    +1440m      -0.0607%   t  -0.71

    MDE:                  +0.0593%
    Effect/MDE:              0.04x
    Control:              +0.0000%   p = 0.8670
    Symbols positive:           2/4
    Agreeing (A5):              2/4
    Per symbol:        BTCUSD -0.0112%  ETHUSD +0.0093%  SOLUSD +0.0161%  XRPUSD -0.0044%
    Halves:            H1 +0.0256%   H2 -0.0211%

    VALID              NOT COMPUTED — Stage-A gate not passed on TRAIN

    Stage A:           INSUFFICIENT POWER

    FINAL:             INSUFFICIENT POWER

```

---

## Mandatory TRAIN / VALID table

### S2-CONT

| Metric | TRAIN | VALID |
|---|---:|---:|
| Events | 3,811 | not computed |
| Effect | -1.30 bps | not computed |
| MDE | +4.98 bps | not computed |
| Effect / MDE | -0.26x | not computed |
| Control | +0.07 bps | not computed |
| Symbols positive | 1/4 | not computed |
| Symbols agreeing with pooled sign (A5) | 3/4 | not computed |
| Gross R | not tested | not tested |
| Cost/R | not tested | not tested |
| Net R | not tested | not tested |

    INFORMATION:    INSUFFICIENT POWER
    REPLICATION:    NO  (VALID not run — TRAIN gate not passed)
    ECONOMIC:       NOT TESTED
    FINAL VERDICT:  INSUFFICIENT POWER

### S2-FAIL

| Metric | TRAIN | VALID |
|---|---:|---:|
| Events | 3,346 | not computed |
| Effect | +0.24 bps | not computed |
| MDE | +5.93 bps | not computed |
| Effect / MDE | 0.04x | not computed |
| Control | +0.00 bps | not computed |
| Symbols positive | 2/4 | not computed |
| Symbols agreeing with pooled sign (A5) | 2/4 | not computed |
| Gross R | not tested | not tested |
| Cost/R | not tested | not tested |
| Net R | not tested | not tested |

    INFORMATION:    INSUFFICIENT POWER
    REPLICATION:    NO  (VALID not run — TRAIN gate not passed)
    ECONOMIC:       NOT TESTED
    FINAL VERDICT:  INSUFFICIENT POWER

---

## Stage-A gate, item by item

| Gate | S2-CONT | S2-FAIL |
|---|---|---|
| A1 effect nonzero | PASS | PASS |
| A2 effect >= MDE | **FAIL** | **FAIL** |
| A3 exceeds control | **FAIL** | **FAIL** |
| A4 same sign H1/H2 | **FAIL** | **FAIL** |
| A5 >=3/4 symbols agree | PASS | **FAIL** |

The verdict is INSUFFICIENT POWER rather than NO INFORMATION because the
pre-declared gate evaluates A2 before A3, and that ordering was frozen. It
is worth saying plainly that **A3 and A4 also failed**: the effects are
indistinguishable from their own permutation controls, and the two halves of
TRAIN disagree in sign. Under the control criterion alone the verdict would
read NO INFORMATION. Both point the same way; only the label differs.

A6 (VALID) was never reached. VALID is run once and only after A1–A5 pass;
spending it on a hypothesis that already failed on TRAIN would consume the
out-of-sample segment for nothing.

---

## What INSUFFICIENT POWER does and does not mean here

The formal verdict follows the pre-declared decision tree: A2 fails, so the
verdict is INSUFFICIENT POWER and never NO EDGE. That wording is required
because it is literally true — an effect smaller than the MDE cannot be
distinguished from zero by this sample.

It would be a serious misreading to conclude *"the effect may be real, we
just need more data."* The relevant comparison is not effect against zero,
it is **MDE against the cost floor**:

    round-trip cost   2 x (5 bps taker x 1.18 GST + 2.0 bps slippage)
                      = 15.8 bps

| | S2-CONT | S2-FAIL |
|---|---:|---:|
| observed effect at +1h | -1.30 bps | +0.24 bps |
| MDE at +1h | +4.98 bps | +5.93 bps |
| cost floor / MDE | 3.2x | 2.7x |

The MDE is roughly **3x smaller than the round-trip cost**. Any effect large
enough to survive execution costs would have been detected comfortably. The
experiment is underpowered only in the region where the phenomenon could
never have been traded anyway.

So the honest statement is narrow and specific: **there is no structural
effect here of a size that could matter economically.** Whether some effect
of 1 bp exists is unresolved and uninteresting.

---

## Supporting observations

- **The control confirms it independently.** The permutation control —
  which preserves symbol, timestamp and the exact direction imbalance, and
  randomizes only the direction assignment — gives p = 0.383 for S2-CONT and p = 0.867 for S2-FAIL. The observed
  effects sit inside the middle of their own null distributions.
- **The two halves of TRAIN disagree in sign** for both families, which is
  what noise looks like and not what a phenomenon looks like.
- **No horizon rescues it.** All six pre-declared horizons are reported
  above; the largest |t| anywhere in the table is 1.17. Bonferroni x6 for
  six horizons would require |t| > 2.64, so no horizon comes close even
  before correction — there is nothing to select from.
- **The continuation effect at +1h is weakly negative** (t -0.73). If
  anything, continuation events are followed by very slightly adverse
  moves, but at a quarter of the MDE this is noise, not a reversal signal,
  and it is not pursued. Pursuing it would be a new hypothesis.

---

## Relationship to H-Structure-1

H-Structure-1 returned NO SIGNAL for this family under a 2R target and a
structural stop. That was a joint test of information and trade geometry,
and H-COST-1 and H-NULL-1 later showed geometry can both hide a real effect
and manufacture a false one — so the joint test could not settle the
question. H-STRUCTURE-2 removed the geometry entirely and asked only
whether the events predict price.

They do not, at any size that matters. The two experiments now agree for
independent reasons, and the family is closed.

---

## FINAL VERDICT

    S2-CONT:  INSUFFICIENT POWER
    S2-FAIL:  INSUFFICIENT POWER

    H-STRUCTURE-2:  INSUFFICIENT POWER

Stage B was not constructed. Per protocol section 6, Stage-A survivors get
an executable strategy and non-survivors do not.

Per the failure stop rule, this hypothesis stops here. No filter, threshold,
timeframe, swing strength, confirmation indicator or exit rule is added —
each of those would be a new hypothesis, and the phase budget is three.

Remaining budget: **H-VOL-1**, then **H-REL-1**.
