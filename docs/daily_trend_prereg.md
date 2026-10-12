# Pre-registration — multi-day trend on 12h and 1D bars (2026-10-13)

Written and committed before the run. Owner's spec of 2026-10-13, mapped onto this engine; every
deviation from the spec is stated here. Script: `scripts/daily_trend_lab.py`.

## Question

Does a plain trend entry on slow bars, where cost per R is 0.02–0.05 instead of ~0.08, earn more than
nothing after fees and funding on Delta India's perps? Earlier daily-horizon tests here were
*cross-sectional* (rank coins against each other: H-XSec-1, H1-Kill-1, both null). This is
*time-series*: each coin against its own past.

## Data and windows

DEV = `data/` (2025-12-20..2026-09-30, 32 coins); OLD = `data_old/` (2024-06-01..2025-12-19, the 27
coins that existed). Bars are built from Delta's 1-minute LTP: 12h bars open at 00:00 and 12:00 UTC,
1D bars at 00:00 UTC; a bar is kept if ≥ 30 % of its minutes traded (thin coins keep their bars;
the 5m completeness rule would drop them). Mark-price bars likewise, for stop triggers. Pre-listing
periods simply do not exist in the data; nothing is filled forward. Indicators need N + 14 bars of
history before the first signal (warm-up).

## The four cells (fixed; no grid)

| cell | bars | entry (on the bar's close; short is the mirror) |
|---|---|---|
| A-12h | 12h | **Donchian**: close above the highest high of the previous 20 bars |
| A-1D | 1D | same, 20 bars |
| B-12h | 12h | **ATR momentum**: close − close[20 bars ago] > 1.5 × ATR(14); fires on the bar it first becomes true |
| B-1D | 1D | same |

Shared: initial stop 2.5 × ATR(14) below/above the entry (that is 1R, so size is inverse to ATR);
the stop then trails 2.5 × ATR behind the best price, only ever tightening, resolved minute by
minute on 1m data; no profit target; time exit after 20 bars; one position per coin; no cost gate.
Fees: taker both legs ×1.18 GST; per-coin slippage = max(2 bps, half the 2026-10-05 quoted spread);
funding charged at every settlement the position crosses. Execution: the engine fills at the signal
bar's close; in a 24/7 market that is the next bar's open — the pre-flight measures the gap.

## Controls and statistics

- **Random control:** per cell and window, 50 runs with the same number of long and short signals per
  coin placed at random bars after warm-up, same exits and costs. p = share of runs whose net R per
  trade ≥ the real cell's.
- **Day-clustered t** of net R per trade against zero (entry days as clusters).

## Pass rule per cell

PASS = net R per trade > 0 in **both** windows, pooled day-clustered t ≥ 2.5, and pooled p vs random
≤ 0.05. Fewer than 60 trades in a window = cannot answer. Reported: trades per month, median hold,
median cost per R, funding per trade, win rate, long vs short.

## Pre-flight (run and recorded before the full run)

1. Bar boundaries on UTC; 2. entry-close vs next-open gap; stop checks start the bar after entry;
3. funding charged (shown per trade in the smoke log); 4. first bar per coin, no forward fill;
5. the four cells above, nothing else; 6. BTCUSD smoke run with the first trades recomputed by hand:
net R = side × (exit − entry) × contracts × contract value − fees − funding, over risk per trade.
