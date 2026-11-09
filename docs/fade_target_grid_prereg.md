# Take-profit targets instead of the 24 h hold — pre-registration

Written and committed 2026-11-09 BEFORE the grid was computed (owner: "instead of 24h hold can we not cap the
target? test different targets"). Earlier evidence: Delta 2025–26 only, targets 0.2–1× (`out/gainer_fade/
target_compare.txt`): no target beat the 24 h hold in 2026; 0.2–0.3× were slightly ahead in 2025.

## Trades
Rule A's picks and stop (top 2 previous-UTC-day gainers, stop = open × (1 + 2 × ATR14%), stops ≤ 32.5% only),
entry at the daily open, minute candles: the first touch wins; a minute touching both stop and target counts as
the STOP; a stop fills at max(stop, that minute's close); a target fills at the target; otherwise the 24 h close.
Fee 0.05% each side; funding left out (as in the Binance test). Targets are a fraction of the stop distance below
the entry: **0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0**, and none (= rule A).
Positive control: the "none" column must reproduce `out/binance/trades.parquet` rule A on the same trades.

## Step 1 — development: Binance 2021, 2022, 2023
The target with the highest pooled mean R over the three years is chosen. It is **promoted** if its mean R beats
rule A's (same trades) in at least 2 of the 3 years.

## Step 2 — held-out, judged once: Binance 2024 · Binance 2025-26 · Delta 2025 · Delta 2026
A promoted target **passes** if its paired difference with rule A (same trades) is above zero in at least 3 of
the 4 sets AND the pooled paired difference has z ≥ 2.

## Also reported (descriptive, not judged)
Every target on every set: win rate, target/stop hit rates, mean R; and the run-3 account (ladder 10/5/3 at
20%/35%, 30% cap, $1,500, refill under $1,000, $3,000 budget) end balance and refills per set.

## Consequence
A pass → the target may replace the 24 h hold in the run-3 rules (a new prereg amendment, owner's yes).
No pass → rule A keeps the 24 h hold.
