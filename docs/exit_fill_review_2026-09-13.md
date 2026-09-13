# Independent review: stop fills and exit mechanisms on `manual_scalp_both_t3`

Six external reviewers, working blind on a clean `master` checkout, each with their
own code, none given the in-house analysis. Commissioned 2026-09-13 after the
operator observed that losing trades often move favourably first and that stop
fills sometimes land past the stop.

Reviews and scripts: `panel_shared/reviews/{ext-exec,ext-exits,ext-scaleout,ext-data,ext-stats,ext-forensics}`
(scratch, NOT durable). This file is the record.

## 1. The finding they agree on

**The research backtester understates losses.** `portfolio.py` triggers a stop on the
mark extreme and books the fill at exactly `pos.stop_price` — no slippage, no LTP.
The live bot (`paper_broker.py:805-814`) triggers on `tick.mark` and fills at
`_slip(tick.ltp, -side)`. Three reviewers calibrated candidate 1m fill models against
the nine live stop fills and two got identical numbers from separate code:

    fill at the trigger bar's LTP close, 2bps    MAE 0.127R     <- best
    fill at the stop price (what the engine does) MAE 0.277R
    fill at the bar's adverse extreme             MAE 0.214R
    mark/LTP basis adjusted                       MAE 0.289R
    capped at the stop                            MAE 0.192R

Capping at the stop is *worse* than letting it slip, which is why stop-limit fails
below. Cost of the correction: **0.043-0.065 R/trade**, i.e. 39-59% of the arm's
recorded edge, and it creates the entire left tail:

    as recorded (engine fill)   +0.110 R   worst -1.34R    0 trades beyond -1.5R
    realistic fill              +0.045..+0.067 R   worst -7.01R    8 beyond -1.5R

`docs/failure_modes.md:180-187` claims bar replay *overstates* stop losses because
live stops "fill close to" the stop. Two reviewers independently contradict it:
**7 of 9 live fills were worse than the stop.** That passage should not be relied on.

## 2. Verdicts

| mechanism | verdict | evidence |
|---|---|---|
| fill realism (Q1) | **SUPPORTED** | unanimous; three independent calibrations |
| LTP trigger (Q2) | **dead** | +0.0040 R/trade once disentangled from the fill assumption; 0 of 378 cases where mark reaches the stop and LTP never does — only ordering differs |
| stop-limit, pure (Q3) | **dead** | fills 262/265; the 3 non-fills are -6.73R, -7.41R, -12.00R |
| stop-limit + market fallback | **only survivor** | 5-min fallback: t=+7.23, FWER p<0.0001 under studentised max-T over 23 variants, 4/4 blocks, positive on all three symbols; worst -6.39R vs -11.89R unprotected |
| breakeven after +XR (Q4) | **dead** | every threshold below as-is, paired, monotone toward it |
| trailing stop (Q5) | **dead** | best of 32 cells is -0.012R vs as-is, P=0.40 |
| scale-out (Q6) | **dead for net R** | 8 of 8 ladders negative; bank < give in all 8 |
| early adverse cut (Q7) | **disputed** | +0.041R P=0.95 (paired, unadjusted) vs +0.019R t=0.69 FWER p=0.91 (multiplicity-adjusted). Unresolved. |

Against the engine's own optimistic fill, **nothing** survives multiplicity
(best t=+0.69, FWER p=0.91). The only thing that survives is repairing the fill.

## 3. Defects found, none previously recorded

1. **`resample_tradable` is off by one bar for the whole series.** `resample_complete`
   drops the head partial bucket; the mask keeps it, and `harness._resampled` truncates
   with `[:len(px)]` instead of reindexing. Mislabels **13.3% / 13.9% / 11.1%** of
   BEAT/AKE/BANK bars. Fixing it moves the arm to **+0.196R, 3/4 blocks, DD 20.1R,
   n=384**. This is larger than any mechanism tested here.
2. **Slippage is double-charged.** `costs.entry_cost`/`exit_cost` add 2bps to the FEE
   while `_slip` moves the PRICE 2bps, both legs — $5.59 over the 13 live trades = 0.127R.
3. **The engine triggers the TARGET on mark; the live bot triggers it on LTP.** Two
   targets differ. Like the stop-fill error, it favours the backtest.
4. **Daily P&L is structurally wrong.** `repository.py:395` windows positions by
   `opened_at`, so 2026-09-13 reports "closed 0 / net $0.00" against `daily_pnl`
   +$44.51. **10 of 13 trades crossed a UTC midnight.**
5. **A missed stop trigger, 1 of 9.** BEATUSD long: 1m MARK touched the stop at
   09-09 08:40 and again 09:20; the bot filled 09:21. Tick sampling (~5s median)
   missed a sub-tick mark dip the candle recorded. **41 minutes, ~0.21R.**
6. **`rejected 956` omits 298 same-bar rejections** counted in `by_symbol`; true 1254.
7. **"Entry slippage" is reported as an absolute value.** Signed, it is -$3.62 over 13
   trades, i.e. net favourable — not a cost.

## 4. The baseline is not what `catalog.py` says

| | net | blocks | n | BEAT 5m bars |
|---|---|---|---|---|
| recorded 2026-09-04 | +0.140 | 3/4 | 373 | 62,961 |
| current cache | +0.110 | 2/4 | 378 | 63,197 |
| current cache, mask bug fixed | +0.196 | 3/4 | 384 | 63,197 |

Code is identical (spec hash `1de79b8048` both ways; `git diff 5a72e77..HEAD` touches
only additive `catalog.py` families). The recorded/current gap is **cache density**,
not span: the current cache has zero missing minutes, and emulating ~2%/5.3% random
minute loss reproduces the recorded figures (n 371+/-14, net +0.060+/-0.079, and
**P(3/4 or better) = 0.50**). A 19-point span-end sweep never reproduces the recorded
block 0; pooled is 2/4 at 18 of 19 cut points.

**"3 of 4 blocks" is a coin flip, not a property.** Under a zero-edge null,
P(>=3 of 4 positive) = 0.31; at a true +0.11R, P = 0.69 — a likelihood ratio of
**2.2:1**. Blocks 0-2 are BEATUSD alone; all 73 AKEUSD/BANKUSD trades sit in block 3.

Honest interval on net: iid bootstrap [-0.063, +0.296], weekly block bootstrap
[-0.062, +0.285], P(net>0) = 0.89, plus +/-0.08 of cache noise.

## 5. What the live sample can and cannot do

    MDE (80%, 1-sided 5%) at n=13:  1.23R      n=40: 0.70R   n=100: 0.44R   n=300: 0.26R
    resolving the claimed +0.11-0.14R edge by mean R:  ~1,600 trades
    variant ranking: 21/23 beat the engine fill live vs 2/23 on backtest; sign agreement 4/23

Live rankings are uncorrelated with backtest rankings. **n=13 cannot rank anything.**

The apparent live/backtest contradiction on breakeven, trailing and ladders reduces to
**one estimable quantity: the +1R -> 3R conversion rate.** A 50%@+1R ladder breaks even
at 47.0%; backtest 100/192 = 52.1%; live 3/8 = 37.5%. n=8 cannot separate them.

"No live winner went 0.5R adverse before its target" is 0 of **4** winners, against a
backtest rate of 70.7% — and it is **length bias**, not luck: adverse-first winners take
a median 1449 min to target vs 415 min for clean ones; among winners closing within 6h
the rate is 24.2%. In a 4.7-day window, 0/4 is expected. P(0 of 4 | p=0.45) = 0.092; the
exact 95% upper bound from 0/4 is 0.527, so 45% is not excluded.

## 6. The live ledger does not describe this arm

Every per-trade figure reconciles to |residual| < $1e-4. The headline does not:

    13 experiment trades          -$35.44    +0.120R    5 wins
    risk_state / daily report     +$106.40   equity 10,106.40, peak 10,269.32, 6 wins

The +$141.84 gap is one orphan trade opened under registration
`MANUAL_SCALP_BOTH_T3-5-20260907-f8435cf` (started 09:29:51Z, STOPPED 09:37:15Z,
identical hashes), closed before 09-07 20:22Z at +$141.84 (~+2.8R), its exit booked
into R2. `risk_state` is account-scoped, not experiment-scoped.

Also: **this arm has no pre-registered stopping rule.** The rule in `catalog.py`
(40 trades / 90 days, -12R, 20% drawdown) belongs to `manual_scalp_cross_both_t3`.
The "30" in the daily report is `MIN_CLOSED_TRADES`, a report default.
`PROGRAM_SUMMARY.md` §5 forbids adding one to a running arm.

On that -12R early stop: on a **zero-edge** arm it fires with probability 0.29 by
n=40 and 0.50 by n=100. It is a risk control, not evidence.

## 7. Pre-registration, if an execution change is to be tested live

Frozen before any deployment. Endpoint chosen because a realistic sample can resolve
it; a mean-R endpoint cannot.

    change      rest the exit as a limit at the stop price;
                market fallback after 5 unfilled minutes
    design      paired counterfactual, no randomisation --
                log BOTH the achieved fill and the 1m path, every exit
    primary     COUNT of exits filled worse than -1.15R, McNemar on discordant pairs
                observed discordant rate 0.127, 96% favouring the fix
                -> 5 discordant pairs = 37 trades ~ 13 days at 2.75 trades/day
    secondary   mean fill deviation (paired SD 0.131) -> 24 trades for 0.08R
    NOT an endpoint   mean arm R (would need ~16,000 trades)
    freeze      40 trades or 30 days, whichever first
    no tuning   the 5-minute fallback and the -1.15R threshold do not move once started

## 8. Order of work

1. Fix `resample_tradable` alignment (§3.1) and re-record the baseline. It moves the
   arm more than any mechanism here, and every delta the panel measured was taken
   against a baseline containing it. The deltas are paired and should survive; the
   level does not.
2. Replace the exact-stop fill with the calibrated model (trigger-bar LTP close, 2bps)
   and re-baseline. Under it the arm is +0.045R with a CI spanning zero — at which
   point the question is no longer "which stop mechanism" but "is there an edge".
3. Fix the double-charged slippage, the mark/LTP target trigger, and the daily-report
   windowing.
4. Re-run the execution variants on the corrected engine. **Nobody has done this:**
   §3.1 was found after the variants were measured.
5. Only then consider §7.

## 9. What was ruled out

Breakeven at any threshold; trailing at any k, arming or target; scale-out at any
ladder; LTP trigger alone; stop-limit without a fallback. Each is negative on n~378
paired, and the live n=13 evidence that appeared to favour them is length bias plus
a sample that cannot rank variants.

---

# Addendum: every variant re-run on the corrected engine

§8 step 4, done. All six reviewers measured against a baseline carrying the mask
misalignment (§3.1), the mark-triggered target (§3.3) and exact-stop fills (§1).
All three are now fixed (`aa3af26`). This re-runs the whole variant family on the
corrected engine, **paired** — entries fixed on the engine's own trade list, so a
variant cannot change which later trades are taken.

Paths walked at 1 minute: stops trigger on MARK, targets and partial take-profits
are resting limits filling at their own price, market exits fill at that minute's
LTP close moved adversely by the slippage. The replay reproduces the engine to a
mean absolute **0.036R** per trade (net +0.1252 replay vs +0.1271 engine, n=385).

Baseline, corrected engine, calibrated fill: **+0.1252R, 3/4 blocks, worst -2.00R,
5 trades beyond -1.5R.**

    variant                          net       d          95% CI       t  blks  worst  <-1.5  FWER
    LTP trigger                   +0.161  +0.036  [+0.000,+0.083]  +1.75   4/4  -1.12      0  0.48
    stop-limit, no fallback       +0.141  +0.015  [+0.006,+0.026]  +2.60   3/4  -1.12      0  0.05
    stop-limit + 5m fallback      +0.136  +0.010  [+0.001,+0.020]  +1.72   3/4  -1.73      1  0.51
    stop-limit + 15m fallback     +0.136  +0.010  [+0.000,+0.020]  +1.65   3/4  -1.84      2  0.57
    breakeven +2.0R               +0.122  -0.003                   -0.12   3/4  -2.00      5  1.00
    adverse cut 0.75R             +0.100  -0.025                   -0.66   3/4  -1.74      1  1.00
    breakeven +1.5R               +0.096  -0.029                   -0.84   3/4  -2.00      5  0.99
    scale 1/2@1.5R                +0.074  -0.051                   -1.72   2/4  -2.00      5  0.52
    scale 1/3@1R + 1/3@2R         +0.048  -0.077  [-0.137,-0.021]  -2.11   1/4  -2.00      4  0.24
    scale 1/2@1R                  +0.039  -0.086  [-0.144,-0.034]  -2.47   1/4  -2.00      4  0.08
    adverse cut 0.50R             +0.037  -0.088  [-0.174,-0.014]  -1.46   2/4  -1.74      1  0.70
    breakeven +0.5R               +0.011  -0.115                   -1.78   2/4  -1.74      3  0.46
    breakeven +1R, lock +0.25R    +0.003  -0.122  [-0.219,-0.036]  -2.24   2/4  -2.00      4  0.17
    breakeven +1.0R               -0.001  -0.126  [-0.229,-0.037]  -2.50   2/4  -2.00      5  0.07
    trail 1.00R after +1R         -0.045  -0.171  [-0.280,-0.061]  -2.72   1/4  -2.00      4  0.03
    trail 0.75R after +1R         -0.046  -0.171  [-0.277,-0.067]  -2.70   1/4  -2.00      4  0.03
    trail 0.50R after +1R         -0.065  -0.190  [-0.293,-0.086]  -2.85   1/4  -2.00      4  0.02
    adverse cut 0.25R             -0.121  -0.246  [-0.380,-0.107]  -3.03   0/4  -1.74      1  0.01

Studentised max-|t| under a week-level sign-flip null, 18 variants: **2.58**.

## What changed against the panel

1. **The panel's champion does not survive.** `ext-stats` put stop-limit + 5-minute
   fallback at t=+7.23, FWER < 0.0001, and recommended pre-registering it. On the
   corrected engine it is **t=+1.72, FWER 0.51** — indistinguishable from noise.
2. **The variant `ext-exec` rejected is now the best one.** It rejected bare
   stop-limit for an unbounded tail (non-fills at -6.73, -7.41, -12.00R). Here bare
   stop-limit has the *tightest* tail of anything tested: worst **-1.12R, zero**
   trades beyond -1.5R, against the baseline's -2.00R and five. It is also the only
   variant clearing the multiplicity bar, at **t=2.60 against a 2.58 critical value**
   — i.e. exactly on the line, at an effect of +0.015R/trade.
   **This disagreement is unresolved.** At 1m a resting sell-limit at the stop fills
   whenever the minute's LTP high reaches it, which on this data is nearly always;
   their model found ~1% never filling. Which is right decides whether this variant
   has a tail at all, and it cannot be settled from 1m candles.
3. **The adverse-cut dispute resolves against it.** `ext-exits` had 0.75R at +0.041R,
   P=0.95; `ext-stats` at +0.019R, FWER 0.91. Paired on the corrected engine it is
   **-0.025R**, and the aggressive setting is significantly HARMFUL: 0.25R at
   t=-3.03, FWER 0.01.
4. **Trailing is upgraded from useless to harmful.** All three settings clear the bar
   in the negative direction (FWER 0.02-0.03). Breakeven +1.0R and scale 1/2@1R are
   close behind (FWER 0.07, 0.08).

## Standing conclusion

Nothing here is worth deploying for edge. The largest positive effect that clears
multiplicity is **+0.015R/trade at exactly the significance boundary**, and its
mechanism is disputed between two models that 1m data cannot separate.

The tail argument is separate and survives on its own terms: bare stop-limit and
the LTP trigger both take the worst trade from -2.00R to -1.12R and remove every
trade beyond -1.5R, at no cost to net R. That is a risk control, and §7's endpoint
(count of fills worse than a threshold, McNemar) is the right way to test it — not
a mean-R endpoint, which at these effect sizes needs thousands of trades.

Every loss-cutting mechanism the operator proposed is now measured negative on a
corrected engine, paired, with multiplicity control: breakeven at four thresholds
and with a lock, trailing at three, scale-out at three ladders, adverse cuts at
three. The fill is where the money was, and fixing the fill is a backtester
change, not a trading one.

---

# Correction: the addendum's conclusion was wrong

A second blind panel (four reviewers, corrected engine, this file withheld from
them) overturned the addendum above on its central claim, and found a defect in
the engine fix itself. Recording it here because the addendum is wrong as it
stands.

## The defect

`portfolio.py` forced `stop_fill = pos.stop_price` whenever `stop_trigger_ltp`
fired. That made the option **two changes at once** — a trigger change and a
no-slippage assumption — and the assumption was doing the work. This is the same
confound the first panel caught in the original analysis; it was then
reintroduced in the code written to fix it. Two of the four reviewers found it
independently. Fixed: the fill now follows `params.stop_fill` like every other
exit.

## What that changes

Separated properly, on a replay validated against `run_portfolio` to
**MAE 0.0000R** over 385 trades with 100% exit-reason agreement:

    trigger alone, fill held fixed   +0.021R  [-0.021, +0.063]   null
    fill alone, trigger held fixed   +0.016R to +0.047R          owns everything

Only 11 of 385 trades differ under an LTP trigger, and the effect is four trades
where mark and LTP disagree about a touch — three saved targets, one lost. It is
a target lottery, not loss control, and it **leaves the tail untouched**: six
trades beyond -1.5R either way, worst -1.77R against -1.97R.

So the addendum's "LTP trigger takes the worst trade from -2.00R to -1.12R" was
the assumption, not the trigger. Likewise its "bare stop-limit is the only
variant clearing the multiplicity bar at t=2.60": reproduced, but rejected — it
rests on six trades and an unverifiable fill assumption, and the second panel's
paired re-derivation puts the same comparison at +0.053R of which +0.034R is
three trades flipping stop to target.

## The number that decides the stop-limit question

    mark triggers on 265 trades; LTP never reaches the stop on 41 of them (15.5%)
    those rest unfilled: median 3 min, mean 94 min, max 1434 min (24 HOURS)
    worst adverse excursion while unprotected: 2.05R

    assume the limit always fills:   0 trades beyond -1.5R
    assume it never fills:          25 trades beyond -1.5R, worst -3.07R

**The option spans 0 to 25 tail trades on the fill assumption alone**, and 1m
candles cannot narrow it. Three estimates of the non-fill rate now exist across
two panels — ~0%, ~1% and 15.5% — from three models of the same event. The
15.5% is the most directly measured.

## What the second panel recommends

| reviewer | scope | recommendation |
|---|---|---|
| skeptic | is the edge real | none — change nothing |
| geometry | stop/target/hold/sizing | none; if forced, 5xATR on the tail argument alone |
| entry | rule, filters, universe | none; 161 cells, best beats chance by less than chance buys |
| loss-min | Q-B in full | **do not switch the trigger to LTP**; if one change, limit + 5m fallback |

Loss distribution under the options, n=385, paired:

    option                              mean    worst     p5   <-1.5R  total lost
    mark / market at LTP  (LIVE TODAY) +0.130   -1.967  -1.270    6      -282.3
    LTP trigger / market at LTP        +0.147   -1.766  -1.276    6      -281.5
    limit at stop + 5m market fallback +0.154   -1.296  -1.091    0      -272.9

The fallback option is worth **3.3% of money lost, not a rescue**, and its tail
figure is only as good as the fill assumption above.

## Standing conclusion, revised

Unchanged: every in-flight loss-cutting rule is negative, and the fill model
rather than any trading rule is where the money was.

Revised: **no execution change is established.** The LTP trigger is a null. The
stop-limit's benefit is an assumption. The arm's own edge is not significant
either — +0.127R with a week-cluster interval of [-0.05, +0.31], 82.9% of the
sample in one symbol, and 84 trades in one 55-day window carrying it; remove that
window and the arm is +0.03R.

The one direction with region-level support is a **wider stop** — (>=5x) minus
(<=3x) over 42 matched target/hold cells is +0.174R, CI [+0.059, +0.283] — but no
single replacement cell survives best-of-336, which buys +0.276R by chance.

AKEUSD is negative in 8 of 9 configurations (-0.241R, n=41) and is 5 of the 13
live trades. Flagged independently by two reviewers. It is the only candidate
change that is a subtraction rather than an addition.
