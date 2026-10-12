# Panel review of the discovery plan v1 — synthesis (2026-10-13)

Five models, five roles, $0.34 (`out/panel/S0-plan-v1/`). The code-review reply came back empty at the
token cap (tooling; cap raised). The four others are summarised here with my assessment of each point.
**Net effect: the plan as drafted should not run.** The points below are the reasons.

## Points that change the plan (all three reviewers agree, independently)

1. **One position at a time makes realistic edges undetectable** (statistics reviewer). With sequential
   1–3 day holds, DEV yields ~60–90 trades and OLD ~100–150. At a per-trade spread of ~1R, the standard
   error is ~0.10R; a true edge of +0.05–0.08R per trade (what survives 0.158 % costs in liquid perps)
   has **7–12 % power** against the plan's own bar. The design would discard genuine edges almost every
   time and pass noise occasionally. *Assessment: correct, and it is the owner's constraint, not a design
   choice; no amount of search fixes it.*

2. **OLD is not an untouched window any more** (leakage reviewer). It has been read by the cheap-fee
   gate, ideas 1–7, idea 6, the flow test, the daily trend test and the listing test. Even where a new
   candidate was never run on it, every design decision since 12 Oct was made knowing OLD's outcomes.
   *Assessment: correct. The only clean confirmation set for a Delta-specific rule is data that does not
   exist yet, i.e. forward months; the Binance 2019–2024 set cannot carry Delta inputs (settlement clock,
   cross-venue gap, Delta funding, listing age), so it is a transportability check, not a confirmation.*

3. **p-values on DEV after a free search are not evidence** (leakage and statistics reviewers). The
   search space, model configurations and candidate-selection rule were not fixed in advance, so "up to
   two candidates" could be the best-looking two of thousands. *Assessment: correct; S4 was mislabelled as
   a test. A free search can only ever be exploratory; the first real test is on forward data.*

4. **The pattern pass conditions on the outcome** (statistics reviewer). Clustering paths that precede
   big moves, without clustering the same paths before quiet periods, finds shapes that precede
   volatility, not direction. *Assessment: correct; a negative control was missing.*

5. **Day-clustering is not enough for multi-day holds** (both). Overlapping 3/5/10-day outcomes need
   block inference with blocks at least the holding period long. *Assessment: correct; this also means the
   daily-trend lead's day-clustered t of 2.19 was itself optimistic.*

6. **The dry-run stopping rule is self-contradictory** (statistics reviewer): "100 trades or 21 days" at
   one position yields 7–20 trades in 21 days. *Assessment: correct; this applies to the current dry run as
   much as to any survivor — at ~2 trades a week, 100 closed trades takes a year.*

## The devil's advocate

Grok argued every mechanism in §3 is either on the kill list or an economic null at the constraints:
lead/lag within costs is spread or latency; clocks are the hour/weekday splits that failed on OLD;
listing first hours are the listing test with less power; path clusters are the representation the
root-cause note killed; the supervised model is "the closed feature kitchen plus the parked lead"; the
option surface was measured efficient. It names the only inputs the kill list leaves open — Delta's own
aggressor flow, the order book, liquidations — and notes the plan does not record them. *Assessment:
mostly right. The two venue-specific items I would still defend as untested (Delta-vs-Deribit IV
relative value; the settlement clock on the perp) are not refuted by prior tests, but point 1 above
means neither can be tested at one position with the data we hold.*

## The breadth reply

DeepSeek proposed funding cross-section (closed: H-Funding-1), intraday volatility seasonality (a sizing
effect, not an edge) and 1–6h reversal (closed: H-Scalp-1). *Assessment: the breadth role adds little;
drop it from future rounds.*

## What follows

- The programme as drafted is withdrawn. A free search at this account size and constraint cannot
  produce a result that could be believed, and the honest confirmation set (forward data) would need
  a year at one position.
- If the owner wants one more thing done, the only defensible form is: one venue-specific rule, fixed in
  advance, run forward on paper for as long as it takes to reach ~100 trades, with no in-sample claim.
- The panel is worth keeping for any future pre-registration: it found a design flaw (overlap inference)
  that applies retroactively to this month's strongest lead.
