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
