# Pre-registration: second idea search -- thirteen families around the gainer fade

Written 2026-11-11 before any result of this search was looked at (owner 2026-11-11: "the challenge must have
other idea to try", "you might want to try other families as well", "search all"). The first search
(`docs/fade_idea_search_prereg.md`) changed the trade's timing and failed its hold-out. Data:
`scripts/fade_families_data.py` (Binance public archive, USDT-M perpetuals; delisted coins included).

Base trade ("rule A"): top 2 previous-UTC-day gainers (turnover >= $250k), short at D's open, stop = open x
(1 + 2 x ATR14%), stops <= 32.5%, stop first on 1m (filled at the worse of stop and minute close), else D's last
close; 0.05% taker each side; no funding. A long is the mirror (stop below, low checked first).

## Families and variants (fixed in `scripts/fade_families_search.py::variants` before the dev run)
1. **Crowding** (rule A taken only when a reading known before D 00:00 says so): last funding rate; D-1 summed
   funding; D-1 taker-buy share; D-1 open-interest change; top-trader and all-account long/short ratio at D-1's
   end; D-1 mean taker long/short volume ratio -- each LOWEST third and HIGHEST third (cut-offs from dev), plus
   last funding < 0 and >= 0. Open interest and the ratios exist from 2021-12 only (dev years 2022-2023).
2. **Liquidations** -- NOT TESTABLE: the archive has no liquidation history (404 for every year checked).
3. **Spot vs futures:** the coin has a Binance spot market / has none; D-1 spot volume / futures volume lowest
   and highest third.
4. **Coin age:** listed < 30 days, 30-180 days, > 180 days before D.
5. **Hedged fade:** rule A plus a BTCUSDT long of 1.0x and 0.5x the notional, same entry and exit minute, two
   more taker fees on the hedge; R stays in units of the short's stop distance.
6. **Mirror trade:** LONG the top 2 previous-day LOSERS (change < 0); the same with a 1.0x BTC short hedge.
7. **Pair:** each day, (mean R of the gainer shorts + mean R of the loser longs) / 2; judged per DAY.
8. **Follow the pump:** LONG rule A's picks, 24 h and 72 h.
9. **Weekly gainers:** short the top 2 by 7-day change (> 0); the same only if D-1 itself was a DOWN day.
10. **Against the market:** rule A only if BTC fell on D-1 / only if it rose; BTC's D-1 change lowest and
    highest third.
11. **Volume surge:** pick the 2 risers with the largest D-1 volume / 14-day mean volume; short them; long them.
12. **Scale in:** half at 00:00, half at +2 h only if the coin is up >= 0.25 of the stop distance (and the
    +1 h / 0.10 version); R per unit of full risk.
13. **Time-of-day exit:** rule A closed at 04:00, 08:00, 12:00, 16:00, 20:00 UTC instead of 24:00.

## Choosing candidates (dev 2021-2023, before the hold-out)
At most **one per family**: the highest dev z among variants with mean > 0 in every dev year in which they have
>= 30 trades (at least two such years), >= 150 dev trades (days for family 7), and -- for families that filter or reshape rule A (1, 3, 4,
5, 10, 12, 13) -- a dev mean above rule A's. A family with no qualifier gets no candidate. The chosen names are
written into `CANDIDATES` and committed before `holdout` is run.

## Hold-out verdict (2024-2026, run once)
The hold-out judged two candidates earlier today and may judge up to twelve more, so the bar is z >= **2.70**
(one-sided 5% over 14 tests). A candidate **PASSES** only with pooled mean net R > 0 at z >= 2.70, mean > 0 in at
least **2 of 3** years, and (families 1, 3, 4, 5, 10, 12, 13) a pooled mean above rule A's on those years. A PASS
means paper-trade it; nothing live changes by itself.

## Known limits
Binance, not Delta (Delta publishes no long/short ratios and its funding differs); funding income is not in R;
the hedge uses BTC's 1m close at the entry and exit minutes; ~45 variants on dev inflate the best dev z, which
is what the hold-out is for.
