# 1m pullback inside a 5m trend — pre-registration (2026-11-16)

Owner, 2026-11-16: works with Williams %R(140) and Supertrend(10, 2), "1m mostly, 5m for confirmation"; asked "do you
see a setup forming here?"; approved a single test of the one candidate. Written before any of the test data was
downloaded.

## Where the candidate came from (already seen — NOT evidence)
Descriptive studies on data A (the owner's coin-days) and B (careful dataset, top gainers): with the 1m AND the 5m
Supertrend on the same side and the 1m %R low in its range (−80..−60 in the move's own terms), the mean price move
to %R's next band close was +0.40% / +0.07% (A, t < 1) and +0.00% / +0.22% (B). Every other state was noise. A close
traded cousin on A (`hand_rule_test.py`, dip+5m) lost under both fill bounds. Expectation stated in advance: **fails**.

## Rule (long; short is the mirror: Supertrends down, %R measured from the top, bands swapped)
- Bars: Delta 1m trade candles; 5m bars built from them on UTC 5-minute boundaries; a 1m bar uses the last 5m bar
  CLOSED at or before its own close.
- STATE at a 1m close: 1m Supertrend(10, 2) up AND 5m Supertrend(10, 2) up AND 1m %R(140) above −80 and at or below −60.
- ENTRY signal: the state is true at this close and was not true at the previous close. One position per coin at a
  time; signals while in a position are ignored. First 300 1m bars of a coin's series are warm-up.
- EXIT signal: the first later 1m close with %R at or above −20 (the band ahead) or at or below −80 (the band behind).
  This is exactly the horizon the descriptive number was measured over. No other stop, target or time limit.
- A position still open when the data ends is dropped.

## Fill model (one, with two bounds)
- STRICT: entry at the OPEN of the bar after the signal, 0.05% worse, taker fee; exit at the open of the bar after
  the exit signal, 0.05% worse, taker fee. The entry bar must have traded (volume > 0), else the signal is skipped.
- LENIENT: entry at the signal bar's close with the maker fee; exit at the exit signal's close with the taker fee.
- Fees: the owner's account, GST included: taker 0.059%, maker 0.0236%.
- Result per trade = % of the position. No leverage (a sizing question after a pass).

## Data (untouched by A and B)
- Universe: the 30 Delta India USD perpetuals with the highest 24 h turnover at download time, excluding the
  stock / metal / oil tokens. (Coins liquid today: a mild selection, not on direction.) List saved with the data.
- Period: 2026-08-01 00:00 UTC .. 2026-11-16 00:00 UTC, 1m candles (`scripts/pullback_data.py` → `out/pullback/`).
- Dropped before judging: (a) any coin-day with more than 20% flat 1m bars (Supertrend's ATR collapses on Delta's
  forward-filled no-trade minutes — found in the descriptive study); (b) any coin-day that is in data A or data B.
  A trade belongs to the coin-day of its entry.

## Pass bar
All of: mean per trade > 0 under STRICT; t ≥ 2 with trades summed by UTC day; STRICT mean > 0 in each half of the
period (split at 2026-09-23); STRICT mean > 0 on at least 60% of the coins with 30+ trades; longs and shorts each
> 0 under LENIENT. If LENIENT mean ≤ 0 the idea is dead and nothing is tuned. No parameter is changed after the run;
numbers are quoted only after a second, independent implementation reproduces them.
