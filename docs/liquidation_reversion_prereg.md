# Pre-registration — liquidation-cascade reversion (2026-10-13)

Owner's protocol of 2026-10-13, with the eight fixes from the review applied; each is marked **[fix]**.
Committed before step 0 ran. Script: `scripts/liquidation_reversion_lab.py` (`count`, then `run`).

## Hypothesis

A forced-liquidation cascade (an extreme hourly move with abnormal volume and a sharp drop in open
interest) pushes a perp's price past fair value for a while. Entering against the move **once the
cascade has exhausted** earns a positive gross and net return over the next day.

## Data

Delta 1-minute LTP and mark (DEV = `data/`, 2025-12-20..2026-09-30; OLD = `data_old/`, 2024-06-01..
2025-12-19), Delta funding; Binance 1-minute volume and dollar volume and 5-minute open interest for
the same coins (`data/binance/`). Prices and fills are Delta's; volume and OI are Binance's (the
global tape is where forced unwinding shows).

## Step 0 — events, counted before any outcome is read

**[fix 1] Bars are 1-hour**, UTC-aligned, built from the 1-minute data. Per coin and bar *t*:
- return: close_t − close_{t−1} ≤ −3.0 × ATR(14) → candidate **long**; ≥ +3.0 × ATR(14) → candidate **short**
  (Wilder ATR on 1h bars);
- volume: Binance hourly volume ≥ 3.5 × its mean over the previous 720 bars (30 days);
- open interest: Binance OI at the bar's end ÷ OI at its start − 1 ≤ −3 %;
- **[fix 5] liquidity floor:** trailing 30-day median daily dollar volume (Binance) ≥ $1M;
- at most one event per coin per 24 h (the first).

**[fix 3] Floors per window:** ≥ 50 events **and** ≥ 25 distinct event days, else that window
"cannot answer". (The owner's "≥ 100 total" cannot support a two-window replication.)

## Trade rules

- **[fix 2] Exhaustion entry:** after the event bar, enter at the close of the first bar (within 3 bars)
  that does **not** extend the event bar's extreme (no new low for a long, no new high for a short).
  No such bar → no trade. Taker fill.
- **Stop:** 0.5 × ATR beyond the event bar's extreme, **and at least 1.0 × ATR from the entry** (fix 2).
- **[fix 6] Target:** the 20-bar volume-weighted average price at entry, frozen; if it is not beyond the
  entry in the trade's direction, no trade. Resting limit, maker fill. Time stop 24 h, taker.
- Resolution: minute by minute; stop triggers on mark, fills at that minute's LTP close less slippage;
  stop before target when both hit in one minute (conservative).
- **Costs [fix 5]:** taker 0.05 % and maker 0.02 %, ×1.18 GST; per-coin slippage = max(2 bps, half the
  2026-10-05 quoted spread); funding at each 8h settlement crossed. Reported also at 2× spread.
- Size: 1R per trade. **[fix 7]** Judged with one open position per coin; **one position at a time**
  (first coin to fire wins) is run and reported, since that is how it would be traded.

## Control and statistics

**[fix 4]** Random control: for each real trade, the same direction, stop distance and target distance
(in ATR) applied at a random bar of the same coin (not within 24h of an event), same exits and costs;
50 runs per window. p = share of runs with mean net R ≥ the real mean. All statistics clustered by
event day (cascades hit many coins on the same day).

## Pass rule

Per window: G1 gross R per trade > 0; G2 net R per trade > 0 **and** p vs random ≤ 0.05.
**PASS = G1 and G2 in both windows.** A window below its floors cannot answer.

**[fix 8] Power:** with ~50 events a window and ±1.5R per trade, only an effect of about +0.4R per trade
or more can clear the bar. A structural overshoot should be that large; a modest real effect will read
as a fail, and that is accepted in advance.

## Conduct

Step 0 is printed and committed before `run`. One run per window; a bug found after: fix, log here,
rerun, report both.

## STEP 0 RESULT 2026-10-13 — CANNOT ANSWER (`out/sweep/five_min_arm_lab/liquidation_reversion/step0_2026-10-13.txt`)

DEV: 17 events on 14 days (12 coins; 14 longs, 3 shorts). OLD: 19 events on 15 days (12 coins; 11 longs,
8 shorts). Both windows are far below the 50-event / 25-day floors. No trade outcome was read. With the
owner's thresholds (3×ATR move, 3.5× volume, −3 % OI, $1M liquidity) a genuine cascade on a liquid coin
happens about twice a month across all 32 coins — about 40 a year — so this setup cannot be tested on
two years of history, and would give one or two trades a month live. Closed as unanswerable; the
thresholds are not loosened after the fact.
