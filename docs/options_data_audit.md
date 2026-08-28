# Delta India options data — audit for the volatility research gate

**Measured 2026-08-28 by reading `data/` directly.** Nothing here is a
hypothesis test and nothing is a verdict on a phenomenon. It answers one
question: what can honestly be measured with the options data this repository
actually holds.

`docs/options_feasibility.md` (archived at `8c3ee34`) is the prior record. This
audit re-derives the inventory independently and adds the quote dataset that
did not exist when that document was written.

---

## 1. Provenance — one venue, no third party

Every byte is **Delta Exchange India**, pulled through
`deltabt/data/client.py` against `api.india.delta.exchange`. There is **no
Deribit data, no Binance data and no vendor data anywhere in the workspace.**
The only reference to another venue is `deltabt/research/leadlag.py`, a
diagnostic module that names Binance symbols and has never been given a cached
series to read.

The venue-mismatch question the gate asks therefore does not arise: there is
nothing cross-venue to transfer. Every result would be same-venue, and every
limitation is Delta's.

---

## 2. The four options datasets

### 2.1 Live quote recorder — `data/quotes/*.parquet`

The only true bid/ask data that exists, at any point in this repository's
history.

| | |
|---|---|
| rows | **420,196** across 5 daily partitions |
| span | 2026-08-24 16:03 → 2026-08-28 19:19 UTC — **4.15 days** |
| snapshots | 400, median spacing **900 s**, zero gaps over 20 minutes |
| contracts | 1,929 unique; 13 expiries (BTC/ETH), 6 (XAUT); 213 strikes |
| underlyings | BTC, ETH, XAUT only |
| fields | `best_bid`, `best_ask`, `bid_size`, `ask_size`, `mark_price`, `spot_price`, `mark_iv`, `bid_iv`, `ask_iv`, `delta`, `gamma`, `vega`, `theta`, `oi_contracts`, `turnover_usd`, `volume` |

**Greeks and IV are published per row by the exchange.** Nothing has to be
inverted, fitted or interpolated to obtain delta or implied vol at a snapshot,
which removes the single largest leakage surface in options research.

### 2.2 Option catalog — `data/meta/options_catalog.parquet`

145,406 contracts, expiries 2020-10 → 2026-10, with strike, expiry, state,
`settlement_price`, `launch_ts`, `contract_value` and per-contract fees.

**It is stale.** Its last `launch_ts` is 2026-08-24 15:20, so **892 of the
1,929 quoted contracts are absent from it** and a naive join silently drops
**23.3%** of quote rows. Strike, right and expiry are therefore parsed from the
contract name, which is self-describing and parses at 100%.

### 2.3 Option candle cache — `data/candles/{MARK-slice, OI:}`

30,765 directories that *look* like a deep options history and are not.

* 8,940 hold a `mark_5m` series of **exactly 25 bars** — a fixed ~2 h window
* 21,825 hold an `OI:` hourly series of **exactly 3 bars**

These are the slices the archived VRP run fetched around each entry instant.
Delta serves option history back to 2024-01 through the API; **this repository
does not hold it**, and the standing scope rule forbids fetching it.

### 2.4 Derived historical sample — `out/vrp/trades/*.csv`

The most valuable historical options asset here, and the only one spanning
years.

| | |
|---|---|
| BTC | **959** daily ATM straddles, 2024-01-06 → 2026-08-24 |
| ETH | **929** daily ATM straddles, 2024-02-07 → 2026-08-24 |
| per row | entry premium, `settlement_price` payoff, `atm_iv`, `rv_trailing`, `rv_forward`, moneyness, per-contract fee |

Validated during this audit: `rv_forward[i] == rv_trailing[i+1]` at **99.69%**
(BTC) and **99.78%** (ETH), so the forward measure is a genuine one-expiry
shift and not a duplicated column. `corr(IV, RV_forward)` = +0.717 Pearson /
+0.796 Spearman on BTC, reproducing the archived information-coefficient result
exactly.

**It is MARK-priced.** Entry is the exchange's own mark series. There is no
bid, no ask, no spread and no size column anywhere in it.

---

## 3. Quality metrics — the quote dataset

```
total rows                          420,196
two-sided quotable rows             321,006    76.394%
invalid rows                         99,190    23.606%
stale rows (bid,ask,mark unchanged)   2,537     0.604%
duplicate (symbol, snapshot_ts)           0     0.000%
```

Failure modes, each measured rather than assumed:

| flag | rows | share |
|---|---:|---:|
| missing expiry / strike via catalog join | 97,925 | 23.305% |
| zero 24 h volume | 52,292 | 12.445% |
| zero open interest | 30,909 | 7.356% |
| already expired at snapshot | 1,136 | 0.270% |
| missing / zero best_bid | 359 | 0.085% |
| missing / zero best_ask | 132 | 0.031% |
| missing / zero bid_size or ask_size | 405 | 0.096% |
| crossed (bid > ask) | 108 | 0.026% |
| locked (bid == ask) | 7 | 0.002% |
| negative price, missing mark, missing spot, missing IV, missing greek, impossible IV, \|delta\|>1, negative gamma/vega | **0** | **0.000%** |

The 23.3% "invalid" figure is dominated entirely by the stale catalog join and
disappears once symbols are parsed from their names. Genuine market defects —
crossed, locked, one-sided, expired — total **under 0.5%**.

**No observation was discarded silently.** Every filter above is a named,
counted exclusion.

### Spread, which is the whole cost story

Median half-spread of mid, two-sided rows only:

| underlying | p25 | median | p75 | p95 |
|---|---:|---:|---:|---:|
| BTC | 0.54% | **0.77%** | 2.15% | 31.03% |
| ETH | 0.60% | **0.84%** | 2.68% | 33.33% |
| XAUT | 2.06% | **2.76%** | 4.88% | 14.29% |

And by how cheap the option is — the finding that closes the wings:

| premium / spot | median half-spread | median bid size | median OI |
|---|---:|---:|---:|
| ≤ 0.1% | **17.65%** | 5,763 | 43,867 |
| 0.1–0.2% | 3.20% | 6,474 | 12,017 |
| 0.2–0.5% | 2.20% | 6,443 | 9,364 |
| 0.5–1% | 1.33% | 6,444 | 6,617 |
| 1–5% | 0.79% | 6,344 | 2,197 |
| > 5% | 0.57% | 5,735 | 451 |

An option priced at a tenth of a percent of spot pays a **17.65% half-spread**.
Independently measured here, this reproduces the archived conclusion that the
cheap far-OTM wing is uninvestable before any model is applied.

---

## 4. Surface coverage — rich in cross-section

Formation rate of a causal surface point (ATM by published delta, plus a
25-delta put and call within a 0.05 tolerance), per snapshot:

| underlying | 0–2d | 2–7d | 7–21d | 21–60d | 60d+ |
|---|---:|---:|---:|---:|---:|
| BTC | 95.0% | 100% | 100% | 100% | 100% |
| ETH | 85.5% | 100% | 100% | 100% | 100% |
| XAUT | 98.2% | 48.0% | 0% | 0% | 0% |

25-delta skew is formable in **96.8–100%** of the snapshots where an ATM point
exists. BTC has a mean of **4.95 of 5 tenor bands simultaneously live**; ETH
4.86. ATM half-spreads run 0.34–1.04% by band.

This is a genuinely good surface. It is enough to compute term structure and
skew causally, today, without fitting anything.

---

## 5. Temporal coverage — the binding constraint

```
earliest quote timestamp   2026-08-24 16:03 UTC
latest quote timestamp     2026-08-28 19:19 UTC
continuous                 yes, no gap over 20 minutes
calendar days              5
non-overlapping 24h obs    4
non-overlapping 7d obs     0
```

**For any hypothesis with a horizon of a day or more the independent sample is
about four observations, not 399.** Fifteen-minute snapshots of the same
surface are not independent draws of a weekly volatility outcome.

The two datasets never overlap usefully:

* the **long** series (2024-01 → 2026-08, 959 expiries) has no bid, no ask, no size — it can measure a phenomenon and cannot price an execution
* the **executable** series (4.15 days) has everything an execution needs and no history to measure a phenomenon in

### The hedge-instrument gap

```
BTCUSD / ETHUSD 1-minute series ends   2026-08-12 10:52 UTC
option quote recorder starts           2026-08-24 16:03 UTC
```

**Zero overlap.** Daily perpetual bars do overlap, but a delta hedge sampled at
15 minutes cannot be executed on a daily bar. Any delta-hedged study needs the
perpetual minute series restarted alongside the option recorder; nothing is
currently recording it.

### Accrual

The recorder (`deltabt.data.quote_recorder --interval 900`, PID 160539, running
since 2026-08-24) accrues **101,513 rows/day, 10.0 MB/day**.

| history | complete on | non-overlapping 7d observations | size |
|---|---|---:|---:|
| 3 months | 2026-11-24 | 13 | 0.9 GB |
| 6 months | 2027-02-24 | 26 | 1.8 GB |
| 12 months | 2027-08-24 | 52 | 3.6 GB |
| 24 months | 2028-08-24 | 104 | 7.3 GB |

---

## 6. Fragility worth recording

`.gitignore` excludes `/data/` and `out/**/trades_*.csv`. **The quote
partitions, the option catalog and the 959-expiry derived sample are all
untracked and exist only on this machine's disk.** The archive commit made
exactly this argument about `pin.py`, which was lost to a `git clean` while
untracked. The recorder's output has no backup and no redundancy, and a gap it
leaves cannot be backfilled from any endpoint.

---

## 7. One incomplete hypothesis found

`out/vrp/pin_btc.csv` holds a **232-expiry** sample (2024-01-13 → 2026-08-20)
with `spot_at_obs`, `settle`, `max_pain`, `total_oi`, `pull` and `move` —
H-Pin-1, a max-pain settlement study built on the undocumented `OI:` history.
Its driver was deliberately not reconstructed when the programme closed, and
there is **no entry in `out/experiments.jsonl` and no recorded verdict.** It is
an open question with a cached sample, listed here so it is not lost twice.
