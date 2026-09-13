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
