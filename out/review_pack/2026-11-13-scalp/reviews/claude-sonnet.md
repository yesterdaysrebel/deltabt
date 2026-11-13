# Review of PROMPT_SCALP (independent researcher: claude-sonnet)

## 1. The core problem

Round-trip cost c is about 0.30% of notional: 2 x 0.05% fee plus 0.1% slippage on each side (0.2% if the 0.1% is round-trip, but plan on 0.3% with the 0.1% target-through rule). Break-even win rate with stop S and target T is p* = (S+c)/(S+T).

| Stop | Target | Break-even win rate | Achieved | Gap |
|---|---|---|---|---|
| 2% | 0.4% | 95.8% | 84% | -12 pts |
| 2% | 1% | 76.7% | 71% | -6 pts |
| 4% | 0.8% | 89.6% | 86% | -4 pts |
| 4% | 2% | 71.7% | 69% | -3 pts |
| 4% | 4% | 53.8% | 50% | -4 pts |

- These coins move 20%+ a day, which is roughly 3-4% of hourly sigma. A 2-4% stop sits inside one hour of noise.
- A driftless price hits the target first with probability S/(S+T): 2/2.4 = 83%, 4/4.8 = 83%, 2/3 = 67%, 4/6 = 67%, 50% for 1:1. The achieved win rates (84/86/71/69/50) match this almost exactly. There is no early-cut edge, so the tight-stop win rate is just gambler's ruin.
- The only edge is a 24 h drift of about +0.02R. That is about 0.4% of price, the same size as the cost c.
- Cost is 0.3% of a 2% stop, which is 15% of R. It is 7.5% of R at a 4% stop and 1.5% of R at the 20% stop. So the cost share shrinks as the stop widens.
- Leverage multiplies P&L and cost equally. R per trade is unchanged, so it only changes ruin probability. With negative or zero R and 20-50x leverage, ruin is certain.
- A quick target converts the 24 h drift (paid on the 55% of trades that close green) into many small wins, and keeps the -1R stop losses. The 1:2 reward:risk wish needs a win rate of at least 1/3 plus cost, and tight stops give 34% before cost.

**Verdict.** The owner's style (high leverage, quick fixed profit, early small cut) cannot work on this strategy with price-path rules. Winners and losers have identical early paths (project finding), and the cost eats the quick profit.

**Nearest achievable version.** Keep rule A (or QUICK at +0.2R), use it at 3x and risk 4-5% per trade (fractional Kelly). Add a drawdown throttle. Accept about 56-66% win rate and about +0.02R. Compounding comes from sizing, not from leverage or cuts.

## 2. Proposals

Pass bar: mean R > 0 in each of 2024, 2025 and 2026, and t >= 2 (paired). Probabilities are honest and low.

### P1. Froth filter on the rule-A short (selection by crowd state not yet tested)
- **Rule.** At 00:03 UTC, trade the top-2 gainers as in rule A only if at least 3 of these 4 flags are true:
  1. Previous-day taker buy share >= 0.55.
  2. Spot exists = false, or perp/spot volume ratio above its 70th percentile (computed on 2021-23).
  3. Listing age < 180 days.
  4. Perp basis at 00:00 above its 70th percentile.
  - Otherwise skip the day or take the next ranked coin. Use the rule A stop (2x ATR, skip if > 32.5%), 24 h close, sizing unchanged (risk 4.4%, cap 60%, 1-3x).
  - Thresholds are fixed on 2021-23 from percentiles only. Do not search over grids.
- **Mechanism.** Pumps with no spot market, with buyers hitting the ask, and at a basis premium are driven by leveraged longs. They mean-revert as funding and liquidations bleed them. Pumps backed by spot volume persist. Variables not yet tested: taker share, spot/perp ratio, listing age, basis (the tested filters were pump, wick, 3d, volume, breakout, ATR, BTC, funding, OI, L/S).
- **Expected.** Win rate 58-62%, R:R about 1:1 on the 24 h close. Break-even win rate is about 54% (the stop is 20% and costs are 0.3%). Mean R target +0.05R. It is the same trade type with fewer trades, so the gain is in R/trade, not leverage.
- **Test.** Choose thresholds on 2021-23 only. Judge on 2024-26 paired against the same-day baseline. Report the filtered trades and the excluded trades. The excluded set should be worse. Require the same sign in the three years.
- **Pass probability: 8%.**

### P2. Funding-settlement short (8 h window, uses the funding rate directly)
- **Rule.** Enter short at 00:03 (rule A pick) only if the last funding rate is >= +0.05% per 8 h (a threshold set on 2021-23). Exit at 08:05 UTC (just after settlement) or at +3% against the entry (a stop). A 3% stop is on top of the 0.3% cost. Target: no fixed target; time exit. Size 3% risk, 3x.
  - Delta funding settlement times should be confirmed before the test. If it settles every 8 h at 00/08/16 UTC, use 08:05 and 16:05 variants.
- **Mechanism.** The short collects funding (about 0.05-0.3% per interval on pumped coins) plus the early mean reversion. It is a carry trade with the stop outside the noise of the settlement window. Funding is not used as a filter on a 1x ATR stop in the tests; it is used as the payoff.
- **Expected.** Win rate about 55%, R:R about 1:1 on a 3% stop. Break-even win rate is about 55% (carry 0.1-0.2% roughly offsets the 0.3% costs). It will be marginal. Mean R near zero is the likely result.
- **Test.** Use the funding history on 2021-23, with the threshold as the only parameter. Judge on 2024-26. Pair against the baseline entered at the same time with a 24 h close.
- **Pass probability: 5%.**

### P3. BTC-regime day selection (bear-trend days only)
- **Rule.** Trade rule A (or QUICK at +0.2R) only on days when the BTC daily close is below its 20-day average AND yesterday's BTC range is below its 14-day median. On other days sit flat. No change to stop, size or exit.
- **Mechanism.** When BTC trends down and is quiet, pumped alts lack a rising-tide bid and give back. Squeeze days (BTC rips) are the stop hits (16% of trades at -1.08R). Only the BTC previous-day return was tested, not the trend regime or vol state.
- **Expected.** About 40-50% of days kept. Win rate 58-60%. Mean R +0.04R. Break-even about 54%.
- **Test.** Fix the 20-day MA and 14-day median before looking at the data (no tuning). Choose on 2021-23, judge on 2024-26, paired with the baseline on the kept days. Also show the dropped days, which should be worse. Check that year 2023 (-0.035R) and 2021 (-0.018R) improve. If both only improve in 2021-23, it is overfit.
- **Pass probability: 7%.**

### P4. Time-conditional exit: profit-lock at 06:00 / 12:00 (a clock condition, not a price target)
- **Rule.** Keep the rule A stop. At 12:00 UTC (hour 12): if the trade is in profit by >= 1% (net of cost), close. If it is at a loss, hold to 24 h. Variant: do the same at 06:00 with a >= 0.5 ATR gain threshold.
- **Mechanism.** Different from the tested take-profit at p%: the test uses time to decide, so it only closes the trades that stayed green over 12 h. Intraday spikes that touch a target are noisy, and a trade that is still green at hour 12 has already survived the early squeeze window. The expected gain is lower variance and a higher win rate. A mean gain only appears if late-day reversals hurt winners more than losers.
- **Expected.** Win rate about 62-66%, R:R about 0.5:1 on the closed trades. Break-even 54%. Mean change vs baseline is about 0, which is why this is low.
- **Test.** Choose the threshold on 2021-23. Pair vs baseline on the same trades. Because the effect is on variance, report the stdev and the drawdown of the account too.
- **Pass probability: 4%.**

### P5. Long the pump for the first 1-2 hours, then flip to the short (direction change)
- **Rule.** At 00:03, go LONG the top-2 gainers with a 2% stop below the entry and a 2% target, time exit at 02:03. If flat after 2 h, enter the rule A short at 02:03 using the rule A stop measured from the new entry (stop skip if > 32.5%).
- **Mechanism.** Momentum for the first hours after a big day (continuation) and then reversal over the day. This is the only long idea; evidence is thin. Tight stops inside the noise have a break-even of about 77%, so the long leg is dead on arrival unless there is real continuation drift.
- **Expected.** Win rate about 50%, R:R 1:1, break-even 57%. I expect the long leg to be negative.
- **Test.** Test the long leg alone first, on 2021-23. If its mean is not > 0.3% (twice the cost), stop. Do not combine legs before the first leg passes.
- **Pass probability: 2%.**

### P6. Risk-throttle sizing (the owner's real lever: lose less through size)
- **Rule.** Rule A (or QUICK), 3x max. Risk per trade = 5% when the account equity is at or above its 20-trade moving average, and 2.5% when below. Cap 60% notional. After 3 consecutive stop-outs, skip 1 day. No change to entries or exits.
- **Mechanism.** The strategy's R is serially clustered by regime (see the year pattern: -0.018, +0.036, -0.035, +0.009, +0.031, +0.065). Trading small in cold stretches and large in warm ones raises the geometric growth without touching R per trade. It cannot change mean R, so it fails the stated R test by construction.
- **Expected.** Same win rate (56%), R:R as baseline. Drawdown falls by about 20-30%.
- **Test.** Compare the account curve and max drawdown against a fixed 4.4% risk, over the same trade list, 2024-26. Judge on terminal growth and drawdown, not mean R.
- **Pass probability: under 3% on the stated bar** (mean R and t are unchanged); about 55% that the drawdown improves.

## 3. Ranking by pass probability

| Rank | Proposal | Pass probability |
|---|---|---|
| 1 | P1 froth filter (taker share, spot/perp, listing age, basis) | 8% |
| 2 | P3 BTC trend and vol regime day selection | 7% |
| 3 | P2 funding-settlement short | 5% |
| 4 | P4 12 h profit-lock | 4% |
| 5 | P6 risk-throttle sizing (fails the R bar by design) | under 3% |
| 6 | P5 long-then-flip | 2% |

## 4. Plain statement

- The owner's style (leverage up, quick fixed profit, early small cut) does not work here. The cost is 0.3% against a drift that is about the same size, winners and losers look identical early, and every tight stop sits inside one hour of noise.
- Leverage does not change R. It only moves the ruin probability from 0 to near certain.
- The nearest achievable version is rule A or QUICK at 3x, risk 4-5% with the throttle (P6), plus at most one selection filter (P1 or P3) that survives paired testing. Expect +0.02 to +0.05R per trade and a 56-66% win rate. It is a slow compounder, not a high-leverage scalp.
- To avoid overfitting: one or two parameters, fixed from percentiles on 2021-23, judged once on 2024-26, paired on the same trades, with a Bonferroni-style correction across the 6 proposals (t >= 2.6 rather than 2 if more than one is tested).
