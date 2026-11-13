# Review: making "high leverage, quick profit, early loss-cut" work on the short strategy

Answered only from PROMPT_SCALP.md. Costs are taken as 0.10% slippage plus 0.05% fee per side, so 0.30% per round trip (C = 0.30%).

## 1. The core problem

**A bracket on a driftless path has zero gross expectation.** If the coin moves like a random walk with no drift, the chance of hitting the target T before the stop S is S/(S+T). The gross expected value is then exactly zero, whatever T and S are (optional stopping). Costs then make every such trade negative by C. The only positive term is the drift, which is about +0.02R per trade (about 0.4% on a 20% stop, or about 0.4% over 24 h for the whole book).

**Break-even win rate.** With stop S and target T (both in %), p* = (S + C) / (S + T):

| Stop / target | Break-even win rate | Achieved | Gap |
|---|---|---|---|
| 2% / 0.4% | 95.8% | 84% | -12 pts |
| 2% / 1% | 76.7% | 71% | -6 pts |
| 4% / 0.8% | 89.6% | 86% | -4 pts |
| 4% / 2% | 71.7% | 69% | -3 pts |
| 4% / 4% | 53.8% | 50% | -4 pts |
| 20% / 0.2R (4%) | 84.6% | not reported | |
| 20% / 2:1 (40%) | 33.8% | target hit 1% | the 24 h-profit share is not a target hit |

The achieved win rates match the zero-drift value S/(S+T) almost exactly (for 2% / 0.4%, S/(S+T) = 83.3% against 84% achieved). So the quick-target brackets are a fair coin with costs on top.

**Cost drag as a fraction of the stop.** C/S is 15% of a 2% stop, 7.5% of a 4% stop, and 1.5% of a 20% stop. This is why tight stops with leverage are structurally worse here: the cost is a larger share of R, and the price noise inside a day (20%+ swings on these coins) crosses a tight stop before any drift arrives.

**Leverage does not change R, and it creates a liquidation constraint.** On a short at L times leverage, liquidation comes at roughly +100%/L adverse, minus maintenance margin. At 20x that is about +5%, which sits inside a 20% stop, so those trades are total losses before the stop is hit. At 5x it is about +20%. For the owner's "no liquidations" requirement, the stop must be about 0.8/L or tighter, so 20% stops allow at most about 4x. High leverage and wide stops are incompatible, and the wide stop is where the measured edge comes from.

**Where the baseline's edge comes from.** Stop hits are 16% of trades at -1.08R (about -0.17R), 24 h profit closes are 55% at +0.47R (about +0.26R), and 24 h loss closes are 30% at -0.23R (about -0.07R). The sum is about +0.02R. The shares sum to 101% because of rounding. The positive term is the 24 h drift of the top gainers, and the 20% stop is what keeps that drift from being cut.

**Conclusion for the owner's goal.** Tight stop, quick target, and high leverage cannot all hold here. To profit from a bracket you need a signal with large conditional drift in units of the stop, E[gross move] / S > C / S (for a 2% stop, above 0.15 of the stop in expected 24 h move). No tested filter reached that out of sample.

## 2. Proposals (ranked by honest pass probability)

Common settings unless stated: short the top 2 previous-day gainers at the 00:03 UTC close. Stop = entry x (1 + 2 x ATR14%), skip if more than 32.5%. Time exit 24 h. Size as rule A. Break-even for any time-exit rule is a mean gross R above C/S = 0.015R.

### Proposal 1 (rank 1): Basis-filtered short

- **Rule.** On the day before the pick (known at 00:00), the perp premium over spot must be at or above a threshold X at the 00:00 minute. Only coins with a spot market qualify. X is one of {0.5%, 1%, 2%}, chosen on 2021-23 as the value that gives the best paired result. Entry, stop, and exit are as in the common settings.
- **Mechanism.** A large perp premium means leveraged longs are paying up over spot. Arbitrage and funding pressure should pull the perp back toward spot within 24 h. A coin with a premium is more likely to have a crowded long base, so the convergence drift is larger. This is a different information set from the tested filters: the tested ones used funding and OI, not the premium or basis.
- **Expected.** About a third of picks. Win rate 55-60%, R:R about 1:1 in the time-exit sense. Break-even mean gross R is 0.015R. My estimate of the true effect is about +0.01R to +0.03R, which is hard to resolve with the sample.
- **Data.** Perp premium/basis history, spot-exists flag, daily OHLCV for the ATR.
- **Test.** Select X on 2021-23 only. Judge 2024-26 as a paired difference against rule A on the same picks. Use studentised max-T across the three X values. Use Binance for all years for sample size and check fills on Delta. Delta data starts in 2025, so the 2024 test cannot run on Delta.

### Proposal 2 (rank 2): Perp-only pumps, short

- **Rule.** Restrict the universe to picks with no spot market and listing age at or below D days, where D is one of {30, 90, 180}, chosen on 2021-23. Otherwise rule A.
- **Mechanism.** With no spot market, nothing arbitrages the perp price back to a spot reference. Pumps are then driven by perp leverage, funding, and thin books, and they should revert harder. Listing age captures the same thin-liquidity effect.
- **Expected.** Smaller sample, probably 20-30% of picks. Win rate 55-60%. Mean gross R plausibly +0.02R to +0.05R if the mechanism holds.
- **Data.** Spot-exists flag, listing age, funding, daily OHLCV.
- **Test.** As in Proposal 1. Note that "no spot" was not among the tested universe filters, but family 7 did test volume surge and ATR thirds, which overlap with thin-liquidity coins. Check that the result is not just a volume effect.

### Proposal 3 (rank 3): Crowded-retail, top-trader divergence short

- **Rule.** On the day before the pick, the top-trader long/short ratio must be below 1.0 (top traders net short) and the account long/short ratio must be at or above its 2021-23 median among picks. Taker buy share on the pump day must be below its 3-day average (buying exhaustion). If all hold, short at 00:03. Otherwise skip.
- **Mechanism.** The pump is retail-led (crowded accounts long) while larger accounts are positioned against it. Divergence between the two groups is a different signal from the single account ratio that already failed in family 7. Buying exhaustion adds a timing confirmation.
- **Expected.** About 15-25% of picks. Win rate 55-60%. Break-even as above. The three-way conjunction is prone to overfitting, so I expect the true effect to be small.
- **Data.** Top-trader ratio, account ratio, taker buy share.
- **Test.** Choose the two ratio cut-points on 2021-23 only. Use a paired comparison against rule A. Report each condition alone as well, to show which one carries the result.

### Proposal 4 (rank 4): Long the first-day pump after continuation confirmation

- **Rule.** Go LONG the top previous-day gainer only if, between 00:03 and 02:00 UTC, the 1 h lows form 3 consecutive higher lows, and the previous day's open-interest change is at or above +5%. Entry at the 02:00 close. Stop = entry x (1 - 2 x ATR14%). Time exit 24 h from entry. Skip if no confirmation.
- **Mechanism.** A pump with rising intraday lows and rising OI is new participation, not just a squeeze, so it may continue for some hours before fading. This is the owner's suggested long-side variant. The "3 higher 1 h lows" pattern was tested only as an exit for shorts, not as an entry for longs.
- **Expected.** Win rate 45-50%, R:R about 1:1. Momentum failed in 2025-26 on large coins, so I expect this to be flat or negative.
- **Data.** 1 h lows from the 1-minute data, previous-day OI change, daily ATR.
- **Note.** OI is only known per day, not intraday, so only the previous day's OI change can be used. Using an intraday OI figure would be look-ahead.
- **Test.** Choose the OI cut-point on 2021-23 only. Paired against the short baseline on the same coins and days is not meaningful, so compare the long rule against a long-side placebo (same entries with random direction) as well.

### Proposal 5 (rank 5): Noon entry on pumps that have not reversed

- **Rule.** Short at the 12:03 UTC close, not 00:03, only if the close at 12:03 is at least 5% above the 00:00 open (the pump has not reversed) and the 00:00-12:00 high is not more than 2 x ATR14% above the close. Stop = entry x (1 + 2 x ATR14%). Time exit 24 h from entry. Skip otherwise.
- **Mechanism.** The first hours after the pump are the most volatile and the most likely to be noise. Waiting until midday, when the pump has survived the early shake-out, lets the drift be measured against a more stable price. The filter keeps only pumps that persist, which should carry more reversal drift.
- **Expected.** About 30-40% of picks. Win rate 55%, R:R about 1:1. Break-even mean gross R above 0.015R. Low confidence.
- **Data.** 1-minute highs and closes for the first 12 h, daily open, daily ATR.
- **Test.** Choose the 5% threshold on 2021-23 only. Paired against rule A on the same coins and days.

## 3. Verdict on the owner's style

The owner's style, high leverage with quick fixed profits and tight early losses, cannot work on this strategy. Under zero drift it has zero gross expectation, costs make it negative, and leverage above about 4x is incompatible with a stop as wide as the edge requires.

The nearest achievable version is rule A (or the QUICK variant) at risk of 4-5% of the balance, leverage 1-3x, and a notional cap of 60%. The measured edge is about +0.02R per trade, so the owner should expect small, slow growth, with large drawdowns on stop-hit clusters. Leverage should be treated as a cap on margin, not as a return lever.

## 4. Probability of passing

Pass means mean R > 0 in each of 2024, 2025, and 2026, and t >= 2. Rule A already fails this on Delta in 2025 (-0.017R), so the bar is high.

| Rank | Proposal | Honest probability of pass |
|---|---|---|
| 1 | Basis-filtered short | about 5% |
| 2 | Perp-only pumps, short | about 4% |
| 3 | Top-trader divergence short | about 3% |
| 4 | Long continuation after higher lows and OI | about 2% |
| 5 | Noon entry on unreversed pumps | about 2% |

These numbers are low by design. I expect each proposal to show a effect below the test's power, and the pass criterion needs all three years positive as well as t >= 2. If one proposal passes on 2021-23 and fails on 2024-26, treat it as a null, as the earlier families were.
