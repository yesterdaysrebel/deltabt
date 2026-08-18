# STRATEGIC DIAGNOSIS — after three failed hypothesis families

Required by the phase protocol's final stop rule. All three families in the
budget failed Stage A, so the research program stops here and this document is
produced **instead of** a fourth family.

No H-STRUCTURE-3, H-MOMENTUM-1, H-MICROSTRUCTURE-1, H-ORDERFLOW-1, H-EMA-4,
H-WPR-2 or H-ADX-2 is proposed.

---

## 1. The required statement

> No sufficiently strong, reproducible predictive phenomenon was found under the
> current data, universe, timeframe and execution assumptions.

---

## 2. What was tested, and what it returned

| | H-STRUCTURE-2 | H-VOL-1 | H-REL-1 |
|---|---|---|---|
| phenomenon | HH/HL/LH/LL transitions | compression → expansion | BTC shock → follower lag |
| TRAIN events at +1h | 3,811 / 3,346 | 317 | 1,527 |
| day clusters | 353 | 160 | 249 |
| effect at +1h | −1.30 / +0.24 bps | −3.74 bps | +1.91 bps |
| MDE at +1h | 4.98 / 5.93 bps | 8.30 bps | 11.23 bps |
| effect / MDE | −0.26 / +0.04 | −0.45 | +0.17 |
| control p | 0.383 / 0.867 | 0.164 | 0.557 |
| verdict | INSUFFICIENT POWER | INSUFFICIENT POWER | INSUFFICIENT POWER |

Across all four event families, six horizons each, the largest |t| observed
anywhere was **1.80**, and no effect at any primary horizon exceeded **3.74 bps**.

The three families were chosen to be structurally different — path shape,
volatility state, cross-asset relationship. They failed the same way and at the
same magnitude. That similarity is itself the finding: the ceiling is not a
property of any one phenomenon.

---

## 3. Execution costs — the binding constraint

    round trip = 2 x (5 bps taker x 1.18 GST + 2.0 bps slippage) = 15.8 bps

Measured against how far price actually moves, unconditionally, on TRAIN:

| horizon | median abs move | p90 abs move | cost as % of median move |
|---|---:|---:|---:|
| +5m | 10.0 bps | 32.0 bps | **158%** |
| +15m | 17.3 bps | 54.5 bps | **92%** |
| +30m | 24.1 bps | 77.1 bps | **66%** |
| +1h | 33.6 bps | 109.2 bps | **47%** |
| +4h | 69.4 bps | 229.3 bps | 23% |
| +1d | 197.9 bps | 587.2 bps | **8%** |

This is the single most important table in the phase.

**At 5 minutes the round trip exceeds the entire typical move.** A perfect
oracle, right about direction every single time, still loses money at that
horizon on a median bar. At 15 minutes it consumes 92% of it. At one hour — the
horizon this phase was built around — it takes 47%.

To profit at +1h you need an edge that survives handing back nearly half of a
typical move on every trade. That is not a demanding edge; it is close to an
impossible one.

---

## 4. Signal-to-noise

The measured effects are 0.2–3.7 bps. A tradable effect at +1h needs to exceed
15.8 bps. **The gap is a factor of roughly 4 to 80.**

This is not a power problem, and it is important to be precise about that
because "INSUFFICIENT POWER" invites the wrong conclusion. In all three
hypotheses the MDE sat **below** the cost floor:

    H-STRUCTURE-2   cost floor / MDE = 3.2x
    H-VOL-1                            1.9x
    H-REL-1                            1.4x

The apparatus could see effects smaller than the smallest tradable one, in every
case. It saw nothing there. More data would narrow the confidence intervals
around 1–4 bps effects, which would still be untradable. **More data does not
change the answer.**

---

## 5. Data resolution

1-minute OHLC, ~847,000 bars per symbol over the study window. Adequate for
everything attempted, and not the limitation.

What is absent is the layer below: no order book, no trade prints, no queue
position, no funding-rate microstructure. Every phenomenon tested was a function
of OHLC bars, and OHLC is the most heavily mined representation of a liquid
market. If exploitable structure survives in these instruments, the prior should
be that it lives in data we do not have, not in a bar pattern we have not yet
tried.

---

## 6. Instruments

BTCUSD, ETHUSD, SOLUSD, XRPUSD carry the study. DOGEUSD and BEATUSD exist in the
cache but are unusable — BEATUSD listed 2026-01-05 so it has no TRAIN data.
AKEUSD and BANKUSD listed 2026-07-22, placing their entire history inside the
locked TEST window.

Four instruments is a real constraint on cross-sectional work: H-REL-1 had one
leader and three followers, and the three followers respond to the same shock at
the same instant, which is why 1,527 events collapsed to 249 independent day
clusters. A universe of 30–50 instruments would give genuine cross-sectional
variation. This universe cannot.

---

## 7. Trading horizon

The cost table points at the one lever with real leverage. Cost as a fraction of
the typical move falls from 158% at 5 minutes to 8% at one day — nearly 20x.

Everything this program has tested lives between 5 minutes and 4 hours, which is
the range where Delta India's taker-plus-slippage structure is most punishing.
The program has been searching the part of the space its own cost structure
rules out.

---

## 8. Is the venue and data suitable for this style of trading?

**For taker-driven intraday directional trading on 1m OHLC across four
instruments: no.** Not marginally — by a factor of several.

The argument does not depend on any single experiment. It is arithmetic:
round-trip cost is 47% of the median hourly move, and nineteen recorded
experiments have produced no effect above 4 bps.

Three things would each change the arithmetic, and they are stated as
observations rather than as proposals:

- **Horizon.** Moving to multi-day holding drops cost to 8% or less of a typical
  move. It also cuts the number of independent observations per year by roughly
  the same factor, so statistical power falls as economic viability rises.
- **Fee structure.** The 15.8 bps assumes taker on both legs. Maker execution
  removes 11.8 of those bps, but introduces fill uncertainty that none of this
  program's machinery models — H-Compress-1's passive arm was the one attempt,
  and it was never validated as a fill model.
- **Data.** Order-book and trade-print data would open the microstructure layer
  where short-horizon edge plausibly still exists. It is also the only direction
  in which the 1m OHLC ceiling is not already binding.

---

## 9. The program as a whole

Nineteen recorded experiments. Verdicts:

    NO SIGNAL            8
    NO ECONOMIC EDGE     5
    INSUFFICIENT DATA    3   (the three phase hypotheses)
    NO EDGE              2
    (none)               1   (H-NULL-1, infrastructure, makes no market claim)

    positive verdicts    0

One genuine asset came out of it. H-NULL-1 established a framework whose bias is
provably zero, which structurally refuses the comparison that manufactured the
H-EMA-3 artifact, and which recovers a planted edge at 97–100% power. Two of the
program's own earlier conclusions were retracted by it. Whatever is tested next,
it can now be tested honestly — and the string of negative results above is
evidence the methodology works, not evidence it failed.

---

## 10. The decision, which is not mine

The protocol reserves the next choice for the operator: change market, change
timeframe, change data, change execution venue, change strategy horizon, or stop
the trading research.

What the evidence supports is narrow and I will not overstate it: **continuing to
search for 5-minute-to-4-hour directional edge in 1m OHLC on these four Delta
India perpetuals is not worth further experiments.** The cost structure rules out
the region, and the phenomena tested there are 4–80x too small.

Which lever to pull instead is a business decision, not a statistical one.
