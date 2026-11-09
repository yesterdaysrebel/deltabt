# The gainer-fade REAL-MONEY run (fixed 2026-11-02, before any order)

> **SUPERSEDED 2026-11-09 by `docs/fade_live_quick_prereg.md`** (rule QUICK, $1,200, 5% risk, floor $960).
> Kept as the record of version 1; its sections still apply where version 2 does not change them.

Owner, 2026-11-02: "worth starting fade in live right now?" → told the evidence (two-year backtest about
break-even: 2025 +0.006R vs random +0.002R, 2026 +0.078R; five paper days prove nothing) → "approved on
tiny run, I have 176$ in account on delta". Choices, same day: **rule A (24 h hold)**, **3% risk per
trade**, **stop the run below $140**, **its own Delta sub-account**. Code: `live/fade_live.py`, inside the
gainer-fade runner. Changing anything below needs the owner and a new version of this file.

## What it trades
- Exactly the paper runner's picks: the previous UTC day's top 2 gainers (`live/fade.py`), short only,
  at most 2 entries a day and at most 2 open positions.
- Entry right after the paper entry (the first traded minute from 00:00 UTC, about 5:31 AM IST): a
  SELL limit, immediate-or-cancel, priced 1% below the best bid (the most it may slip), sent once
  with a deterministic client order id.
- Stop = entry reference × (1 + 2 × 14-day ATR%), the paper stop, held BY DELTA as a bracket on the
  entry order (triggered on mark, capped 1.5R beyond). If no stop is seen resting within 30 s of the
  fill, the position is closed at once and the run halts.
- Exit = 24 h: at the next 00:00 UTC the bot closes with a reduce-only buy, then cancels what is left.
  Yesterday's positions are always closed before today's are opened, also when a coin repeats.
- Leverage: **1x**, set explicitly on the coin before every entry and read back (Delta's default on
  these coins is 20x); no order is sent if it does not read 1x. Liquidation must sit at least 3× further
  than the stop (`live/broker.py` LIQUIDATION_BUFFER): with 2.5% maintenance margin that allows stops
  up to ~32% away; a pick whose stop is further is skipped.

## Size
- Risk = 3% of the sub-account's wallet balance at entry, between the entry reference and the stop,
  rounded DOWN to whole contracts; a position's size is also capped at 45% of the balance, so two fit.
- A pick where one contract risks more than 3% is skipped (≈ 4–15% of picks on the backtest).

## When it stops by itself
- **The run ends** (no new entries, ever, until the owner restarts it) when the wallet balance at an
  entry check is **below $140**. Open positions keep their stops and close at their 24 h exit.
- **It halts** (no new entries until the owner looks) on: a position at Delta the bot has no record
  of; a position with no resting stop; an order whose outcome cannot be established; a wallet that
  does not look like this sub-account (balance above 1.25 × $176 at start).
- The kill-switch file (`live/guards.py`) and `dry_run = "1"` in the stack's sizing both stop new
  entries. Disabling the Trading key at Delta stops everything.

## What is recorded and how it is read
- Every order, fill, fee and exit in `fade_live_positions`, beside the paper A trade on the same pick,
  so the report shows the REAL fill against the paper fill (slippage) and the real P&L against paper.
- The question this run answers is **"does real trading match paper?"** (fills, stops, fees, funding),
  not "does the fade have an edge": at ~2 trades a day it would take ~10 months to tell +0.07R from 0.
- The daily report gains a LIVE line in the account table and a real-trade journal; any halt, a
  missing stop, a skipped pick with a reason, or a fill worse than 0.25R from the paper entry is flagged.

## Before the first real trade
1. This code is merged and deployed with `dry_run = "1"` (the real account stays off).
2. The owner makes the sub-account, moves the $176 in, creates a Trading key whitelisted to the bot's
   address 13.207.73.230, and stores it in AWS Secrets Manager (`deltabt-paper/live/bot/venue-credentials`)
   themself; it never passes through chat.
3. With the owner's yes: `python -m live fade-live-probe` on the host — reads the wallet, sets 1x on a
   liquid coin, sells ONE contract with a far bracket, confirms the stop is resting, closes it and
   cancels the rest. Costs a few cents of fees; proves the exact order shape before it matters.
4. A second PR sets the bot's sizing to `dry_run = "0"`, `equity_usd = "176"`, `risk_per_trade = "0.03"`;
   the owner merges and approves the deploy. The first real entry is the next 5:31 AM IST.
