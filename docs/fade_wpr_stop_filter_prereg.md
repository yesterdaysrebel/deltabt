# Pre-registration: rule A with a Williams %R filter and a stop-distance filter

Written 2026-11-11 before the test was run (owner 2026-11-11: "can we not add filter with WPR and SL in this fade?
run some tests"). Also answers, descriptively, "how many stopped trades came back?" after run 3's ARIAUSD stop.

## Base
Rule A on Binance USDT-M perpetuals, exactly `scripts/fade_binance_test.py`: top 2 previous-UTC-day gainers
(change > 0, turnover >= $250k), short at D's open, stop = open x (1 + 2 x ATR14%), stops <= 32.5% only, stop first
on 1m candles filled at max(stop, minute close), else exit at D's last 1m close; 0.05% taker each side; no funding.

## Filters (a trade is TAKEN only if the filter says so; nothing else changes)
- **W (%R overbought):** daily Williams %R(14) on D-1's close >= -20, i.e. D-1 closed in the top 20% of its
  14-day high-low range. %R = -100 x (highest high - close) / (highest high - lowest low), 14 daily bars to D-1.
- **S (stop not too wide):** the stop is <= 20% away (2 x ATR14% <= 0.20).
- **WS:** both.

## Judged
Binance **2021-2024**. A filter **PASSES** only if all three hold for its TAKEN trades' net R (rule A):
1. pooled mean > 0 with z >= **2.4** (three filters tested; a Bonferroni-style bar instead of 2.0),
2. mean > 0 in **at least 3 of 4** years, and
3. mean above the unfiltered rule A's in **at least 3 of 4** years.

Otherwise FAIL; the live rule is unchanged. Binance 2025-2026 is reported only (Delta 2026 tuned the fade).

## Reported, not judged: stopped trades that came back
For every rule A trade that hit its stop: was D's last close back under the stop, and under the entry? And what
would the trade have made with NO stop (exit at D's last close)? Shown per year, with the no-stop rule A over all
trades for comparison. "No stop" is not a candidate: a 1x short with no stop is open to unlimited loss.
