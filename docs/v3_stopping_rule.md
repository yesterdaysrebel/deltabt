# V3 forward test — evaluation stopping rule

**Frozen 2026-08-21, at n = 31 closed trades.** Recorded *before* any further
trades were observed. Any deviation is documented as a deviation, not silently
absorbed.

## Why this document exists

This run has already been evaluated twice, informally, and the two looks
disagreed:

| looked at | n | mean R | t | impression |
|---|---|---|---|---|
| 2026-08-21 (earlier) | 26 | +0.277R | +0.94 | "looks promising" |
| 2026-08-21 (later) | 31 | +0.114R | +0.42 | "no signal" |

Five trades moved the apparent verdict. That is **optional stopping**: if the
decision to conclude is taken whenever the numbers happen to look good, the
false-positive rate is not 5% and is not knowable. The only defence is to fix
the stopping point and the decision rule in advance, and then not look.

## What is under test

| | |
|---|---|
| experiment | `H-WPR-1-PAPER-ATR-20260820-4` |
| stack | `v3` (`deltabt-paper-v3`) |
| strategy | `app.strategy.atr_arm.AtrArmConfig` — 5m primary, 1m confirmation, stop 2 × ATR(10), target 2R |
| symbols | BTCUSD, ETHUSD, SOLUSD, AKEUSD, BEATUSD |
| risk | 0.5% of equity per trade |
| circuit breakers | **disabled** for the duration (`max_daily_loss_pct = 1.0`, `max_trades_per_day = 20`) |

Breakers stay off deliberately. A breaker censors the sample — it conditions
the mean on the day not already having gone badly — so a gated mean is not an
unbiased estimate of expectancy. Measured on this run: gated and ungated
friction per trade were `0.239R` and `0.241R`, identical, while `se` rose from
0.272 to 0.335. Gating costs information and changes no conclusion.

## Stopping point (frozen)

**Evaluate once, at whichever comes first:**

1. **100 closed trades** in this experiment, or
2. **2026-09-04** (14 days from freezing).

No evaluation before that point. No evaluation after it either — the
evaluation happens once, at the stopping point, and its outcome is acted on.

## The metric (frozen)

**Primary: gross win rate**, tested against the break-even rate, not against
zero and not against the random-entry rate.

The arm's measured friction is **0.241R per trade**, so at a 2R target the win
rate that merely breaks even is

    p_breakeven = (1 + 0.241) / 3 = 41.4%

Random entry at a 2R target wins `1/(1+R)` = 33.3%. The arm must therefore beat
**41.4%**, not 33.3%, before any profit exists. At n = 100 with the measured
design effect of 1.28, the one-sided 95% threshold is

    p_breakeven + 1.645 × sqrt(0.414 × 0.586 / 100) × sqrt(1.28) = **50.6%**

**Secondary: event-level mean R.** Entries within 15 minutes of each other are
collapsed into one event before the mean and its standard error are computed.
On this run, 31 trades collapsed to 18 events and 44% of multi-symbol events
resolved unanimously — the five symbols are not five independent bets, and a
per-trade `t` overstates the evidence. The design effect measured at n = 31 was
`n_eff = 24.3 / 31 = 1.28`; it is recomputed at the stopping point rather than
assumed.

## What this rule can and cannot conclude

Stated in advance so the result is not over-read. With `sd = 1.516` and a
design effect of 1.28, the minimum effect detectable at n = 100 with 80% power
is about **+0.34R per trade**.

**n = 100 can only resolve a large edge.** If the true edge is the +0.114R
currently showing, this evaluation will not detect it and must not be reported
as having refuted it. Resolving +0.114R needs roughly **1,780 trades**.

## Decision rule (frozen)

| outcome | condition at the stopping point | action |
|---|---|---|
| **PROMISING** | gross win rate ≥ 50.6% **and** event-level mean R > 0 | extend to n = 250 under this same rule; no config changes |
| **NO EDGE** | event-level mean R ≤ 0 | stop the arm; the entry family is not carried forward |
| **UNDECIDED** | anything else — the expected outcome | stop the arm. An undecided result at n = 100 combined with the frozen-data prior below is not a reason to keep spending; it is a reason to change the question |

**UNDECIDED means stop, not continue.** Written down now because at the
stopping point it will be tempting to read "not refuted" as "keep going".

## The prior this is tested against

Recorded so the forward result is interpreted against what is already known,
not in isolation. From `deltabt.research.run_stop_width`, 4 majors, ~20 months,
train and validation, ~9,000 trades per cell:

- Gross expectancy is **indistinguishable from zero at every stop width**
  tested, from 0.75× to 8× ATR (`gross_r` between −0.054 and +0.056).
- The win rate converges on **exactly `1/(1+R)` = 33.3%** as the stop widens
  (0.187 → 0.340) — the signature of entries carrying no information.
- Net expectancy is **negative at every multiplier**, best case −0.124R at 8×,
  with confidence intervals entirely below zero.

From `deltabt.research.run_symbol_tiers`, all 220 listed perpetuals: **no
symbol on the venue has a lower cost/R than the ones already traded** (0 of 34
cheap-tier candidates beat BEATUSD's 0.105), so the friction is not addressable
by symbol selection.

The forward test is therefore testing a hypothesis the historical data already
rejects. That is a legitimate thing to do — the live arm's geometry differs and
31 trades cannot overturn 9,000 — but it means a positive result at n = 100
should be treated as **surprising and provisional**, not as confirmation.

## What may change during the run

Nothing that enters the strategy, risk, or execution hash. Specifically
forbidden until the stopping point: the ATR multiplier, the symbol set, the
target R, the risk fraction, and the circuit-breaker settings. Each of these
changes the composite experiment identity, `bind_experiment()` fails closed,
and the sample resets to zero.

Permitted: operational monitoring — is the process alive, is `/readyz`
passing, is the config drifting, are candles arriving. Reading the daily report
for **health** is expected. Reading it for **performance** is the thing this
document exists to prevent.

<!-- FROZEN ABOVE THIS LINE -->

SHA-256 of everything above the marker, computed at freeze time:

    ba6ced0b8586a484c639a689973278c7a5b86bf299c04524fc23654d6b4e2100

Verify with:

    sed "/^<!-- FROZEN ABOVE THIS LINE -->$/,\$d" docs/v3_stopping_rule.md | sha256sum

---

## DEVIATION — recorded 2026-08-21, same day as the freeze

**The rule did not fire. The experiment was stopped early, at n = 31.**

`H-WPR-1-PAPER-ATR-20260820-4` was stopped by operator decision hours after
this document was frozen, well before either stopping condition (100 closed
trades, or 2026-09-04). The frozen section above is unchanged and its hash
still verifies; this deviation is appended, not merged into it.

**What triggered the decision, and what did not.** Not v3's tape — at n = 31 it
was still `+0.114R, t = +0.42`, exactly as uninformative as when this document
was written. The new evidence was **v4**, the flip arm, which was not covered
by this rule:

| | v3 (ATR arm) | v4 (flip arm) |
|---|---|---|
| trades | 31 | 45 |
| mean | +0.1141R | +0.0173R |
| t | +0.42 | **+0.08** |
| win rate | 45.2% (break-even 41.4%) | 37.8% (break-even **37.2%**) |
| cost/R | 0.162 | **0.104** |
| net without its single best event | −3.11R | −3.01R |

v4 is a different strategy on a different timeframe carrying **half v3's
friction per trade**, and it independently reproduced the same null while
sitting exactly on its own break-even win rate. Both arms are net-positive only
because of one entry event each; strip those and the two together are −6.11R
over 54 events. That is replication of the family-level result, not a reading
of v3's sample, which is why it was treated as grounds to act where the tape
alone would not have been.

**Was this optional stopping?** Partly, and it is recorded as such rather than
justified away. The defensible part: the decision rests on v4 plus nine
pre-registered offline nulls, all of which either predate this document or were
outside its scope. The indefensible part: the stop still happened at a moment
chosen after looking, and a rule abandoned the day it was written provides no
protection. **The correct handling next time is to freeze the rule across the
whole family before any arm starts, not per-arm after one is already running.**

**Consequence for the record.** v3's forward test is classified UNDECIDED at
n = 31 and is **not** evidence for or against the entry family on its own. The
family's verdict rests on the offline registry and on v4. Neither arm's numbers
should later be cited as a forward-test result; both are terminated samples of
a size this document already stated could not resolve the question.
