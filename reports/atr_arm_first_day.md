# ATR arm — first-day trade management review

Experiment `H-WPR-1-PAPER-ATR-20260819-2`, started 2026-08-19 11:22:37Z, stack
`v3`, variant `V4`, strategy hash `8a564836b862ea74`. Observed 2026-08-19
~18:40Z, so roughly seven hours of running time.

**This is not a performance report.** Six closed trades against a
pre-registered stopping rule of thirty. The daily report's own verdict is
`6 / 30 closed trades — INSUFFICIENT SAMPLE`, and nothing below changes that.
What is being checked here is whether the machinery closed trades correctly,
not whether the strategy is any good.

## 1. The ledger discrepancy was the expected one

The daily report showed `wins 6 losses 3` — nine trades — while the
experiment-scoped table showed six. That gap is exactly the caveat recorded
when `cmd_start` was patched to reset the ledger: the reset zeroes the
counters, but three V3 positions were still OPEN at the moment of the reset,
and their exits were credited to the new ledger.

Split by `opened_at` against the experiment start:

| cohort | closed | realised | sum R |
|---|---|---|---|
| ATR (opened at or after start) | 6 | +230.76 | +4.670 |
| inherited V3 (opened before)   | 3 |  +97.78 | +1.988 |
| **ledger total**               | **9** | **+328.54** | |

Equity 10328.53 against a 10000.00 start reconciles to the cent. `trades_today`
reads 6, not 9, because it counts ENTRIES today, and the three inherited
positions were entered on previous days. The ledger is arithmetically correct;
it is only mis-*attributed*, and only for these three trades.

**Consequence for the record: the +4.67R headline belongs entirely to the ATR
arm.** The inherited +1.99R must not be pooled with it — those trades were
entered under V3's rules, with V3's stop geometry, and merely exited during
the ATR run. Any analysis at n=30 must scope on `opened_at`, not `closed_at`.

## 2. Trades are being managed correctly

Every closed position carries a terminal exit reason, and there are **zero**
open positions and zero positions in any intermediate state. No orphans, no
rows stuck OPEN behind a restart, no exits without a reason.

All-time tally across both arms: 15 TAKE_PROFIT, 12 STOP_LOSS, 1 TIME_EXIT.

The six ATR trades:

| symbol | side | exit | R | hold |
|---|---|---|---|---|
| SOLUSD | long | TAKE_PROFIT | +1.834 | 0.08h |
| ETHUSD | long | TAKE_PROFIT | +1.569 | 0.08h |
| BTCUSD | long | TAKE_PROFIT | +1.584 | 0.23h |
| AKEUSD | short | TAKE_PROFIT | +1.829 | 0.60h |
| BEATUSD | short | STOP_LOSS | -1.084 | 0.19h |
| BEATUSD | short | STOP_LOSS | -1.063 | 0.96h |

Two things in that table are worth reading as evidence rather than as results.

**The R multiples confirm the cost identity is actually being applied.** Wins
land at +1.57 to +1.83R rather than a clean +2R; losses at -1.06 to -1.08R
rather than -1R. That asymmetry is what the execution model predicts — targets
fill as maker with no slippage, stops pay taker plus slippage — and it matches
the ~0.3 to 0.5 cost/R computed for ATR-width stops. A run showing exactly
+2.000R and -1.000R would mean the cost model had been bypassed.

**AKEUSD and BEATUSD are trading.** Under V3 they were refused outright: 15 of
15 setups rejected on `max_stop_pct`, because V3's structural stop
`min(leg_low, supertrend)` takes the WIDER candidate. The 2 x ATR(10) stop is
narrow enough to clear the gate. That was the point of the arm, and it is
working — though note both losses so far are BEATUSD, which is the symbol the
narrow stop most changes.

Hold times are minutes, not hours: 5 minutes to 58 minutes, against V3's
routine multi-hour holds. Tighter stops make a 2R target reachable in far less
time. This has a direct consequence for the next section.

## 3. Time-bound position close — VERIFIED, fired once

The 24-hour time exit is confirmed working end to end, on live data:

```
ETHUSD  long  TIME_EXIT  R=+0.871  hold=57.21h  closed 2026-08-19 10:37Z
```

This is the shape a correct fix produces. `DELTABOT_MAX_HOLD` reached a
container for the first time when the plumbing fix deployed — before that it
was 0 in every container regardless of intent, because b63e365 shipped the
code and called itself "Apply 1 of 2" and apply 2 never landed. The ETHUSD
position had by then been open 57 hours. On the first evaluation after the
restart it was already past the 24h threshold, so it closed immediately at
market. A position 57 hours old closing under a 24-hour rule is not the rule
misfiring; it is the rule meeting a backlog on its first tick.

Confirmed in the running container: `DELTABOT_MAX_HOLD=86400`.

**It has not fired since, and should not have.** The longest ATR hold is 0.96h.
Because ATR trades resolve in minutes, the time exit will rarely bind on this
arm — it is a backstop against a position that stops resolving, not a routine
exit path. The single V3 firing is the only live evidence available, and no
further firing should be read as a problem.

One boundary case worth noting: BEATUSD closed TAKE_PROFIT at a 23.21h hold,
inside the window by 47 minutes. The threshold was not tested from below.

## 4. What is still not known

- Nothing about whether this strategy works. n=6 of 30.
- Whether the win rate holds once BEATUSD and AKEUSD have more than three
  trades between them. They were excluded from V3 entirely, so they have no
  forward-test history at all under any arm.
- Whether the time exit binds correctly at the boundary rather than on a
  backlog. That needs a position that crosses 24h while the rule is live.

## 5. Infrastructure state at time of writing

- Instance `i-0c9d862c68a20318d`, `t4g.small`, **ap-south-1b** — the second
  subnet added after the 2026-08-19 capacity outage. `t4g.small` was still
  unavailable in ap-south-1a five hours in; the same shape launched in 1b in
  fourteen seconds.
- Container up 7 hours, healthy.
- `test`, `infrastructure` and `daily-report` workflows all green on a8d0344.
- `allow_instance_replacement` back to false; `ami_id` pinned.

Outstanding housekeeping, unrelated to the experiment: the AWS root
credentials (`arn:aws:iam::132203050472:root`) are still in use for
administration and should be rotated and replaced with a role.

---

# Addendum — the arm was restarted the same evening

`H-WPR-1-PAPER-ATR-20260819-2` was stopped at 19:41Z after 6 closed trades and
replaced by `H-WPR-1-PAPER-ATR-20260819-3`. Everything above describes the
STOPPED run and still stands; it is a record of six trades, not a result.

## Why

Two gates were measuring something other than what they were meant to.

**`max_trades_per_day` was 6, sized for V3's multi-hour holds.** The ATR arm
resolves trades in 5 to 58 minutes, so it spent all six entries between 11:36
and 15:22 and then refused every setup for the rest of the UTC day. 31 of 64
refusals were this gate, and 100% of refusals in the final three and a half
hours. The cost was not throughput -- 30 trades at 6/day still meets the
stopping rule in five days -- it was SELECTION: the sample became whatever
fires earliest in the UTC day, and nothing in the resulting numbers would
reveal it. Now 20, which leaves the global cooldowns and the 2% net daily loss
limit as the gates that actually bind.

**`minimum_rr` was coin-flipping exactly-2R setups.** The arm builds
`target = entry +/- 2.0 * risk_per_unit` and the engine recomputed
`rr = |target - entry| / rpu` against a 2.0 minimum. Algebraically exact; in
floating point the subtraction cancels most of the significant digits and the
answer lands either side by ~1e-14. 7 of 64 refusals were this, at values like
`rr=1.9999999999850013`. Identical setups accepted or refused at random, which
is unattributable variance in a forward test rather than a filter. Now compared
against a 1e-9 tolerance. Pre-existing -- V3 had the same `target_r` against
the same minimum.

Both are `RiskConfig` fields, so the risk hash moved
`89f939adcd0a8567 -> f9a34a4b27a35684` and the running experiment could not
adopt them. Ending it was the only way.

## An open position belongs to no experiment

**ETHUSD LONG, opened 19:45:01Z, `experiment_id IS NULL`.**

`bind_experiment` has three outcomes, and "no active experiment" is the one
that returns True: it logs `running unbound. Decisions will not carry an
experiment id` and then TRADES. So the seven minutes between the container
restarting on the new image (19:42:18Z) and `forward-test start` binding it
(19:49:40Z) were live trading time, and one setup fired in them.

This is a systematic contamination path, not a one-off. Every deploy opens the
same window, and the position's P&L lands in the NEXT experiment's ledger while
its NULL experiment id excludes it from any experiment-scoped query. The ledger
and the trade table disagree by construction -- the same shape as the three
inherited V3 positions above, arriving by a different route.

It is left open. Closing it here would fabricate an exit the strategy never
produced, which is what the stop path already refuses to do. It is at least
cleanly identifiable: `experiment_id IS NULL`.

**Analysis of run -3 must exclude it**, by `experiment_id`, not by `opened_at`.
Expect the ledger to show one more closed trade than the experiment-scoped
table once it resolves, and expect ETHUSD entries to be blocked until it does.

Worth fixing properly: an unbound bot in a pre-registered study should evaluate
and record, not enter. That is a code change and therefore another restart, so
it is noted rather than done.

## State at handover

```
experiment_id    H-WPR-1-PAPER-ATR-20260819-3
strategy_hash    8a564836b862ea74   (unchanged -- the rules did not move)
risk_hash        f9a34a4b27a35684   (was 89f939adcd0a8567)
config_hash      0d6e1b438c32e628
git_sha          341456cf82490409459ac15c4713bf4827bd2540
equity           10000.00, counters zeroed
max_trades_per_day 20   (verified live via /api/risk)
```

Three CI defects were fixed to get this deployed, all of them silent: the
`paper-deploy` environment was never added to the OIDC trust policy;
`terraform_wrapper` survived the OpenTofu conversion unrenamed, so the wrapper
swallowed `-detailed-exitcode` and a real plan reported itself as a no-op with
a GREEN run; and the preflight still required v1's unprefixed CloudWatch
alarms, which v1's decommissioning correctly destroyed. Each was invisible
because something stayed green.
