# Gates on the gainer fade — pre-registration

Written and committed 2026-11-09 BEFORE any gate was computed (owner: "look for improvements … if we add more
gates in trading we can run better"). The plain fade failed on Binance 2021-2024 (`docs/fade_binance_prereg.md`)
and was ~0 on Delta 2025 after costs; it was positive in 2025-26 on Binance and 2026 on Delta. This tests
whether a gate known before entry separates good fades from bad ones in years the gate was not chosen on.

## Gates (each a yes/no on day D for a pick; only data up to D−1's close; direction fixed here)
- **G1 bear market:** BTC's D−1 close is below its 20-day average close. *Fades work better when the market is weak.*
- **G2 big pump:** the pick's D−1 gain is ≥ 25%. *Bigger pumps retrace more.*
- **G3 rejection wick:** on D−1 the pick closed ≥ 30% of its day's range below the high, (high−close)/(high−low) ≥ 0.30.
  *Buyers already failed to hold the top.*
- **G4 new coin:** the pick's first daily candle on the venue is < 90 days before D. *New listings fade harder.*
- **G5 lone pump:** fewer than half of all liquid coins rose on D−1. *A pump against a falling market is less supported.*
- **G6 volume spike:** the pick's D−1 turnover is ≥ 3× its median turnover over D−21..D−2. *Spikes exhaust.*

Picks, stops, fills, fees, entry and the 32.5% stop filter are unchanged (`scripts/fade_binance_test.py`,
`scripts/fade_costed.py`). A gate removes picks; the removed slots are NOT refilled.

## Step 1 — choose on DEVELOPMENT data only: Binance 2021, 2022, 2023
A gate is **promoted** if, under its rule (A or QUICK, judged separately): the gated mean net R is above the ungated
mean in at least 2 of the 3 years, the gated pooled mean is > 0, and it keeps at least 30% of the trades.
If two or more gates are promoted, their AND-combination is tested as one more candidate (only if it keeps ≥ 15%).

## Step 2 — judge ONCE on four held-out sets the gates were never chosen on
Binance 2024 · Binance 2025-26 · Delta 2025 · Delta 2026 (Delta with funding and the bot's real entry).
A promoted gate **passes** if, in at least 3 of the 4 sets, its gated mean R is above the ungated mean AND above 0,
and the pooled held-out gated mean is ≥ 2 standard errors above zero.

## Consequence
- A pass → that gated rule may go to a forward paper test with these exact numbers before any real money.
- No pass → gates do not rescue the fade; the verdict of 2026-11-09 stands.
- Delta/Binance 2025-26 were already looked at UNGATED; that is disclosed, and why Binance 2024 is in the held-out sets.
