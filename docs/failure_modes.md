# Failure modes

What can go wrong, how it is detected, and what the bot does. Each row is
implemented and tested; the test file is named.

---

## The one that matters most

**A silently stale feed.** The process is alive, the TCP socket is open, the
event loop is turning, and no market data is arriving. Every process-level probe
— `kubectl get pods`, a TCP check, a `/ping` endpoint — reports this as healthy.

It is worse than a crash. A crash restarts. This does not, and the bot goes on
believing the last price it saw while positions run unmanaged against a market
it can no longer see.

Three independent defences:

1. The WebSocket client imposes its own receive deadline (30s) and treats expiry
   as a fatal connection error rather than waiting on TCP.
   (`test_halt_and_feed.py::test_silent_socket_raises_stale_not_hang`)
2. `/healthz` returns 503 on data age, not process state, so Kubernetes restarts
   the pod. (`test_recovery.py::TestHealth`)
3. The stale event is counted and alerted, so a *recurring* staleness that
   self-heals each time is still visible.

---

## Market data

| Failure | Detection | Response | Test |
|---|---|---|---|
| Clean disconnect | socket close | Reconnect, exponential backoff + jitter, cap 60s, resubscribe | `test_connection_error_counts_and_resubscribes` |
| **Stale feed** | no message in 30s | Force-close, reconnect, count | `test_stale_feed_triggers_a_reconnect` |
| Undecodable frame | JSON parse failure | Drop, count, continue — one bad frame must not kill the feed | `test_undecodable_frame_is_dropped_not_fatal` |
| Duplicate candle update | same `last_updated` | Drop, count | `test_identical_update_is_a_duplicate` |
| Out-of-order update | older `last_updated` | Drop, count | `test_out_of_order_update_within_bar_ignored` |
| **Late update for a closed bar** | `start <= last_closed` | **Never applied.** A signal may already have been emitted from that bar; retro-editing it makes the audit trail a lie | `test_closed_bar_is_immutable` |
| Missing minutes | bar-open discontinuity | Gap recorded immediately, REST backfill, `/healthz` fails while gaps are recent | `test_missing_minutes_are_detected_and_counted` |
| Impossible OHLC | `high < low`, close outside range, non-positive price | Reject the bar, name the reason, count. **Never repaired** | `test_impossible_bars_are_named` |
| Symbol prints nothing for a minute | no update to roll the bar | Clock fallback closes it after a grace period — deliberately a *fallback*, since the message-driven path does not depend on the local clock | `test_clock_rollover_needs_the_grace_period` |
| Incomplete 5m bucket | fewer than 5 constituent minutes | Bar flagged incomplete; **strategy declines to evaluate** rather than acting on a short bar | `test_incomplete_bucket_is_flagged_not_repaired` |

Timestamps: the socket sends **microseconds**, REST sends **seconds**. The
conversion happens exactly once, at the normalisation boundary. Mixing them
silently produces bar timestamps ~50,000 years in the future.

---

## Exchange maintenance

Delta halts roughly monthly for 60–120 minutes. It appears as a long run of
forward-filled `o=h=l=c`, `volume=0` bars, then a gap-open auction. One measured
reopen was **+0.32% in a single minute**.

That reopen bar is the danger. Every trend indicator in the stack reads it as a
powerful breakout, so a bot that evaluates straight through a maintenance window
will reliably take a large position into an artifact.

```
LIVE ──20 flat zero-volume bars──▶ HALTED ──first real bar──▶ REOPENING ──▶ LIVE
                                      │                           │
                            positions SUSPENDED,          reopen bar is
                            new signals suppressed        NOT tradable
```

- Positions are **suspended, not closed** — Delta does not trigger stops during
  maintenance, so neither does the bot.
  (`test_paper_execution.py::TestHaltBehaviour`)
- The reopen bar is skipped explicitly; trading resumes only after a genuine
  post-reopen bar has closed. (`test_trading_resumes_only_after_a_post_reopen_bar`)
- **A restart during a halt comes up HALTED**, primed from backfilled history —
  otherwise a pod restart mid-maintenance would trade the reopen.
  (`test_restarting_inside_a_halt_comes_up_halted`)
- Live detection is checked against `deltabt.data.quality.halt_mask`, so backtest
  and live agree on what a halt is.
  (`test_halt_state_agrees_with_the_research_halt_rule`)

19 flat bars is thin liquidity. 20 is maintenance. The threshold is the research
constant, not a new invention.

---

## Process and state

| Failure | Response | Test |
|---|---|---|
| `kill -9` with a position open | Rebuilt from the database: same entry, stop, target, quantity, initial risk, same `position_uid` | `test_open_position_survives_a_hard_restart` |
| `kill -9` after signal, before fill | No position. An unfilled order does not become one | `test_crash_after_signal_before_fill_leaves_no_position` |
| `kill -9` after fill | No second fill — `paper_fills` has a unique index on `order_uid` | `test_crash_after_fill_does_not_double_fill` |
| `kill -9` mid-candle | Only the forming minute is lost; backfill re-fetches it once closed | `test_restart_while_a_candle_is_forming_loses_only_that_candle` |
| Restart with a position open | Stop is **immediately** armed. The ticks that would have hit it while down are gone, so waiting for a post-entry tick would leave it unprotected | `test_a_recovered_position_is_immediately_protected` |
| Replayed signal after restart | Refused by the `idempotency_key` unique constraint | `test_restart_does_not_reopen_the_same_signal` |
| Two bot processes | Second fails `pg_try_advisory_lock` and exits | `test_second_instance_refuses_to_start` |
| Lock holder is `kill -9`ed | Postgres releases the lock when the connection dies — no cleanup code of ours runs, and none is needed | `test_lock_released_when_connection_dies` |
| Duplicate open positions found at startup | **Refuses to become ready.** Corrupt state stops the bot; it is not quietly tidied up | `test_duplicate_open_positions_block_startup` |
| Open position in an unconfigured symbol | Refuses to become ready | `test_position_in_an_unconfigured_symbol_blocks_startup` |
| Postgres unreachable or read-only | `/healthz` 503 via an actual write probe (a readable database can be read-only after failover) | `test_unwritable_database_is_unhealthy` |

**Shutdown leaves open positions open.** Closing them on SIGTERM would fabricate
exits the strategy never produced and make every deploy look like a losing
trade.

---

## Strategy and execution

| Failure | Response | Test |
|---|---|---|
| Indicators not warmed | `SUPPRESSED` with the bar count, never a signal from NaN | `test_short_history_is_suppressed_not_guessed` |
| Supertrend leg outruns the window | `SUPPRESSED`. The leg extreme is the one indicator that does **not** converge with window length — measured to move the structural stop by 60% on a strong trend. Suppressed rather than substituting a different stop, which would be a silent rule change. Fires on 0.3% of real bars | `test_truncated_leg_is_suppressed_not_traded` |
| Long and short both satisfied | Take neither (structurally impossible, but not assumed away) | in `rules.py` |
| Inverted geometry (long stop above entry) | Cannot become an order intent — raises at construction | `test_inverted_geometry_never_becomes_an_intent` |
| Position rounds to zero contracts | Rejected with the arithmetic: risk budget, stop distance, contract size | `test_position_that_rounds_to_zero_is_rejected_with_a_reason` |
| Strategy "asks" for a bigger size | Ignored. The risk engine reads the market observation, never a requested size | `test_explanation_fields_cannot_raise_the_risk_fraction` |
| Strategy claims a fat reward/risk | Recomputed from entry/stop/target, not trusted | `test_explanation_reward_risk_is_recomputed_not_trusted` |
| **Same-bar target look-ahead** | A passive entry filled on the bar's low cannot claim that bar's high as a target. The original bug produced 356 same-bar targets against 1 same-bar stop | `test_passive_entry_cannot_claim_the_same_bar_target` |
| Entry order never fills | Expires after 90s and is swept even with no ticks at all. A silent feed would otherwise accumulate working orders that all fill at once when it resumes | `test_expiry_is_swept_even_with_no_ticks_at_all` |
| Price runs away before the fill | Refused. Measured **in R, not percent** — see below | `test_chasing_is_measured_in_R_not_percent` |
| Adverse fill inflates realised risk | Quantity is reduced so the approved budget still holds. Never increased on a favourable fill | `test_adverse_slip_reduces_the_size_not_the_budget` |
| Adverse fill degrades reward/risk | Floored at fill time and the realised figure recorded | `test_a_fill_below_the_floor_is_refused` |

The look-ahead guard is **asymmetric on purpose**: the *stop* is claimable on the
entry bar, because an adverse move after a passive fill is entirely ordinary —
price reached the limit and kept going. Suppressing that too would flatter the
record in the opposite direction.

---

## What is deliberately not defended against

Stating these plainly, because an unlisted gap reads as a claim of coverage.

- **Delta returning wrong data that is internally consistent.** A plausible but
  incorrect price passes every validation. There is no second venue in V1 to
  cross-check against.
- **Clock skew beyond NTP.** Bar boundaries come from exchange timestamps, but
  staleness detection uses the local clock. A badly wrong local clock makes
  `/healthz` wrong.
- **Postgres data loss.** The database *is* the product. Back it up; nothing
  else reconstructs the forward-test record.
- **Slippage realism.** Modelled at a flat 2 bps. Real slippage depends on book
  depth at the moment of the fill, which V1 does not consume. Paper fills are
  therefore optimistic in fast markets. Stop fills in particular: live, a stop
  triggers on the first tick past it and fills close to it; in a bar-granularity
  replay the fill is bounded only by the bar's own extreme, which overstates the
  loss (measured R = −1.84 on a stop that should be about −1.0 before the replay
  harness was corrected to walk open/low/high/close).
- **Partial fills.** V1 fills whole orders or nothing. Real fills on a thin book
  can be partial.
- **Funding on open paper positions.** The schema carries a `funding` column and
  the cost model supports snapshot funding, but the live loop does not yet apply
  it. Positions held across a settlement will understate cost. This is a known
  gap, not an oversight — it needs the settlement grid wired into the tick loop.
