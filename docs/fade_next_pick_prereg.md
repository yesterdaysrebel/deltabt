# Pre-registration: rule A "next pick" -- replace a skipped top-2 gainer with the next one that fits

Written 2026-11-11 before any data for this test was fetched or looked at (owner 2026-11-11: "option 2 approved").

## Why
Real-money run 3 (`docs/fade_live_ladder_prereg.md`) skips a pick whose stop (entry x (1 + 2 x ATR14%)) is more than
32.5% away. In the first two weeks of paper picks (10-29 .. 11-11) that hit 11 of 28 picks, 11 of the last 18. Every
backtest so far measured only the top-2 picks with stops <= 32.5%; nothing has measured what a slot left empty by a
skip would earn if it went to the next gainer instead.

## The rule tested ("A-next")
On day D: rank the eligible coins (D-1 change > 0, D-1 turnover >= $250k, ATR14% known) by D-1 change; walk down
from rank 1 to **rank 10 at most** and take the first **two** whose stop is <= 32.5% away. Everything else is rule
A unchanged: short at D's daily open, stop = open x (1 + 2 x ATR14%) checked first on 1m candles and filled at
max(stop, that minute's close), else exit at the last 1m close of D; taker 0.05% each side; no funding.

## What is judged
Only the **substitute** trades: picks A-next takes at ranks 3-10 (they exist only because a higher pick was
skipped). The top-2 picks with stops <= 32.5% are the trades rule A already takes and are not re-judged.

Judged data: **Binance USDT-M perpetuals 2021-2024** (`scripts/fade_binance_test.py`'s archive, daily panel and
trade simulation, delisted coins included). Neither the fade's rules nor these substitute trades were developed on it.

**PASS** only if both hold for the substitutes' net R per trade under rule A:
1. pooled mean > 0 with z = mean / (sd / sqrt(n)) >= **2.0**, and
2. mean > 0 in **at least 3 of the 4** years.

Otherwise **FAIL**, and the live rule stays as it is (skip, no substitute).

## Reported, not judged
- Binance 2025-2026 (the positive-control years; Delta 2026 is the period the fade was tuned on, so it cannot judge).
- How often a substitute is taken (days, share of slots), its rank, and how many slots stay empty even at rank 10.
- Substitutes under QUICK (0.2x target), for reference only.

## Not changed by this test
Nothing live changes on a PASS by itself: the owner decides, and the switch is a separate pull request with its
own review (the paper runner first, then the real account).
