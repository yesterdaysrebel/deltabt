# Pre-registration: second idea search -- crowding, a BTC hedge, and the mirror trade (losers)

Written 2026-11-11 before any result of this search was looked at (owner 2026-11-11: "the challenge must have
other idea to try", "you might want to try other families as well"). The first search
(`docs/fade_idea_search_prereg.md`) changed the trade's timing and failed its hold-out. This one adds information
no test here has used, and two different trades. Data: `scripts/fade_families_data.py` (Binance public archive).

## Families and search space (fixed in `scripts/fade_families_search.py` before the dev run)
**C -- crowding (rule A unchanged, taken only when a reading says so).** Readings known before D 00:00:
last funding rate; D-1's summed funding; D-1 taker-buy share of volume; D-1 open-interest change; top-trader and
all-account long/short ratios at D-1's end; D-1 mean taker long/short volume ratio. For each: the LOWEST third and
the HIGHEST third (cut-offs from dev only), plus "last funding < 0" and "last funding >= 0". Open interest and the
ratios exist from 2021-12 only, so their dev years are 2022-2023.

**H -- hedged fade.** Rule A's short plus a BTCUSDT long of 1.0x (and 0.5x) the same notional, opened and closed
with it; two more taker fees on the hedge. R stays in units of the short's stop distance.

**L -- mirror trade.** LONG the top 2 previous-day LOSERS (change < 0, same turnover floor, stop = open x
(1 - 2 x ATR14%), stops <= 32.5%, low checked first, filled at min(stop, minute close), else D's last close);
and the same with a BTC short hedge of 1.0x.

## Choosing candidates (after dev 2021-2023, before the hold-out)
At most **one per family**: the highest dev z among variants with mean > 0 in every dev year they have, at least
150 dev trades, and (C, H) a dev mean above rule A's. A family with no qualifier gets no candidate. The names are
written into `CANDIDATES` and committed before `holdout` is run.

## Hold-out verdict (2024-2026, run once)
The hold-out has already judged two candidates today, so the bar is higher: a candidate **PASSES** only with pooled
mean net R > 0 and z >= **2.58**, mean > 0 in at least **2 of 3** years, and (C, H) a pooled mean above rule A's on
the same trades' years. A PASS means paper-trade it; nothing live changes by itself.

## Known limits
Binance, not Delta (Delta publishes no long/short ratios; its funding differs); funding income is not in R; the
hedge uses BTC's 1m close at the entry and exit minutes; ~25 variants on dev inflate the best dev z.
