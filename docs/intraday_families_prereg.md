# Seven intraday families on Delta's liquid perps — pre-registration (2026-11-16)

Owner, 2026-11-16: "You seem to look at the same families. Search for families." Written before any of these rules
was run. Everything tested today was either the gainer fade or %R / Supertrend / sharp-move rules on one coin's own
bars. These seven use DIFFERENT information: another coin, the fair price, volume, the clock, the cross-section,
volatility regime, yesterday's range.

## Data
30 most-traded Delta crypto perps (`out/pullback/universe.json`), 1m trade candles, liquid days only (at most 20% flat
1m bars). **DEV = 2026-03-01 .. 07-31, TEST = 2026-08-01 .. 11-15.** (Both periods were used before, for other rules.)
In at the open of the minute after the signal (that minute must have traded), out at the close N minutes later.
No stop, no target: this measures whether the signal says anything about price. Before fees (owner's instruction);
the after-fee line uses two taker fees (0.118%).

## Families and every variant
- F1 LEAD-LAG. BTC moved theta or more over the last k minutes and the coin has done less than half of that: trade the
  coin in BTC's direction. k in {1, 3, 5}, theta in {0.3, 0.5, 1.0}%, hold in {3, 10, 30} min.
- F2 LAST vs FAIR PRICE. Last price is g or more away from Delta's mark price on a traded minute: trade toward the
  mark. g in {0.1, 0.2, 0.4}%, hold in {3, 10, 30} min.
- F3 VOLUME CLIMAX. A minute with 10x the average volume of the previous hour and a move of r or more: fade it.
  r in {0.5, 1.0}%, hold in {5, 15, 60} min.
- F4 HOURLY CROSS-SECTION. At each hour, rank the liquid coins by their return over the last L; buy the 3 weakest and
  sell the 3 strongest for one hour. L in {1 h, 4 h}.
- F5 SQUEEZE BREAKOUT. The last hour's range is in the lowest quarter of the last 24 hours and the price closes
  outside that hour's range: follow. hold in {15, 60, 240} min.
- F6 YESTERDAY'S HIGH / LOW. The day's first close above yesterday's high (or below its low): follow.
  hold in {15, 60, 240} min.
- F7 HOUR OF DAY. All coins, each UTC hour held open to close: DEV picks the best hour to be long and the best to be
  short.
Every family is tested in the stated direction AND its mirror (before fees the mirror is the exact negative).

## Selection and bar
Per family: the variant and direction with the highest DEV t (trades summed by UTC day, at least 200 DEV trades) is
frozen and is the ONLY one judged on TEST. PASS = TEST mean > 0 before fees, t >= 2.7 (seven families tested), both
halves of TEST > 0. A pass is reported with its after-fee result and is then re-run by a second implementation.
Expectation stated in advance: at most one passes before fees, none after.
