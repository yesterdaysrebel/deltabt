# Flexible leverage with an ROE take-profit, never liquidated — pre-registration

Written and committed 2026-11-09 BEFORE the test ran (owner: "I started this project to avoid liquidations so
that's off table, I think we can use flexible leverage to gain best ROE% (not less than 20%) per trade, if we don't
reach ROE we hold till 24h").

## Rule
Rule A's picks and stop (stop = entry × (1 + 2 × ATR14%), stops ≤ 32.5%), entry at the daily open, minute candles.
- Leverage per trade L = the HIGHEST whole leverage (cap 10x) whose liquidation (1/L − 0.5% maintenance) is at
  least **2× the stop distance** away, measured from a fill 1% under the open — so rule A's stop always triggers
  first; a liquidation can only come from a gap through the stop.
- Take-profit at ROE **X** = a price fall of X ÷ L. X ∈ **{20%, 30%, 40%, 50%}**; X = none is the 24 h hold at
  the same leverage (the comparison).
- First touch wins; a minute touching both is the stop; a stop fills at max(stop, that minute's close) and is
  counted a LIQUIDATION if that fill is at or past the liquidation price (margin lost). Else the 24 h close.
- Fee 0.05% of notional each side; funding left out. Fixed **$300 isolated margin** per trade, 2 trades a day;
  results as ROE and $.

## Judgement (as docs/fade_roe_exit_prereg.md)
Step 1, development Binance 2021–23: the X with the highest pooled mean ROE; promoted if it beats the same-leverage
hold in ≥ 2 of 3 years. Step 2, held-out once (Binance 2024, Binance 2025-26, Delta 2025, Delta 2026): pass if the
paired difference with the hold is above zero in ≥ 3 of 4 sets AND pooled z ≥ 2. Liquidations are reported per
set; any setting with a liquidation rate above 0.5% is flagged as against the owner's rule.
Also reported: year-by-month $ for the chosen X and the hold, and both against the 1x hold.
