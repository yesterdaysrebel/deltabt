# The gainer-fade REAL-MONEY run, version 2: rule QUICK (fixed 2026-11-09, before any Quick order)

Supersedes `docs/fade_live_prereg.md` (version 1: rule A, $176, 3% risk, floor $140; 4 real trades
2026-11-03..11-08, −$1.08, −0.22R). Version 1's sections apply unchanged except where this file says
otherwise. Code: `live/fade_live.py`. Changing anything below needs the owner and a new version of this file.

## Why, said once
Owner, 2026-11-09, after seeing paper Quick ahead of paper A since 11-03: "I prefer it" → "approved, we
will deploy fade quick in real"; capital $1,200; risk 5% per trade. Told first, and recorded here:
- Quick was the one cell of 60 in the 2026-10-28 win-rate scan (CONTEXT.md "WIN-RATE SCAN"):
  2025 +0.010R ±0.013 (not distinguishable from random shorts), 2026 +0.032R ±0.015; rule A was
  +0.009R / +0.076R. It is the fade effect reshaped, not a new edge; picked after both years were seen.
- 9 forward trades each (Quick ≈ +1.0R, A ≈ −0.7R since 11-03) are noise.
- One stop costs about five target wins. A 75%-of-capital sizing was simulated and declined
  (median worst drop ~40% over 60 trades); 5% risk was chosen (simulated median worst drop ~13%).

## What changes from version 1
- **Exit:** the entry's exchange-held bracket gets a **take-profit** leg as well as the stop:
  target = entry reference − 0.2 × (stop − entry reference), rounded DOWN to the tick, so a target win is
  never smaller than paper's. Both legs trigger on MARK (paper Quick fills on the 1m trade low; the
  real target may therefore fire less often — that difference is what this run measures). Whatever
  is still open at the next 00:00 UTC is bought back reduce-only, as in version 1.
- A close by the take-profit is a normal exit (`TAKE_PROFIT`), like a stop; any other close at Delta halts.
- A take-profit leg not seen resting after the fill is logged (`TAKE_PROFIT_MISSING`, flagged in the
  daily report) but the position is KEPT: its stop and the 24 h exit still protect it.
- **Size:** risk = **5%** of the wallet balance at entry (code cap `MAX_RISK_PCT` 0.05), whole contracts
  rounded down, notional still ≤ 45% of the balance, leverage still 1x (stops beyond ~32% still skipped).
  At $1,200: about −$60 a stop, about +$12 a target win.
- **Capital and floor:** start **$1,200**; the run STOPS (final) when the balance at an entry check is
  **below $960** (80% of the start). The wallet must read $960–$1,500 (1.25 × start) when the bot
  starts, else the account stays off.
- **Compared against:** the paper QUICK account on the same picks (the report's "vs paper" columns).

## Switch-on sequence
1. Owner tops the sub-account up to about $1,200. The running version-1 bot then HALTS itself at the next
   entry check ("wrong account?": balance > $220) instead of trading rule A; open positions still exit.
2. This code is merged and deployed with `dry_run = "1"`, `equity_usd = "1200"`, `risk_per_trade =
   "0.05"` — AFTER any version-1 position has had its 24 h exit (an OFF account does not run exits).
3. With the owner's yes: `python -m live fade-live-probe` on the host — one contract with BOTH legs
   (25% away each), stop and take-profit seen resting, bought back, leftovers cancelled.
4. A second PR sets `dry_run = "0"`; owner merges and approves the deploy; then
   `python -m live fade-live-resume` clears step 1's HALT. The first Quick trade is the next 5:31 AM IST.
