# Spike Sell (real-money run 4) — rules fixed before any order (2026-11-16: 20% margin, plain 5% trail, no month-off)

**Name (owner 2026-11-15): Spike Sell.** "Run 4" stays its run number in the bot's database.

Owner, 2026-11-15: "we want to deploy this version to prod, decommission rule A"; hard stop **"Month: 2 stops only"**;
paper accounts **kept running**; **"approved"** for: stop triggered on the **last traded price**, **extra isolated
margin after the fill**, **every stop's fill logged against its trigger**. Then, after the careful simulation and the
margin sweep: **"I suggest we deploy with 10% margin"** and, of the three tested exits, **"C approved"** (break-even,
then a 5% trail). Supersedes run 3 (`docs/fade_live_ladder_prereg.md`, rule A ladder) for the real account. Code:
`live/spike_live.py` (the rules), on run 3's machinery in `live/fade_live.py`. Paper accounts unchanged.

## What the evidence says (recorded so the review cannot rewrite it)
All on Delta's OWN 1m prices, 48 h per pick, 1,049 picks 2025-01-15..2026-11-14 (`docs/careful_sim_spec.md`,
`scripts/careful_dataset.py`), every number from two independently written implementations that agree on every trade
(`scripts/careful_be_trail.py`, `scripts/careful_margin.py`, `scripts/careful_margin_sweep.py`). STRICT = a triggered
stop fills at the worse of its price and that minute's close (+0.1%); LENIENT = at its price (+0.1%). Real fills land
between.

| these rules, from $3,184 (first fill wins, month off after 2 full stops, $1,000 floor) | STRICT | LENIENT |
|---|---|---|
| full history 2025-01-15..2026-11-14, end balance | **$1,801** | **$3,890** |
| deepest drop on the way | 67% | 53% |
| 2026 from Jan 1, end balance | $3,784 | $6,071 |
| 20 real picks 2026-10-29..11-14, end balance (4 winners of 12 trades, 1 full stop) | $4,055 | $4,303 |
| 22 monthly starts (each to 2026-11-14): ended under $1,000 | 0 | 0 |
| 22 monthly starts: ended above $3,184 | 11 | 22 |
| 22 monthly starts: median / worst end | $3,177 / $1,801 | $5,345 / $3,210 |

Per trade the exit is about break-even: at a fixed size the same 452 trades total −$17,470 (strict) .. +$16,585
(lenient) at 49% margin (`out/careful/be_trail.txt`), i.e. the result hangs on how the break-even stops fill. About
12% of trades win (average ~10x the average loss), the ten largest winners outweigh the whole result, and the months
are very uneven. No margin from 5% to 49% makes any tested exit profitable on strict fills
(`out/careful/margin_sweep.txt`); 10% is about the largest that never reached the floor in any start (15%: 5 .. 1 of
22 did). The underlying edge is slow (the short gains ~+1.4% of the position over 24 h, 0% over 15 min;
`out/careful/drift.txt`) and a 20x position cannot hold that long. **The history says roughly break-even with deep
drops; the three recent weeks were a good stretch. The owner chose to run it knowing this.**

## The rules
**Owner change, 2026-11-17 ("approved on C") -- RULE C, in force from this date; it overrides rules 2, 3 and 7 below
where they differ.** The first live trade (CROSS, 2026-11-17) filled at 0.154 at 05:02:51 and was stopped at the 2.25%
stop 27 s later, during the same spike; CROSS then fell below the entry. In the history 405 of run 4's 447 losers did
the same. Rule C, chosen from `scripts/spike_fewer_losers.py` and checked in `scripts/spike_verify.py`
(`out/careful/spike_verify.txt`):
- **Stop +10%** from the limit (was +2.25%).
- **Leverage at most 5x** (was 20x): 20% margin is a position of ~1x the balance; a full stop costs ~10% of it;
  liquidation ~+17.5% before and ~+27% after the unchanged extra margin (25/49 of the trade margin).
- **Trail 10% above the lowest 1m low since the fill, only once that level is at or below the entry** (first after a
  ~9.1% fall), never up; bought back at market if the price is already above the new level (was: 5% from the fill).
- Everything else unchanged: picks, the 5% spike entry, one position at a time, 24 h after the fill, no month-off rule,
  the $1,000 floor.
Evidence, 617 trades 2025-01..2026-11, two implementations agreeing on every trade, before fees: **win 59%, average
win +8.1%, average loss -9.2% of the position, +1.07% per trade STRICT (+1.50% LENIENT)**; positive in all four
half-years (+0.75 / +0.65 / +1.81 / +1.08%), 15 of 23 months up, +0.77% without the 5 best days; a random-minute short
with the same exit -0.09%. At a fixed $3,184 with a stop costing 9%: win +$247, loss -$270, **+$43 a trade** (+$41 with
real fees). Compounding from $3,184 the STRICT account's lowest point was **$897 (worst fall 89%)** before it ended far
higher. Known limits, accepted by the owner: average loss larger than average win; ~1x position, not high leverage;
chosen from ~70 variants, so expect less. The real bot code replayed minute by minute (`scripts/replay_bot.py`,
2026-08..11) made the same 68 trades as the reference, to the cent.

1. **Picks:** the paper runner's rule A picks (top 2 previous-UTC-day gainers), within 30 minutes of 00:00 UTC; a pick
   whose 2 × ATR14% stop would be more than 32.5% away is skipped (the universe every test used).
2. **Entry:** a resting **post-only GTC limit sell 5% above the last traded price** at entry time, with an
   exchange-held **stop +2.25% from the limit, triggered on the last traded price** (market stop). **No take-profit
   order.** Skipped if the bid is already at the limit. Not filled by **24:00 UTC** → cancelled ("no spike").
**Owner change, 2026-11-16 ("We'll use 20% margin", after being shown the per-trade and account numbers at 5, 10, 15
and 20%): rule 3 is now 20% of the balance and rule 5's extra margin 25/49 of that (10.2% of the balance).** About
$12,400 of position on $3,184; a full stop about −$335, a liquidation about −$960. With the plain 5% trail and no
month-off rule, from $3,184, strict .. lenient: the full history reaches the $1,000 floor in April 2025 ($985 ..
$951); started 2026-01-01 it also ends at the floor ($961 .. $987); 19 .. 16 of 22 monthly starts end under $1,000.
Per trade at a fixed $3,184: win 29–30%, average win +$717 .. +$706, average loss −$305 .. −$277, longest losing run
11 trades. `scripts/replay_bot.py` (the real bot code) equals the research rule on every trade at 20%: full history
51 / 60 trades, from 2026-01 65 / 81 trades, same wallets.

3. *(10% until 2026-11-16)* **Size:** **10% of the wallet balance** as isolated margin per resting order (two may rest), at the most leverage
   **up to 20x** Delta allows for that notional on that coin (`initial_margin`, `max_leverage_notional`,
   `initial_margin_scaling_factor`, `position_size_limit` from /v2/products), leverage set and read back. About
   $6,200 of position on $3,184; a full stop costs about $177.
4. **One position at a time:** the first pick to fill is the trade; the other pick's resting order is cancelled at once.
   No new resting orders while a position is open. A partial fill keeps what filled; the rest is cancelled.
5. **After the fill:** the stop must cover the position within 30 s (else bought back and HALT); isolated margin is
   checked (else bought back and HALT) — both run 3's rules; then **5.1% of the balance is added as isolated margin**
   (POST /v2/positions/change_margin; 25% × 10/49, the tested liquidation distance), moving liquidation from about
   +2.5% to about +5%. A top-up whose reply is lost is read back, never resent. **Option A:** while an entry rests, a
   watcher checks it every 2.5 s (`SpikeLive.watch`), so the extra margin lands within seconds of the fill; the
   watcher and the 30 s pass share one lock, and a top-up is attempted once per position (refused or unclear: logged).
**Owner change, 2026-11-16 ("I would change lock to trailing SL" → tested → "1 approved"): rule 6 is REMOVED and
rule 7 starts at the fill.** The stop stays at +2.25% and trails 5% above the lowest 1m low since the fill as soon as
that is lower than the stop it has (the first move needs a fall of about 2.6%). `BREAKEVEN_LOCK = False` in
`live/spike_live.py`; `True` restores rule 6. Tested as "timed trail 0m / 5.00%" in `scripts/careful_timed_trail.py`
(two implementations equal on every trade; a timed start made no difference; 2.25% and 3% trails were worse).
With this exit and NO month-off rule (rule 9), from $3,184, strict .. lenient: 5% margin $979 .. $4,708; **10% margin
the $1,000 floor ($948 .. $994, reached June–August 2025; started 2026-01-01: $5,046 .. $17,595; 10 .. 7 of 22
monthly starts end under $1,000)**; 15% and 20% margin the floor by April–May 2025 (20%: 19 .. 16 of 22 starts under
$1,000). `scripts/replay_bot.py` (the real bot code against history) equals the research rule on all 93 / 131 trades
of the full history; from 2026-01, 216 of 217 (the one difference is position size above a leverage tier: the bot
uses whole-number leverage, 19x, where the research formula uses 19.9x).

6. *(removed 2026-11-16, kept for the record)* **Break-even:** once a **1m low since the fill reaches the price where the trade nets +$30.61 × balance / $3,184**
   after costs (maker 0.02% + 0.05% slippage in, taker 0.05% out; about a 0.6% fall), the stop is **moved to
   break-even** — the price netting $0 after those costs (PUT /v2/orders, read back). If the last price is already at
   or above that level, the bot **buys back at once** ("LOCK"). The move happens on the bot's next pass (30 s).
   ($30.61 = the tested +$150 on a 49%-margin position × 10/49: the same price fall.)
7. **The trail:** from then on the stop trails **5% above the lowest 1m low since the fill**, only when that is lower
   than the stop it has, and never up; if the last price is already at or above the new level the bot buys back at
   once ("LOCK"). Moved by the bot (PUT /v2/orders, read back), not Delta's native trailing stop (a native trail would
   start 5% above the current price — far looser than break-even).
8. **Exits:** the stop at Delta; **24 h after the fill** the bot buys back ("TIME"). TIME, LOCK and LIQUIDATION (a
   jump past the stop: a tested risk, counted as a full stop) are planned; anything else unexplained HALTs, as run 3.
9. **Hard stop per month: REMOVED by the owner, 2026-11-16** ("remove that setting"; confirmed "Remove it entirely"
   after being shown the test). Until then: after 2 full stops (STOP_LOSS or LIQUIDATION while the stop was still the
   original one) in a calendar month (UTC), no new entries until the 1st — **every number in the table above is WITH
   that rule.** Without it, on the same history from $3,184 at 10% margin (`out/careful/timed_trail.txt` and the
   month-off check recorded in CONTEXT.md): these rules end at **$955 (strict) .. $4,304 (lenient), deepest drop
   70% .. 59%**; with a plain 5% trail in place of the break-even step they end at the **$1,000 floor ($948 .. $994)
   during 2025**. Full stops are still counted and logged; `MONTH_STOP_LIMIT = N` in `live/spike_live.py` restores
   the rule.
10. **Kept from run 3:** the $1,000 floor (STOPPED until a refill, `fade-live-capital`), the $3,000 refill budget, HALT
    on anything unexplained, the capital check, exits-only without the switch, the kill switch.
11. **Start:** Spike Sell begins on the funded wallet ($3,184.13 on 2026-11-15). The host's `DELTABOT_EQUITY` still
    says 1500 (run 3); Spike Sell uses `RUN_START_USD = 3184` for the new-run wallet check ($1,000..$3,980), so no
    host change is needed. The dollar levels scale with the balance (× balance / $3,184).
12. **Logged:** every stop fill against its trigger (`STOP_FILL`: trigger, fill, slip %), every break-even move
    (`LOCK_MOVED` / `LOCK_AT_MARKET` / `LOCK_FAILED`), every trail move (`TRAIL_MOVED` / `TRAIL_AT_MARKET` /
    `TRAIL_FAILED`), every margin top-up (`EXTRA_MARGIN*`).

## When Delta or the network misbehaves (fixed 2026-11-15 after four code-review passes; each has a test)
These are not trading rules; they are what the bot does so that the rules above hold when a call fails.
* **Both picks fill before a cancel lands:** the first fill is the day's trade; the other is recorded and **bought back
  at once** ("DOUBLE_FILL", planned, no halt), on that pass or any later one, and never gets extra margin. The other
  pick's order is cancelled before the fill is even checked, so this needs both to fill within moments.
* **The day's first fill is final:** once a pick has filled, the other pick's order is cancelled even after the trade
  has closed (one trade a day, as tested).
* **An entry order the bot cannot find** is looked for three ways — by its client id, among the coin's open orders,
  and as a position on the coin. Found resting → tracked; a short held → recorded as the fill; found nowhere for
  2 minutes → written off (a just-sent order) or cancelled by the id from the send and written off (one seen resting).
  It is never assumed absent on one failed lookup, and never left resting unwatched.
* **A lost reply on the entry order** HALTs the account (the owner looks, `fade-live-resume`); while halted the bot
  finds the order and cancels it.
* **Resting sells are cancelled** whenever the account may not enter: HALTED, STOPPED, the kill switch, entries off.
  A fill that lands first is still recorded, protected and managed.
* **A fill is recorded at Delta's own fill time**, so a stop that fires seconds after the fill — before the bot has
  seen it — is still booked as that position's stop (and counts toward the month's two).
* **Partial fill:** the rest is cancelled and the final size read again before the position is recorded.
* **Extra margin:** only after the isolated-margin check has PASSED; a failed read before the top-up costs nothing
  and the next look (2.5 s watcher or 30 s pass) tries again; refused or unclear once → logged, never resent.
* **Break-even and trail moves** are recorded only once the stop reads back at the new price; any failure leaves the
  row as it was and the next pass tries again. With no last price the stop is still moved; the bot never buys back
  blind. A stop that fires during a break-even buy-back stays a full stop.
* **A cancel Delta refuses because the order is already gone** is an answer, not an error; any other refusal is a
  failed pass (counted, halts after 10).
* **Only this run's rows** get the extra margin, the moves and the 24-h-after-the-fill exit, and count toward the
  month's stops (run 3's stop of 2026-11-11 does not). `fade-live-capital` works on this run; it refuses while a
  sell rests.
* **The probe** fails (not passes) if nothing sold or the position is not on isolated margin.

## How the bot itself was tested (not only the rule)
`scripts/replay_bot.py` runs the REAL bot code (`live/spike_live.py` on `live/fade_live.py` and PostgreSQL) minute by
minute against a simulated Delta driven by Delta's own historical 1m prices, and compares every trade with the
research rule; `scripts/replay_battery.py` is the battery (results: `out/careful/replay_battery.txt`).
On the final code (2026-11-15):
* **Clean runs** (full history and 22 monthly starts, strict and lenient; 44 runs, 268,044 bot passes): 4,458 bot
  trades, **4,441 identical** to the research rule (coin, fill minute, exit minute, exit kind, P&L); the full history
  matches trade for trade: **$1,801.05 (strict) .. $3,889.83 (lenient)**. The 17 others are two trades on coins whose
  20x is capped at $10,000 of position: the bot must use a whole-number leverage and holds a few % less.
* **Real tick and contract sizes** (the bot rounds prices and contracts): 157 .. 165 of 171 identical, wallet $1,895 ..
  $4,042; one trade exits a few minutes apart on a rounding tie.
* **A pass every minute while orders rest:** identical.
* **Faults** (64 runs, ~19,800 injected faults, ~30,800 bot restarts, 10,064 trades): read timeouts, lost replies
  before and after Delta acted, refused top-ups, failed stop edits, order-lookup misses, cancels Delta refuses,
  restarts mid-trade. **0 halts, 0 passes with a position and no stop, never two positions, margin never added
  twice, nothing left open.** Trades then differ from the fault-free list by a pass here and there (end wallet from
  -$1,961 to +$5,684 around it); restarts alone change nothing.
* **A lost reply on the entry order** (4 runs): HALTED as designed, nothing left open or resting.
* NOT covered by the replay: both picks filling in the same minute never happened in the history (covered by unit
  tests only); a stop inside the fill minute (the research rule starts the minute after; one unit test).
Tests: `tests/live/test_spike_live.py` 42 (24 of them for the review findings); tests/live + tests/live_exec 2,910 passed.

## Before the first order
1. Merge the PR (no `user_data` change: the image deploy rolls the bot; the run 3 → 4 switch happens at the first pass).
2. **Probe** (`python -m live fade-live-probe XRPUSD`, owner's yes): proves the post-only resting sell, the last-traded
   stop leg, the stop edit and the margin top-up on prod; PASS required.
3. The halt from the manual SOPH trade (2026-11-14) needs no `fade-live-resume`: a new run begins from the wallet.

## Review
After 20 filled trades or 2026-12-31, whichever comes first: per-trade P&L, stop fill vs trigger — above all the
BREAK-EVEN stops (the result between STRICT and LENIENT hangs on them: a losing break-even exit cost ~$98 strict vs
~$40 lenient per $30k of position in the test) — liquidations, exits at market. Stop the run if the wallet is under
$1,500 at a review.

## History: the first version (49% margin, +$500 target, +$200 lock, 0.8% trail) — NOT what runs
Kept as recorded. It was built first and replaced before any order, after the careful simulation showed it at the
$1,000 floor in 22 .. 14 of 22 monthly starts (`out/careful/report.txt`).

**Superseded 2026-11-15 by the careful simulation** (`docs/careful_sim_spec.md`, Delta's own 1m prices for all 1,049 picks, 48 h each, three implementations agreeing on every trade, `out/careful/report.txt`): per trade at $3,184 **-$94 (strict) .. -$41 (lenient)**; from $3,184 with the $1,000 floor: 20 real picks $5,702 .. $7,248, 2026 from Jan 1 $950 .. $825 (floor by May), 22 monthly starts ended under $1,000 in 22 .. 14 and above $3,184 in 0 .. 2. The numbers below are the earlier ones (Binance prices, trades cut at 23:59), kept as recorded.

Backtest of exactly these rules on one stated fill model (`scripts/clean_compare.py`, `checks()` asserting known Delta
facts), verified by an independent re-implementation (`scripts/verify_independent.py`: **604/604 history fills and
14/14 real fills identical**, `out/verify_independent.txt`). Start $3,184. STRICT = a triggered stop fills at the worse of
its price and that minute's close (+0.1%); LENIENT = at its price (+0.1%). Real fills will land between.

| | STRICT | LENIENT |
|---|---|---|
| 20 real picks 2026-10-29..11-14 (5 targets, 7 locks, 2 stops), sum per trade | **+$1,463** | **+$2,172** |
| same, as an account with the hard stop | $2,452 | $3,013 |
| full Delta history 2025-01..2026-10 (604 fills), per trade | **−$127** | **−$37** |
| full history as an account | $31 | $262 |
| fills liquidated (a 1m jump past +2.5%) | 57 of 604 | 0 |

**As built (49% margin, 25% extra margin from the fill via the option A watcher; `scripts/run4_final_numbers.py`,
`out/run4_final_numbers.txt`, extra-margin case verified 604/604 by `scripts/verify_independent.py --extra`):** 20 real
picks +$1,494 (strict) .. +$2,196 (lenient), no liquidation; full history **−$71.5 .. −$33.8 per trade**, 3 liquidations
in 604 fills (strict) .. 0 (lenient), account with the hard stop $45 .. $292; of 21 monthly starts 12 .. 8 fell under
$1,000. Liquidation is NOT impossible: a jump past ~+5% inside a minute, or past +2.5% in the seconds before the top-up.
Every other variant tested is logged in CONTEXT.md ("20x SPIKE VERSION"). **The history says this rule loses money; the
three recent weeks were a good stretch. The owner chose to run it knowing this.**

### Its rules
1. **Picks:** the paper runner's rule A picks (top 2 previous-UTC-day gainers), within 30 minutes of 00:00 UTC; a pick
   whose 2 × ATR14% stop would be more than 32.5% away is skipped (the universe every test used).
2. **Entry:** a resting **post-only GTC limit sell 5% above the last traded price** at entry time, with an exchange-held
   bracket: **stop +2.25% from the limit, triggered on the last traded price** (market stop), and a **take-profit where
   the trade nets +$500 x balance / $3,184** after costs (owner 2026-11-15: "Scale can be kept, cap is no") (maker 0.02% + 0.05% slippage in, taker 0.05% out). Skipped if the bid is already
   at the limit. Not filled by **24:00 UTC** → cancelled ("no spike").
3. **Size:** **49% of the wallet balance** as isolated margin per resting order (two may rest: 2 × 49% plus Delta's fee
   reserve fit the wallet), at the most leverage **up to 20x** Delta allows for that notional on that coin
   (`initial_margin`, `max_leverage_notional`, `initial_margin_scaling_factor`, `position_size_limit` from /v2/products),
   leverage set and read back.
4. **One position at a time:** the first pick to fill is the trade; the other pick's resting order is cancelled at once.
   No new resting orders while a position is open. A partial fill keeps what filled; the rest is cancelled.
5. **After the fill:** the stop must cover the position within 30 s (else bought back and HALT); isolated margin is
   checked (else bought back and HALT) — both run 3's rules; then **25% of the balance is added as isolated margin**
   (POST /v2/positions/change_margin), moving liquidation from about +2.5% to about +5%. A top-up whose reply is lost is
   read back, never resent. **Option A (owner 2026-11-15):** while an entry rests, a watcher checks it every 2.5 s
   (`SpikeLive.watch`, started by the paper runner), so the extra margin lands within seconds of the fill; the watcher
   and the 30 s pass share one lock, and a top-up is attempted once per position (refused or unclear: logged).
6. **The lock:** once a **1m low since the fill reaches −1%** from the entry, the stop is **moved to the price that nets
   +$200 x balance / $3,184** (PUT /v2/orders, read back). If the last price is already above that level, the bot **buys back at once**
   ("LOCK"). The move happens on the bot's next pass (30 s).
6b. **The trail (owner 2026-11-15, "Yes add it"):** after the lock the stop trails **0.8% above the lowest price since
   the fill**, only when that is tighter than the stop it has (in practice from a ~1.58% fall, just short of the ~1.76%
   target) and never up; if the last price is already above the new level the bot buys back at once ("LOCK"). Moved
   by the bot (PUT /v2/orders, read back), not Delta's native trailing stop: set at the lock, a native trail would start
   0.8% above the current price -- looser than the +$200 lock. Tested (`scripts/run4_trailing.py`): 2026 from $3,184
   $871 (strict) .. $2,241 (lenient) vs $847 .. $2,125 without; the same 29 targets in 2026 (no winners lost).
7. **Exits:** the take-profit or the stop at Delta; **24 h after the fill** the bot buys back ("TIME"). TIME, LOCK and
   LIQUIDATION (a jump past the stop: a tested risk, counted as a full stop) are planned; anything else unexplained
   HALTs, as run 3.
8. **Hard stop:** after **2 full stops** (STOP_LOSS or LIQUIDATION, not after a lock) in a calendar month (UTC), **no new
   entries until the 1st**.
9. **Kept from run 3:** the $1,000 floor (STOPPED until a refill, `fade-live-capital`), the $3,000 refill budget, HALT on
   anything unexplained, the capital check, exits-only without the switch, the kill switch.
10. **Start:** Spike Sell begins on the funded wallet ($3,184.13 on 2026-11-15). The host's `DELTABOT_EQUITY` still says 1500
    (run 3); Spike Sell uses `RUN_START_USD = 3184` for the new-run wallet check ($1,000..$3,980), so no host change is needed.
11. **Logged:** every stop fill against its trigger (`STOP_FILL`: trigger, fill, slip %), every lock (`LOCK_MOVED` /
    `LOCK_AT_MARKET` / `LOCK_FAILED`), every margin top-up (`EXTRA_MARGIN*`).

### Its scaling and trade count (owner 2026-11-15)
The target and lock scale with the balance (the same ~1.8% / ~0.8% price fall at any balance). A +$2,000 monthly cap was
tested and NOT adopted. 2026 from $3,184 with scaling (`scripts/run4_scaled_cap.py`): $847 (strict) .. $2,125 (lenient)
by Nov 14; full history $38 .. $235; 21 monthly starts: 12 .. 8 fell under $1,000. Trade count 2026: 482 picks -> 82
trades: 342 skipped by the 2-stops hard stop (it fired early in every month), 45 never spiked 5%, 13 blocked by an open
trade (`out/run4_2026_pick_accounting.txt`). Without the hard stop 260 trades, but every history start ran down to ~$45.
