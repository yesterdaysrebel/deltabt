# High leverage + ROE take-profit on rule A — pre-registration

Written and committed 2026-11-09 BEFORE the test ran (owner: "I ran manual backtest on real bot (rule A) and found
that most of the losers may have won with certain ROE, with good position size (couple of hundred units) and
leverage we can achieve good buck ... taking home $1000 monthly is possible with couple of trades per my manual back
tests on delta 2026").

## Trades and exits
Rule A's picks (top 2 previous-UTC-day gainers, stops ≤ 32.5% as in every test), entry at the daily open, minute
candles over 24 h, isolated margin **M = $300 per trade** (two trades a day use $600 of margin).
- Leverage **L ∈ {5, 10, 20}**; ROE take-profit **X ∈ {10, 20, 30, 50, 75, 100%}** = a price fall of X ÷ L.
- Stop variants: (a) **liquidation only** — the position is liquidated when price rises 1/L − 0.5% (maintenance)
  and loses the whole margin; (b) **stop at −50% ROE** (price +0.5/L), filled at max(stop, that minute's close),
  never worse than the liquidation loss.
- First touch wins; a minute touching both counts as the loss. Otherwise the 24 h close.
- Fee 0.05% of notional each side (= 0.1% × L of the margin per trade). Funding left out.
Result per trade in dollars = M × ROE. Monthly $ = the sum of a calendar month's trades.

## Judgement
Delta 2026 is where the owner found the pattern, so it is **in-sample** and cannot confirm it. The best (L, X,
stop) on Delta 2026 by total $ is chosen there. It **passes** only if, at that same setting, ALL hold on the six
other periods (Binance 2021, 2022, 2023, 2024, 2025-26, Delta 2025):
1. the total is positive in at least 5 of 6;
2. the median month across those periods is positive;
3. the mean month across them is at least **$1,000** (the owner's goal) — reported regardless.
Also reported for every setting: win rate, liquidations, total $, months up, worst month.

## Consequence
Pass → a rules amendment can be written for the real account (owner's yes). Fail → not used.

## Addendum A — smaller leverage (written before it ran; owner: "how do the result look with smaller leverage?")
Same trades, margin, targets, fees and judgement, with **L ∈ {1, 2, 3}** and a third stop variant (c) **rule A's
stop** (2 × ATR14%, the real bot's), filled at max(stop, that minute's close), never worse than liquidation. The
best of these settings on Delta 2026 is chosen and judged on the six other periods by the same three conditions.
Result of the first run (5–20x): FAIL (`out/gainer_fade/high_lev_roe.txt`).
