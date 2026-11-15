# Careful simulation — the rules every implementation follows

Owner 2026-11-15: "a careful simulation over every trade with available data … we need 2–3 runs independent".
Three implementations (`scripts/careful_sim_a.py`, `_b.py`, `_c.py`) are written from THIS text. They share only the
frozen data (`out/careful/dataset.npz`, `picks.csv`, built by `scripts/careful_dataset.py`) and Delta's product table
(`out/delta_margin/product_limits_2026-11-15.json`). They must agree on every trade before any number is used.

## Data
Every pick: Delta's own 1m trade candles from D 00:00 to D+2 00:00 UTC (minutes 0..2880). A minute with no trade:
high = −inf, low = +inf, close = the last close. History 2025-01..2026-10 = the Delta panel's rule A picks (top 2 by
previous-day change, eligible, 2 × ATR14% ≤ 32.5%); real 2026-10-29..11-14 = the paper runner's picks and ranks.

## Sizing (Delta)
`margin` → position notional N: N × (IM(N) + 0.12% fee reserve) = margin, IM(N) = im + ims × max(0, N − cap)
(im, ims, cap = initial margin %, scaling per $, max_leverage_notional), capped at contract limit × contract value ×
price. Liquidation distance = IM(N) − MM(N) (MM = mm + mms × max(0, N − cap)) + extra / N. A coin missing from the
table: im 5%, mm 2.5%, ims 0.001%, mms 0.0005%, cap $50,000, no contract limit.

## Run 4 trade (as built)
1. Ref = close of minute 2 (00:02). Limit sell L = 1.05 × ref, resting. Fills in the first minute k in 3..1439 with
   high ≥ L × 1.0005. Entry e = L. Not filled by minute 1439 → no trade.
2. Margin 0.49 × B, extra 0.25 × B (B = balance at the day's start). Scale s = B / 3,184.
3. Costs: entry 0.02% + 0.05% (of N); exit 0.05% × N × exit/e; cost c = 0.12%.
   P&L(x) = N × (−(x/e − 1) − 0.0002 − 0.0005 − 0.0005 × x/e), never below −(margin + extra).
4. Target T = e × (1 − (500s/N + c)); stop S = 1.0225 e; lock price K = e × (1 − (200s/N + c)); liquidation
   Q = e × (1 + liquidation distance).
5. Each minute j from k+1 to min(2880, k+1440), in this order:
   a. high ≥ S → stop fill x = max(S, close) × 1.001 (STRICT) or S × 1.001 (LENIENT). Not yet locked and x ≥ Q →
      LIQ, P&L = −(margin + extra). Else STOP (not locked) or LOCKED (locked), P&L(x). Done.
   b. low ≤ T × 0.999 → TARGET, P&L = N × (−(T/e − 1) − 0.0002 − 0.0005 − 0.0005 × T/e). Done.
   c. lowest = min(lowest, low) (lowest starts at e).
   d. not locked and low ≤ 0.99 e → locked; S = min(S, K); close ≥ S → LOCKED at close × 1.001. Done.
   e. locked → S = min(S, 1.008 × lowest); close ≥ S → LOCKED at close × 1.001. Done.
   No exit by min(2880, k+1440) → TIME at that minute's close × 1.001.

## Re-sell A (only in the variants that have it)
After a run-4 STOP or LIQ at minute m ≤ 1438, if the account may still trade (below), with B' = balance after it:
1. top = high of minute m. For j = m+1..1439: if low ≤ 0.95 × top → sell at market, price
   p = min(0.95 × top, close) × 0.999 (both fill models). Otherwise top = max(top, high). No trigger → no re-sell.
2. Margin 0.49 B', extra 0.25 B', sized at p. Stop S = 1.0225 p; Q as above. No target, no lock, no trail.
3. Each minute i from j+1 to min(2880, m+1440): high ≥ S → x = max(S, close) × 1.001 (STRICT) or S × 1.001
   (LENIENT); x ≥ Q → LIQ (−(margin + extra)), else STOP with P&L = N × (−(x/p − 1) − 0.0005 − 0.0005 × x/p), floored.
   No stop → TIME at the close of min(2880, m+1440) × 1.001.

## The account
Start $3,184. UTC days in order; month = the pick day's calendar month.
* A day is skipped if: balance < floor (floor $1,000 = the bot's STOPPED state; $50 in the "no floor" variant), or
  the month already has 2 full stops (STOP / LIQ, re-sells included), or the last trade's exit minute (absolute) is
  after this day's minute 2.
* The day's picks each rest an order; the trade is the one that FILLS FIRST (earliest minute; same minute → better
  rank); the other is cancelled. One trade per day, plus its re-sell if any.
* After the trade, balance = max(balance + P&L, 0). A STOP/LIQ adds 1 to the month's stops. Re-sell only if then the
  month's stops < 2 and the balance ≥ the floor.
* Banking variant: on the first pick day of each month (traded or skipped), anything above $3,184 is moved to the
  bank before trading (never traded again). Total = bank + balance.
* Every pick day is visited (the month's stops reset on its first pick day), so a month with no pick days does nothing.
