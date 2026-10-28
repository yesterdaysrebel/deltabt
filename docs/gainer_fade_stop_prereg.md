# Gainer fade WITH A STOP — pre-registration (2026-10-28)

Written and committed before any stopped-version result exists. Follows `docs/gainer_fade_prereg.md`,
whose no-stop version FAILED on dev (net −0.30%/trade; median +1.90%, win 57%, killed by squeezes,
worst −162%). This version was suggested BY that dev result, so **the dev window cannot judge it;
only the sealed hold-out can.** The hold-out (pick days 2026-08-01 → 2026-10-27) has not been opened
for any version of the fade.

Owner, 2026-10-28: "yes" to testing (A) the fade with a stop and (B) the same restricted to the coin
groups that looked better in both dev halves — both judged once on the hold-out.

## Common to A and B
- Day D, universe and eligibility exactly as `docs/gainer_fade_prereg.md` (crypto perps listed today,
  D−1 change > 0, D−1 turnover ≥ $250k). K = 2 picks per day, largest D−1 change first.
- **Entry:** short at D's 00:00 UTC open (5:30 AM IST).
- **Stop:** entry × (1 + k × ATR%), ATR% = 14-day average true range of the DAILY candles up to and
  including D−1, divided by D−1's close. **k is chosen on dev from {1.0, 1.5, 2.0}** by A's mean net R,
  then frozen and used for A and B on the hold-out. A coin without 14 prior daily candles is skipped.
- **Stop fill:** walk D's 1-minute last-traded-price candles; the first minute whose HIGH reaches the
  stop exits at max(stop, that minute's CLOSE) — the repo's `ltp_close` convention. A coin-day with no
  1m data is skipped and counted.
- **Otherwise:** exit at D's last 1-minute close (24:00 UTC). No target.
- **Costs:** taker 0.05% on entry and exit notional. Funding not included (reported if available).
- **R per trade** = (short return − fees) / stop distance (as a fraction of entry).

## B's groups (fixed now)
A pick is in B's set if its Delta tag (`deltabt.signal_features.tag_of`, today's tags) is **defi** or
**layer**, OR its D−1 turnover is **below $712,048** (the 33rd percentile of D−1 turnover among dev
top-10 picks, computed 2026-10-28 before this file). B takes the top 2 eligible gainers among those.

## Baseline: random coins under the same stop
For each day, 2 random coins from the turnover-eligible set (A: all liquid coins; B: liquid coins in
B's groups), same entry, same k × ATR stop, same fill, same costs. 2,000 draws.

## Decision
- **Dev (Jan–Jul):** only chooses k. Reported, not judged.
- **Hold-out (read once):** a version PASSES if (1) mean net R > 0 AND (2) the share of random draws
  with mean R ≥ its own is ≤ **0.025** (0.05 split over A and B). If neither passes, the fade idea is
  CLOSED. If one passes: a $250 account replay is reported and the owner decides whether it goes to
  paper on the bot — never straight to money.
- Reported as context only: win rate, median, worst, stop-hit share, the $250 account (2% risk per
  trade, 2 positions a day, 5x cap), funding.
