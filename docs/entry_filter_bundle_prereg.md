# Pre-registration — entry filter, bundle of three new information sources (2026-10-09)

Written and committed **before** the open-interest data was fetched and before any result was
seen. Script: `scripts/entry_filter_bundle_lab.py`. Owner approved the plan and the independent
review's fixes on 2026-10-09 (IST).

## Question

Can information the candle ML (`scripts/entry_filter_ml_lab.py`, AUC 0.490, closed 10-05) never
had pick the better entries of `manual_scalp_both_t3`, well enough to beat fees?

This is the **fourth** filter tried on the same trades (symbol choice, daily selection, candle ML,
this). A pass is a lead for a forward check, not a change to the bot.

## Data

- Entries: the 4,950 paired trades of `scripts/symbol_screen.py`
  (`out/sweep/five_min_arm_lab/symbol_screen/trades/*.csv`), 32 symbols, 2025-12-20..2026-09-30.
- Label: hold-to-3R reached its target (`r_baseline >= 2.0`), as in the candle lab.
- Open interest: Delta's undocumented `OI:<SYMBOL>` candle series, 5m, 2025-12-12..2026-10-01,
  fetched once to `data/candles/<SYMBOL>/oi_5m.parquet` (gitignored). Fetch time, row counts and
  sha256 go in `out/sweep/five_min_arm_lab/symbol_screen/entry_filter_bundle/oi_manifest.json`.
- OI consistency check, before use: for BTCUSD, ETHUSD, SOLUSD, XRPUSD, BEATUSD, VELVETUSD the 5m
  close at bar t must equal the 1m close at t+240 and the 1h close at t must equal the 5m close at
  t+3300, on two sampled days. Any mismatch is reported; more than 1% mismatched stops the run.

## Timing

The engine fills at the close of the 5m signal bar; `entry_time` is that bar's open. Decision
instant **D = entry_time + 300**. New features use only 1m bars that started before D, 5m OI bars
that started at or before `entry_time`, and the engine's own 5m %R at the signal bar. Every entry
must match an engine signal of the same side at `entry_time`; any that does not is reported.

## Features

**Base (29):** the candle lab's features, unchanged except one fix: funding uses only the last
funding bar that had **closed** before the lab's cut (the old code read the open hour's bar, up to
~1h ahead).

**Open interest (9):** log OI change over 5m (the signal bar), 15m, 1h, 4h, 24h; log(OI / its 7-day
mean); the 1h change as a z-score against the prior 7 days of 1h changes; the 1h and 4h OI change
times the sign of the price move in the trade's direction. NaN if the last OI bar is more than 1h
stale.

**Signal bar (9):** the signal bar's move in the trade's direction, range, body share, close
position in its range (trade-aligned), volume vs the 24h average 5m volume, traded minutes in it;
the trade-aligned move over the last 10 minutes; the engine's 5m %R at the signal bar and its jump
from the bar before (both mirrored for shorts).

**Regime (7):** trade-aligned 3-, 7- and 30-day return (z-scaled); 24h volatility vs 30-day
volatility; trade-aligned BTC 7-day return; the share of the other coins whose 24h return points the
trade's way, and their mean trade-aligned 24h return.

## Model and walk-forward

Identical to the candle lab: LightGBM with the lab's fixed settings, label used only once a trade
has closed, monthly walk-forward April..September 2026, threshold = the score the top q% of the
previous month cleared under a model that had not seen it. **Scores are the mean of 5 seeds
(11, 22, 33, 44, 55); every number comes from that one averaged model.**

**PRIMARY model = all features except the fee inputs (`fee_r`, `vol_24h`).** The fee rule is judged
separately; the model must find something the fee rule does not.

Random baseline: **day-matched** — the same number of picks on each UTC day, drawn at random from
that day's entries, 2,000 draws; p = share of draws at least as good.

## Pass rule — PRIMARY model, 25% depth only

| | rule |
|---|---|
| P1 | mean of the six monthly out-of-sample AUCs **>= 0.530** |
| P2 | net R per trade beats day-matched random at **p <= 0.05** on **both** hold-to-3R and trail |
| P3 | net R per trade **above the cheap-fee rule** at 25% on **both** exits |

PASS = P1 and P2 and P3. Anything else = FAIL.

Validity, checked **first** (before the real result is computed):
- **V1 planted signal:** a feature `0.25 * label + N(0,1)` (rng seed 0) added to the PRIMARY set must
  give mean AUC **>= 0.520**. If not, the run is **INVALID** (the pipeline cannot see a signal of
  the size P1 asks for) — not a fail.
- **V2 shuffled labels:** the PRIMARY set trained on shuffled labels must **not** beat day-matched
  random at 25% (p > 0.05 on both exits). If it does, the run is **INVALID**.

P1's 0.530 only catches a signal about as strong as a lone feature with AUC ~0.58 (measured
10-09: a planted 0.576 came out at 0.534 among 29 features).

Reported, not judged: 10% and 50% depth; the all-features model; base-plus-one-group ablations;
the PRIMARY model without the 11 symbols whose OI history is patchy (AIOUSD, AKEUSD, AXSUSD,
BANKUSD, BLESSUSD, HYPEUSD, MUBARAKUSD, STRKUSD, SUIUSD, WIFUSD, ZROUSD — 777 trades); the
candle-only PRIMARY baseline under the same day-matched test.

## Hold-out (only on PASS)

After 2026-12-31: generate the October–December 2026 entries with `scripts/symbol_screen.py`
unchanged, continue the same monthly walk-forward with every rule frozen. Confirmed if the PRIMARY
25% picks beat all entries on both exits **and** beat day-matched random at p <= 0.10 on both.
At ~390 picks this needs about +0.10R over random (per-trade R spread ~1.7R); smaller real effects
will read as unconfirmed.

## Conduct

- One run. No change to features, settings, depth or rules after any result is seen.
- A bug found after the run: fix it, log it here with the date, rerun, report both runs.
- The finished feature table (4,950 rows) is committed with the result, with library versions.
