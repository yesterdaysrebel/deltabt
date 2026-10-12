# Pre-registration — new-listing drift on Delta India perps (2026-10-13)

Committed before any listing's data was fetched. Owner approved the fetch and the test on 2026-10-13.
Scripts: `scripts/fetch_listings.py` (data), `scripts/new_listing_lab.py` (test).

## Hypothesis

A newly listed perp tends to fall over its first weeks (early holders and airdrop recipients sell into
thin books). A short opened once the listing-day chaos has passed, held for three weeks, earns a
positive net return — and more than the same coin shorted at a random later point in its life.

## Universe and windows

Every perp in Delta's 2026-10-05 ticker list with a launch date in the window (`launch` field):
OLD = launched 2024-06-01..2025-12-19 (107 perps); DEV = launched 2025-12-20..2026-09-01 (70 perps,
so ≥ 30 days of data exist). **Survivorship:** perps delisted before 2026-10-05 are absent; those fell
most, so the bias runs *against* the hypothesis. Floors: ≥ 30 listings with usable data per window,
else that window cannot answer.

## Data

For each listing: Delta 1-minute LTP and mark from the first served candle to +45 days, funding
(1h series), and a later-life block for the control (days 90..135 after listing, where it exists by
2026-10-13). Read-only public API, ~9,000 requests, into `data/listings/` (gitignored).

## Trade rules (fixed)

- **Entry:** short at the close of the minute 48 hours after the first served candle (day 2 close).
- **Stop:** 30 % above entry, checked minute by minute on mark; fills at that minute's LTP close plus
  slippage. **Exit:** 21 days after entry at the close (7 and 14 days reported). Whichever first.
- **Costs:** taker both legs (0.05 % × 1.18), slippage 10 bps per leg (new perps are thin; the spread
  snapshot does not cover them), funding at every 8h settlement crossed. 1R = the 30 % stop distance,
  so returns are reported in % of entry and in R.
- One position per coin. **One position at a time** (chronological, first listing wins) reported.

## Control

For each listing with a later-life block: the same short at a random minute within days 90..135, same
stop, hold and costs; 50 draws per window. p = share of draws whose mean net return ≥ the real mean.
This separates "new listings fall" from "small perps fall anyway".

## Pass rule

Per window: N1 net return per trade > 0; N2 listing-day-clustered t ≥ 2; N3 beats the control at
p ≤ 0.05. **PASS = N1, N2, N3 in both windows.** Power: ~70–107 listings a window with per-trade spread
~40 % gives a standard error near 4 %, so an average 3-week drift of about 8 % or more is detectable.

## Liquidity, reported

The result restricted to listings whose first-45-day median daily dollar volume ≥ $250k (the dry run's
own screen floor), since a $250 account cannot short the rest.

## Conduct

One run per window; a bug found after: fix, log here, rerun, report both. Trade tables committed.

## RESULT 2026-10-13 — FAIL (`out/sweep/five_min_arm_lab/new_listing/result_2026-10-13.txt`)

177 listings fetched (all with early data, 163 with a later-life block); 105 / 70 produced a trade.

| window | 21-day short, net per trade | launch-day t | control (same coins, random later start) | stopped out | N1 / N2 / N3 |
|---|---|---|---|---|---|
| OLD (105) | **+6.8 %** (+0.23R), win 58 % | +1.63 | −0.3 %, p 0.00 | 42 % | PASS / fail / PASS |
| DEV (70) | **+2.8 %** (+0.09R), win 59 % | +0.59 | −2.7 %, p 0.00 | 30 % | PASS / fail / PASS |

Direction replicated: shorting a new perp beats shorting the same perp later in its life, in both
windows, and the 7 → 14 → 21-day profile rises in both. The size is modest and noisy — the launch-day t
is 1.6 and 0.6 against a bar of 2 — because new perps are wild: a 30 % stop was hit on 30–42 % of
trades and the winners are the ones that kept falling. **The tradeable subset is inconsistent:** liquid
listings (median daily $ volume ≥ $250k) were −13.6 % on OLD (18 listings) and +4.6 % on DEV (40). One
position at a time: 18 trades +2.0 % (OLD), 8 trades +8.7 % (DEV). Funding cost 0.2–0.5 % a trade.
Survivorship runs against the hypothesis (delisted launches are missing), so the true drift is if
anything larger — but on perps a $250 account could not short. Verdict unchanged: FAIL on N2.
