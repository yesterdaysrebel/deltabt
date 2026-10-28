# Gainer fade — four filters from EXTERNAL sources (pre-registered 2026-10-28)

Owner, 2026-10-28: "I challenge your findings … we can use external refs to improve fade." These four
filters come from published work or practitioner rules, NOT from our data, so they may be judged on
BOTH years. Committed before any of them is computed. Honest caveat: our 2026 search (764 filters,
`CONTEXT.md`) already looked at turnover and funding in other forms, so H1/H3 are not perfectly blind;
H2's abnormal volume and H4's open interest were never computed on this data.

Base: rule A (`docs/gainer_fade_stop_prereg.md`, k = 2.0), universe incl. the 23 delisted perps
(`out/gainer_fade/delisted_rows_2025_2026.parquet`). Each filter is applied to the eligible set BEFORE
taking the top 2 by D−1 change. Pick days 2025-01-15 … 2025-12-31 and 2026-01-15 … 2026-10-27.

- **H1 — reversal lives in the illiquid coins** (Zaremba et al. 2021, IRFA: daily reversal driven by
  illiquidity, the largest coins show momentum; Kozlowski, Puleo & Zhou 2020: strongest in small,
  illiquid coins). Keep a coin if its D−1 turnover is BELOW the median D−1 turnover of all liquid coins
  that day.
- **H2 — low-volume moves reverse, high-volume moves continue** (Llorente, Michaely, Saar & Wang 2002;
  "Predictable patterns following large price changes", Review of Behavioral Finance: large changes on
  low volume reverse, on high volume continue). Abnormal volume = D−1 turnover ÷ the mean of D−31 … D−2.
  Keep a coin if it is BELOW the median abnormal volume of that day's eligible coins.
- **H3 — crowded longs unwind** (practitioner rule: persistently high funding precedes corrections).
  Funding at entry normalised to 8 h (rate × 8 h ÷ the product's settlement interval; 8 h assumed for
  delisted products). Keep a coin if it is AT OR ABOVE the median of that day's eligible coins.
- **H4 — open interest built during the pump = fuel** (practitioner rule: rising OI at extreme funding
  means the crowd is still adding). OI change = OI at D 00:00 ÷ OI at D−1 00:00 − 1, from Delta's
  `OI:<SYMBOL>` 1h series. Keep a coin if its OI ROSE (> 0). A coin with no OI value is kept out.

## Decision (per filter, alpha 0.05 / 4 = 0.0125)
ADOPT only if: (1) the filter's mean net R minus rule A's is > 0 in **2025 AND 2026 separately**;
(2) a day-block bootstrap over both years pooled (5,000 resamples) gives P(difference ≤ 0) ≤ 0.0125;
(3) the filtered rule's pooled mean net R > 0. Win rate, average win/loss, funding-inclusive R and the
$250 account are reported, not judged. No combinations in this round.
