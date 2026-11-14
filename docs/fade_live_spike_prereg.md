# Fade real-money run 4 — "sell the spike" at 20x, $3,184 — rules fixed before any order

Owner, 2026-11-15: "we want to deploy this version to prod, decommission rule A"; choices: hard stop **"Month: 2 stops
only"**, size **"50% margin as tested"**, paper accounts **kept running**; "we will keep lock after a 1% fall, keep these
ideas logged"; then **"approved"** for: stop triggered on the **last traded price**, **extra isolated margin after the
fill**, and **every stop's fill logged against its trigger**.
Supersedes run 3 (`docs/fade_live_ladder_prereg.md`, rule A ladder) for the real account. Code: `live/spike_live.py`
(the rules), on run 3's machinery in `live/fade_live.py`. Paper accounts (A, QUICK, LADDER, funding, options) unchanged.

## What the evidence says (recorded so the review cannot rewrite it)
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

Extra margin of 25% after the fill (approved, below) removes most liquidations: −$75 (strict) per trade in the history.
Every other variant tested is logged in CONTEXT.md ("20x SPIKE VERSION"). **The history says this rule loses money; the
three recent weeks were a good stretch. The owner chose to run it knowing this.**

## The rules
1. **Picks:** the paper runner's rule A picks (top 2 previous-UTC-day gainers), within 30 minutes of 00:00 UTC; a pick
   whose 2 × ATR14% stop would be more than 32.5% away is skipped (the universe every test used).
2. **Entry:** a resting **post-only GTC limit sell 5% above the last traded price** at entry time, with an exchange-held
   bracket: **stop +2.25% from the limit, triggered on the last traded price** (market stop), and a **take-profit where
   the trade nets +$500** after costs (maker 0.02% + 0.05% slippage in, taker 0.05% out). Skipped if the bid is already
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
   read back, never resent.
6. **The lock:** once a **1m low since the fill reaches −1%** from the entry, the stop is **moved to the price that nets
   +$200** (PUT /v2/orders, read back). If the last price is already above that level, the bot **buys back at once**
   ("LOCK"). The move happens on the bot's next pass (30 s).
7. **Exits:** the take-profit or the stop at Delta; **24 h after the fill** the bot buys back ("TIME"). TIME, LOCK and
   LIQUIDATION (a jump past the stop: a tested risk, counted as a full stop) are planned; anything else unexplained
   HALTs, as run 3.
8. **Hard stop:** after **2 full stops** (STOP_LOSS or LIQUIDATION, not after a lock) in a calendar month (UTC), **no new
   entries until the 1st**.
9. **Kept from run 3:** the $1,000 floor (STOPPED until a refill, `fade-live-capital`), the $3,000 refill budget, HALT on
   anything unexplained, the capital check, exits-only without the switch, the kill switch.
10. **Start:** run 4 begins on the funded wallet ($3,184.13 on 2026-11-15). The host's `DELTABOT_EQUITY` still says 1500
    (run 3); run 4 uses `RUN_START_USD = 3184` for the new-run wallet check ($1,000..$3,980), so no host change is needed.
11. **Logged:** every stop fill against its trigger (`STOP_FILL`: trigger, fill, slip %), every lock (`LOCK_MOVED` /
    `LOCK_AT_MARKET` / `LOCK_FAILED`), every margin top-up (`EXTRA_MARGIN*`).

## Before the first order
1. Merge the PR (no `user_data` change: the image deploy rolls the bot; the run 3 → 4 switch happens at the first pass).
2. **Probe** (`python -m live fade-live-probe XRPUSD`, owner's yes): proves the post-only resting sell, the last-traded
   stop leg, the stop edit and the margin top-up on prod; PASS required.
3. The halt from the manual SOPH trade (2026-11-14) needs no `fade-live-resume`: a new run begins from the wallet.

## Review
After 20 filled trades or 2026-12-31, whichever comes first: per-trade P&L, stop fill vs trigger (where between STRICT
and LENIENT), liquidations, locks at market. Stop the run if the wallet is under $1,500 at a review.
