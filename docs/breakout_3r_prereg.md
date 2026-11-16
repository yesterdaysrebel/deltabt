# %R breakout with a 3-to-1 bracket — pre-registration of the confirmation run (2026-11-16)

Owner, 2026-11-16: "find setup where average loss is less than win"; "don't worry about fees from now on"; Delta's
scalper offer waives the exit fee on positions closed within 15 minutes (the owner's fills confirm: exit fee zero on
99% of such trades, entry fee still charged). Written before the confirmation data is downloaded.

## Where it comes from (already seen — NOT evidence)
`scripts/win_gt_loss.py` (54 entry x stop x way-out cells, zero fees): the breakout entry with a fixed 3-to-1 target
was the only family positive on both data A (owner's coin-days) and U (30 most-traded perps, 2026-08-01..11-15) under
EXACT fills: stop 2% / target 6%: A +0.276% (t 4.0), U +0.187% (t 2.8), win 30-31%, avg win ~+5%, avg loss ~-2%.
Against it: STRICT fills are negative (-0.09 / -0.19%); on A random entries with the same bracket made +0.18% (those
coin-days were chosen for trending); on U random LONGS made +0.19% and random shorts -0.10% (a rising market), so the
signal's excess over random is about +0.15% (t ~2). Expectation stated in advance: **the excess over random does not
repeat**.

## Rule (frozen)
- 1m bars. LONG signal: Supertrend(10, 2) is up and %R(140) closes above -20 having closed at or below -20 the bar
  before. SHORT: Supertrend down and %R closes below -80 having closed at or above -80. First 300 bars = warm-up.
- In at the next bar's open (the bar must have traded). Stop 2% from the entry, target 6%, everything closed at the
  close 24 h after entry. One position per coin at a time. Secondary (reported, not judged): stop 1% / target 3%.

## Fills
- EXACT (primary): entry at the open; the stop wins a shared bar and fills at its price, or at the bar's open when the
  bar opens beyond it; target on touch.
- MID: EXACT with the entry 0.02% worse and stop fills 0.05% worse.
- STRICT: entry 0.05% worse; stop at the worse of stop and that bar's close, 0.1% worse; target must be passed by
  0.1%; time exit 0.05% worse.
- Headline is before fees (owner's instruction). One extra line with the real fees: entry taker 0.059%, target maker
  0.0236%, stop / time exit taker 0.059%, exit fee waived when the trade closes within 15 minutes.

## Control
Random entries: per data stretch, the same number of longs and shorts as the signal, at random bars, 5 draws, same
bracket, same fills. The signal must beat the random entries of the SAME side — that removes market direction.

## Data
The same 30 coins as `out/pullback/universe.json`, Delta 1m candles, **2026-03-01 .. 2026-07-31 UTC** (the five months
before the period already used) → `out/breakout_oos/`. Same hygiene: coin-days with more than 20% flat 1m bars dropped,
coin-days in data A or B dropped.

## Pass bar
All of: EXACT mean > 0 with t ≥ 2 (trades summed by UTC day); EXACT mean > 0 in each half of the period; MID mean
> 0; signal longs > random longs AND signal shorts > random shorts under EXACT. STRICT is reported as the lower
bound. Nothing is tuned after the run; numbers are quoted only after a second implementation reproduces them.
