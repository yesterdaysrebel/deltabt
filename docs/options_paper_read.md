# What the option-selling paper account is read for (fixed 2026-10-28, before its first sale)

Owner, 2026-10-28: "let's start options now" -> "approved". Code: `live/options_paper.py` (runs in the
gainer-fade runner's loop). Rule = the sealed-2026 pass in `docs/option_selling_prereg.md`: each day at
12:00 UTC sell one BTC and one ETH strangle (put ~1% below, call ~1% above the perp's 1h close), one
contract per leg, held to the next day's 12:00 UTC expiry. Paper only; no order path.

## What the backtest could not know, and is measured here
1. **Fill.** The lab sold at 0.90 x mark. Here each leg sells at the real best bid at entry; the report
   shows the average bid / mark. Below 0.90 on average means the backtest was optimistic about fills.
   (One snapshot on 10-28 read 0.99-1.00, thousands of contracts at the bid.)
2. **Settlement.** The lab settled at the perp's 1h close; here at Delta's own `settlement_price` (its
   index's 30-min average; 10-28: 81,400.67 vs the 30-min index average 81,397, perp close 81,288).

## How the result is read
- Benchmarks from the sealed 2026 test: **76% of strangles won, mean +24.4 bps of notional per
  strangle, worst −1,577 bps.**
- ~120 strangles (BTC + ETH over ~60 days) are needed before a mean of this size sits two standard
  errors from zero (2026 per-strangle SD 134 bps over 600 strangles → SE 17 bps at n = 60, 12 bps at
  n = 120). Earlier weeks are illustration.
- Win rate is not the test: a few losses erase many wins. The test is mean P&L per strangle after fees.
- A loss beyond the backtest's worst (−15.8% of notional) is reported as a new tail event, not noise.

Nothing here moves to real money. That needs Delta's short-option margin rules read for the account
size, a Trading key, and the owner's decision.
