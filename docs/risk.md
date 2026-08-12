# Risk engine

This is the component the project exists for.

The problem statement was not signal quality. It was: *"I have historically lost
money because of emotional decisions, inconsistent position sizing, poor
risk/reward discipline, overtrading, revenge trading and failure to follow
exits."*

Every one of those has a named, tested gate here.

| The problem | The gate |
|---|---|
| inconsistent position sizing | `risk_per_trade`, computed from equity and stop distance, never from conviction |
| poor risk/reward discipline | `minimum_rr`, recomputed from prices — not trusted from the signal |
| overtrading | `max_trades_per_day`, `max_open_positions`, `cooldown_after_trade` |
| revenge trading | `max_consecutive_losses`, `cooldown_after_loss` (deliberately longer) |
| emotional decisions | there is no manual override path in the code |
| failure to follow exits | stop and target are placed with the position and evaluated on every tick |

---

## The gates, in evaluation order

First failure wins, and it is recorded with the limit's name, its configured
value, and the observed value.

```
 1  setup_detected                the explanation must be a DETECTED setup
 2  market_live                   not HALTED, not REOPENING
 3  symbol_allowed                in the configured universe
 4  in_session                    inside a configured trading window
 5  no_existing_position_in_symbol
 6  max_open_positions
 7  max_daily_loss                fraction of START-OF-DAY equity
 8  max_drawdown                  from peak equity
 9  max_trades_per_day
10  max_consecutive_losses
11  cooldown_after_trade
12  cooldown_after_loss
13  stop_distance_positive
14  minimum_rr                    RECOMPUTED, not read from the signal
15  contract_spec_present
16  quantity_positive             after integer contract rounding
17  max_position_notional
18  max_total_notional            counts existing open positions
19  max_leverage
20  realised_risk_within_budget   after rounding, checked again
```

Defaults:

```python
risk_per_trade              = 0.005      # 0.5%
minimum_rr                  = 2.0
max_open_positions          = 1
max_daily_loss_pct          = 0.02
max_drawdown_pct            = 0.10
max_trades_per_day          = 6
max_consecutive_losses      = 3
max_leverage                = 3.0
cooldown_after_trade        = 900s       # 15m
cooldown_after_loss         = 3600s      # 60m, deliberately longer
```

**When a limit is breached, new entries are blocked. Existing positions are
left alone** unless `close_positions_on_breach` is switched on. Force-closing on
a limit breach would convert a risk control into an exit signal the strategy
never produced.

---

## Sizing

```
risk_amount   = equity × risk_per_trade
units         = min( risk_amount / |entry − stop| ,
                     equity × max_leverage / entry ,
                     max_position_notional / entry )
quantity      = floor( units / contract_value )        # whole contracts
```

Rounding is **downward**, so realised risk is always at or below budget — and
gate 20 checks that again afterwards, because "should never" is not a guarantee.

The quantisation is not cosmetic. SOLUSD contracts are 1 SOL; on a small account
a wide stop can round the position to **zero contracts**, and that is rejected
with the arithmetic spelled out rather than silently sized to one.

Every input is recorded on the signal: `equity_before`, `risk_amount`, `entry`,
`stop`, `stop_distance_pct`, `quantity`, `notional`, `estimated_fee`,
`estimated_slippage`.

Costs come from `deltabt.costs.SymbolCosts` using the live product
specification: per-symbol maker/taker fees **× 1.18 GST**, per-symbol tick size,
contract value and funding interval. None of it is hardcoded, because none of it
is uniform across Delta India.

---

## The strategy cannot override any of this

Enforced structurally, not by convention:

- `RiskEngine.evaluate()` takes an `Explanation` — a *description of what was
  observed*. No field on it can raise a limit, change the risk fraction, or skip
  a check. Tested: an explanation claiming `risk_amount = 9999` and
  `quantity = 1_000_000` still sizes to 100 contracts and $50 of risk.
- `reward_risk` is **recomputed** from entry/stop/target. A signal claiming
  RR 99 on a 1R geometry is rejected for failing `minimum_rr`.
- `PaperBroker.submit_order` accepts only an `ApprovedOrderIntent`, which raises
  at construction without a `risk_evaluation_id` and the list of checks it
  passed.
- The strategy package does not import the execution package at all. A test
  asserts this against the import graph, so there is no expression a strategy
  could write that produces an order.

---

## State that survives restarts

```python
equity, peak_equity, day (UTC), day_start_equity, daily_pnl,
trades_today, consecutive_losses, last_trade_at, last_loss_at,
realized_pnl, wins, losses
```

Persisted to `strategy_state` after every position open and close, and restored
before the bot becomes ready. A restart does **not** reset the consecutive-loss
counter or the daily trade count — otherwise restarting the pod would be a way
to escape the discipline, which is precisely backwards.

**Daily counters roll on the UTC date**, not IST. The exchange's funding and
settlement grid is UTC, and a daily loss limit resetting on a different boundary
from the venue's own day is a limit nobody can reason about. IST is a display
concern only.

---

## Why "rejected" is the normal outcome

On real BTCUSD data the strategy detects roughly **23 setups per day per
symbol**. With one position at a time, six trades a day, and a 15-minute
cooldown, the overwhelming majority are rejected.

That is the system working. Every rejection is persisted with its reason, so the
question *"why didn't it take that one?"* is answerable in SQL:

```sql
SELECT bar_open, symbol, rejection_reason, conditions_failed
  FROM strategy_signals WHERE outcome = 'REJECTED'
 ORDER BY bar_open DESC LIMIT 50;

SELECT limit_name, count(*), max(observed_value)
  FROM risk_events WHERE event_type = 'REJECTION' GROUP BY 1 ORDER BY 2 DESC;
```

That second query is the useful one after a week: it tells you which constraint
is actually binding, which is the thing worth knowing before changing anything.
