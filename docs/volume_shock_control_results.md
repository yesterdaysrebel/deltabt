# Volume-shock contemporaneous-volatility control — RESULT

Verdict: **KILLED**.

Spec: `docs/volume_shock_control_spec.md`, frozen before the run, sha256
`1e14d3ab4933f634aff51bbfd2e1efa76d728e63b2ed7cb451a351bbcc4ed7a6`.
Decision rule applied verbatim from §7 of that spec. Nothing was changed after
results were seen.

## 1. Primary endpoint: raw |log return|, h = 30 min, stratified by sigma_contemp

| symbol | n_shock | unstratified | **stratified** | 95% CI | p (perm) |
|---|---:|---:|---:|---:|---:|
| BTCUSD | 5,365 | 1.2535 | **1.0429** | 1.0035 – 1.0797 | 0.0078 |
| ETHUSD | 5,480 | 1.2307 | **1.0030** | 0.9718 – 1.0388 | 0.4447 |
| SOLUSD | 7,958 | 1.0792 | **0.9698** | 0.9404 – 0.9980 | 0.9894 |
| XRPUSD | 9,806 | 0.9894 | **0.9728** | 0.9482 – 0.9997 | 0.9895 |

Conditioning on contemporaneous volatility removes essentially the entire
effect. BTC's 1.2535 becomes 1.0429; ETH's 1.2307 becomes 1.0030.

## 2. The kill criteria, checked one by one

Both KILL conditions fire independently:

1. stratified ratio `<= 1.05` on either primary symbol — **fires on both**
   (BTC 1.0429, ETH 1.0030).
2. bootstrap CI includes 1.0 on either primary symbol — **fires on ETH**
   (0.9718 – 1.0388).

Criterion 5 of the SURVIVES list would have failed too: BTC's third
chronological third is 0.9838 (`thirds.csv`), below 1.0.

SOLUSD and XRPUSD are not merely null. Their one-sided permutation p-values of
0.989 mean the ratio is significantly **below** 1: at matched contemporaneous
volatility a volume shock there precedes a *smaller* subsequent move.

## 3. The normalised endpoint survives, and it is an artifact

`control.csv` shows the volatility-normalised secondary at 1.33 / 1.28 / 1.20 /
1.21 with p = 0.0001 on all four majors. That number must not be read as a
rescue. `scripts/volume_shock_denominator_diagnostic.py`:

| symbol | raw strat. | normalised strat. | sigma_trail shock/base | raw ÷ denominator | residual |
|---|---:|---:|---:|---:|---:|
| BTCUSD | 1.0429 | 1.3349 | 0.7959 | 1.3103 | 0.0246 |
| ETHUSD | 1.0030 | 1.2807 | 0.8160 | 1.2292 | 0.0515 |
| SOLUSD | 0.9698 | 1.1962 | 0.8276 | 1.1719 | 0.0242 |
| XRPUSD | 0.9728 | 1.2113 | 0.8233 | 1.1815 | 0.0298 |

The normalised ratio is reproduced to within 0.02–0.05 by
`raw_ratio / sigma_trail_ratio` on every symbol. The endpoint divides by
trailing volatility, which is 18–20% lower at shock times — and that is
precisely what the event rule `rvol_median >= 5.0` selects for: a bar whose
volume is large *relative to a quiet trailing window*. The normalised endpoint
substantially re-measures its own selection rule. It is not evidence about the
subsequent move.

This is why the discovery gate refused to promote it post-hoc. Had it been
promoted, this programme would now hold a "validated" effect that is a
definitional artifact.

## 4. What this closes

The volume-shock line is closed. The discovery's raw effect was real as
measured and is explained by the alternative the control was built to test:
a volume shock is a faster detector of a volatility regime change than a
1440-bar trailing median, and the larger subsequent move is the regime, not
information the shock adds.

Per §8 of the spec, a KILLED verdict authorises nothing. No forward test is
registered, no parameters are frozen, no entry is added to
`out/experiments.jsonl`.

## 5. Provenance

* discovery spec sha256 `8f8dc68075e5275916cf8198b06eb15fbd9e46437db460113091658cb3c22594`
* outputs: `out/volshock_control/{control,deciles,thirds,denominator_diagnostic}.csv`, `run.json`
* 34 tests green at the gate, including the two that prove the control works:
  a pure volatility confound is driven from >2.0 to 0.90–1.10, and a planted
  genuine 1.5x effect survives above 1.35.
