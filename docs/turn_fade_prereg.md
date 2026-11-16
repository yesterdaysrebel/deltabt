# The owner's fade, made mechanical: "fade a sharp move once it has turned" — pre-registration (2026-11-16)

Owner, 2026-11-16: "my trades that you analysed, you should find something there with quick entry and exit" →
"yes test it". Written before the rule is run on the check data.

## Where it comes from (already seen — NOT evidence)
Of the owner's 761 real entries only the 93 taken AGAINST a 15-minute move of 1%+ went his way (+0.5% in 5–10 min).
Fading any 15-minute move mechanically showed nothing. Profile of his 93 fades against every mechanical fade chance
on the same coin-days (medians): move faded 2.5% (mechanical 1.4%); 15-minute range 6.7% (2.5%); he enters 3 minutes
after the extreme (1), with the price already 41% of the way back across the range (16%), and the last minute already
going his way (+0.30% vs −0.25%). Inside his fades, "last minute already his way" made +1.0% at 10 min (58 trades),
"still against him" −0.16% (35). He does not catch the move; he waits for it to turn. Expectation: **fails**.

## Rule (frozen). For a LONG (fading a fall); a SHORT is the mirror.
At a 1m close, over the last 15 one-minute bars (this bar included):
1. the close is 2% or more below the close 15 bars ago;
2. the lowest low was 2 to 7 bars ago (this bar = 0);
3. the close has come back 25% to 66% of the way from that low to the 15-bar high;
4. this bar closed above the previous close.
In at the next bar's open (that bar and the 3 before it must have traded). Target 1.5%, stop 1%, everything closed at
the close 15 minutes after entry. No new entry on the coin for 15 minutes after an entry.
SECONDARY (reported, not judged): the same with 1% in (1), 1 to 7 bars in (2), 20%+ in (3).

## Fills, fees, control
EXACT (primary): entry at the open; the stop wins a shared bar and fills at its price, or at the open when the bar
opens beyond it; target on touch. MID: entry 0.02% worse, stops 0.05% worse. STRICT: entry 0.05% worse, stop at the
worse of stop and that bar's close and 0.1% worse, target must be passed by 0.1%, clock exit 0.05% worse.
Headline before fees (owner's instruction); one line with the real fees inside the scalper window: entry taker
0.059%, no exit fee. CONTROL: per data stretch the same number of longs and shorts at random bars, 5 draws, same
bracket; and the plain mechanical fade (condition 1 only) with the same bracket.

## Data
U = 30 most-traded Delta perps, 2026-08-01..11-15; O = the same coins, 2026-03-01..07-31; coin-days of data A and B
removed. Two cuts: LIQUID days (at most 20% flat 1m bars — as before) and ALL days (only the traded-bar condition
above). A (the owner's coin-days) is printed for reference only.

## Pass bar (U and O pooled, LIQUID days, primary rule)
EXACT mean > 0 with t ≥ 2 (trades summed by UTC day); EXACT mean > 0 on U and on O separately; MID mean > 0; the rule
beats the random entries and the plain fade. At least 200 trades, otherwise "not enough trades to judge".
Nothing is tuned after the run; numbers are quoted only after a second implementation reproduces them.
