# Selling daily BTC/ETH options — pre-registration (2026-10-28, before any option price is fetched)

Owner, 2026-10-28: "look for something which has 70-80% win rate in both years. I am open to anything"
→ chose "option 2": selling out-of-the-money options, a family that wins most days by construction and
loses on large moves. No option data has been looked at before this file, so 2025 builds and 2026 is a
sealed test.

## The trade (one "strangle" per underlying per day)
- **Instruments:** Delta India DAILY options on BTC (contract 0.001 BTC) and ETH (0.01 ETH), which
  settle at 12:00 UTC (5:30 PM IST).
- **Entry:** at 12:00 UTC the day before expiry D (24 h to expiry). S = the perp's (BTCUSD / ETHUSD)
  1h close at 12:00 UTC. Sell 1 PUT at the listed strike nearest S × (1 − m) and 1 CALL at the strike
  nearest S × (1 + m). "Listed" = the first strike with Delta mark-price history, probing outwards from
  the rounded target (BTC grid $200, ETH grid $10; up to ±4 steps).
- **Entry price:** Delta's `MARK:` 1h close at 12:00 UTC × **0.90** (a 10% haircut for selling at the
  bid in wide daily-option spreads; fixed now).
- **Exit:** hold to expiry. Settlement value = intrinsic from the perp's 1m close at 11:59 UTC on D
  (Delta settles on an index average; the perp is the proxy).
- **Fees:** Delta options fee, min(0.01% of S × contract, 3.5% of premium), charged at entry and again
  at settlement on the settlement value (0 when it expires worthless).
- **P&L per strangle** (USD) = premiums × 0.9 − intrinsic − fees. **Win** = P&L > 0.
  Also reported in basis points of underlying notional so BTC and ETH are comparable.

## Build (2025-01-15 … 2025-12-31) — chooses m only
m ∈ {1%, 2%, 3%, 4%, 5%}: the m with the highest mean P&L (bps, BTC and ETH pooled) among those with a
build win rate ≥ 70%. Frozen before the test is computed.

## Test (2026-01-01 … 2026-10-27, opened once)
**PASS** requires, pooled over BTC and ETH: (1) win rate ≥ 70%; (2) mean P&L > 0 with one-sided p ≤ 0.05
(t-test over DAYS, each day's BTC+ETH result one observation); (3) mean P&L > 0 for BTC and for ETH
separately. Reported, not judged: worst day, worst week, longest losing run, puts-only and calls-only,
and a $250 account selling one BTC and one ETH strangle a day.

## Risks stated up front
Short options lose without a cap on a big move: a 10% day costs ~5–8% of notional on one side against
premiums of a few basis points. The test will show how often that happened in 2025–2026, not how bad
the next one can be. Margin and liquidation rules for short options on a $250 account are NOT modelled
here and must be checked before any paper trading.
