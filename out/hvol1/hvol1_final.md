# H-VOL-1 — FINAL REPORT

Hypothesis 2 of 3 in the MARKET PHENOMENON DISCOVERY phase.

> Volatility compression followed by expansion contains directional
> information.

Pre-registration `out/hvol1/hvol1_preregistration.md`, sha256
`624d0b2848bbc58555f347d4f1e33027dbc46f9590846b7cfad6f3be851b36a5`.
Manifest `out/hvol1/manifest.json`.

---

## Journal

```
V1-EXP   (EXP_UP + EXP_DOWN)

    Events:                   317
    Long:                     153
    Short:                    164
    Day clusters:             160

    TRAIN  (effect = mean signed forward return)
    ------------------------------------------
    +5m         -0.0004%   t  -0.04
    +15m        -0.0166%   t  -1.12
    +30m        -0.0369%   t  -1.80
    +60m        -0.0374%   t  -1.26   <-- PRIMARY
    +240m       -0.0538%   t  -0.69
    +1440m      +0.1178%   t  +0.62

    MDE:                  +0.0830%
    Effect/MDE:             -0.45x
    Control:              +0.0002%   p = 0.1640
    Symbols positive:           1/4
    Agreeing (A5):              3/4
    Per symbol:        BTCUSD -0.0574%  ETHUSD -0.0004%  SOLUSD +0.0264%  XRPUSD -0.1118%
    Halves:            H1 -0.0405%   H2 -0.0349%

    VALID              NOT COMPUTED — Stage-A gate not passed on TRAIN

    Stage A:           INSUFFICIENT POWER

    FINAL:             INSUFFICIENT POWER
```

---

## Mandatory TRAIN / VALID table

| Metric | TRAIN | VALID |
|---|---:|---:|
| Events | 317 | not computed |
| Effect | -3.74 bps | not computed |
| MDE | +8.30 bps | not computed |
| Effect / MDE | -0.45x | not computed |
| Control | +0.02 bps | not computed |
| Symbols positive | 1/4 | not computed |
| Symbols agreeing with pooled sign (A5) | 3/4 | not computed |
| Gross R | not tested | not tested |
| Cost/R | not tested | not tested |
| Net R | not tested | not tested |

    INFORMATION:    INSUFFICIENT POWER
    REPLICATION:    NO  (VALID not run — TRAIN gate not passed)
    ECONOMIC:       NOT TESTED
    FINAL VERDICT:  INSUFFICIENT POWER

---

## Stage-A gate

| Gate | V1-EXP |
|---|---|
| A1 effect nonzero | PASS |
| A2 effect >= MDE | **FAIL** |
| A3 exceeds control | **FAIL** |
| A4 same sign H1/H2 | PASS |
| A5 >=3/4 symbols agree | PASS |

---

## Power is the binding constraint, and it is much tighter than H-STRUCTURE-2

This must be stated plainly rather than buried, because the margin here is
genuinely narrow:

| | H-STRUCTURE-2 | H-VOL-1 |
|---|---:|---:|
| TRAIN events at +1h | 3,811 | 317 |
| day clusters | 353 | 160 |
| MDE at +1h | 4.98 bps | 8.30 bps |
| round-trip cost floor | 15.8 bps | 15.8 bps |
| cost floor / MDE | 3.2x | 1.9x |

The squeeze definition is intrinsically rare — a 20th-percentile ATR state
held for at least four consecutive bars fires roughly 80 times per symbol per
year. The conclusion still holds, but with less room: **the MDE remains below
the cost floor, so an effect large enough to trade would still have been
detected** — by a factor of about two rather than three.

The observed effect is -3.74 bps against an MDE of +8.30 bps, and the permutation control gives
p = 0.164. Nothing here is distinguishable from its own
null.

---

## One observation that is reported but NOT pursued

The effect is negative at every horizon from +15m to +4h, the two halves of
TRAIN agree in sign, and 3 of 4 symbols agree. Read loosely that resembles
mean reversion after expansion — the opposite of the hypothesis.

It is not a finding, for three reasons:

1. At the primary horizon it is 0.45x the MDE. The
   sample cannot distinguish it from zero.
2. The permutation control gives p = 0.164. It is inside
   its own null distribution.
3. The largest |t| at any horizon is 1.80, at +30m. Six pre-declared horizons
   require |t| > 2.64 under Bonferroni. It does not survive even before
   correction.

Turning this into a reversal hypothesis would mean flipping the direction of
a pre-registered hypothesis after seeing that it failed in the stated
direction. That is precisely the move the phase protocol's anti-loop rule
exists to prevent, and it is not made. It is recorded here so that the
observation is on file rather than quietly discarded.

---

## Relationship to H-Compress-1 and H-Compress-1-rev2

Both returned NO SIGNAL, measuring gross R under a passive-limit retest entry
with volume and body-size confirmation, on 169 and 227 trades. H-VOL-1 kept
their frozen compression *state* — deliberately, so that no threshold could
be accused of having been picked today — dropped every execution parameter,
and measured price directly on 317 events.

Three experiments now point the same way for different reasons. The
volatility-compression family is closed.

---

## FINAL VERDICT

    H-VOL-1:  INSUFFICIENT POWER

Stage B was not constructed. Per the failure stop rule no percentile, window,
duration, volume filter or timeframe is adjusted — each would be a new
hypothesis.

Remaining budget: **H-REL-1**.
