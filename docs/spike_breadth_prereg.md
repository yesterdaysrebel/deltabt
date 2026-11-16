# Spike fade on coin-days it has never seen — pre-registration (2026-11-16)

Owner, 2026-11-16: "Run as many tests as you want, check the strategy." Written before any of these coin-days was
downloaded.

## Why
Everything known about the spike fade (sell 5% above the 00:02 close, stop 10%, out after 8 h) comes from one set of
1,049 coin-days: the two top previous-day gainers of each day. The 8-hour clock and the 10% stop were chosen on that
set. The same rule on OTHER coins of the same days is the nearest thing to fresh evidence that exists today, and it
also says whether the pick rule matters.

## Groups (same days, 2025-01-15 .. 2026-10-27, from `gainer_fade_2025_lab.panel`, nothing known after 00:00 UTC)
- RANK 3-5: the 3rd, 4th and 5th eligible coins by previous-day change, same volatility filter as the picks
  (2 x ATR14% <= 32.5%).
- OTHER GAINERS: 2 random eligible coins per day ranked 6th or lower (seeded).
- NON-GAINERS: 2 random liquid coins per day whose previous day was flat or down (seeded).

## Rule (frozen, identical for every group)
Resting sell 5% above the close of the 00:02 minute; filled in the first minute 00:03..23:59 whose high is 0.05%
through it; stop 10%; out at the close 8 h after the fill. STRICT and LENIENT fills, `spike_flip_back.trade`.
Before fees (headline) and after the real fees (0.0212% in, 0.0531% out). Delta 1m trade candles, public API.

## What would count
- SUPPORT: RANK 3-5 mean per trade > 0 under STRICT, positive in 2025 and in 2026.
- AGAINST: RANK 3-5 <= 0. Then the result is specific to the two coins it was tuned on, and the recommendation
  weakens to "unproven".
- The two control groups are descriptive: if they are as good as the gainers, the edge is "sell any 5% intraday
  spike", not "fade yesterday's gainer".
Nothing is tuned after the run; a second implementation reproduces the headline before it is quoted.
