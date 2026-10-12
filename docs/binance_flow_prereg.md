# Pre-registration — does order flow (from Binance's archive) predict this entry's outcome? (2026-10-12)

Written and committed before any Binance file was downloaded. Owner approved step 1 on 2026-10-12.
Step 2 (raw trade-by-trade files on a rented box) and anything touching the bot need their own yes.

## Question

Trade flow is the one input never tested against `manual_scalp_both_t3`'s entries. Delta keeps no
trade history, so the flow comes from Binance's public archive for the same coins (Delta's prices
follow Binance). The question is: in the minutes before each past Delta entry, did Binance's buying
and selling, and its positioning data, predict whether that Delta trade reached 3R?

## Data

- **Entries and outcomes:** the engine's own trades on each window, per-coin spread slippage, exactly
  as the 10-12/13 labs (`scripts/labkit.py`): DEV = `data/` (2025-12-20..2026-09-30, 32 coins), OLD =
  `data_old/` (2024-06-01..2025-12-19, the 27 coins that existed). Judged exit: hold-to-3R. The trail
  is reported on the entries both lists share.
- **Binance 1-minute candles** (`data/futures/um/monthly/klines/<SYMBOL>USDT/1m/`): volume, trade
  count, taker-buy volume. **Binance 5-minute metrics** (`.../metrics/`): open interest, top-trader
  long/short ratios (accounts and positions), global long/short account ratio, taker buy/sell volume
  ratio. Delta `XUSD` ↔ Binance `XUSDT`. Months a coin was not yet listed on Binance are missing by
  design (404); its entries then carry NaN flow features, which the model may use only as "unknown".
- **Timing:** decision instant D = entry_time + 300 (the fill at the signal bar's close). Candle
  features use 1m bars that CLOSED by D (open_time ≤ D − 60). Metrics use the last snapshot with
  timestamp ≤ D. Binance timestamps are UTC milliseconds; Delta bars are UTC seconds.

## Features (fixed now)

From candles, over the last 5, 15, 30 and 60 minutes: taker-buy share of volume aligned with the
trade's side (`al_tbs_w`); its change (5m − 60m); volume and trade-count surge vs the trailing 24h
per-window average; average trade size vs 24h. From metrics: log OI change over 1h, 4h, 24h; log
taker buy/sell volume ratio, log top-trader long/short account ratio, log top-trader position ratio,
log global long/short ratio — each aligned with the side — and the 1h change of the taker ratio.
About 20 features. **PRIMARY = these + the candle lab's 29 base features without the fee inputs**
(the base features are what every earlier filter test had; the question is what flow adds).
Reported: flow-only, base-only.

## Model and test (the machinery of `scripts/entry_filter_bundle_lab.py`, unchanged)

LightGBM with the fixed settings, monthly walk-forward (DEV: April..September 2026; OLD: October 2024
..December 2025), threshold fixed from the previous month, 5-seed mean, label = hold-to-3R target hit,
labels used only once closed. Random control matched by UTC day, 2,000 draws. Validity first: planted
signal `0.25·label + N(0,1)` must give AUC ≥ 0.52; shuffled labels must not beat random (p > 0.05).

## Pass rule — judged on DEV, then confirmed on OLD; PASS only if both windows pass

| | rule (25 % depth, PRIMARY) |
|---|---|
| P1 | mean monthly out-of-sample AUC ≥ 0.530 |
| P2 | net R per trade beats day-matched random at p ≤ 0.05 (hold-to-3R) |
| P3 | net R per trade above the cheap-fee rule at 25 % |

Fewer than 1,500 test entries with non-NaN flow features in a window = that window cannot answer.

## Conduct

One run per window. No change to features, settings or depth after a result is seen. A bug found
after a run: fix, log here, rerun, report both. The feature tables are committed with the result.
