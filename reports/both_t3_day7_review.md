# manual_scalp_both_t3 — day-7 review of the running arm

Experiment `MANUAL_SCALP_BOTH_T3-5-20260907-f8435cf-R2`, stack `atr`, variant
`SPEC:manual_scalp_both_t3@5`, started 2026-09-07 09:37:15Z, day 7 of a
planned 30. Reviewed 2026-09-13 against the daily report generated
2026-09-13T19:19:54Z (run 34777350023) and the code at 276c6de.

The question asked was whether the arm "can do better."

## The answer first

**On the signal: the data cannot say yet, and acting on it now would be the
fourth repetition of a recorded mistake.** 13 closed trades carry a 95%
confidence interval of roughly ±0.97R on the mean; the live mean (+0.009R),
the backtest point estimate (+0.140R), and "meaningfully negative" all sit
inside it. Every observable that *is* measurable at n=13 — exit mix, win
rate, cost per R, hold times — is tracking the pre-registered backtest
closely. There is no evidence of underperformance to act on, and the program
has now ended three arms early against or without their own rules
(`h1dir` 2026-09-04, `hours` and `cross` 2026-09-10). A fourth makes the
month's forward-testing uninterpretable as a body of evidence.

**On everything around the signal: yes, it can do better, in five specific
places** — none of which touches the experiment's identity. They are listed
under Findings; the two that matter most are that WIFUSD cannot form bars
live (a quarter of the universe is inert, and the 2026-09-03 evidence for it
is untestable), and that live stop exits are paying a gap-through cost the
backtest's fill model does not carry, of the same order as the entire fee
bill.

## Live against the pre-registered expectation

The catalog's frozen numbers for this cell (373 backtest trades, thin three,
4×ATR, 3R target, 72h cap) against the run so far:

| | backtest | live (n=13) |
|---|---|---|
| mean net R | +0.140 | +0.009 |
| bootstrap / CI | [-0.041, +0.321] | ±0.97 (95%, SD 1.78) |
| win rate | 31% | 38.5% (5/13) |
| exit at target | 27% | 23% (3) |
| exit at stop | 69% | 69% (9) |
| exit on 72h cap | 4% | 8% (1) |
| trades/week | 11.9 | ~14 |
| cost per trade | — | 0.085R avg |

The exit mix is the strongest early diagnostic this family has — the 4R arm
was withdrawn precisely because its target was unreachable on half the
universe — and here it matches the forecast almost exactly. The winners land
at +2.73 to +3.07R against a 3.0 plan, the losses cluster at −1.0 to −1.25R,
and the one time exit fired at 72h00 on the nose (BANKUSD, +0.881R), which
is the first clean boundary firing of the time stop on record — the v3
review noted the threshold had only ever been met from a backlog.

The difference between +0.009 and +0.140 is a t of about −0.27. Nothing.
Note also that the headline is currently hostage to the two open positions
(AKEUSD +0.86R, BEATUSD +1.04R): both reaching target puts the run near
+6R, both stopping puts it near −2R. A day-7 read is a coin flip either way.

## Per symbol, and why it must not drive a decision

| symbol | closed | sum R | backtest expectation |
|---|---|---|---|
| BEATUSD | 7 | −4.55 | +0.155 (n=303, carried the arm) |
| AKEUSD | 5 | +3.79 | −0.30 (the known drag) |
| BANKUSD | 1 | +0.88 | +0.64 (n≈40) |
| WIFUSD | 0 | — | +0.043 (n=299) |

BEATUSD and AKEUSD have both flipped sign against the backtest attribution.
Each is well within noise at n=7 and n=5 — and jointly they are the
program's own meta-result repeating: per-symbol attributions measured on
this venue have not transferred out of sample yet. Re-cutting the universe
on this table would be fitting the run to its first week. It is recorded so
that at review it is compared against the same table at n=30, not so that
anyone acts on it now.

The breaker counterfactual in the daily report ("production" profile +32.64
vs actual) is the other seductive number, and it is the documented trap:
gating censors the sample (PROGRAM_SUMMARY, lesson 3 — measured on v3, the
gate changed no conclusion and only widened the standard error). It is not
evidence the gates would "do better."

## Findings

### F1 — WIFUSD cannot form bars live; a quarter of the universe is inert

WIFUSD produced **68 one-minute bars in 24 hours** (AKEUSD: 1437) and **zero
setups** in the probe window; it has no entries among the run's 15. At ~5%
minute density, `resample_complete` (one absent minute tolerated per 5m
bucket) almost never forms a 5m bar, and %R(140) on the 5m needs 140 of
them. The 2026-09-03 evidence that added it — net +0.043R over 299 archive
trades — assumed a bar density the live feed does not have, so the addition
cannot be forward-tested at all: the symbol contributes candle-gap noise
(60 unrepaired gaps/24h) and nothing else. BANKUSD is only marginally
better (135 bars, 105 gaps, one trade in seven days).

No action mid-run — `bot_symbols` is identity and changing it ends the
experiment. The action is at review: record now, before the sample
completes, that WIFUSD's cell is unevaluable live, so its removal from a
successor is a data-density decision taken today rather than a P&L decision
taken after seeing n=30. And the next time a symbol is added on archive
evidence, gate the addition on *live* bar density first — the archive's
density is a property of the archive.

### F2 — live stops pay gap-through the backtest does not model

`deltabt/engine.py` fills a triggered stop **at the stop price**
(engine.py:236). The live broker triggers on mark and fills at LTP plus
slippage (paper_broker.py:812), which is more honest — and it shows.
Reconstructing the price-move R (reported R plus per-trade cost) for the
nine stop exits:

| trade | R | cost/R | move beyond −1R |
|---|---|---|---|
| BEATUSD 2026-09-12 (54m) | −1.679 | 0.018 | **0.66R through** |
| BEATUSD 2026-09-12 (51h) | −1.247 | 0.010 | 0.24R |
| BEATUSD 2026-09-10 | −1.186 | 0.074 | 0.11R |
| BEATUSD 2026-09-09 | −1.232 | 0.149 | 0.08R |
| BEATUSD 2026-09-08 | −1.248 | 0.197 | 0.05R |
| AKEUSD ×2 | −1.039 / −1.027 | 0.027 / 0.023 | ~0 |
| BEATUSD 2026-09-09 (5h23) | −0.888 | 0.233 | favourable-entry geometry, excluded |
| AKEUSD 2026-09-08 | +0.059 | 0.041 | **see F3** |

That is roughly **+1.1R of losses beyond plan across seven clean stops, ~0.07R
per trade over the run** — the same order as the entire fee-and-funding bill
(0.085R/trade). It concentrates where it must: BEATUSD's fast moves, where
the worst trade gapped ~6% past a 9.4% stop in under an hour. The backtest's
+0.140R estimate carries none of this, because bar-replay stop fills are
exact and slippage is 2 bps, not 300.

This is not fixable in the live arm and should not be: the live number is
the true one. The fix is backtest-side, legal at any time — give the engine
a stop-overshoot term measured from live fills (this run is producing
exactly the data for it), and re-state the cell's expectation with it before
the review reads live-vs-backtest divergence as a strategy result.

### F3 — one stop exit is physically unexplained and cannot be triaged from the database

AKEUSD LONG, 2026-09-08 12:40→22:30 IST, exit reason STOP_LOSS, **closed
+0.059R — above its own entry** — on a position whose stop sat ~9% below
entry. The only path to that row is the live trigger: `tick.mark <=
stop_price` with the fill at LTP (paper_broker.py:802,812). Either AKEUSD's
mark genuinely diverged ~9% from last-traded, or one bad-but-numeric
`mark_price` print did it — `normalize_ticker` checks presence and
numericness only (normalize.py:121), with no plausibility check against LTP.

Two consequences. First, one of thirteen exits (7.7% of the sample) was
decided by mark/LTP mechanics rather than by the strategy's geometry.
Second, and worse, `_close` records only the fill price — **the mark that
pulled the trigger is not persisted anywhere** (the exit order's
`requested_price` holds the stop, not the trigger), so this cannot be
settled from the trade record; it needs the tick log if one still covers
2026-09-08. Action: persist the triggering mark (and its divergence from
LTP) on stop and time exits. That is a code roll, not an identity change —
but see F4c for why any roll should wait for, or carry, the bind fix.

### F4 — the observability around the run has three defects

**(a) A false alarm on late-dispatched reports.** Today's report notes
"2 position(s) opened today with no APPROVED evaluation in the probe window."
Both approvals exist: the probe lifts approvals from a *trailing 36h window*
(db_probe.py, `bar_open > now() - interval '36 hours'`) while the note keys
on the *report day* (daily_report.py:846-875). Dispatched at 19:19Z for day
2026-09-12, the window opens at 07:19Z on the 12th — after both entries
(03:05Z, 05:20Z). On the 01:30Z schedule the window covers the whole report
day, so the scheduled run never lies; every late manual dispatch can. Scope
the approvals query to the report day. This repo has already written down
what a recurring false alarm costs ("it trains you to ignore the one that is
real"); this one is new since #55 and cheap to fix.

**(b) healthz has been red for the life of the run, by construction.** The
container reads "Up 3 days (unhealthy)"; `no_recent_gaps` is red on 387
unrepaired gaps/24h that are a property of BEATUSD/BANKUSD/WIFUSD liquidity,
not a fault — the report itself says so, then leaves the light red anyway. A
health signal that is red 100% of the time protects nothing. Either budget
expected gaps per symbol (the data for a threshold is in every daily
report) or split "feed integrity" from "instrument liquidity" so the red
lamp is reserved for the one that means the socket died.

**(c) The ledger and the experiment table disagree by one trade, again.**
All-time counters read 6W/8L — fourteen trades — against thirteen
experiment-scoped closes, and equity (10106.40) does not reconcile to the
experiment table's −35.45 by inspection. The shape is familiar: the `-R2`
suffix means a predecessor registration existed in `deltabt_both`, and the
unbound-deploy-window contamination path documented on 2026-08-19 ("an
unbound bot in a pre-registered study should evaluate and record, not
enter") was noted rather than fixed and remains open. One query — positions
in `deltabt_both` with `experiment_id` null or ≠ the current run — settles
where the fourteenth trade came from. At n=30 the analysis must scope on
`experiment_id`, and the bind-before-trade fix should ship with the *next*
planned roll rather than being forgotten a third time.

### F5 — the arm has no frozen decision rule, and the review is ~8 days away

The run has a 30-day plan and the report gates on a 30-trade sample, but no
continue/stop criterion is frozen anywhere for `manual_scalp_both_t3` —
`docs/` holds stopping rules for v3 and v5, the catalog holds one for
`cross_both_t3`, and nothing for this cell. PROGRAM_SUMMARY lesson 5 is
"do not freeze a stopping rule per-arm after an arm is already running";
the honest reading is that a rule frozen at day 7, before anyone has seen
n=30 and while the run sits at breakeven, is still worth far more than the
alternative, which is deciding at n=30 with the number on the screen —
the process that has now ended three arms.

Freeze this week, adapting the cross template to this universe's reality
(WIFUSD inert, BANKUSD ~1 trade/week, so "2 of 3 symbols positive"
effectively means BEATUSD and AKEUSD):

- no decision before 30 closed trades **and** the per-symbol table at that
  n; early stop only at cumulative −12R or 20% drawdown from peak;
- at review, continue only if net R > 0 with the F2 gap-through cost
  included in the comparison baseline;
- WIFUSD's disposition is decided on F1 (bar density), not on its P&L.

At the observed 2.1 trades/day, 30 closed trades arrive around day 14–15 —
about a week from now.

## What is verified working

For completeness, because most of the machinery this program has had to fix
is now holding: 29 orders produced 29 fills and no orphans; every closed
position carries a terminal exit reason; the 72h time stop fired once,
exactly at the boundary; the position opened 2026-09-09 23:40Z survived the
2026-09-10 12:29Z container restart and closed normally two days later;
risk hash matches expectation (`cbd18ca…`, no drift); feed silence peaked at
4.8s over 288 heartbeats; zero application errors in 24h. The R multiples
carry the cost asymmetry the execution model predicts (wins below +3R,
losses below −1R), so the cost identity is being applied, not bypassed.

## Recommendation

Let the arm run. Freeze the decision rule now (F5). Fix the two report
defects (F4a, F4b) and add trigger-mark persistence (F3) in one deliberate
roll that also carries the bind-before-trade fix (F4c). Start the
stop-overshoot measurement against live fills (F2) so the day-30 review
compares live results to a backtest that pays the same exit costs. Decide
WIFUSD at review on data density, which is already conclusive, not on P&L,
which never will be at this trade rate (F1).

"It can do better" is true of the apparatus today and undecidable of the
signal until roughly day 15. The one way to guarantee the run tells you
nothing is to change it now.
