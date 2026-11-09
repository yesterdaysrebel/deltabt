# Close at a return-on-margin (ROE) target instead of the 24 h hold — pre-registration

Written and committed 2026-11-09 BEFORE the grid was computed (owner: "can we not close the position if we hit
certain ROE%, can you run tests on it with percentage starting from 20 up to 70?").

## Definition
ROE = the position's profit ÷ its isolated margin. With leverage L from amendment 1 (`live.fade.lever_plan`, at
the ladder's 10% step — the step most trades are taken at; a lower step only lowers L), an ROE of X is reached
when the price has fallen X ÷ L below the entry. Grid: **X = 20%, 30%, 40%, 50%, 60%, 70%**, and none (= the
24 h hold).

## Trades
As `docs/fade_target_grid_prereg.md`: rule A's picks and stop (stops ≤ 32.5%), entry at the daily open, minute
candles, first touch wins, a minute touching both is the STOP, a stop fills at max(stop, close), the target at
the target, else the 24 h close; fee 0.05% each side; funding left out. Positive control: "none" must equal the
grid's `hold` column on the same trades.

## Step 1 — development: Binance 2021, 2022, 2023
The ROE level with the highest pooled mean R is chosen; **promoted** if it beats the hold in ≥ 2 of 3 years.

## Step 2 — held-out, judged once: Binance 2024 · Binance 2025-26 · Delta 2025 · Delta 2026
A promoted level **passes** if its paired difference with the hold is above zero in ≥ 3 of 4 sets AND the
pooled paired difference has z ≥ 2. Also reported: each level on each set, and the run-3 account with
leverage (amendment 1) per set.

## Consequence
Pass → may replace the 24 h hold (amendment, owner's yes). Fail → the 24 h hold stays.
