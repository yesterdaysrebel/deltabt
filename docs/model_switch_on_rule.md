# When may the model filter trades? (fixed 2026-10-15, before the first weekly grade)

The owner approved this rule on 2026-10-15, before any live grade existed, so that nothing seen
afterwards can shape it. Code: `live/learning.py` (`switch_check`, constants `SWITCH_*`,
`REACH_1R_BREAK_EVEN`). Changing the rule, the model settings (`deltabt/signal_model.py`) or the
features (`deltabt/signal_features.py`) restarts the count from zero.

## What the model is for

The bot sees ~15–25 takeable signals a day on the coins it trades and takes at most 2, one at a
time, first come first served (since 2026-10-16 it also records ~3x as many on coins it never
trades; see the amendment below). A model that scores signals could skip weak ones and spend the two daily slots on strong
ones. It has never been allowed to touch a trade: `grade-model` only grades.

## The weekly grade (Mondays, with the daily report)

- Train on the 9-month history (`out/signal_lab/signals.parquet`, 5,273 takeable signals) plus
  every labelled takeable live signal entered before the graded week minus 72 h.
- Test on the graded UTC week's **live** takeable signals only (history rows are never scored).
- Two targets, same frozen model settings:
  - **3R**: will the signal end a winner (net R > 0 under the bot's 3R exit)?
  - **1R** (owner, 2026-10-15): will the as-if trade reach +1R before it ends?
- Each signal's score and outcome is stored in the grade (`detail.scored`, `detail.scored_1r`).

## The rule

A week **qualifies** if it has at least **30** live test signals. All qualifying weeks are pooled;
none may be dropped. In each week take the top **30%** by score; compare the pooled result with
2,000 draws of the same number of random signals from each week.

- **3R target ELIGIBLE** when, over at least **4** qualifying weeks, the pooled top-30% mean net R
  beats the random draws at **p ≤ 0.05** and is **above zero**.
- **1R target ELIGIBLE** when, over at least **4** qualifying weeks, the pooled top-30% share
  reaching +1R beats the random draws at **p ≤ 0.05** and is above **52%**. (A 1R exit pays about
  +0.97R and a stop costs about −1.07R after fees and slippage, so ~52% is break-even.) Using it
  would also mean changing the exit to take profit at 1R — a separate decision.

### Amendment 2026-10-16 (owner; before the first grade on 2026-10-19)

From 2026-10-16 the bot also RECORDS signals on coins it never trades: today's gainers ranked
11–30 and a fixed watchlist (BTCUSD, ETHUSD, BEATUSD, AKEUSD; `live/universe.py`). They are
labelled, marked takeable and trained on like every other signal, and they are graded too. Each
scored row (`detail.scored`, `detail.scored_1r`) now carries a 5th element: 1 if the signal's coin
was in the trade universe that day, 0 if it was record-only. Rows graded before this amendment have
four elements and count as trade-universe signals (every coin was tradeable then).

A target is **ELIGIBLE only when the rule above passes twice**: on ALL recorded live signals, AND,
separately, on the trade-universe subset alone — same test, same thresholds (≥ 4 qualifying weeks,
top 30% vs 2,000 random draws per week, p ≤ 0.05, and mean R > 0 / reach share > 52%). For the
subset, a week qualifies only with at least **30 trade-universe signals**. The point: a model may
only filter the trades the bot can actually take, so it must work on those coins, not only on the
wider pool. `switch_check` returns the all-signals figures under `3R`/`1R` (their `eligible` is the
combined verdict) and the subset's under `trade_universe`; the Monday line shows both.

### Amendment 2026-10-19 (owner): the live rule changes to `flip_st_short`

The bot's rule changes from `manual_scalp_both_t3_st` to the owner's flip setup, shorts only
(`SPEC:flip_st_short@5/1`), still watch-only. A model learns "which of *this rule's* signals win",
so from this change on: the history file is the 2,176-signal `flip_st_short` dataset (the image
names the rule it belongs to and `grade-model` ignores a file for another rule); labelling and
takeability run per rule (each signal carries its rule's `strategy_config_hash`); only live signals
of the running rule are graded; and the switch-on count pools only grades of the running rule, so
**the count restarts at zero**. The old rule's signals, labels and its one grade (week of 12 Oct,
AUC 0.65) stay in the database, unmixed and unused.

ELIGIBLE means the owner may switch it on; it does not switch itself on. First use is at 0.5%
risk, like any change to real trading. The weekly report prints the rule's state each Monday.

## What the history says (said once)

On the sealed Aug–Oct 2026 history the 3R model scored AUC 0.52 and its top 30% did no better than
random (−0.088R vs −0.084R); the 1R model scored AUC 0.51 (top 30% reached +1R 50% vs 49% all).
With p ≤ 0.05 the rule will also pass a useless model about 1 time in 20 by chance; a pass is a
reason to try it small, not proof.
