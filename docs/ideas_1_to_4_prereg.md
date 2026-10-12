# Pre-registration — ideas 1–4 of the 2026-10-13 list (owner: "run 1–5 right now")

Written and committed before any of the four ran. Idea 5 (a trade-flow recorder) is a deploy and
is handled separately. All four are local, ₹0, on `manual_scalp_both_t3` (the %R entry, 4×ATR stop,
3R / 72h) unless an idea changes exactly one of those parts.

## Shared machinery

- **Two windows.** DEV = `data/` (2025-12-20..2026-09-30, 32 coins) — every rule so far was read from
  it. OLD = `data_old/` (2024-06-01..2025-12-19, the 27 of those coins that existed; survivor-biased
  and stated as such). OLD has been touched only by the cheap-trade gate (closed 10-12).
- **Costs.** Per-coin slippage = max(2 bps, half the 2026-10-05 quoted spread); taker 0.05 %, maker
  0.02 %, both ×1.18 GST; funding charged. The 0.15R cost gate stays on in every arm so the entry set
  is comparable.
- **Noise.** Any comparison of two runs is unpaired, and unpaired runs here swing by more than most
  effects. The test is a **day-clustered bootstrap** of the difference in net R per trade (2,000
  draws of entry-days with replacement, both runs resampled on the same days). p = share of draws
  where the difference is ≤ 0.
- **Exits.** hold-to-3R is judged; the trail is reported.
- **One run per idea.** A bug found after a run: fix, log it here, rerun, report both.

## Idea 1 — limit-order entries

Engine change (`StrategyParams.entry_limit_atr`, `entry_limit_wait_bars`; `None` = today's market
entry): at a signal, rest a limit at `close ∓ k·ATR` (k = 0 means at the close); it fills only when a
later 1m bar trades **through** it (strictly beyond, a touch is not a fill), at the limit, maker fee,
no slippage; cancelled after 3 primary bars (15 min) unfilled; a new signal while one rests replaces
it. A position filled inside bar *i* is first checked for exits at bar *i+1* (same as a market entry
at bar *i*'s close) — a stop or target hit later in the fill bar is missed; stated. Stop price is the
one the signal computed; R and target are measured from the fill.

Judged cell: 32 coins, hold-to-3R, k = 0, wait 3. L1: limit beats market on DEV, p ≤ 0.05. L2: the
same on OLD. **PASS = L1 and L2.** Reported: fill rate, signals per month, k = 0.25, the trail,
prod 3 coins. Expectation: fees fall ~0.025R per trade; unfilled signals are biased toward the
winners (price ran without dipping), so the net is unclear.

## Idea 2 — stop on price structure (`leg_extreme`: the swing low/high of the current leg)

One change: `stop = "leg_extreme"` instead of 4×ATR. Median stop width on BEATUSD: 2.54 % vs 4.41 %.
Control: an ATR stop with its multiplier set so the pooled median width on DEV matches leg_extreme's
(a stop of the same size placed without structure). Judged on OLD only (DEV is where it would be
chosen): S1 leg_extreme beats 4×ATR, p ≤ 0.05; S2 leg_extreme beats the width-matched ATR stop,
p ≤ 0.05. **PASS = S1 and S2.** DEV is reported.

## Idea 3 — simple splits

Three one-line rules on the base trade list: **side** (long vs short); **weekday** (the best single UTC
weekday on DEV, ≥ 200 trades); **hour block** (best of four 6-hour UTC blocks on DEV, ≥ 200 trades).
The subgroup is chosen on DEV. On OLD it must have net R > 0 **and** beat its complement at
day-clustered p ≤ 0.05. **PASS per split = both.** The ML already saw hour and side and found nothing,
so the prior is that all three fail.

## Idea 4 — forced selling reverses (open interest)

New entry, not a filter. Data: 1h bars from the 1m LTP cache (both windows), plus Delta's `OI:<coin>`
1h history 2024-06-01..2026-10-01 for the 32 coins (one fetch, ~200 read-only requests, gitignored).
**Event** at hour *t* on coin *c*: |1h log return| in the top 0.5 % of *c*'s trailing 90-day
distribution **and** the 1h log change in OI in the bottom 1 % of its trailing 90 days (forced), or
the same move with OI change above its trailing median (ordinary). A coin counts only in hours
where its trailing 30-day median daily dollar volume (from the candles) ≥ $1M. Two events on one coin
within 24h: keep the first.

**Step 0 (before any outcome is read):** forced events on ≥ 60 distinct days in DEV and ≥ 60 in OLD,
else STOP (the test cannot answer).

Outcome: the fade return over the next 24h (4h reported) in units of trailing 24h vol, entered at the
next hour's open and exited at +24h, both taker, 0.158 % round trip. Statistics clustered by day.
Judged on DEV: F1 forced fade > 0, day-clustered t ≥ 2.5; F2 forced beats ordinary, t ≥ 2; F3 positive
after costs; F4 same sign in both halves. Then OLD: F5 fade > 0 after costs, t ≥ 2. **PASS = F1–F5.**

### Idea 4 implementation details, fixed 2026-10-13 before its run
Trailing thresholds need ≥ 30 days of a coin's history (`min_periods` 720 hours); the liquidity median
needs ≥ 10 days; the trailing daily vol uses ≥ 20 of the previous 24 hourly returns. The 24h exit is the
close of the hour that ends 24h after entry (4h likewise). The OI 1h fetch: `scripts/fetch_oi_1h.py`,
manifest in `out/sweep/five_min_arm_lab/forced_selling/`.

## RESULTS 2026-10-13 (`out/sweep/five_min_arm_lab/ideas_2026-10-13/`) — all four closed or unanswerable

| idea | DEV (2025-12..2026-09) | OLD (2024-06..2025-12) | verdict |
|---|---|---|---|
| 1 limit entry at the close | −0.134R vs −0.094R market (p 0.98); fill 88%; fee $1.10 → $0.48 a trade, win rate 26.4% → 24.7% | −0.097R vs −0.076R (p 0.90) | **FAIL** — fees halve, but the fills are the trades that went against you (adverse selection) |
| 2 leg_extreme stop | −0.154R vs −0.094R (4×ATR), −0.077R (width-matched ATR) | −0.087R vs −0.076R / −0.047R (p 0.66 / 0.94) | **FAIL** — structure adds nothing over a same-size ATR stop |
| 3 splits (long / Thursday / 12–18 UTC) | chosen as the best groups | long −0.095R, Thu −0.036R, 12–18 UTC −0.084R; none > 0 | **FAIL** all three |
| 4 forced-selling reversal | step 0: 37 forced-event days (< 60) | 61 | **CANNOT ANSWER** — stopped before outcomes were read; not loosened after the fact |

Reported only: prod 3 coins, limit entry +0.051R vs +0.002R market (DEV only; those coins did not exist in OLD).

## Idea 6 (added 2026-10-13, owner: "consider all coins") — trade a coin only after a win on it

The one real pattern found so far: a coin's next trade does better after a win than after a loss
(prod coins 36.7 % vs 24.9 % wins; 32 coins 29.9 % vs 25.4 %, hold-to-3R). The pause tried to use it
through the engine and failed. This is the direct form: per coin, money goes on a signal only when
the coin's **last closed trade was a win**; after a loss the bot follows the coin's signals **on
paper** until a paper trade wins, then goes real again. Every signal is still followed, so the
virtual bot holds exactly the slots the real one would — the base trade list is labelled, no re-run.

- Label at ENTRY from trades on that coin CLOSED before it. A coin's first trade is "real".
- Judged on hold-to-3R, 32 coins DEV / 27 coins OLD. **W1** real beats virtual on DEV, day-clustered
  p ≤ 0.05. **W2** the same on OLD. **W3** real net R per trade > 0 on OLD. **PASS = W1, W2, W3.**
- Reported: share of trades that are real, the trail, prod 3 (DEV), after-win / after-loss tables.

**Idea 6 result 2026-10-13** (`out/sweep/five_min_arm_lab/ideas_2026-10-13/idea6_win_follow_result.txt`): **FAIL**
— W1 PASS (DEV real +0.026R vs virtual −0.138R, p 0.007), W2 fail (OLD −0.020R vs −0.097R, p 0.056),
W3 fail (OLD real net −0.020R). The direction held on the untouched window (after a win beats after a
loss by ~0.08R in both), which no other rule has done; the size is "lose less", not profit. Real mode
takes 27 % of signals. Prod 3 coins, DEV only: real +0.381R on 122 trades (reported). On the trail the
pattern is absent (p 0.72 / 0.36).

## Idea 7 (added 2026-10-13, owner) — the same entry on 1h and 4h bars, with and without the after-a-win rule

`manual_scalp_both_t3` at a **60m primary (12m confirmation)** and a **240m primary (48m)**, 4×ATR stop,
3R target, hold caps 10 and 30 days, per-coin spread slippage, the 0.15 cost gate. Smoke test before
the run (BTCUSD, DEV): median stop 1.3 % at 5m → 2.3 % at 1h → 4.6 % at 4h, so fee drag roughly halves
and quarters — not the 10× first guessed. **Thin coins mostly drop out** (the completeness rule refuses
bars with missing minutes: BEATUSD keeps 13 signals at 1h, none at 4h); the test is mainly on liquid coins.
Arms: every signal; money only after a win on the coin (idea 6 labelling). Both windows, all screen coins.
Judged on hold-to-3R per cell (timeframe × arm): **PASS = net R > 0 in both windows and pooled
day-clustered t ≥ 2.5**; fewer than 100 trades in a window = cannot answer. The trail is reported.
