# Quick trades on Delta's non-crypto perps — pre-registration (2026-11-16)

Owner, 2026-11-16: "look for unpopular strategies with quick entry and exit for futures, US stock perps".
Written while the data was downloading, before any price was looked at.

## Why this venue segment, and not crypto again
Crypto quick trades died on cost (0.05% taker + 0.1% spread vs a zero short-horizon drift). Delta's 26
stock-token / gold / silver / oil perps charge 0.01–0.02% a side and, at 07:51 UTC 2026-11-16 (US closed),
quoted 0.001–0.045% spreads (KORUB 0.28%, OIL 0.11%, SOXLB 0.09%). Round trip ~0.03–0.08%: a third of crypto's.
They also have STRUCTURE crypto lacks: an underlying that only trades 13:30–20:00 UTC on weekdays (14:30–21:00 in
US winter time, after 2026-11-01) while the perp trades 24/7, and two gold tokens (PAXG, XAUT) on one metal.

## Data
`scripts/tradfi_quick_data.py`: Delta 1m TRADE candles and 1m INDEX candles (`.DE<symbol>`) from each listing
(2026-02-19 .. 2026-10-13) to 2026-11-16 00:00 UTC. A minute with no trade has no trade candle.

## Fill model (one, with two bounds)
- Signals use only data up to the close of minute t. Entry and exit fill at the OPEN of the next minute that has a
  trade candle, if it is within 2 minutes; otherwise the trade is skipped (entry) or the exit waits for the next traded
  minute (exit).
- Each fill is worsened by `slip`: STRICT 0.05% (crypto-style half spread + impact), LENIENT 0.01%.
- Fees: the product's taker rate on both sides (0.02% stocks, 0.01% gold/silver).
- No leverage in the test: results are % of notional per trade. Leverage is a sizing question after a pass.

## Families (every parameter listed; nothing added after seeing results)
- **F1 basis snap-back.** gap = perp close / index close − 1 on a traded minute. If |gap| ≥ k, trade toward the index;
  exit when |gap| ≤ k/3 on a minute close or after H minutes. k ∈ {0.15, 0.30, 0.50}%, H ∈ {5, 15, 60}. One position per
  symbol at a time.
- **F2 off-hours fade at the US open (stock tokens, not metals/oil).** move = perp price at open−5 min / perp price at
  the previous US close − 1 (weekends included: Friday close → Monday). If |move| ≥ m, at open−5 min go AGAINST it
  (F2f: WITH it), exit at open + X. m ∈ {0.5, 1, 2}%, X ∈ {15, 60, 120} min. Open/close follow US daylight time
  (13:30/20:00 UTC to 2026-10-31, 14:30/21:00 UTC from 2026-11-02); US market holidays are skipped.
- **F3 gold pair.** r = ln(PAXG/XAUT) on minutes where both traded; dev = r − its trailing 1440-minute mean. If
  |dev| ≥ d, short the rich leg / long the cheap one, equal notional; exit when dev crosses 0 or after 240 min.
  d ∈ {0.05, 0.10, 0.20}%. Fees and slip on all four fills.
- **F4 intraday momentum (Gao et al. 2018, stock tokens + SPYX/QQQX).** sign of the return from the previous US close to
  open+30 min; trade that sign from close−30 min to the close. No parameters.

## Split and bar
- DEV: days before 2026-08-15. TEST: 2026-08-15 .. 2026-11-15. Per family the parameter set with the best DEV mean
  (STRICT) is frozen and is the only one judged on TEST.
- PASS: TEST mean per trade > 0 under STRICT, t ≥ 2 with trades grouped by day, positive in both halves of TEST, and
  (F1, F2, F4) positive on at least 60% of the symbols that traded. Anything that passes is re-run by a second,
  independent implementation before a number is quoted.
- Expectation stated in advance: most or all of these fail. F1 on thin stock tokens is the most likely to look good
  and be an artifact of stale last-trade prints; the next-traded-minute fill is there to catch that.
