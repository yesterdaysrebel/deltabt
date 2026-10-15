# When may the model filter trades? (fixed 2026-10-15, before the first weekly grade)

The owner approved this rule on 2026-10-15, before any live grade existed, so that nothing seen
afterwards can shape it. Code: `live/learning.py` (`switch_check`, constants `SWITCH_*`,
`REACH_1R_BREAK_EVEN`). Changing the rule, the model settings (`deltabt/signal_model.py`) or the
features (`deltabt/signal_features.py`) restarts the count from zero.

## What the model is for

The bot sees ~15–25 takeable signals a day and takes at most 2, one at a time, first come first
served. A model that scores signals could skip weak ones and spend the two daily slots on strong
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

ELIGIBLE means the owner may switch it on; it does not switch itself on. First use is at 0.5%
risk, like any change to real trading. The weekly report prints the rule's state each Monday.

## What the history says (said once)

On the sealed Aug–Oct 2026 history the 3R model scored AUC 0.52 and its top 30% did no better than
random (−0.088R vs −0.084R); the 1R model scored AUC 0.51 (top 30% reached +1R 50% vs 49% all).
With p ≤ 0.05 the rule will also pass a useless model about 1 time in 20 by chance; a pass is a
reason to try it small, not proof.
