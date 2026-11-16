# Spike fade, 10% stop, out after 8 hours — forward test on paper (pre-registered 2026-11-16)

Owner, 2026-11-16: disliked the 24 h hold ("it generally flips back"); on "use the one place where the bars carry
information, on paper first" said "Try it". Written before any day after 2026-11-14 was looked at.

## Why forward
The 8-hour cut and the 10% stop were chosen on data already searched (617 trades, 2025-01 .. 2026-11-14: win 56%,
avg win +7.4%, avg loss −7.5%, mean +0.82% strict / +1.01% lenient before fees, t ≈ 2). Only days that did not exist
when the rule was chosen can confirm it.

## Rule (frozen)
- Picks: the paper runner's own rule A picks for each UTC day (both ranks), read from production (`fade_positions`),
  starting 2026-11-15. No pick is added, dropped or filtered.
- Entry: a resting sell 5% above the close of the 00:02 UTC minute; it fills in the first minute from 00:03 to 23:59
  whose high is at least 0.05% through that price. No fill = no trade.
- Stop: 10% above the entry. No target. Out at the close 8 hours after the fill.
- Fills: STRICT (stop at the worse of stop and that minute's close, 0.1% worse; clock exit 0.1% worse) and LENIENT
  (stop 0.1% worse than its price) — `scripts/spike_flip_back.py::trade`, validated against
  `review_real_fees.trade` and a second implementation.
- Prices: Delta 1m trade candles, public API. Result = % of the position, before fees (headline) and after the bot
  account's real fees (maker in 0.0212%, taker out 0.0531%).

## Judging
- The ledger `out/careful/spike8h_forward.csv` gets one row per pick per finished day; nothing is ever removed.
- Verdict at 60 filled trades (about two months) and again at 120: PASS = mean per trade > 0 under STRICT after real
  fees AND at least half the trades win. Anything earlier is a progress note, not a verdict.
- No rule change while it runs. A change restarts the count.
- Paper only: no order is placed and the real account stays as the owner left it.
