# Strategy: H-WPR-1 Variant A, frozen

## What this is, and what it is not

This is the **previously researched configuration**, forward-tested to validate
the execution engine. It is not a strategy believed to be profitable.

The research program's verdict on this exact family:

| Experiment | Verdict | Gross expectancy |
|---|---|---|
| H-WPR-1 | **NO ECONOMIC EDGE** | +0.032R train, **−0.051R validation** |
| H-TREND-1 | **NO SIGNAL** | +0.058R train, **−0.043R validation** |

Gross expectancy — before any cost — was **negative on the validation segment**.
Net expectancy was −0.35R to −0.46R. Nothing about running it live changes that.

The reason to forward-test it anyway is that it is *frozen and measured*: its
behaviour is known, so any divergence between live and backtest is an engine
bug rather than an open question. A strategy nobody had measured would make the
forward test unfalsifiable.

**Do not read the forward-test record as evidence about the edge.** At roughly a
handful of trades per day, thirty days has nowhere near the statistical power to
overturn or confirm a measured effect this small.

---

## The rule

```yaml
strategy:
  timeframe_primary: 5m
  timeframe_confirmation: 1m
  supertrend:  {atr_period: 10, multiplier: 2}
  adx:         {period: 28, di_period: 14, minimum: 25}
  williams_r:  {period: 140, rule: variant_a}
  target_r: 2.0
  max_stop_pct: 0.05
```

**Long** (short mirrors exactly):

| Timeframe | Condition |
|---|---|
| 5m primary | Supertrend direction bullish (`direction < 0`, Pine's convention) |
| 5m primary | `ADX >= 25` |
| 5m primary | `+DI > -DI` |
| 5m primary | `WPR > -80` **and** WPR rising vs the previous closed 5m bar |
| 1m confirm | Supertrend direction bullish |
| 1m confirm | `ADX >= 25` and `+DI > -DI` |

**Stop** (the frozen H-WPR-1 structural stop):

```
LONG:  stop = min(lowest low since the Supertrend last flipped, supertrend)
SHORT: stop = max(highest high since the flip,                  supertrend)
```

**Target** = entry ± `2.0 × |entry − stop|`. Rejected if the stop is more than
5% from entry.

Everything is computed from **closed bars only**. The evaluation instant is the
5m close; the confirmation reads the 1m bar that closes with it.

---

## Two deliberate departures from the research code

Both are documented in `app/config/strategy.py` and neither is silent.

### 1. The timeframes are inverted

The research implementation evaluates on a **1m** grid with 5m supplying
confirmed trend context. V1 was specified the other way round, and follows the
specification:

```
research H-WPR-1 Arm A:  5m regime AND 1m Supertrend AND 1m ADX/DI AND 1m WPR
                         evaluated every 1m close
V1:                      5m Supertrend AND 5m ADX/DI AND 5m WPR
                         AND 1m Supertrend AND 1m ADX/DI
                         evaluated every 5m close
```

The *structure* is identical — a full indicator stack on the signal timeframe
plus trend agreement from the other — but the roles are swapped. **The two are
not expected to produce the same trades**, and the backtester was not modified
to make them agree.

Measured on 40 days of real BTCUSD 5m data: **922 setups, ~23/day/symbol**, both
directions, no NaN or ordering faults. (H-WPR-1 at a 1m base produced ~12.4
trades/day across four symbols; the higher raw detection rate here is throttled
by `max_open_positions: 1`, `max_trades_per_day: 6` and the cooldowns.)

### 2. ADX period 28, not 14 — a conflict in the brief

Section 2 of the V1 brief says `ADX: period = 14`, while its own heading says
**FREEZE THE RESEARCH RULE** and its body says the configuration is used
"because it is the previously frozen/researched configuration". The frozen
H-WPR-1 constant is `ADX_PERIOD = 28` (Wilder smoothing of DX) with
`DI_PERIOD = 14`.

Both instructions cannot be satisfied. The implementation follows the **stated
intent** — freeze the research rule — because a value invented at implementation
time would make the live signal incomparable to every measured backtest, which
is the one thing V1 must not do.

Changing it is a one-line edit to `ADX_SMOOTHING`. Doing so changes the config
hash, so every signal recorded afterwards is distinguishable in the audit trail
from every signal recorded before. That is the intended way to make the
decision.

---

## Indicators are not reimplemented

Every value comes from `deltabt.indicators` — the same numba functions the
backtester and all eight research experiments used, tested against hand-computed
Wilder values. The structural stop reuses `deltabt.research.hwpr._leg_extreme`.

**Bounded-window recomputation**: on each closed 5m bar, the trailing 1500 bars
are handed to those functions unchanged. This costs milliseconds and makes
backtest/live divergence *structurally impossible* rather than something tested
for and hoped about. Incremental indicator state is where live bots silently
drift from their backtests.

Two preconditions, both enforced:

**Window invariance.** Wilder smoothing forgets its seed, so a long enough
window reproduces the whole-history value at the tail. Tested at bit-equality
for `st`, `direction`, `adx`, `wpr`, `plus_di`, `minus_di` across W and 2W.

**The leg extreme is the exception — and it is the interesting one.** An
extremum-since-the-last-flip does *not* converge: if the leg started before the
window began, the value is the extremum of an arbitrary truncation. Worse, the
first apparent direction change inside a window is a Supertrend *seeding
artifact*, not a market event — measured on a strong trend it moves the
structural stop by **60%** between window lengths.

So it is detected and the bar is **SUPPRESSED**, rather than substituting a
different stop, which would be a silent rule change. On real BTCUSD 5m data this
fires on **0.3% of bars** (37 of 11,368), so it is a genuine edge case rather
than an over-trigger.

**Percentile ADX thresholds are impossible, not merely discouraged.** The
research `_threshold` resolves a percentile over the entire array, which is
non-causal and cannot exist live. `Adx` has no percentile field at all, and a
test asserts it never gains one.

---

## Why the Williams %R rule needed pinning

"Williams %R 140" names a period, not a rule. Four candidates were on the table
in this project and they differ by orders of magnitude in firing rate:

| Rule | Definition | Firing rate |
|---|---|---|
| Original Pine | `prev < −80 and wpr > prev` | **~4 trades / 346 days** — effectively never |
| Traverse latch | arm below −80, fire crossing −20, 30-bar expiry | measured *worse* than off at 15m |
| **Variant A** | `wpr > −80 and rising` | the frozen H-WPR-1 baseline |
| Variant C | `crossover(wpr, −80)` | pre-declared alternative |

V1 accepts **only** `variant_a`. The config loader raises on anything else
rather than falling back, because a silent fallback between rules this different
would invalidate every comparison to the research.

---

## Frozen for the forward test

Do not change, during the 30 days:

- WPR period or rule
- ADX threshold, ADX period, DI period
- Supertrend period or multiplier
- stop logic
- target multiple
- risk model

Changing any of them is a separately registered experiment. The config hash
makes the boundary visible in the data whether or not anyone remembers to write
it down.
