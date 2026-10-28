# Gainer fade — 2025 replication and four squeeze filters (pre-registered 2026-10-28)

Owner, 2026-10-28: concerned the gain is slight ("we need more") → approved: (1) write every rule
first, (2) replicate rule A on 2025, which nobody has looked at, (3) four filters, fixed now, judged
once on 2025. Committed BEFORE any 2025 data is fetched or any filter is computed on any window.

## Rule A (unchanged, `docs/gainer_fade_stop_prereg.md`, k = 2.0)
Day D: crypto perps listed today; eligible if D−1 change > 0 and D−1 turnover ≥ $250k; top 2 by D−1
change; short at D 00:00 UTC; stop = entry × (1 + 2 × ATR14%) (daily ATR up to D−1 / D−1 close); first
1m HIGH ≥ stop exits at max(stop, that minute's close); else exit at D's last 1m close; taker 0.05%
each side; R = (short return − fees) / (2 × ATR14%).

## Window
**2025:** pick days 2025-01-15 … 2025-12-31 (14 days of daily candles needed for the ATR). Coins listed
on Delta India today only (74 had data on 2025-01-01, 150 by 2025-12-01) — survivorship is heavier
than in 2026; coins delisted during 2025 are missing.

## Step 2 — does rule A replicate on 2025? (the primary question)
PASS if mean net R > 0 AND the share of 2,000 random draws (2 random liquid coins per day, same stop,
fill and costs) with mean R ≥ A's is ≤ 0.05. If A FAILS on 2025, the filters are reported but none can
be adopted, and the fade is NOT taken to paper without the owner re-deciding.

## Funding (reported, not part of any decision)
Rate = the `FUNDING:` 1h series (percent per settlement interval), sampled at the product's settlement
instants (today's `funding_interval_seconds`) strictly after entry and strictly before exit. A short
RECEIVES a positive rate. In R: Σ rate/100 / (2 × ATR14%). Shown as "with funding" next to each result.

## Step 3 — four filters, each applied to the eligible set BEFORE taking the top 2 (keeps 2 a day)
- **a — closed near the high:** skip if D−1 closed in the top quarter of its range,
  (high − close) / (high − low) < 0.25. Reason: strong close into midnight = momentum still on.
- **b — weakness confirmed:** entry moves to D 04:00 UTC (9:30 AM IST), only for coins whose 04:00 price
  (close of the 03:59 1m candle) is BELOW D's 00:00 open; stop 2 × ATR14% above the 04:00 entry; exit at
  D+1 04:00 UTC (still 24 h). Ranking still by D−1 change among the confirmed coins.
- **c — shorts not crowded:** skip if the funding rate in force at D 00:00 (last 1h value ≤ D 00:00) is
  below 0 (shorts already paying longs = squeeze fuel).
- **d — not a two-day run:** skip if D−2's change was also > 0 (up two days in a row).

**A filter is ADOPTED only if, on 2025:** its mean net R exceeds rule A's (same days) and a day-block
bootstrap (5,000 resamples of days) puts P(difference ≤ 0) ≤ **0.0125** (0.05 over four filters), AND
the difference also has the same sign on 2026 (Jan–Oct, already seen — a direction check only).
Win rate, average win/loss and the $250 account are reported for each, not judged. No combination of
filters is tested in this round.
