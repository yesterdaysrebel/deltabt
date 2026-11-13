# Make "high leverage, quick profit, early loss-cut" work on a crypto short strategy

You are an independent quantitative trading researcher. Everything you need is in this message; you cannot open files
or run code. I will backtest your proposals myself, so they must be EXACT, mechanical rules.

## The owner's goal (do not argue with it; find the best way to achieve it)

> "If we increase leverage and cut trades at some point of movement we take more profit; for losers we cut them
> before the stop-loss is hit."

The owner wants: bigger positions (more leverage), winners closed quickly at a fixed profit, losers cut early and
small, no liquidations, reward:risk ideally at least 1:2 or a high win rate, and a growing account. Start balance about
$1,640 on Delta Exchange India (USDT-margined perpetuals, up to about 20-50x on most coins, taker fee 0.05% a side).

## The strategy today ("rule A")

- Every day at 00:00 UTC, rank all Delta perpetuals by the PREVIOUS UTC day's % change. Short the top 2 gainers.
- Stop-loss = entry x (1 + 2 x ATR14%), where ATR14% is the 14-day average true range as % of price. Median stop
  is about 19-20% above entry. Skip the coin if the stop would be more than 32.5% away.
- No target. Close after 24 hours.
- Sizing: risk 10% of the balance per trade (loss at the stop), notional capped at 60% of the balance, leverage 1-3x.
- "QUICK" variant: same, but close the whole trade at +0.2R (a fall of 0.2 x the stop distance, about 4%).

## How it performs

Binance, 2021-2026, 3,608 picks: +0.011R per trade on average, 56% win.
By year: 2021 -0.018R, 2022 +0.036R, 2023 -0.035R, 2024 +0.009R, 2025 +0.031R, 2026 +0.065R.

Delta, 2025-01 to 2026-10, 1,022 picks, realistic fills: +0.021R per trade (2025 -0.017R, 2026 +0.067R).

| Exit type (Delta) | Share of trades | Average |
|---|---|---|
| Stop hit | 16% | -1.08R |
| 24 h close in profit | 55% | +0.47R |
| 24 h close at a loss | 30% | -0.23R |

So the edge is thin. It comes from many coins drifting down a bit over 24 h, and the wide stop survives the big
intraday swings: these coins just pumped and often move 20%+ in a day.

## Fill model used in every test (keep your rules compatible)

- Entry at the 00:03 UTC close. The bot cannot fill at exactly 00:00.
- A target fills only if the price trades 0.1% through it.
- A stop fills at the worse of the stop price and that minute's close.
- Costs: 0.1% spread/slippage plus 0.05% fee per side.
- 1-minute data. When a stop and a target fall in the same minute, the stop counts.

## Data available for your rules (all known at decision time, nothing else)

- For each pick: 1-minute high, low and close for the 3 days from entry. Intraday rules may use only the minutes
  already passed.
- Daily OHLCV for all coins. From it: the previous day's pump %, wick, 3-day return, volume vs the 7-day average,
  breakout vs the 7-day high, and ATR.
- BTC 1-minute and daily data.
- Per coin and day:
  - funding rate: the last one before 00:00, and the previous day's sum;
  - open-interest change;
  - long/short account ratio and top-trader ratio;
  - taker buy share;
  - spot vs perp volume ratio and whether a spot market exists;
  - listing age in days.
- Perp premium/basis history.

## ALREADY TESTED: all failed (paired vs the baseline, on the same trades)

**1. Take profit when the coin falls p% in our favour, keeping the 2x ATR stop.**

| Take profit at | Mean R | Win rate |
|---|---|---|
| 2% | +0.017R | 89% |
| 3% | +0.011R | 82% |
| 5% | +0.012R | 72% |
| 8% | +0.010R | 64% |
| 10% | +0.005R | 61% |
| 15% | +0.004R | 58% |
| 20% | +0.010R | 57% |
| 0.2x / 0.5x / 1.0x the stop distance | +0.005R / +0.006R / +0.008R | — |

None beat the baseline with paired z >= 2.

**2. Tight FIXED stop plus a quick target, with leverage.** Fixed $ risk on Binance 2021-26, ideal fills, from $1,500.

| Stop | Target | Win rate | Mean per trade | Account at 2x leverage |
|---|---|---|---|---|
| 2% | 0.4% | 84% | -0.12% | $17 |
| 2% | 1% | 71% | — | $96 |
| 4% | 0.8% | 86% | -0.01% | $501 |
| 4% | 2% | 69% | — | $103 |
| 4% | 4% | 50% | — | $0 |
| 2% or 4% | 2x the stop | 34% | — | — |

- At 5x, 10x and 20x, every cell goes to about $0.
- On the 30 paper QUICK trades (Delta 1m replay, 25% margin):
  - an 8% stop with a 1.6% target gave $1,500 -> $1,779;
  - an 8% stop with an 8% target gave -> $1,999;
  - 2% and 4% stops lost money.

**3. ATR-scaled stop with a 2:1 target (Delta, 1,022 trades, real fills).**

| Stop | Target | Mean R | Win rate | Account |
|---|---|---|---|---|
| 2x ATR (about 20%) | 2:1 | +0.016R | 55% | — (target hit only 1%) |
| 1x ATR (about 10%) | 2:1 | -0.050R | 47% | -90% |
| 0.5x ATR (about 5%) | 2:1 | -0.160R | 37% | -99% |

**4. "Flip" early-exit signals, on top of the stop or instead of it.** All eight were worse than the baseline:
- a new 1 h / 2 h / 4 h high;
- the price closes above its 4 h average by 0.25 or 0.5 ATR;
- 3 higher 1 h lows;
- BTC up 1%;
- adverse by 0.5 ATR at hour 1.

**5. Stall exit: close if the price is stuck in a 2-5% band for 2-6 hours** (always / only in profit / only in loss).
No variant passed. The best was +0.017R vs +0.011R, with z 1.3.

**6. Entry-timing triggers with a structure stop (above the day's high so far) and 2:1 or 3:1 targets.**

| Trigger | Mean R |
|---|---|
| Breakdown below the first 1/2/4 h low | -0.06 to -0.12R |
| Failed push +3%/+5%/+10% back below the open | -0.06 to +0.03R |
| Lower high | -0.02R |

**7. Filters with a 1x ATR stop or a fixed 5% stop and a 2:1 target.** Thirds of pump, wick, 3-day return, volume
surge, breakout, ATR, BTC's previous day, funding, OI change and long/short account ratio, plus pairs. All were chosen
on 2021-23 and all failed on 2024-26.

**8. Scale-out.**
- Close 1/2 or 2/3 at +0.2R and let the rest run (Delta). This raises the win rate to about 66% but leaves the mean
  unchanged.
- Trailing stops: no gain.

**9. Size and leverage.**
- R per trade does not change with leverage.
- Kelly-optimal risk is about 4-8% of the balance (rule A 4.4%, QUICK 7.5%).
- Risk of 15-20% of the balance per trade loses money even with the same trades.
- Leverage above about 3x adds nothing, because the notional rarely needs it.

**10. Other families on 20 large coins**, judged out of sample on 2024-26: pullback, dip, breakout + BTC, Asia session,
NR7, momentum. All failed the bar. Trend/momentum showed something in 2021-24 but was flat or negative in 2025-26.

Project finding so far: on these pumped coins, the eventual winners and losers pull back by about the same amount in
the first hours, so no early-cut rule has separated them yet.

## What I want from you

1. **The core problem.** Explain briefly why quick-target plus tight-stop plus leverage loses here. Use the numbers
   above: win rate needed vs achieved, and the cost drag as a fraction of a small stop.

2. **Up to 6 NEW proposals** that could make the owner's style work. Each must be different from everything in the
   tested list, or say exactly what is different. A proposal may change any of:
   - which coins / which days are traded (a selection where intraday noise is smaller relative to the move);
   - when to enter (later in the day, after a specific event);
   - the exit (a loss-cut rule using information that plausibly separates losers early);
   - the instrument or direction (for example go LONG the pumped coins for the first hours, or trade a different
     universe).

3. **For each proposal give:**
   - the exact rule with numbers (entry time / condition, stop, target, time exit, size, leverage);
   - the mechanism: WHY it should work, and why the early-cut signal should separate eventual losers from winners;
   - the expected win rate and R:R, and the break-even win rate after costs;
   - which data from the list above it uses;
   - how to test it without overfitting (chosen on 2021-23, judged on 2024-26; paired with the baseline).

4. **Rank your proposals by your honest probability that they pass**: mean R > 0 in each test year 2024, 2025 and
   2026, and t >= 2. Say plainly if you think the owner's style cannot work on this strategy and what the nearest
   achievable version is.

Be concrete and concise. No generic trading advice.
