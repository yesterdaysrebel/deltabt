# Volume-shock: contemporaneous-volatility control — frozen specification

**Written 2026-08-28, BEFORE any control result was computed.** Frozen ahead of
the run. Any later change is a deviation and is recorded as one.

Follows `docs/volume_shock_discovery_spec.md` (discovery, verdict
INCONCLUSIVE) and commit `0135546`.

---

## 1. What the discovery left open, stated precisely

Discovery found, on 588.5 days of 1-minute data:

| endpoint | BTC | ETH | SOL | XRP |
|---|---:|---:|---:|---:|
| raw median-|return| ratio | 1.253 | 1.231 | 1.079 | **0.989** |
| volatility-normalised ratio | 1.378 | 1.350 | 1.216 | 1.200 |

and one diagnostic: trailing volatility is **lower** at shock times than at
baseline on every symbol (0.926 / 0.926 / 0.906 / 0.839). Volume shocks
interrupt quiet periods.

That produces a specific alternative explanation which the discovery could not
rule out and did not pre-specify a control for:

> **A volume shock is not information about future movement. It is a faster
> detector of a volatility regime change than a trailing estimator, which is
> lagging by construction. The larger subsequent move is the new regime, not
> anything the shock adds to it.**

If that is the whole story, the effect must disappear once shocks are compared
against non-shock bars **whose contemporaneous volatility is the same**.

## 2. THIS IS A KILL TEST, NOT A VALIDATION

**No untouched historical window exists.** Discovery reported results on the
full 2025-01-01 → 2026-08-12 span for all five symbols, including the
chronological thirds. Holding out the final third now would be confirmatory in
name only, because its numbers have already been seen.

The asymmetry that makes this run worth doing anyway:

* a **negative** result is dispositive. Sample reuse cannot manufacture the
  disappearance of an effect; you cannot data-mine your way to a null.
* a **positive** result is **not** confirmation. It licenses one thing only:
  freezing these parameters for a forward test on data that does not yet exist.

The verdict language is therefore `SURVIVES` / `KILLED` / `INCONCLUSIVE`, never
`VALIDATED`.

## 3. Contemporaneous volatility

    sigma_contemp(t) = stdev( log-return over bars [t-14 .. t] )      # 15 bars

**Why it is not look-ahead.** A bar labelled `t` opens at `t` and closes at
`t+60s`. The event is classified from `volume(t)`, which is only known once
that bar closes, so the decision instant is `t+60s`. The outcome
`r(t,h) = |log(close(t+h) / close(t))|` runs from `t+60s` to `t+h*60+60s` —
strictly after. `sigma_contemp` uses closes through `t+60s` and therefore uses
exactly the information available at the decision instant, and none after it.

15 bars: short enough to register the burst the shock belongs to, long enough
to estimate a standard deviation. Declared, not tuned.

## 4. Stratified comparison

Shock and baseline bars are assigned to **deciles of `sigma_contemp`**, cut on
the full sample for the symbol.

Full-sample cuts are used deliberately and are conservative here: this is a
*conditioning* variable, not a selection rule and not a signal. Nothing is
traded on the bucket, and using the whole sample to define it can only make
the strata better matched, which works **against** the effect surviving.

Within each decile `d`:

    ratio_d = median |r| over shocks in d  /  median |r| over baseline in d

The headline is the **shock-count-weighted mean of `ratio_d`** across deciles
carrying at least 30 shock events. Deciles below that are reported and
excluded from the weighted mean.

## 5. Endpoints

* **PRIMARY: raw `|log return|` at h = 30 min, stratified by `sigma_contemp`.**
  Raw rather than the normalised endpoint: once the comparison is already
  conditioned on contemporaneous volatility, dividing by *trailing* volatility
  as well answers a muddled question. The clean one is — at the same current
  volatility, does a volume shock predict a larger move?
* SECONDARY: the volatility-normalised endpoint, same stratification.
* SECONDARY: h = 15 and h = 60.

Event definition, cooldown, baseline and validity rules are inherited
UNCHANGED from `volume_shock_discovery_spec.md`: `rvol_median >= 5.0`,
L = 1440, 60-minute episode cooldown, baseline excluding the 60-minute
post-event window, 1440 contiguous bars required, exact outcome bar required.

## 6. Statistics

* CI on the weighted ratio: **stratified block bootstrap**, resampling shocks
  and baseline within each decile, 10,000 resamples.
* p-value: **stratified permutation**, shuffling the shock label within each
  decile so the null preserves the `sigma_contemp` distribution exactly,
  10,000 permutations, one-sided.

Medians throughout — bar-level absolute returns are heavy-tailed.

## 7. Decision rule, fixed in advance

**KILLED** if either holds:

1. the weighted stratified ratio is `<= 1.05` on **either** BTCUSD or ETHUSD; or
2. the bootstrap CI includes 1.0 on **either** primary symbol.

**SURVIVES** requires all of:

1. weighted stratified ratio `> 1.10` on **both** BTCUSD and ETHUSD;
2. bootstrap CI excluding 1.0 on both;
3. stratified permutation `p < 0.01` on both;
4. ratio `> 1.05` on **at least 3 of 4** majors — the discovery's raw endpoint
   collapsed to 0.989 on XRPUSD, and a control that ignores that is not a
   control;
5. the weighted ratio positive in **all three** chronological thirds on both
   primary symbols.

Anything else is **INCONCLUSIVE**.

## 8. What a `SURVIVES` verdict does and does not authorise

It authorises exactly one thing: freezing these parameters and registering a
**forward** validation on data recorded after this commit. It does not
authorise a strategy, a backtest, a P&L, or an options study.

## 9. Out of scope

No P&L, no Sharpe, no execution, no costs, no options data, no strategy, no
threshold search. This gate ends at `SURVIVES` / `KILLED` / `INCONCLUSIVE`.
