# Delta India options — feasibility measurement

**Measured 2026-08-24.** This is a *measurement*, not a hypothesis test. It
was run before any pre-registration was written, as the coverage measurement
for H-XSec-1 was, so that a prereg can state its power honestly rather than
discover it afterwards. **Nothing here is a verdict and nothing here is in
`out/experiments.jsonl`.**

`PROGRAM_SUMMARY.md` closed the perpetual programme with thirteen nulls and a
rule: *do not test another indicator combination on 1m/5m crypto perps.*
Options are not that. The tradeable quantity is variance rather than
direction, and the daily expiry cycle sits at exactly the horizon where the
summary says the cost wall stops binding. Whether that is enough is the
question below.

---

## 1. The data exists, and it is deeper than expected

| | |
|---|---|
| Live option products | 1,070 (486C / 475P / 11 MOVE) against 28 perps |
| 24h option turnover | **$1.41B** against $3.22B on perps — 0.44x, not a sideshow |
| Breadth | 928 of 1,070 traded in the last 24h; top-25 names are 44% of volume |
| By underlying | BTC $985M, XAUT $249M, ETH $174M |
| Catalog depth | **145,406 option products**, expiries 2020-10 → 2026-10 |
| Settled with a price | 144,329 of 145,406 |
| Usable candle history | **2024-01 onward** — earlier products exist in the catalog but serve no candles |
| Expiry cadence | BTC expires **every single day**: 364 expiries in 2024, 365 in 2025 |
| Chain width | median 37 strikes per BTC expiry (p25 30, p75 47) |
| Contract lifetime | median **72h** from listing to expiry; p05 16h |

Both series a study needs are served for expired contracts: traded premium
(`C-BTC-...`) and the exchange's own mark (`MARK:C-BTC-...`). Expired products
carry a populated `settlement_price`, so expiry payoff is ground truth.

Two traps found while establishing this:

* **Spot index symbols are not regular.** BTC is `.DEXBTUSD`, but ETH is
  `.DEETHUSD` (no X) and XAUT is `.DEXAUTUSD`. Guessing the pattern silently
  returns zero bars. Read `spot_index.symbol` off the product.
* **`meta.total_count` is wrong on the products endpoint.** It reported 10,000
  while the cursor happily served 145,406. Paginate until the cursor is
  exhausted; do not trust the count.

## 2. IV can be reconstructed, and the reconstruction is validated

Delta publishes **no history of quotes, IV or greeks** — `mark_iv`, `bid_iv`,
`ask_iv` and the greek set exist only in the live `/v2/tickers` snapshot. Any
volatility study therefore has to invert the exchange's mark price back through
the model that produced it, which is a modelling assumption and not a
measurement.

`research/validate_iv.py` tests it directly: `/v2/tickers` publishes both the
mark price and Delta's own `mark_iv` for every live option, so invert the
former and compare. Across 986–997 live contracts:

| | |
|---|---|
| Calibrated forward rate | **r = 0.000** — Delta prices off spot, with no carry |
| Median absolute IV error | **0.0004–0.0011 vol points** (0.06–0.21% of IV) |
| p95 absolute error | 0.025–0.031 vol points |
| Worst tenor bucket | `<1d`, median 0.0008–0.0079 |
| Non-invertible | ~1% (mark outside arbitrage bounds) |

The rate result is not a rounding artifact: `r = 0.05` triples the median error
and `r = 0.15` makes it 20x worse. **Reconstructed IV is trustworthy**, so the
`MARK:` history back to 2024-01 is usable as an IV history.

One bug was found and fixed while establishing this. At the wings the time
value underflows float64 — a deep-ITM call at F=80,000 / K=40,000 has a time
value near 1e-30 and prices to *exactly* intrinsic — and bisection on a
numerically flat function returned a confident **0.604 for a contract priced at
0.20 vol**. `MIN_TIME_VALUE_FRAC` now makes the inversion return NaN there. A
reconstructed IV history containing those values would have been undetectably
wrong.

## 3. The options cost law

The perpetual programme's one durable positive result was a cost identity,
`cost_r = round_trip / stop_pct`. The options analogue comes out a different
shape because **the fee is charged on notional, not on premium**.

Writing `p = premium / spot`, per side:

    fee / premium  =  min(fee_rate / p, cap) * gst
    round_trip / premium  =  2 * (fee/premium + half_spread_frac)

Two consequences with no perpetual analogue:

1. **Cost per unit of premium rises as the option gets cheaper.** A contract
   priced at 0.1% of spot pays ten times the premium-relative fee of one at 1%.
2. **The cap creates a hard floor regime.** Below `p = fee_rate / cap` the fee
   becomes a flat percentage of premium. At the 10% cap that is a **20% round
   trip before spread**. The cheap far-OTM end of this surface is uninvestable
   on fees alone, and that can be asserted without a backtest.

At the current 0.01% rate, with the measured 1.34% half-spread:

| premium / spot | fee, % of premium | round trip, % of premium |
|---|---|---|
| 5.00% | 0.24% | 3.15% |
| 1.00% | 1.18% | 5.04% |
| 0.20% | 5.90% | 14.48% |
| ≤0.10% | 11.80% (capped) | 26.28% |

### The fee rate is not constant, and this matters

`taker_commission_rate` reads 0.0001 today, but across the catalog it has
stepped **three-fold, twice**, inside the available history:

| expiries | rate |
|---|---|
| 2024-01 .. 2025-07 | 0.0300% |
| 2025-07 .. 2025-12 | 0.0150% |
| 2025-12 onward | 0.0100% |

Maker equals taker on all 145,406 products — there is no maker/taker split on
options. Any study spanning the change is comparing two cost regimes, and
hardcoding today's rate understates 2024 friction threefold. Costs must be
built per contract from the catalog row.

### What is NOT measured, and cannot be

The **quoted spread is the dominant cost and has no history**. Measured live
across the 720 contracts turning over >$10k/24h: median half-spread **1.34% of
mid** (p25 0.90%, p75 2.75%). On any past date that is an assumption, not a
measurement. Every result below is therefore reported against a spread sweep.

Since nothing can recover the spread on a date already past, forward collection
started on **2026-08-24**: `deltabt/data/quote_recorder.py` polls `/v2/tickers`
every 15 minutes and appends quotes, IV and greeks for all ~1,070 live
contracts to a daily Parquet partition. See `deploy/recorder/` for the systemd
unit — a session-scoped process is fine for a day and wrong for a quarter,
because the gap a closed laptop leaves cannot be backfilled from any endpoint.
The point at which it changes a conclusion is when there is enough history to
replace `DEFAULT_HALF_SPREAD_FRAC` with a measured, time-varying,
per-moneyness spread.

## 4. Is there a variance risk premium? — no

The cleanest model-free form of "implied exceeds realised": 24h before each
settlement, sell the ATM call and put; at settlement, pay their intrinsic
value. No volatility model is on this path at all — entry is the exchange's own
`MARK:` series, and the payout is the published `settlement_price`.

*Validation:* summed settlement prices reproduce `|S_expiry − K|` with
correlation **0.991** and a signed bias of **−0.0038 of premium**, consistent
with Delta settling on a time-averaged index against a point read of the spot
close. The small bias runs *against* short straddles looking good, so it does
not flatter the null.

**BTC, 959 daily expiries, 2024-01-06 → 2026-08-24**, return per unit of
premium collected:

| | mean | median | 95% CI | t |
|---|---|---|---|---|
| gross | **−0.0011** | +0.2761 | [−0.0648, +0.0581] | **−0.035** |
| net | −0.0959 | +0.1758 | [−0.1591, −0.0371] | −3.072 |

Median straddle premium 1.66% of spot; median modelled round trip 9.0% of
premium. Win rate 63.0%.

**ETH agrees**, on 929 expiries over the same window (2024-02-07 →
2026-08-24). Note *agrees*, not independently replicates — see the caveat
below the table:

| | mean | median | 95% CI | t |
|---|---|---|---|---|
| gross | **−0.0242** | +0.2745 | [−0.0949, +0.0450] | **−0.685** |
| net | −0.1151 | +0.1865 | [−0.1864, −0.0462] | −3.268 |

Win rate 62.1%, worst single expiry −13.95, median round trip 7.1% of premium
(lower than BTC's because ETH's straddle is richer, 2.42% of spot against
1.66%).

**These are not two independent experiments, and an earlier draft of this
document wrongly implied they were.** Measured on the 929 shared expiry dates,
BTC and ETH straddle gross returns correlate **+0.743** (Spearman +0.615) and
carry the same sign on **77.5%** of them. The close agreement on the median
(+0.2761 vs +0.2745) is largely one result observed twice. Treat the combined
evidence as roughly one and a quarter samples, not two.

**Gross is zero.** Not "positive but eaten by costs" — zero, with a
confidence interval that excludes anything harvestable. And it is zero in
every fee era independently:

| fee era | n | window | gross mean | t |
|---|---|---|---|---|
| 0.0300% | 560 | 2024-01-06 .. 2025-07-21 | +0.0204 | +0.52 |
| 0.0150% | 143 | 2025-07-22 .. 2025-12-11 | −0.0316 | −0.50 |
| 0.0100% | 256 | 2025-12-12 .. 2026-08-24 | −0.0311 | −0.46 |

Spread sensitivity changes nothing, because gross does not depend on it:

| half-spread | round trip | net mean | t |
|---|---|---|---|
| 0.00% | 6.33% | −0.0691 | −2.21 |
| 0.50% | 7.33% | −0.0791 | −2.53 |
| 1.34% | 9.01% | −0.0959 | −3.07 |
| 2.75% | 11.83% | −0.1241 | −3.98 |

Even at **zero spread and zero fees** the gross is −0.0011. There is nothing
to harvest to begin with.

### The distribution is the familiar short-vol shape

Wins often, mean zero, and the mean is decided by a handful of days:

* win rate 63.0%, median **+0.276** of premium
* worst single expiry **−11.42** of premium (2026-08-20: BTC moved ~12% in a
  day against a straddle priced for ~1%)
* the 5 worst expiries sum to **−32.79**; the whole 959-expiry sample sums to
  **−1.07**. Remove those five and it is +31.72.

This is why the median is a trap here and the mean is the only number that
matters. A strategy that wins 63% of the time and earns nothing is exactly
what the perpetual programme kept finding, arrived at by a different route.

## 5. Does the premium appear at longer tenors? — no

Friction is a fraction of premium, and premium grows roughly as `sqrt(T)`, so
cost per unit of premium should fall as `1/sqrt(T)`. If a small variance
premium existed and were merely hidden by friction, longer tenors are where it
would surface. This is the same argument H-Scalp-3 ran on perps — and
H-Scalp-3 is also the reason to distrust it, because there cost fell exactly as
predicted and the gross still did not survive.

`research/vrp_term.py` sweeps the entry horizon. **The sample changes at 72
hours and this is not a clean sweep**: Delta lists daily contracts exactly 72h
before settlement, so 24h and 48h run on ~950 daily expiries while 72h and
beyond run on ~135 weeklies and monthlies — a different, smaller, and
different-in-kind population.

| | horizon | n | premium %spot | cost %prem | gross | 95% CI | t |
|---|---|---|---|---|---|---|---|
| BTC | 24h | 959 | 1.66 | 9.01 | −0.0011 | [−0.065, +0.058] | −0.04 |
| BTC | 48h | 957 | 2.37 | 6.98 | −0.0378 | [−0.129, +0.043] | −0.87 |
| BTC | 72h | 137 | 3.33 | 5.95 | −0.1339 | [−0.344, +0.051] | −1.33 |
| BTC | 120h | 136 | 4.19 | 5.36 | −0.0783 | [−0.294, +0.106] | −0.76 |
| BTC | 168h | 136 | 4.82 | 4.87 | −0.0765 | [−0.271, +0.095] | −0.81 |
| BTC | 336h | 135 | 6.92 | 4.26 | −0.0791 | [−0.268, +0.097] | −0.85 |
| BTC | 720h | 30 | 10.22 | 3.75 | −0.1022 | [−0.333, +0.104] | −0.91 |
| ETH | 24h | 929 | 2.42 | 7.10 | −0.0242 | [−0.095, +0.045] | −0.69 |
| ETH | 48h | 928 | 3.36 | 5.85 | −0.0798 | [−0.170, +0.008] | −1.75 |
| ETH | 72h | 133 | 4.72 | 4.92 | −0.1674 | [−0.380, +0.043] | −1.53 |
| ETH | 120h | 132 | 5.78 | 4.66 | −0.1419 | [−0.326, +0.022] | −1.59 |
| ETH | 168h | 132 | 6.79 | 4.38 | −0.0620 | [−0.227, +0.087] | −0.77 |
| ETH | 336h | 131 | 9.74 | 3.92 | −0.1299 | [−0.302, +0.032] | −1.52 |
| ETH | 720h | 29 | 14.12 | 3.55 | −0.2479 | [−0.536, −0.004] | −1.81 |

**No horizon shows a positive gross.** The bar was fixed before the run —
positive on both underlyings, at adjacent horizons, surviving cost stress —
and nothing comes near it. With 14 cells at a nominal 5% bar, 0.7 false
positives were expected and zero appeared.

### The cost premise is true, but it has a floor

Cost does fall with tenor, from 9.01% to 3.75% on BTC. It falls **more slowly
than `1/sqrt(T)`**, and increasingly so:

| underlying | 24/72h base | 168h | 336h | 720h |
|---|---|---|---|---|
| BTC | 1.00 | 1.25 | 1.55 | 2.00 |
| ETH | 1.00 | 1.36 | 1.72 | 2.28 |

(observed cost ÷ `1/sqrt(T)` prediction, anchored within each population)

The reason is structural: only the **fee** term scales with premium. The
spread term is a fixed 1.34% of premium per side and does not scale at all, so
total cost approaches a floor of `2 × 1.34% = 2.68%` no matter how long the
tenor. BTC's 720h cell is at 3.75%, already close to it. **Tenor cannot be used
to escape the spread**, which makes the recorder in §3 the binding constraint
on any future options work here.

### One post-hoc observation, recorded as a hypothesis and not a result

**All 14 cells are negative.** Short vol loses gross at every horizon on both
underlyings; the one CI excluding zero (ETH 720h) excludes it *below*. Since a
long straddle is the exact negative of a short one, that means the positive
gross sits on the **long** side — and the long side also has the better risk
shape, bounded loss against unbounded gain, where the short side produced the
−11.42 and −13.95 single-expiry outcomes recorded above.

This is stated as flatly as possible about what it is worth:

* **No single cell is significant.** The largest |t| is 1.81.
* **The cells are not independent.** Overlapping holding windows on the same
  expiries share the same underlying moves, so 14 negative signs are nowhere
  near 14 independent coin flips and the naive sign-test p-value is not valid.
* **It was not pre-registered.** This sweep was designed to test short vol.
  Reading the sign off the output and turning it into a long-vol thesis is
  precisely the move `PROGRAM_SUMMARY.md` lesson 6 warns against.
* Long vol pays the *same* friction, 3.5–9% of premium.

It is written down because the alternative — noticing it and not recording it
— is worse. It is a candidate for a pre-registered test, with a freeze written
before any long-side return is computed, and it must be labelled post-hoc in
that document. It is not a finding.

> **Retired by §6.** The independent corroboration this appeared to gain from
> the `IV − RV` estimator was microstructure noise in 5m realised vol. At
> noise-free sampling the gap changes sign, and §6 shows it is smaller than
> the irreducible spread cost in either direction. No long-vol test is
> warranted.

## 6. Why is the premium zero? — because the surface is *efficient*

The P&L tests establish that nothing is earned. They cannot distinguish two
very different reasons for it, and the distinction decides whether anything
else here is worth trying:

* the surface is **noise** — implied vol says nothing about future realised
  vol, and the flat P&L is a symptom; or
* the surface is **accurate and correctly priced** — a well-functioning
  options market, where the absence of a premium is efficiency.

`research/iv_ic.py` separates them with an information coefficient, the same
instrument that settled H-Regime-1 after thirteen P&L tests could not. Rank
correlation isolates the surface's accuracy from friction, strike selection
and the directional lottery, all of which the straddle P&L confounds.

The benchmark is deliberately hostile: not zero, but **trailing realised vol
over a matched window** — the cheapest possible forecast, and the one implied
vol has to beat to be adding anything.

| underlying | sampling | IC(IV, RV) | IC(trailing, RV) | **IC(IV given trailing)** |
|---|---|---|---|---|
| BTC | 5m | 0.796 | 0.601 | **0.610** |
| BTC | 1h | 0.680 | 0.420 | **0.544** |
| BTC | 2h | 0.622 | 0.317 | **0.529** |
| ETH | 5m | 0.741 | 0.597 | **0.543** |
| ETH | 1h | 0.590 | 0.384 | **0.483** |
| ETH | 2h | 0.519 | 0.285 | **0.449** |

**The surface is strongly informative.** IC of the incremental term is 0.45–0.61
with t ≈ 20–25, stable across sampling frequency, and implied vol beats
trailing vol by roughly two to one at every frequency. This is the second
branch: Delta India's option market forecasts volatility well, and prices it
close to fair.

That is a materially different finding from thirteen perpetual nulls. Those
said *there is no signal*. This says *there is a great deal of signal, and it
is already in the price.*

### A negative premium appeared, and it was an estimator artifact

At 5m sampling, `IV − RV` came out **−0.026 vol points on BTC (t = −4.34)** and
−0.025 on ETH (t = −2.89) — implied significantly *below* realised, a negative
variance premium. It appeared to corroborate the 14 negative signs in §5 from
a completely independent estimator.

It does not survive a sampling-frequency check. Realised vol falls
monotonically as the interval coarsens, and the gap crosses zero:

| sampling | mean RV (BTC) | IV − RV |
|---|---|---|
| 5m | 0.4414 | −0.0260 |
| 15m | 0.4363 | −0.0209 |
| 30m | 0.4266 | −0.0112 |
| 1h | 0.4206 | −0.0052 |
| 2h | 0.4103 | **+0.0051** |

That monotone decay is the signature of microstructure noise — bid-ask bounce
and stale index prints adding spurious variance at high frequency. The
"negative variance risk premium" was the 5m realised-vol estimator being
biased upward, not the market underpricing volatility.

**The post-hoc long-vol hypothesis from §5 is retired.** Its apparent
independent corroboration was an artifact of the same kind of error this
repository exists to catch, found by a check run because the result was
convenient.

### The gap is smaller than the irreducible cost, in either direction

Converting the clean (2h) gap through vega into the units the cost law uses:

| underlying | gap, vol pts | vega/premium | **edge, % of premium** | cost at 24h | long-tenor spread floor |
|---|---|---|---|---|---|
| BTC | +0.0051 | 2.518 | **1.28%** | 9.01% | 2.68% |
| ETH | +0.0117 | 1.718 | **2.01%** | 7.10% | 2.68% |

Taking the gap at face value, in whichever direction it points, and assuming
an infinitely long tenor so the fee term vanishes entirely and only the
unavoidable spread remains, the edge is **still smaller than the floor**: net
−1.40% of premium on BTC and −0.67% on ETH.

This closes the variance premium quantitatively rather than by failing to
reject. There is no gap large enough to survive the part of the cost that
cannot be engineered away.

## 7. Is the premium zero everywhere, or only on average? — a candidate that failed

A zero average is not zero everywhere, so `research/vrp_conditional.py` tests
whether the premium is state-dependent. Three conditioning variables fixed in
advance — IV level, IV minus trailing realised vol, trailing realised vol —
split into terciles, six tests, and a bar written before the run: positive on
both underlyings, bootstrap CI excluding zero on both, and net of costs.

**One state cleared it.** Sorting by IV level gave a monotone pattern on both:

| | tercile 0 | tercile 1 | tercile 2 | top − bottom |
|---|---|---|---|---|
| BTC gross | −0.0681 | −0.0462 | **+0.1108** [+0.022,+0.195] | +0.1789 [+0.018,+0.344] |
| BTC net | −0.1867 | −0.1370 | **+0.0359** | +0.2226 |
| ETH gross | −0.0992 | −0.0606 | **+0.0870** [+0.005,+0.167] | +0.1861 [+0.011,+0.372] |
| ETH net | −0.1881 | −0.1297 | **+0.0251** | +0.2132 |

Monotone, positive top tercile with a CI excluding zero, net positive after
costs, on both underlyings. This is the exact profile of H-Scalp-3's 120m cell,
and it was treated accordingly.

### It fails two of the four stress tests

**1. The tercile boundaries were fitted on the whole sample.** To act on this
you would need tomorrow's IV distribution today. Recomputing the split
causally — boundaries from an expanding window of history only, 120-expiry
warmup — the significance disappears on both:

| | in-sample split | causal split |
|---|---|---|
| BTC top − bottom | +0.1789 [+0.018, +0.344] | +0.1262 **[−0.030, +0.291]** |
| ETH top − bottom | +0.1861 [+0.011, +0.372] | +0.0737 **[−0.102, +0.257]** |

The causal buckets also come out badly unbalanced (BTC 391/263/185), which is
the diagnosis: **the IV distribution is non-stationary**, so an in-sample
tercile leaks the future into the split.

**2. It does not hold across time.** Splitting the sample in half and
tercile-ing within each:

| | first half | second half |
|---|---|---|
| BTC | +0.2636 [+0.068, +0.460] | +0.0826 **[−0.152, +0.342]** |
| ETH | +0.3617 [+0.118, +0.605] | +0.0190 **[−0.221, +0.290]** |

The whole effect lives in the first half and is gone in the second, on both
underlyings. That is the H-Scalp-3 failure verbatim: a real mechanism does not
evaporate between adjacent windows.

**3. It is not a tail artifact** — the one test it passes. Dropping the five
best expiries in the top tercile moves BTC from +0.1108 to +0.0969.

**4. Net survival depends entirely on the assumed spread.** At the measured
p75 half-spread of 2.75%, ETH's top tercile goes to **−0.0031** and BTC's to
+0.0077. Since no historical spread record exists, this margin is an
assumption, not a measurement.

### Verdict

**No conditioning state clears the bar once the bar is applied honestly.** The
apparatus caught this one the same way it caught H-Scalp-3's 120m cell, and
via defences fixed before the run rather than invented afterwards.

One thing the run exposed about the bar itself, recorded rather than quietly
fixed: "positive on both underlyings" was written assuming BTC and ETH are
close to independent evidence. §4 shows they correlate **+0.743**. The
two-underlying requirement is therefore weaker than intended, and a future
prereg on this venue should not treat BTC and ETH agreement as replication.

## 8. What this does and does not settle

**Settled.** There is no harvestable variance risk premium in ATM daily
straddles on this venue, gross of all costs, over 959 consecutive BTC expiries
and 929 ETH expiries. Selling daily ATM vol here is not a business.

This is worth stating plainly because it is the *opposite* of the equity-index
prior that motivated the test. SPX and most listed equity vol carry a
persistent, well-replicated variance premium; the outside prior said this was
the one place an options edge was likely. It is not here.

**Not settled, and deliberately out of scope of a feasibility run:**

* **Unhedged, and underpowered by exactly the margin that matters.** The
  straddle carries the variance premium *and* a directional lottery, and the
  lottery dominates. On BTC, `sd = 1.010` at `n = 959` gives a naive standard
  error of 0.0326, so detecting an effect at 80% power requires
  **|effect| > 0.091 of premium — against a 24h cost of 0.090.** The test is
  powered to see an edge large enough to trade and is blind to anything
  smaller. A true premium of 1-5% of premium is fully consistent with this
  data; §6 is what rules that range out, not this table.
* **The point estimate is tail-determined.** Dropping the single worst expiry
  moves the BTC mean from −0.0011 to **+0.0108**; dropping the five worst
  gives +0.0332. Only the interval carries information. Any reading of the
  sign of the mean is a reading of one day.
* ~~**One tenor.**~~ Now swept — see §5. Dailies through 30-day, all negative.
* **One strike.** ATM only. The wings are untested — though §3 says the cheap
  wing is closed by the fee cap regardless.
* **One entry time.** Fixed at 24h before settlement, chosen before any result
  was seen and not swept.

**A prereg written after this must state that these numbers were seen.** The
feasibility result is not a licence to test the same thing again and call the
second look pre-registered.

## 9. Reproducing

    python -m deltabt.research.validate_iv            # IV reconstruction check
    python -c "from deltabt.data.options import OptionCatalog; OptionCatalog().refresh()"
    python -m deltabt.research.vrp_feasibility --underlying BTC --out out/vrp/btc_atm_straddle.csv
    python -m deltabt.research.vrp_feasibility --underlying ETH --out out/vrp/eth_atm_straddle.csv

    python -m deltabt.research.vrp_term --out out/vrp/term_structure.csv
    python -m deltabt.research.iv_ic --out out/vrp/iv_ic.csv
    python -m deltabt.research.vrp_conditional --out out/vrp/vrp_conditional.json
    python -m deltabt.data.quote_recorder --once     # spread snapshot

Results are recorded in `out/vrp/vrp_feasibility.json` and
`out/vrp/term_structure.csv` alongside the per-expiry samples.

The catalog pull is ~145k products over ~150 pages and takes about 8 minutes;
it is incremental afterwards. The VRP sample fetches ~3 candle series per
expiry and is cached to Parquet, so a re-run is offline.
