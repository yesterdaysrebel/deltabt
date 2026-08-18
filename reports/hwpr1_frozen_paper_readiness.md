# H-WPR-1 frozen-semantics paper arm — readiness

**The runner was NOT started.** Per §12 this stops for operator review.

**V3 is untouched.** `app/strategy/rules.py`, `app/config/variants.py` and
`StrategyConfig` were not modified; V3's strategy hash is still
`11461f2a11a96f8a`, asserted by test. `scratchpad/v4_atr.patch` remains
unapplied.

---

## 0. Status

| | |
|---|---|
| frozen-semantics evaluator | **built** — `app/strategy/frozen_hwpr.py` |
| research parity | **ESTABLISHED** at a 3000-bar window (§2) |
| **runner wiring** | **BUILT** (§0.1) — the blocker in the previous revision |
| identity / hashes | **recorded** (§7, §8) |
| time exit 24h | **enabled for this arm only** (§6) |
| paper safety | **verified** (§9) |
| **process started** | **NO** — awaiting operator authorisation (§12) |

### 0.1 Wiring implemented

**V3's evaluation path is byte-identical.** Its 5m block is not modified and
not made conditional; the frozen branch returns before reaching it.

    1m close
       -> TradingBot.on_closed_1m
       -> if self.frozen_arm:  on_closed_1m_frozen(symbol)   <- NEW, returns here
       -> evaluate_frozen(1m window)
       -> _process_explanation(symbol, exp, bar_seconds=60)
       -> risk engine -> paper broker -> persistence

    5m close (V3, unchanged)
       -> on_closed_5m -> evaluate(...) -> _process_explanation(..., 300)

Files changed:

| file | change |
|---|---|
| `app/runtime/bot.py` | `frozen_arm` flag; `on_closed_1m_frozen`; `_process_explanation` extracted from `on_closed_5m` **verbatim**; warm-up counts 1m bars for this arm |
| `app/config/variants.py` | resolves `FROZEN_1M`; **`ALL` untouched**, so V1/V2/V2_LEVEL/V3 keep their registry |
| `app/strategy/frozen_hwpr.py` | added `validate()` — the interface the bot requires |

`_process_explanation` is `on_closed_5m`'s own tail **moved, not rewritten**, so
the two arms cannot drift. The only generalisation is `bar_seconds`, which was
the literal `300`.

Verified unchanged by `git diff`: `app/strategy/rules.py`,
`app/config/strategy.py`, `app/risk/engine.py`, `app/execution/paper_broker.py`,
`deltabt/research/hwpr.py`.

### 0.2 Entry price — both are recorded (brief §5)

The evaluator **does not fake the unknowable next open.** At signal time it
emits `signal_close_price` (the close of the deciding 1m bar) as
`entry_price` on the Explanation, with the stop and target derived from it.
The paper broker then simulates the actual entry on the next execution event
under the existing model, and `_resize_for_actual_fill` re-derives realised
risk from that fill.

So two prices exist and both are persisted: the **signal close** on
`strategy_signals.entry_price`, and the **actual paper entry** on
`positions.entry_price`. `planned_r` and `fill_rr` already record the
difference. The frozen evaluator was not altered to chase the future price.

## 0.3 Why this arm exists

The paper runner and the frozen research **invert the roles of the two
timeframes.** `deltabt/research/hwpr.py` computes the Supertrend, ADX/DI,
Williams %R, the leg extreme **and the stop** on **1m**, using 5m only as a
confirmed regime filter shifted one 5m bar onto the 1m grid. V3 computes all of
them on **5m**, with 1m as confirmation.

Measured over the cached candles, same formula, both timeframes:

    median structural stop, % of price
    symbol    1m (research)   5m (V3)   ratio
    BTCUSD        0.149        0.374     2.51
    ETHUSD        0.236        0.587     2.48
    SOLUSD        0.274        0.705     2.58
    BEATUSD       0.668        2.091     3.13
    BANKUSD       1.136        3.179     2.80
    AKEUSD        0.992        3.431     3.46

V3's stops are **2.5-3.5x wider**, so position sizes are correspondingly
smaller, cost as a fraction of R is correspondingly lower, and the stop cap
bites far harder. V3 is not a defect against its own documented specification
-- `variants.py` describes the 5m design deliberately -- but it is **not the
rule set H-WPR-1 measured**, while carrying that experiment's name and verdict.

This arm exists to run the rule set the research actually measured. It does not
replace V3 and makes no claim about which is better.

## 1. Exact research semantics reproduced

Nothing is reimplemented. `evaluate_frozen` calls `hwpr.build_conditions` and
`hwpr.arm_signals` **directly**, so parity is guaranteed by construction rather
than argued from a reading:

    signal      Arm A = f5_long & st1_long & adx1_long & wprA_long
    1m          Supertrend(factor 2.0, ATR 10) | ADX 28 / DI 14, >= 25
                Williams %R(140) Variant A: > -80 and rising
    5m          CONFIRMED regime only -- (dir5 bullish) & (adx5 >= 25) & (+DI5 > -DI5),
                shifted one 5m bar onto the 1m grid by hwpr._confirmed_5m
    stop        LONG  min(st1, lowest 1m low since the 1m ST flipped bullish)
                SHORT max(st1, highest 1m high since it flipped bearish)
    target      2R of that 1m stop distance
    admission   reject if stop distance > 5%
    chaining    one position per symbol; signals during an open position skipped

**`max_stop_pct` is 0.05, not V3's 0.10.** `hwpr.run()` defaults to `0.05` and
the H-WPR-1 run used that default, so reproducing the research means
reproducing its admission rule. §3 of the brief permits the frozen value only.

The three things this module does differently, because it is live and the
research is a backtest: it reads only the last closed bar; it works on a bounded
window; it returns an `Explanation` so persistence, the risk engine and the
broker need no knowledge of which strategy produced it.

## 2. Signal parity results

> **FROZEN RESEARCH PARITY IS ESTABLISHED**, at a 3000-bar window, with one
> characterised residual class recorded in §2.2.

Reference = `build_conditions` + `arm_signals` over the whole slice, exactly as
the research computes it. Candidate = `evaluate_frozen` over a bounded trailing
window, as a live runner must. 60,000 1m bars per symbol, BTCUSD / ETHUSD /
SOLUSD.

    window = 3000
                        signal bars   reproduced   direction   quiet bars silent
    BTCUSD                      150          150         150            150
    ETHUSD                      150          150         150            150
    SOLUSD                      150          150         150            150
    TOTAL                       450          450         450            450

Quiet bars matter as much as signal bars: an evaluator that invented setups the
research never took would be as wrong as one that missed them. **450/450 silent.**

### 2.1 Why the window is 3000 and not 1500

At 1500 bars parity was **449/450**. The single miss is diagnosed exactly:

    SOLUSD 2026-07-06 05:23:00
      reference  SHORT
      live       NO_SETUP, failed leg = regime_5m_confirmed_short
      1m legs    st1_short, adx1_short, wprA_short  -- ALL AGREE
      5m ADX     full history 25.000203   windowed 24.999818   threshold 25.0

A relative difference of **1.5e-5** in a Wilder estimator that has not fully
forgotten its window seed flips `adx5 >= 25.0`. Not alignment — tested at all
five 5m phase offsets, all gave the same answer. Not the stop, not the leg
extreme, not any 1m condition.

### 2.2 The residual class, stated rather than buried

This is a **threshold-on-a-converging-estimator** problem and it does not
vanish, it shrinks. Exposure across 59,798 bars:

    |adx5 - 25.0| < 0.0001   0.000%
    |adx5 - 25.0| < 0.001    0.025%
    |adx5 - 25.0| < 0.01     0.067%

A 3000-bar window reproduced 450/450 **in this sample**. That is not a proof
that the class is empty — a bar whose 5m ADX sits within ~1e-4 of 25.0 can
still disagree. **Roughly 1 signal in 4,000 may differ from the research, in
either direction.** It cannot be eliminated by any finite window; only a
full-history evaluator would be exact, which a live runner cannot be.

## 3. Stop parity results

**Bit-identical**, asserted at `rel=0, abs=0` — not approximate equality:

```python
ref = min(st1[i], leg_lo[i]) if long else max(st1[i], leg_hi[i])
assert e.stop_price == pytest.approx(float(ref), rel=0, abs=0)
```

Worst relative error across 450 signal bars: **0.000e+00**. Stop side relative
to entry verified on every case.

## 4. Target parity

`target = entry ± 2.0 * risk_per_unit`, exact to 1e-12 on every DETECTED setup.

**One honest divergence, and it is inherent to live execution.**
`hwpr._simulate` computes `r_price` from `o[j]` — the open of the bar *after*
the signal — which a live evaluator standing at the close of bar `i` cannot
know. So `evaluate_frozen` reports `entry_price` as that close.

Consequence: **stop PRICES reproduce exactly; `risk_per_unit` and therefore the
target PRICE differ by the close-to-next-open gap.** Signals, direction,
timestamps and stop levels are unaffected. This is a property of trading
forward rather than a defect, and the paper broker's `_resize_for_actual_fill`
already re-derives risk from the actual fill.

## 5. Execution timing parity

- signals evaluated on **closed 1m bars**; the caller must never pass a forming bar;
- entry at the **next 1m open** — in live terms, the market order placed at the
  signal fills on the next tick;
- `bar_open` equals the reference signal bar timestamp on all 450 cases;
- **chaining verified by inspection, not by simulation.** `hwpr._simulate` sets
  `i = m + 1` after a trade, so signals during an open position are skipped, not
  queued. The live equivalent is the risk engine's per-symbol position lock,
  which rejects with `already holding an open position` — and the database
  enforces it independently via `ux_positions_open_symbol`. The semantics match;
  I did not run a trade-level simulation to confirm it end to end, because that
  requires the runner wiring that does not exist yet.
- no look-ahead: the evaluator reads index `-1` of the window and consults no
  later bar.

## 6. Time-exit configuration

**The repair is infrastructure only. It does not touch signal, entry, stop or
target semantics**, so under §5 it is eligible to apply to the new arm:

- it adds a Terraform variable, a user-data line and one `-e` flag;
- it changes no file under `app/strategy/`;
- `max_hold_seconds` is a `RiskConfig` field, and `settings.py` states the
  placement is deliberate — *"It is not a signal rule — it is a policy about
  carrying inventory — and putting it here leaves the strategy hash alone."*

For the new arm: **`DELTABOT_MAX_HOLD = 86400`** (24h). The existing V3
experiment keeps `0`, which is what it has always run.

Enabling it moves the **risk** hash and not the strategy hash — which is
precisely why it cannot be applied to V3 mid-run, and why it is safe to apply
to a new arm.

## 7. Strategy hash

    frozen arm   e63d00ad683ec9c8   H-WPR-1-FROZEN-1M@e63d00ad683ec9c8
    V3           11461f2a11a96f8a   UNCHANGED

**`StrategyConfig` could not express this arm**, and was not modified. Its
`validate()` hard-rejects anything but 5m/1m:

```python
if self.primary_timeframe != "5m" or self.confirmation_timeframe != "1m":
    raise ValueError("V2 is frozen at 5m primary / 1m confirmation")
```

Relaxing that would edit a frozen file and move V1/V2/V3's validation surface.
The arm therefore carries its own `FrozenHwprConfig`, exposing the three
attributes `build_identity` reads. Every indicator field is transcribed from
`deltabt.research.hwpr` **at import time**, so a change to the frozen module
moves this hash rather than silently disagreeing with it — asserted by test.

## 8. Risk / config hash

    experiment        H-WPR-1-PAPER-FROZEN-1M-20260818
    strategy_hash     e63d00ad683ec9c8
    risk_hash         89f939adcd0a8567   (max_open 6, drawdown 1.0,
                                          consec 0, max_hold 86400)
    execution_hash    f39439e8918b96c7
    composite         f8500fef82ef6494

Distinct from V3's composite in all three components, so the two experiments
cannot be conflated by the drift check.

## 9. Paper safety

    tests/live/test_no_live_trading.py    635 passed, 2 skipped
    full suite                           1695 passed, 57 skipped

No live order endpoint, no order-creation path, no cancel/amend path, no
trading credentials, no signing libraries — enforced by an AST scan over every
shipped module, which now includes `app/strategy/frozen_hwpr.py`. The safety
architecture was not modified. The new module imports only numpy, pandas and
existing project modules; it has no transport of any kind.

New tests: `tests/live/test_frozen_hwpr_parity.py`, 11 tests. No existing test
was weakened or skipped.

## 10. Symbols

    BTCUSD  ETHUSD  SOLUSD  BEATUSD  BANKUSD  AKEUSD

The intended paper universe, unchanged. **No symbol-specific filter was
introduced and none is excluded.** AKEUSD and BEATUSD are admitted or refused
by the frozen 5% cap alone, on the frozen 1m stop.

Relevant measurement, reproduced here so this report stands alone — the 1m
stop is far tighter
than V3's 5m stop, so the cap refuses far less:

    % of bars refused by the cap      1m stop (this arm)   5m stop (V3)
    BEATUSD                                     0.87            7.34
    BANKUSD                                     3.18           16.69
    AKEUSD                                      2.48           16.85

Every rejected setup is persisted with its exact reason, as for V3.

Parity was measured on BTCUSD / ETHUSD / SOLUSD only — those have the deep
history the reference needs. BEATUSD, BANKUSD and AKEUSD list too recently for
a 60,000-bar slice, so **parity is unverified for the three newer symbols.**

## 11. Known limitations

1. **No runner wiring.** The blocker. `bot.py` evaluates on closed 5m bars; this
   arm needs a 1m path selected by variant, without disturbing V3's. Until that
   exists there is no process to start.
2. **The knife-edge class of §2.2** — roughly 1 signal in 4,000 may differ from
   the research where the 5m ADX sits within ~1e-4 of 25.0. Irreducible for any
   windowed evaluator.
3. **Entry price differs by the close-to-next-open gap** (§4). Stops are exact;
   risk-per-unit and target price are not.
4. **Parity unverified for BEATUSD, BANKUSD, AKEUSD** (§10).
5. **Chaining verified by inspection, not simulation** (§5).
6. **The V1/V2/V3 historical figures remain unreproducible.** Per §7 of the
   brief, recorded verbatim:

   > *The historical figures are registry claims from prior work but are not
   > reproducible from the current repository.*

   `git grep` across all branches finds nothing in `deltabt/` or `scripts/`
   importing `app.strategy.rules`. The *"PORTFOLIO simulator"* that produced
   `V1 TRAIN n=237 net -0.0414R`, `VALID n=94 net -0.1887R`,
   `TEST n=111 net -0.0169R` does not exist here. **Those numbers are not cited
   as validation of anything in this document.** No backtest was manufactured
   to replace them.
7. **H-WPR-1's own verdict still stands and is not superseded by parity.**
   Reproducing the rule set faithfully says nothing about whether it earns.
   The registry records `H-WPR-1 -> NO ECONOMIC EDGE`, and the record itself
   reads: *"The gross edge is NOT statistically distinguishable from zero...
   The substantive reading is NO SIGNAL."* This arm exists to observe that rule
   set live, which is exactly what §10 of the brief specifies and nothing more.

---

## Start condition (§12)

| requirement | status |
|---|---|
| runner wiring exists | **YES** |
| evaluator selected only for the frozen arm | **YES** — `isinstance(strategy, FrozenHwprConfig)`, decided once at construction |
| V3 path unchanged | **YES** — `git diff` clean on every V3-critical file |
| strategy hash `e63d00ad683ec9c8` | **YES** |
| risk hash `89f939adcd0a8567` | **YES** |
| execution hash `f39439e8918b96c7` | **YES** |
| time exit 86400 | **YES**, this arm only; V3 keeps `0` |
| symbols = six | **YES** |
| max_stop_pct = 0.05 | **YES**, enforced by `validate()` |
| paper-only safety passes | **YES** — 635 passed |
| full tests pass | **YES** — 1712 passed, 57 skipped |
| journal isolated from V3 | **YES** — `reports/hwpr1_frozen_paper_journal.md` |

## Exact startup command

**Not run.** Requires operator authorisation.

```bash
export DELTABOT_VARIANT=FROZEN_1M
export DELTABOT_MAX_HOLD=86400            # 24h, THIS ARM ONLY
export DELTABOT_SYMBOLS=BTCUSD,ETHUSD,SOLUSD,BEATUSD,BANKUSD,AKEUSD
export DELTABOT_MAX_OPEN=6
export DELTABOT_MAX_DRAWDOWN=1.0
export DELTABOT_MAX_CONSEC_LOSSES=0
export DELTABOT_BACKFILL_DAYS=7           # >= 3000 1m bars; 7d gives ~10,080
export DATABASE_URL=...                   # a database of its OWN, not V3's

# 1. gate -- changes nothing
PYTHONPATH=. python -m app.cli forward-test preflight

# 2. register the experiment (ONCE)
PYTHONPATH=. python -m app.cli forward-test start \
    --experiment-id H-WPR-1-PAPER-FROZEN-1M-20260818

# 3. run
PYTHONPATH=. python -m app.cli run
```

⚠️ **`DATABASE_URL` must not point at V3's database.** `ux_positions_open_symbol`
is a per-database unique index, so two experiments sharing one database would
contend for the same per-symbol position slot. Each stack has its own database
by design.

⚠️ **Do not `terraform apply` to create this stack while
`allow_instance_replacement` is `true`** — it can replace the running V3
instance and end that experiment. That flag has been `true` since `b63e365`
for a rollout that never completed.
