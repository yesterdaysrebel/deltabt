# Rule A / QUICK / the ladder on Binance futures 2021–2024 — pre-registration

Written and committed 2026-11-09 BEFORE any Binance data was downloaded (owner: "all four" — review the
rule A ladder results, including a test on data never used to build any rule). Nothing in this repo has
ever looked at Binance prices for these years. Changing anything below after the data is fetched voids it.

## Data
- Binance USDT-margined perpetual futures, from Binance's public archive (data.binance.vision): every
  symbol the archive lists under `futures/um`, **including delisted ones**; symbols ending in `USDT` only,
  no dated contracts (`_`), no index products (`BTCDOM`, `DEFI`, `FOOTBALL`, `BLUEBIRD`).
- Daily candles from 2020-12-01 (ATR warm-up); pick days **2021-01-15 .. 2024-12-31**, judged per calendar
  year (four years). 1-minute candles only for picked coin-days.

## The rules (identical to the Delta research, nothing re-tuned)
- Picks: on day D, coins with previous-day change > 0 and previous-day quote volume >= $250,000; the top 2
  by previous-day change. ATR14% from daily candles up to D−1.
- Entry at D's daily open; stop = entry × (1 + 2 × ATR14%); only stops <= 32.5% away (what a 1x account
  takes). First touch on 1m candles; a minute touching both stop and target counts as the STOP; a stop fills
  at max(stop, that minute's close); else exit at the last 1m close of D. Taker 0.05% each side. No funding.
- **Rule A:** no target. **QUICK:** target 0.2 × the stop distance below the entry.
- Accounts: $1,500 at the start of each year; sizing exactly `scripts/fade_ladder_account.py` (day's trades
  sized off the morning balance, 45% position cap at 1x, compounding): flat 5%, flat 10%, ladder 10/5/3
  (10% < 20% below the high-water mark, 5% from 20%, 3% from 35%, back to 10% only at a new high).
  Step-up variants: high-water mark over a rolling 90 days; step up one level when the last 20 trades won
  >= 65% (held unless a further −10%).

## Pass rules
1. **Rule A has an edge** if its mean net R per trade is > 0 in **at least 3 of the 4 years** AND the pooled
   mean is **>= 2 standard errors** above zero.
2. **QUICK has an edge**: the same test on QUICK's R.
3. **The ladder 10/5/3 is kept over flat sizing** if, in at least 3 of 4 years, its worst drop is smaller
   than flat 10%'s AND its end balance is >= flat 5%'s.
4. **A step-up variant replaces the ladder** only if, in at least 3 of 4 years, it ends higher than the
   ladder AND its worst drop is no more than 5 points deeper.

## What follows from the result
- Rule A fails (1) → no real-money rule A (ladder or flat); the real account stays on, or moves to, QUICK only
  if QUICK passes (2), else neither is funded beyond the current tiny run.
- Rule A passes but the ladder fails (3) → rule A at flat 3%.
- This is a different venue (coins, liquidity, fees differ slightly): a pass says the effect is general, not
  that Delta will match; a fail is strong evidence against funding it.
