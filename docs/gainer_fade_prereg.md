# Gainer fade — pre-registration (2026-10-28, before any result was looked at)

Owner, 2026-10-28: "start different idea" → chose **fade the gainers**, judged **strictly**.
Written and committed BEFORE the lab script exists and before any return is computed.

## Why this and not another indicator
Every indicator entry on the daily top gainers is break-even or worse over 9 months
(CONTEXT.md: flip_st_short −0.053R, just %R −0.046R, all ML/OI/flow filters null). Across every
rule tested, shorts on gainers lost far less than longs ("gainers mean-revert", 2026-10-19). This
tests that observation directly, with no indicator at all.

## The rule (one primary cell)
- **Universe, each UTC day D:** Delta India crypto perps listed today (`top_tag == "crypto"`),
  daily candles (`ltp`, `1d`) from the local `CandleStore` (Delta REST). Eligible on D if day D−1
  had **change > 0** and **turnover ≥ $250,000** (same definition as `scripts/top_gainers_backtest.py
  cmd_picks`: change = close/open − 1 of D−1, turnover = volume × contract value × typical price),
  and day D has a candle.
- **Picks:** the **K = 2** eligible coins with the largest D−1 change.
- **Trade:** short at D's open (00:00 UTC = 5:30 AM IST, the bot's re-pick), cover at D's close
  (24:00 UTC). No stop, no target (this measures the raw effect; sizing/stops come later, and only
  if this passes).
- **Return per pick:** `short = −(close/open − 1)`; **net** = short − 0.10% (Delta taker 0.05% each
  side). Funding is NOT included in the decision (reported separately when available).

## The baseline (what "edge" means)
The market moves all coins together; a short on a falling day wins regardless of the pick. So the
primary metric is **excess**: pick's short return minus the **same-day mean short return of all
coins eligible by turnover alone** (turnover ≥ $250k on D−1, any change). Fees cancel in excess.

## Windows (sealed hold-out)
- **Dev:** pick days 2026-01-01 … 2026-07-31. Built, debugged and read first.
- **Hold-out:** pick days 2026-08-01 … the last complete UTC day before the run (≈ 2026-10-27).
  Not computed until the dev result is written into CONTEXT.md and committed.

## Decision (fixed now)
**Dev PASS** requires all three, on K = 2:
1. mean **net** short return per pick > 0;
2. mean **excess** > 0 with one-sided p ≤ 0.05 (t-test over DAYS: each day's mean excess is one
   observation, so two picks on one day are not counted as independent);
3. beats **random picks**: 2,000 draws of 2 random coins per day from the turnover-eligible set;
   share of draws with mean short return ≥ the picks' ≤ 0.05.

**Hold-out PASS** (read once): mean net > 0 AND mean excess > 0. p is reported, not required
(≈ 3 months is too short to require it).

Only if both pass: a second pre-registration for the tradable version (stop for 2%-risk sizing,
$250 account, 2 a day, 5x cap) — it is NOT designed here, so it cannot be tuned on this result.

## Reported as context only (not decisions)
K = 1, 5, 10; long side (buy the gainers) as a sanity check; a split by D−1 change size.
None of these can turn a FAIL into a PASS.

## Known biases, stated up front
- **Survivorship:** only coins listed today. Delisted coins (often ones that collapsed) are missing,
  which most likely makes shorts look WORSE than reality, not better — noted, not corrected.
- Daily candles: entry exactly at 00:00 UTC; the bot would enter ~30 s later.
