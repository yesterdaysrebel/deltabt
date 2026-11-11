# Pre-registration: idea search for the gainer fade (entry timing, condition, holding period, pump shape)

Written 2026-11-11 before any result of this search was looked at (owner 2026-11-11: "I would challenge on no
edge, look for idea"). Everything tested so far kept rule A's shape -- short at 00:00 UTC, hold 24 h -- and
filtered on daily readings. This search changes the shape instead.

## Data and split
Rule A's picks on Binance USDT-M perpetuals (`scripts/fade_binance_test.py`: top 2 previous-day gainers, stops
<= 32.5%), with each pick's 1m path for D, D+1, D+2 (`scripts/fade_idea_paths.py`).
- **Dev: 2021-2023.** Explored freely, but only over the search space below.
- **Hold-out: 2024, 2025, 2026 (to October).** Not looked at for any variant until the candidates are frozen.

## Search space (fixed in `scripts/fade_idea_search.py::variants` before the dev run)
Same stop (2 x ATR14% from the entry price), fills and fees as rule A.
1. Enter later: +1, +2, +4, +8, +12 h after 00:00, held to day end.
2. Enter at +1/+2/+4/+8 h only if the coin is already DOWN >= 0.10 / 0.25 of the stop distance from D's open.
3. Enter at +1/+2/+4/+8 h only if it is UP >= 0.10 / 0.25 / 0.50 of the stop distance (a better short price).
4. Hold 48 h, 72 h.
5. Skip a day (enter D+1 00:00); the same only if D closed up / down.
6. Pump size thirds (D-1 change; cut-offs from dev); rank 1 only, rank 2 only.

## Choosing candidates (after dev, before hold-out)
At most **two** variants: the highest z on dev among those with mean > 0 in **all three** dev years, at least 150
dev trades, and a dev mean above the baseline's. If none qualifies the search ends: no candidate, no hold-out run.
The chosen names are written into `CANDIDATES` and committed before `holdout` is run.

## Hold-out verdict (run once)
A candidate **PASSES** only if, on 2024-2026: pooled mean net R > 0 with z >= **2.24** (two candidates), mean > 0
in at least **2 of 3** years, and pooled mean above rule A's on the same years. A PASS is a reason to paper-trade
the variant, not to trade it with money.

## Known limits
Binance, not Delta; no funding; entry at the previous minute's close with no slippage beyond the taker fee;
~40 variants on dev means the best dev z is inflated -- which is what the hold-out is for.
