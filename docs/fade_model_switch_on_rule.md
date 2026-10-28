# When may a model choose the gainer-fade trades? (fixed 2026-10-28, before any grade)

Owner, 2026-10-28: "how will model training work here?" → "approved". Fixed before the recorder
ran, so nothing seen afterwards can shape it. Code: `live/fade_learning.py`. Changing these rules,
the features (`FEATURES`) or the model settings (`deltabt/signal_model.model()`) restarts the count.

## What is recorded and labelled
- Every morning, after the two paper trades are entered: every eligible gainer (D−1 change > 0,
  turnover ≥ $250k, a 14-day ATR), up to the top 20 by D−1 change, traded or not.
- Readings known at entry: rank, D−1 change, log turnover, ATR%, close position in D−1's range,
  D−2 change, D−1 volume vs its 30-day mean, funding (per 8 h), open-interest change over D−1,
  BTC's D−1 change, and — new, no history exists — order book (spread, USD depth within 1% each
  side, imbalance) and the last 50 trades (aggressive-buyer share, USD traded).
- A day later: as-if R under both exits (A, QUICK), size-free and before funding (the research R),
  entered at the close of the first traded minute from 00:00 UTC (within 15 min) like the real trades.

## The weekly grade (Mondays, from 01:00 UTC)
- Train on every labelled candidate from before the graded week minus one day (`MIN_TRAIN` 400);
  test on the week's candidates (`MIN_TEST` 50). Target: R_A > 0.
- Each test day: the model's own top 2 by score vs the rule's top 2 (rank 1–2, what the accounts
  trade) vs 2 random candidates (2,000 draws). Mean R under both exits is stored per day.

## The rule
A week **qualifies** with ≥ 50 test candidates over ≥ 5 days. All qualifying weeks are pooled;
none may be dropped. The model is **ELIGIBLE** to choose the daily two (for one exit) only when,
over at least **4** qualifying weeks pooled:
1. its picks' mean net R (that exit) is **above zero**;
2. it beats the **rule's** picks by a day-block bootstrap with P(not better) ≤ **0.05**; and
3. it beats **random** picks with P ≤ **0.05**.

Eligible is not adopted: the owner decides, and a switch starts its own paper run first.

## What to expect (stated so a FAIL is not a surprise)
On 2025–2026 history no daily reading predicted which gainers squeeze (764 filters; the ranking
flipped between years, rank correlation −0.23). A model on the daily readings alone is expected to
FAIL. The order-book and trade-flow readings are the only new information; ~2–3 months of
recording are needed before the first fair verdict (training needs ≥ 400 labelled rows ≈ 4 weeks,
then 4 qualifying graded weeks).
