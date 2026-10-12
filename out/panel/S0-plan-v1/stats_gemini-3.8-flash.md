# stats — google/gemini-3.8-flash

### 1. Pass Bars
* **DEV bar ($t \ge 2.0$, $p \le 0.05$ vs. random control, net $> 0$ after costs):** Inadequate for in-sample/discovery data. Because DEV is used to inspect structure (S1), cluster shapes (S2), and fit the LightGBM models (S2), S4 is essentially an in-sample verification. A classical $t \ge 2.0$ ($p \approx 0.046$ two-sided) has virtually zero hurdle against a model that searched thousands of split interactions or arbitrary path clusters.
* **OLD bar (frozen rule on untouched historical window):** Appropriate as a preliminary hold-out, provided no hyperparameter tuning or threshold relaxing occurs upon failure.
* **S8 Dry-run bar ("100 trades or 21 days"):** **Self-contradictory.** The design strictly enforces §2: *"one (or few) positions at a time, capital idle most of the time, horizons of hours to days."* If a position is held for 1 to 3 days sequentially, 21 days yields **7 to 20 trades at most**, never 100. Halting at 21 days with $\sim 10$ trades guarantees a standard error of $\sigma / \sqrt{10} \approx 0.32R$, making any $t$-bar or equivalence test statistically impossible.

---

### 2. Controls
* **Leakage and null controls (Planted signal $\ge 0.52$, shuffled labels):** Sound. Planted signal checks pipeline sensitivity (preventing false negatives from buggy label joins), and shuffled labels confirm zero-information baseline.
* **Random entry control with identical exits:** Critical and correctly included. As proven in the 1D trend test (where random entries achieved $+0.17R$ solely because of a $2.5\times\text{ATR}$ trailing exit), entry filters must be judged against the exit's intrinsic drift.
* **Missing Control in S2 Pattern Pass:** The plan clusters 2-day paths preceding large moves ($> 2\times$ range). **This lacks a negative control.** Conditioning solely on extreme outcomes without running the identical clustering algorithm on quiescent or random 2-day windows commits severe selection bias (conditioning on the outcome). It will identify clusters that appear predictive simply because they precede volatility, even if identical shapes appear equally often before mean-reverting flat markets.

---

### 3. Clustering & Autocorrelation
* **Day-Clustering is Insufficient:** The plan specifies "day-clustered $t \ge 2$." However, §3a specifies predicting **"3 / 5 / 10-day moves"** with trade horizons of **"hours to days."** 
  * When holding periods exceed 24 hours, returns of concurrent and overlapping positions are autocorrelated across time. 
  * Day-clustering only adjusts for cross-sectional correlation on a single calendar day; it **fails to correct for multi-day serial correlation** (moving average errors of order $q = \text{holding period}$).
  * Standard errors will be severely understated and $t$-statistics inflated unless computed via Hansen-Hodrick / Newey-West (with lag $\ge \text{max holding period}$), block bootstrap, or by sampling non-overlapping trade sets.

---

### 4. Power & Sample Size
* **The Single-Position Bottleneck:** Judging every candidate in its "one-position-at-a-time" form (§2) collapses the effective degrees of freedom.
  * Over 15 months of DEV data, sequential trades lasting 1–3 days will generate only $N \approx 100\text{--}150$ trades.
  * Over 9 months, $N \approx 60\text{--}90$ trades.
* **Statistical Power:**
  * Assuming standard unit-risk dispersion ($\sigma_R \approx 1.0R$), the standard error is $\text{SE} \approx 1.0 / \sqrt{100} = 0.10R$.
  * For two-sided $\alpha = 0.05$ ($z = 1.96$) and standard $80\%$ power ($z = 0.84$), required test statistic distance is $z_{1-\alpha/2} + z_{1-\beta} = 2.80$.
  * Realistic market edges in liquid/semi-liquid perps after $0.158\%$ taker costs rarely exceed $+0.04R$ to $+0.08R$ per trade.
  * At $N = 100$ and a true edge of $+0.05R$, statistical power is:
    $$Z = \frac{0.05}{0.10} - 1.96 = -1.46 \implies \text{Power} \approx 7.2\%$$
  * At a true edge of $+0.08R$, power is only $\approx 12.3\%$.
  * The design is **severely underpowered** to detect realistic edges when constrained to one position at a time. It will systematically discard genuine positive-expectancy mechanisms because they fail to clear $t \ge
