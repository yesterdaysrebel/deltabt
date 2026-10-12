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

## RESULT 2026-10-13 — FAIL on all four cells (`out/sweep/five_min_arm_lab/daily_trend/result_2026-10-13.txt`)

| cell | DEV net R (n) | OLD net R (n) | p vs random DEV / OLD | pooled day-t | verdict |
|---|---|---|---|---|---|
| A-12h Donchian | +0.101 (401) | **−0.045** (636) | 0.14 / 0.96 | +0.24 | fail |
| A-1D Donchian | +0.226 (176) | −0.000 (295) | 0.28 / 0.44 | +1.12 | fail |
| B-12h ATR momentum | +0.052 (449) | +0.030 (750) | 0.34 / 0.34 | +0.93 | fail |
| **B-1D ATR momentum** | **+0.208 (195)** | **+0.091 (349)** | **0.06 / 0.02** | +2.19 (bar 2.5) | fail on t and pooled p |

What the control showed: on DEV, random entries with the same 2.5×ATR trailing exit also earn money
(+0.05R at 12h, +0.17R at 1D) — the window trended, and a trailing stop with a 20-bar hold harvests
that whatever the entry. Three cells are no better than that. Donchian 12h is *worse* than random on OLD.

**B-1D is a lead, not a pass.** Positive in both windows, beats random in each window on its own
(p 0.06 and 0.02), 47 % wins, cost per R 0.009–0.011, ~19 trades a month across ~25 coins. It misses
the pre-registered bar on pooled day-t (+2.19 vs 2.5) and on the pooled random p (0.23). The pooled
random p is a weakness of this design, noted after the fact and not used to change the verdict: pooling
mixes DEV's high random mean (+0.114R, a trending window) with OLD's (−0.015R), so a real effect in both
windows can still read as "no better than random" pooled. A confirmation would be on data no rule has
touched: Binance daily candles 2019–2024 for the majors (close-to-close execution, 0.158 % costs), with
the cell frozen exactly as here. That needs its own yes.
