# Review: making "high leverage, quick profit, early loss-cut" work on the pumped-coin short

Reviewer: Claude (Fable 5.2), answering from the prompt only. No files read, no code run. Every number below is either
quoted from the prompt or derived from it by arithmetic; derived numbers are marked "derived" and should be checked.

Conventions used throughout:

- R = the rule A stop distance (2 x ATR14%, about 20% of price).
- Cost c = 0.30% of notional per round trip (0.10% slippage + 0.05% fee, per side). If the 0.10% is meant once per
  round trip, c = 0.20%; the conclusions do not change.
- "Gross drift" = what the short earns before costs. Derived from Delta: +0.021R x 19.5% = 0.41% net, so about 0.7%
  gross per 24 h. Binance: 0.011R x 20% = 0.22% net, about 0.5% gross.

## Verdict first

The owner's style as literally stated (tight stop, quick target, high leverage) cannot work on an unhedged short of
yesterday's top gainers. It is an arithmetic result, shown in section 1, and no stop/target pair fixes it.

The nearest achievable version is already in your own table: **a 2% quick target with the wide 2 x ATR stop and the
24 h time exit.** It wins 89% of the time at +0.017R. The "early loss-cut" is the 24 h clock, which already closes 30%
of trades at -0.23R so that only 16% ever reach the stop. Leverage is bounded by the stop, not by the exchange: with
no liquidations allowed, the exchange leverage setting must satisfy L <= 0.8 / stop distance, which is 3-4x for a 20%
stop.

What can still be improved is the drift per trade (proposals 1, 3, 4), the number of independent bets (proposal 2),
and where the holding time is spent (proposal 5). Only proposal 6 changes the instrument so that tight stops become
natural, and it probably cannot be traded on Delta alone.

## 1. The core problem

**The intraday price is a fair coin at every bracket you tested.** For a driftless price, the chance of touching a
target T before a stop S is S / (S + T). Your achieved win rates sit on that line, and the break-even after costs,
(S + c) / (S + T), sits above it:

| Stop / target | Fair-coin win rate S/(S+T) | Achieved | Break-even after 0.30% costs |
|---|---|---|---|
| 2% / 0.4% | 83.3% | 84% | 95.8% |
| 2% / 1% | 66.7% | 71% | 76.7% |
| 4% / 0.8% | 83.3% | 86% | 89.6% |
| 4% / 2% | 66.7% | 69% | 71.7% |
| 4% / 4% | 50.0% | 50% | 53.8% |
| 2% / 4% and 4% / 8% (2:1) | 33.3% | 34% | 38.3% / 35.8% |

The achieved rate beats the fair coin by 0-4 points. The break-even needs 2-12 points. That gap is the whole loss.

**Costs are per trade; the edge is per hour held.** The edge is about 0.7% gross per 24 h. A bracket exits after
roughly S x T / (daily volatility)^2 days. With 10% daily volatility (derived from ATR):

| Structure | Typical time in trade | Gross drift harvested | Cost | Cost as share of the stop |
|---|---|---|---|---|
| 2% stop / 0.4% target | about 12 minutes | 0.006% | 0.30% | 15% (0.15R) |
| 4% stop / 4% target | about 4 hours | 0.11% | 0.30% | 7.5% (0.075R) |
| 0.5 x ATR stop (5%) | hours | under 0.2% | 0.30% | 6% (0.06R) |
| Rule A, 20% stop, 24 h | 24 hours | 0.7% | 0.30% | 1.5% (0.015R) |

The edge is +0.011R to +0.021R. A 2% stop pays 0.15R in costs per trade, seven to fourteen times the edge. Only the
24 h hold collects enough drift to cover the round trip. This also explains test 3: halving the stop to 1 x ATR took
the result from +0.016R to -0.050R.

**Stop overshoot does not shrink with the stop.** Stops fill at -1.08R on a 20% stop, an overshoot of 1.6% of price.
That overshoot comes from the minute the stop is hit, so on a 2% stop the same minute costs a large fraction of R.

**Leverage multiplies a negative number and adds a liquidation.** Derived for 2% / 0.4% at 5x on the full balance: a
win adds about 1.5%, a loss removes about 10.5%, 84% / 16%. Log growth per trade is
0.84 x ln(1.015) + 0.16 x ln(0.895) = -0.005, which is zero dollars after a few hundred trades. This matches your
"every cell goes to $0". At 20x the liquidation price is under 5% away on coins that move 20% in a day.

**Why the early-cut rules fail.** Winners and losers pull back equally in the first hours, so every price-based cut
removes as many winners as losers and pays the cost early. Proposals below therefore use non-price information
(funding, premium, listing structure) or change the number and length of bets. None uses a new price pattern.

**Two things worth checking in your own results before testing anything new** (both derived, not known):

- The 2% target has the same mean as rule A but far less spread. From your table: winners net +0.085R, so the 11%
  losers average about -0.53R, and the per-trade standard deviation is about 0.25R against about 0.65R for rule A.
  The paired test on mean R called it a failure, but for account growth it is better. Your own Kelly figures point the
  same way (rule A 4.4%, QUICK 7.5%). Compute average log growth per trade for the 2% target at 4%, 6%, 8% and 10%
  risk.
- Rule A on Binance 2024-26 is positive in all three years, and by my estimate (about 2,000 trades, mean about
  +0.033R, standard deviation about 0.65R) its t is close to 2 already. So the pass bar is partly met by the
  baseline. I therefore give two probabilities per proposal in section 4.

## 2. Proposals

Shared settings unless a proposal says otherwise: entry at the 00:03 UTC close; stop at 2 x ATR14% with the 32.5%
skip; target 2.0% below entry; time exit at 24 h; isolated margin with exchange leverage L = min(3, floor(0.8 / stop
distance)); your fill model unchanged. With the 2% target, a win is +0.085R, the average loss is about -0.53R, and the
break-even win rate is 0.53 / (0.53 + 0.085) = 86%.

**Funding must be in the P&L for every test below, baseline included.** A short receives the funding rate x notional
at each settlement when the rate is positive and pays when it is negative. The prompt's fill model does not mention
it. On pumped coins one day of funding is the same size as the whole edge (0.4% of notional).

### Proposal 1. Paid-to-wait short: funding and premium gate, with a carry-flip cut

- **Rule.** From the top 2 gainers, trade a coin only if all three hold at 00:00 UTC:
  - last funding rate before 00:00 >= 0;
  - previous day's funding sum >= 0;
  - last perp premium before 00:00 >= -0.05%.
  Otherwise skip it (no replacement, so trades stay paired with the baseline).
- **Loss-cut.** While the trade is open, close at the next 1-minute close if a funding settlement prints below -0.05%
  (per 8 h equivalent). If premium history is hourly or finer, also close if the 60-minute mean premium is at or
  below -0.30%, checked on the hour.
- **Exit and size.** Shared settings. Risk 6% of balance per trade, notional cap 60%.
- **Mechanism.** Negative funding and a perp trading under its index after a pump mean shorts are already crowded and
  the buying is spot-led. Those are squeeze conditions, and the short also pays carry. Positive funding means
  leveraged longs are paying to hold, which is the population that sells into a stall. The cut uses positioning, not
  price: a flip from being paid to paying marks new short crowding, which a pullback chart cannot show.
- **Expected.** Win rate 89-91%, R:R about 1:6, mean +0.03R to +0.04R. Break-even win rate 86%.
- **Data.** Last funding, previous-day funding sum, premium history.
- **What is different from test 7.** Funding was tested in thirds on a 1 x ATR or 5% stop with a 2:1 target, a
  structure that loses 0.05-0.16R whatever the filter. Here the thresholds are fixed at zero in advance, the exit is
  the one with a positive mean, funding cash is counted, and the premium and the intraday flip were never tested.
- **Test.** Thresholds are not fitted. On 2021-23 check only the sign: mean R of skipped trades must be below mean R
  of kept trades. If not, stop. Then one run on 2024-26.

### Proposal 2. Breadth: short the top 5 at half the risk each

- **Rule.** Short ranks 1 to 5 instead of 1 to 2. Shared exit settings. Risk 3% of balance per trade (15% open risk,
  down from 20%), notional cap 30% of balance each, so up to 150% of balance in total notional.
- **Mechanism.** No new signal. The edge is a small drift shared by many pumped coins, and the noise is mostly
  coin-specific. Five bets at half size carry the same expected profit as two at 7.5% risk with a smaller daily swing,
  so the account can hold more total notional. This is the legitimate use of leverage here: more positions, not
  tighter stops.
- **Expected.** Same 89% win rate and R:R per trade if ranks 3-5 behave like ranks 1-2. Derived: with a 0.3
  correlation between same-day trades, the daily return-to-risk ratio rises about 20%.
- **Data.** Daily OHLCV only.
- **What is different.** The number of picks was never varied.
- **Test.** Score ranks 3-5 as their own strategy against the bar. On 2021-23 they must have mean R above zero, or
  stop. Report the correlation of same-day trades; above about 0.6 the benefit is gone.

### Proposal 3. Seasoned coins with a spot market only

- **Rule.** From the top 2 gainers, trade a coin only if a spot market exists and listing age is at least 180 days.
  Shared exit settings, risk 6%.
- **Mechanism.** On an old coin with spot, holders have inventory to sell into the pump and spot-perp arbitrage can
  supply shorts. A new or perp-only listing has neither. Its pumps run for several days on thin float, and its 14-day
  ATR is not yet a fair measure of its range, so the "2 x ATR" stop is too tight exactly there. This selects coins
  where the stop is sized correctly relative to the noise.
- **Expected.** Win rate 89-90%, mean +0.03R, fewer -1.08R stop-outs. Break-even 86%.
- **Data.** Spot-market flag, listing age.
- **What is different.** Neither variable is in the tested filter list.
- **Test.** 180 days is fixed in advance. Sign check on 2021-23 (stop-out share must be lower in kept trades), then
  2024-26. Secondary split to report but not trade: spot vs perp volume ratio on the pump day.

### Proposal 4. Hold 72 hours and use leverage for the overlap

- **Rule.** Same entry and the same 2 x ATR stop. No target. Time exit at 72 h (alternative 48 h; pick one on
  2021-23). No new entry in a coin already held. Risk 3% of balance per trade, up to 6 open positions, 18% open risk.
- **Mechanism.** Drift grows with time held and noise with its square root, and the cost is paid once. If the fade
  continues on days 2 and 3, the same round trip buys three times the drift: about (2.1% - 0.3%) / 20% = +0.09R
  before the extra stop-outs. Overlapping positions are what need the extra notional.
- **Expected.** Win rate 55-60%, R:R about 1:1, mean +0.04R to +0.06R if the drift persists, and worse than the
  baseline if it does not. Break-even about 50%.
- **Data.** The 3 days of 1-minute data per pick; funding for the carry.
- **What is different.** Every test so far holds 24 h or less. This goes against "quick", and I include it because it
  is the direction the arithmetic favours.
- **Test.** First on 2021-23, measure the mean short return from hour 24 to 48 and from 48 to 72, with no stop. If
  either is not positive, abandon without looking at 2024-26.

### Proposal 5. Clock window: trade only the hours where the drift accrues

- **Rule.** Enter at 00:03, exit at 08:00 UTC (alternative 04:00; pick one on 2021-23). Stop scaled to the shorter
  window: 1.15 x ATR14% for 8 h (about 11.5%), 0.82 x ATR14% for 4 h (about 8%). No target in the primary test. Risk
  6% of balance. Notional cap raised to 150% of balance, L = min(5, floor(0.8 / stop distance)).
- **Mechanism.** If most of the 24 h fade happens in a known part of the day, the remaining hours add noise and no
  profit. Dropping them allows a tighter stop, a larger position and a shorter hold: the owner's style, reached by the
  clock and not by price.
- **Expected.** Win rate about 55%. It beats the baseline only if the window holds most of the drift. Derived: the
  first 8 h must contain more than about 75% of the 24 h gross drift, or the first 4 h more than about 67%. An even
  spread would be 33% and 17%.
- **Data.** 1-minute data only.
- **What is different.** Tests 4 and 5 exit on price behaviour. This exit ignores price.
- **Test.** On 2021-23, split the unstopped 24 h short return into six 4-hour blocks. If no window clears the share
  above, abandon. Otherwise freeze the window and run 2024-26 once.

### Proposal 6. Hedged premium convergence (different instrument)

- **Rule.** Universe: yesterday's top 10 gainers with a spot market. On each hour in the next 24 h, if the perp
  premium is at least +1.00% and the last funding rate is at least +0.05%: short the perp and buy equal spot
  notional. Target: premium at or below +0.10%. Stop: premium at or above entry + 1.00%. Time exit 24 h. One
  position per coin. Notional 50% of balance per leg.
- **Mechanism.** The premium is the one series here that must return to zero, and its noise is a fraction of a
  percent, not 20%. A tight stop and a quick target are natural on it.
- **Expected.** P&L = premium captured + funding - 0.60% (four fills). A win is about +0.3% plus funding, a loss about
  -1.6%, so the break-even win rate is about 84%. I expect 85-90%, few trades.
- **Data.** Premium history, funding, spot-market flag.
- **Limits.** It needs a venue where the owner can hold spot. If the hedge sits on another venue, the perp leg alone
  can still be liquidated by the coin's move, so its leverage stays at 2-3x. Treat as research unless both legs can
  share margin.
- **Test.** No baseline to pair with. Fix the thresholds above, run 2021-23 for the sign, then 2024-26.

**Not proposed: going long the pumped coins for the first hours.** The fair-coin table shows no continuation to
collect. The one conditional case, long when funding is deeply negative, is the mirror of proposal 1 and would have
too few trades to reach t = 2. I put it under 5%.

## 3. Test protocol for all six

- Use one day as the unit for t-statistics. Same-day trades are correlated.
- Six proposals are six tries. Require a paired z of at least 2.64 instead of 2.0 for a claim of improvement.
- Run each with stops filled exactly at the stop and at the minute close. Believe a result only if it survives both.
- Binance 2024-26 is the judge. Delta 2025-26 is confirmation only: 1,022 trades at this edge size cannot reach t = 2
  (derived: a +0.02R edge with a 0.65R spread needs about 4,200 trades).

## 4. Ranking

"Improves" = passes the bar and beats rule A on the same trades (the standard used in your tested list).
"Bar only" = mean R above zero in 2024, 2025 and 2026 with t at least 2, which the baseline may partly meet already.

| Rank | Proposal | Improves | Bar only |
|---|---|---|---|
| 1 | 1. Funding and premium gate with carry-flip cut | 15% | 35% |
| 2 | 2. Top 5 at half risk (ranks 3-5 judged alone) | 15% | 30% |
| 3 | 3. Seasoned coins with spot only | 10% | 25% |
| 4 | 4. Hold 72 h with overlap | 10% | 15% |
| 5 | 5. Clock window | 6% | 8% |
| 6 | 6. Hedged premium convergence | 5% | 5% |

The chance that at least one of the six genuinely improves on rule A is, in my judgement, about 35-40%. The chance
that any of them delivers the literal style (stop under 5%, leverage of 10x or more, growing account) on the unhedged
short is under 5%.

**Plain answer.** Tight-stop scalping of these coins cannot work: the price is a fair coin intraday and the costs are
7-14 times the edge at a 2% stop. The version the owner can have is: 2% quick target, about 89% wins, the wide stop
kept as a disaster stop, the 24 h clock as the loss-cut, risk of about 5-6% of balance per trade, and exchange
leverage of 3-4x used as a margin setting. If proposal 1 or 2 passes, add it on top of that.
