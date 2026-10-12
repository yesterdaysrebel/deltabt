# breadth — deepseek/deepseek-v4-pro

1. **Funding rate cross-sectional predictive power**  
   *Mechanism*: On Delta India, funding rates settle every 8 h and reflect the cost of leverage. Extremely positive funding signals overcrowded longs that may unwind; extremely negative funding signals overcrowded shorts. A coin’s funding rate percentile relative to the cross‑section should therefore predict its subsequent return over the next funding interval.  
   *Measurement*: At each funding timestamp (00/08/16 UTC), sort the 32 perps by their last funding rate, form a long‑short portfolio (top vs bottom quintile), and hold for 8 h. Compute the mean return, Sharpe ratio, and t‑statistic after 0.158 % round‑trip costs.  
   *What number shows it is real*: A Sharpe > 0.5 after costs, with a t‑statistic > 2.0 on the long‑short spread, and the sign of the spread consistent across the DEV and OLD windows (e.g., both negative, meaning high‑funding coins underperform).

2. **Intraday volatility seasonality**  
   *Mechanism*: Crypto markets exhibit time‑of‑day patterns in volatility due to the overlap of global trading sessions. On Delta India, which serves predominantly Indian retail, activity may peak during Indian daytime (e.g., 04:00–12:00 UTC) and dip during the US night. If volatility is predictably higher in certain hours, position sizing or trade timing can be adjusted.  
   *Measurement*: For each hour of the day (UTC) and day of the week, compute the mean absolute 1‑minute return across all 32 perps, using the full Jun 2024–Sep 2026 sample. Test for stability by splitting the data into halves and checking rank correlation of the hourly pattern.  
   *What number shows it is real*: The hourly pattern has a rank correlation > 0.8 between the first and second halves of the data, and a simple rule (e.g., only trade during the top‑quartile volatility hours) improves the Sharpe ratio of a baseline strategy by > 0.1 in a walk‑forward test.

3. **Short‑term reversal (1 h–6 h)**  
   *Mechanism*: Overreaction and liquidity‑taking pressure often cause temporary price dislocations that revert within hours. On a venue with thin altcoin spreads, this effect may be pronounced. A strategy that buys the worst‑performing coins over the past 1–6 hours and sells the best‑performing should earn the re
