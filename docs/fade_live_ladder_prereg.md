# Fade real-money run 3 — rule A ladder, $1,500 — rules fixed before any order

Owner, 2026-11-09: "Let's run rule a ladder in paper and in real"; "Plan 10/5/3 @20/35, cap 45% is fine by me";
"I can add funds if we hit 900 bottom"; then "approved on 1-6" (refill at $1,000 with the high-water mark reset,
30% cap, market stop, a $3,000 refill budget, the code fixes, judging on per-trade R).
Supersedes `docs/fade_live_prereg.md` (run 1, $176 at 3%) and `docs/fade_live_quick_prereg.md` (run 2, never run).
Code: `live/fade_live.py` (real), `live/fade.py` account `LADDER` (paper, same rules).

## What the evidence says (recorded so the review cannot rewrite it)
Rule A has no proven edge (CONTEXT.md VERDICT 2026-11-09). Pooled over 4,636 trades it is +0.016R (z 1.57);
without Delta 2026, the period it was developed on, +0.008R (z 0.76). Three independent reviews (market, quant,
trader) advised against 10% risk; growth-optimal sizing is 1.5–5%. The owner chose this run knowing that. With the
rules below, the eight historical years (Binance 2021–26, Delta 2025–26) ended stopped at the floor in five without
refills; with refills the expected cost is about $1,000 a year of deposits, more in a bad year.

## The rules
1. **Picks:** the paper runner's rule A picks (top 2 previous-UTC-day gainers, Delta perpetuals, turnover
   ≥ $250k), entered once paper has entered them, within 30 minutes of 00:00 UTC; a pick not entered by then is
   recorded as skipped.
2. **Order:** sell at 1x leverage (read back), immediate-or-cancel limit 1% under the bid, with an exchange-held
   **market** stop (mark-triggered) at entry × (1 + 2 × ATR14%). No take-profit. Bought back reduce-only 24 h later.
   Skipped when: the stop is too far for 1x (liquidation not 3× the stop distance away, ≈ 32.5%), the spread is
   wider than 1% of the bid, the mark is at or past the stop, two positions are open, the coin is still open
   from yesterday, or less than one contract.
3. **Size:** risk per trade = 10% of the wallet balance; 5% from 20% under the high-water mark; 3% from 35%
   under; back to 10% only at a new high. Each position's notional ≤ **30%** of the balance.
4. **High-water mark:** the best accepted wallet balance of the run. A refill resets it to the new balance; any
   other deposit or withdrawal moves it by the same dollars (never below the balance).
5. **Stop and refill:** under **$1,000** at an entry check the run STOPS (no entries; open positions keep their
   stop and 24 h exit). It restarts only when the owner refills the sub-account to at least $1,400 (target
   $1,500) and runs `python -m live fade-live-capital` with the account flat. Refills are capped at **$3,000**
   for the run; past that the run is over.
6. **Halts** (entries stop until the owner looks; `fade-live-resume`): a position the bot has no record of; a
   fill without a resting stop covering the whole position within 30 s (bought back at once); an order whose
   outcome cannot be established; an exit that could not complete; a close at Delta that is neither the stop
   nor the bot's own buy-back; 10 failed passes in a row; a wallet move no trade explains (> max($10, 2%) on two
   looks 5 min apart) — record it with `fade-live-capital`.
7. **Deploys and restarts** never strand a position: with prod keys the account always reconciles and buys back
   at 24 h, also with entries switched off (exits-only), and the wallet band ($1,000–$1,875) is checked only
   when a run begins.
8. **Paper twin:** paper account `LADDER` runs rules 1–5 on the same picks with $1,500, refilling at once (no
   owner delay) and stopping when $3,000 of refills is spent.

## Before switching on (in order, each with the owner's yes)
1. Merge with `dry_run "1"` — the deployed account becomes exits-only, so run 1's open positions are still bought
   back. Top up the sub-account to $1,500.
2. `python -m live fade-live-probe` — one contract through the exact order shape; PASS needs the market stop seen
   resting and covering the position, and the account flat afterwards.
3. A second PR sets `dry_run "0"`. The first pass begins run 3 (wallet must read $1,000–$1,875).

## Review — 2026-12-10, and every month after
Judged on **per-trade R**, pooled over every closed real trade of run 3 (and, separately, the paper LADDER
account's), never on one month's balance (a month of zero-edge trades ends above $1,800 one time in five).
- About 60 trades a month: SE ≈ 0.087R. A month is **uninformative** unless its mean is beyond ±0.175R.
- **Continue** while the pooled mean R is above −0.05R and refills stay within budget.
- **Stop for good** if, after 120 or more trades, the pooled mean R is below 0 with z ≤ −1.5, or the refill
  budget is spent.
- **Evidence of an edge** only at n ≈ 400 trades with z ≥ 2 (≈ +0.07R). Until then no increase in size or
  budget.
- Also reported each review: real vs paper entry slippage in R, spreads (`detail.spread_bps`), stops filled
  beyond 1.5R, skips by reason, refills and halts.

## Validation done before the first order
`scripts/fade_live_sim.py` runs this module's code against a simulated Delta through every calendar day of Delta
2025 and 2026, plus a stress year (2025 with 0.10R taken off every trade, which spends the refill budget)
(`out/gainer_fade/live_sim.txt`, 26/26 checks pass, exit code 1 on any failure). Against a reference written
from these rules with the numbers typed in (not imported from the bot), every day-end balance agrees within
0.005% (2025), 0.021% (2026) and 0.005% (stress), with identical trades, ladder steps and refills; the stress
year's sixth refill is refused at $3,199. With a restart every day and 3% of venue reads failing, results agree
with the clean run to 0.001%. Controls: setting the bot's ladder to 15%/30%, its cap to 45% or its floor to
$900 each fails the checks. Two independent reviews (code; simulation and tests) were done before the commit;
their findings are fixed and each has a test in `tests/live/test_fade_live.py`.

Known limits of the simulation: entries fill at the bid (real fills may be up to 1% worse), prices are
normalised (whole-contract rounding and coarse ticks are not exercised), stops fire at a fixed hour at the
recorded price, venue writes never fail in it (ambiguous and rejected orders are unit-tested instead), and the
shape of Delta's stop rows (size field or not) is checked only by the live probe.

## What "10% risk" means in practice
The 30% position cap binds whenever the stop is under 33% away, which is almost always: in the simulation the
risk actually taken averaged **5.0% (2025) and 4.5% (2026)** of the balance at the "10%" step, maximum 9.7%.
