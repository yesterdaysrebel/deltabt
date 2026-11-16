# Fade the %R band exit — pre-registration (2026-11-16)

Owner, 2026-11-16: "I mean with wpr what setup can you find". Written before the test data is looked at for this rule.

## Where it comes from (already seen — NOT evidence)
Every "go with the %R move" reading has lost (1m / 5m / 15m / 1h, large coins and hot coins, with and without
Supertrend). The one regularity in the descriptive study (`out/wpr_band_behaviour.txt`, data A and B) points the other
way: after %R(140) leaves a band it closes back inside the SAME band 83–86% of the time, and the mean price move over
that trip, measured in the direction of the leave, was −0.08% on 1m and −0.16..−0.17% on 5m (no costs). Trading
AGAINST the leave therefore showed +0.16% gross on 5m. Two reasons it may not be real money: (1) a fade of a
last-trade move can be bid-ask bounce, which nobody can capture; (2) 0.16% is about the size of the costs.
Expectation stated in advance: **fails under STRICT**.

## Rule
- Bars: 5m bars built from Delta 1m trade candles on UTC 5-minute boundaries. %R over 140 bars.
- BUY signal: %R closes below −20 having closed at or above −20 on the bar before (it has just left the top band).
  SELL signal: %R closes above −80 having closed at or below −80 on the bar before.
- EXIT signal: the first later 5m close with %R at or above −20 or at or below −80 (either band).
- One position per coin at a time; signals while in a position are ignored. A position open at the end is dropped.
- Secondary, reported but not judged: the same rule on 1m bars.

## Fill model (one, with two bounds)
- STRICT (the judge, and the bounce check): entry at the OPEN of the first 1m bar after the signal bar closes, 0.05%
  worse, taker fee; that bar must have traded. Exit at the open of the first 1m bar after the exit signal, 0.05% worse,
  taker fee.
- LENIENT: entry at the signal bar's close with the maker fee; exit at the exit signal's close with the taker fee.
- Fees: taker 0.059%, maker 0.0236% (the owner's account, GST in). Result = % of the position per trade.

## Data
`out/pullback/` — the 30 most-traded Delta crypto perps, 1m, 2026-08-01 .. 2026-11-15, downloaded for
`docs/pullback_5m_trend_prereg.md` and used once, for that rule only. Same hygiene: coin-days with more than 20% flat
1m bars are dropped, and coin-days that are in data A or B are dropped. This is the second rule judged on this data.

## Pass bar
All of: STRICT mean per trade > 0; t ≥ 2 with trades summed by UTC day; STRICT mean > 0 in each half (split
2026-09-23); STRICT mean > 0 on at least 60% of coins with 30+ trades; buys and sells each > 0 under LENIENT.
If LENIENT mean ≤ 0 the idea is dead. Nothing is tuned after the run; numbers are quoted only after a second,
independent implementation reproduces them. Also reported: the worst single trade and the worst day, because the
profile is many small wins and a few large losses.
