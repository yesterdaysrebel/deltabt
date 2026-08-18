# MARKET / EXECUTION FEASIBILITY — paths A, B and C

Decides whether changing the trading environment creates enough economic
opportunity to justify another research cycle. **No signal was tested.** No
indicator, no direction, no parameter search, no VALID. Every number below
describes the environment, measured on TRAIN only; TEST was never touched.

Sources: `feasibility_economics.json`, `feasibility_gate.json`.

---

## 0. The measured baseline

    taker     5.90 bps   (0.05% x 1.18 GST)
    maker     2.36 bps   (0.02% x 1.18 GST)
    slippage  2.00 bps   (taker only)

    round trip, taker both legs        15.80 bps
    round trip, maker in / taker out   10.26 bps
    round trip, maker both legs         4.72 bps

Funding, measured over 8,460 settlements per symbol on TRAIN, is **not** a
material cost and this is worth stating because it was the obvious objection to
Path A. Mean rate per 8h settlement: BTC +0.291 bps, ETH +0.491, SOL +0.330,
XRP −0.206. A persistently long position pays **0.68 bps over a full day** and
2.04 bps over three days. A direction-balanced strategy pays approximately zero,
because funding is a transfer rather than a fee.

---

## 1. Decision table

Duty cycle 11.2% — the discovery phase's **best** observed event rate
(H-STRUCTURE-2's 3,811 events against 33,892 non-overlapping +1h bars). Cluster
inflation 1.40×, calibrated rather than assumed: at that duty cycle the iid MDE
at +1h is 3.55 bps and the measured cluster MDE was 4.98.

| path | horizon / data | realistic cost | typical move | cost/move | min viable edge | events/yr | MDE | MDE/required | current evidence | data required | feasibility |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| **A** | +4h, taker | 15.91 bps | 69.4 bps | 22.9% | 15.9 bps | 952 | 19.7 bps | 1.24× | −3.56 to +7.71 bps measured | none — have it | **NOT MEASURABLE** |
| **A** | +12h, taker | 16.14 bps | 134.4 bps | 12.0% | 16.1 bps | 317 | 59.5 bps | 3.69× | not measured | none — have it | **NOT MEASURABLE** |
| **A** | +1d, taker | 16.48 bps | 197.9 bps | 8.3% | 16.5 bps | 158 | 117.0 bps | 7.10× | +0.27 to +1.18 bps | none — have it | **NOT MEASURABLE** |
| **A** | +3d, taker | 17.84 bps | 370.2 bps | 4.8% | 17.8 bps | 52 | 337.9 bps | 18.94× | none | none — have it | **NOT MEASURABLE** |
| **B** | +1h, maker both legs | 4.72 bps | 33.6 bps | 14.0% | 4.7 bps | 3,811 | 5.0 bps | 1.05× | effects 0.2–3.7 bps | fill model | **BORDERLINE** |
| **B** | +1h, maker + adverse selection | 7.17 bps | 33.6 bps | 21.3% | 7.2 bps | 3,811 | 5.0 bps | 0.69× | effects 0.2–3.7 bps | fill model | **MEASURABLE** |
| **B** | +1h, maker in / taker out | 10.26 bps | 33.6 bps | 30.5% | 10.3 bps | 3,811 | 5.0 bps | 0.48× | effects 0.2–3.7 bps | fill model | **MEASURABLE** |
| **C** | +5m, L2+prints, taker | 15.80 bps | 10.0 bps | **158.0%** | 15.8 bps | n/a | n/a | n/a | none | L2 + prints, ~12 mo | **DEAD** |
| **C** | +15m, L2+prints, taker | 15.80 bps | 17.3 bps | **91.3%** | 15.8 bps | n/a | n/a | n/a | none | L2 + prints, ~12 mo | **DEAD** |
| **C** | +1h, L2+prints, **maker** | 7.17 bps | 33.6 bps | 21.3% | 7.2 bps | 3,811 | 5.0 bps | 0.69× | effects 0.2–3.7 bps | L2 + prints | **= Path B** |

---

## 2. The feasibility gate

> Required edge ≤ 2 × the largest credible effect observed, **OR** a concrete,
> evidence-based reason the new regime produces materially larger effects.

"Largest credible effect" has no single defensible value, and choosing one would
let the answer depend on my choice. All four readings are reported:

| reading | value | gate | basis |
|---|---:|---:|---|
| D1 strict | 0.00 bps | 0.00 | nothing was credibly established — every effect sits below its own MDE and inside its own control distribution, across nineteen experiments with zero positive verdicts |
| D2 largest point estimate at +1h | 3.74 bps | 7.47 | the largest \|effect\| actually measured at the pre-declared horizon |
| D3 largest CI upper at +1h | 9.77 bps | 19.55 | the largest effect the data cannot *exclude* — not one it supports |
| D4 largest CI upper, any horizon | 48.97 bps | 97.94 | large mainly because long-horizon returns are noisy |

**Clause 1 results:**

| path | D1 | D2 | D3 | D4 |
|---|---|---|---|---|
| A — any horizon, taker | fail | **fail** | pass | pass |
| B — maker both legs | fail | **PASS** | pass | pass |
| B — maker + adverse selection | fail | **PASS** | pass | pass |
| B — maker in / taker out | fail | **fail** | pass | pass |
| C — standalone, taker | fail | **fail** | pass | pass |

D2 is the only reading that is both non-degenerate and grounded in a measured
quantity. D1 fails everything by construction; D3 and D4 pass everything,
because a wide confidence interval is a statement about noise, not evidence of
an effect. **Under D2, Path B passes and Paths A and C fail.**

---

## 3. Path A — longer horizon

**Break-even.** 15.91 bps at +4h rising to 17.84 at +3d — essentially flat,
because funding is negligible. The round trip does not care how long you hold.

**Required edge.** Unchanged at ~16 bps. This is the central fact about Path A
and it is easy to miss: **a longer horizon does not lower the bar.** It raises
the typical move, so cost falls from 22.9% to 4.8% *of that move* — but the
absolute edge you must produce is the same ~16 bps it has always been.

**Is that magnitude plausible?** It is 4.3× the largest effect ever measured
(3.74 bps). There is a real argument that effects grow with horizon: a signal
with constant predictive R² produces an effect scaling as √h, so a +1h effect of
3.7 bps would be ~7.4 bps at +4h and ~18 bps at +1d. That would clear the bar.

**And that argument is exactly why Path A fails.** Noise scales the same way,
and the observation count falls. MDE ∝ SD/√n ∝ √h / √(T/h) = **h/√T — linear in
horizon**, while the effect grows only as √h. Detectability therefore degrades
as √h no matter how favourable the economics become:

    +4h    MDE 19.7 bps vs 15.9 required    1.24x    blind
    +12h        59.5          16.1          3.69x    blind
    +1d        117.0          16.5          7.10x    blind
    +3d        337.9          17.8         18.94x    blind

**Can the existing dataset measure it? No.** At every horizon the smallest
detectable effect exceeds break-even. You could trade a Path A strategy; you
could never establish that it works rather than that it was lucky. Given a
program built on the principle that an effect below the MDE is never called an
edge, that is disqualifying.

**Observations obtainable:** 952/yr at +4h down to 52/yr at +3d, at the phase's
best duty cycle. To detect a break-even edge at +1d would need 4,081 independent
observations against 1,412 available per year — **2.9 years of history**, and at
+3d, 20.6 years. The study window is 1.5 years with TEST locked.

**What kills it before any strategy research:** it is already killed. The
measurement is impossible before the strategy is written.

---

## 4. Path B — maker execution

**Break-even.** 4.72 bps if both legs rest passively; 10.26 bps if only the
entry does. Against 15.80 taker, that is a **3.3× reduction in the bar**.

**Required edge.** 4.72–10.26 bps, or 7.17 bps once the measured adverse
selection is charged. This is the only path that moves the required edge into
the range of things this program has actually measured.

**Is that magnitude plausible?** 7.17 bps is 1.9× the largest measured effect
(3.74 bps) — inside the gate's 2× allowance. It is the first time in nineteen
experiments that the bar has been within reach of the observations.

**Adverse selection, measured.** A passive buy at the prior 1m close is touched
88.0% of the time; conditional on being touched the next-bar return is
**−1.221 bps**, against an unconditional +0.006. A passive sell mirrors it at
+1.225. So adverse selection is **≈1.23 bps per leg** — against a fee saving of
11.08 bps. Net saving 8.63 bps.

**That 1.23 bps is an underestimate, and the report must not lean on it.** With
1m OHLC there is no queue depth and no trade prints, so "the low touched my
limit" cannot be distinguished from "my order filled." An 88% touch rate is the
tell: price oscillates across the prior close almost every minute, which is not
what a fill rate looks like. Real fills concentrate in the cases where price
traded *through* the level — precisely the adverse ones. **The existing data
cannot support a credible fill model.**

**What data is required, and how much.** Delta's public websocket already
carries `all_trades` (individual prints, microsecond timestamps) and the client
subscribes to it. It does **not** currently subscribe to `l2_orderbook`. Neither
has a REST history endpoint, so neither can be backfilled — both must be
recorded forward. What is needed:

- `l2_orderbook` depth snapshots — to know how much size rests ahead at the
  limit price;
- `all_trades` with aggressor side — to know how much of that queue drained;
- together these give queue position, and queue position is what a fill model is.

**How long that takes is the good news.** Validating the fill model does not
need a research-length dataset. Fill rate to ±2% needs ~600 resting orders;
adverse selection at the 1-minute scale (SD ≈ 10 bps) to ±1 bp needs ~400 fills.
Both are **weeks of recording, not months** — and they are the entire question.

**Observations obtainable:** unchanged at 3,811/yr for a signal with the phase's
best duty cycle, giving MDE 5.0 bps against 7.17 required (0.69×). Measurable.

**What kills it before any strategy research:** a single pre-declared number.
The maker-both-legs saving is 11.08 bps, so **adverse selection of 5.54 bps per
leg wipes it out entirely.** The OHLC proxy says 1.23. If recorded fills put it
at or above 5.54, Path B is dead and no strategy should be written. That is a
cheap, fast, falsifiable test of the whole path.

---

## 5. Path C — better market data

**Break-even.** Unchanged at 15.80 bps. This is the decisive point and it is
structural: **better data does not make execution cheaper.** It changes what you
can predict, not what you pay.

**Required edge.** 15.80 bps, at whatever horizon the new data is used.
Microstructure signals live at seconds to minutes, where the typical move is
10.0 bps at +5m and 17.3 bps at +15m. So the required edge is **158% of the
typical 5-minute move and 91% of the 15-minute move.**

**Is that magnitude plausible? No.** It requires capturing more than the entire
typical move, correctly, every time. No amount of order-flow information changes
that arithmetic while paying taker fees on both legs.

**Can the existing dataset measure it? No.** There is no order book, no trade
print history, no queue data. Nothing about Path C is testable today.

**Observations obtainable:** zero until recording starts. With no backfill, a
TRAIN/VALID/TEST structure comparable to the current one is roughly **12 months
of wall-clock away** before the first experiment could run.

**What kills it before any strategy research:** it is killed by the cost
identity alone, in its standalone form.

**But Path C is not worthless, and the reason matters.** Its real value is
*instrumental*: L2 depth and trade prints are exactly the data Path B needs to
build a credible fill model. Path C as a signal source is dead; Path C as the
measurement apparatus for Path B is the cheapest useful thing in this table.
The last row of the decision table is Path C's data used at Path B's cost
structure — and it is simply Path B.

---

## 6. Verdict

| path | gate clause 1 (D2) | clause 2 (evidence for larger effects) | proceed |
|---|---|---|---|
| A — longer horizon | fail (16 bps vs 7.47) | effects do grow as √h, but MDE grows as h — the regime becomes unmeasurable faster than it becomes profitable | **NO** |
| B — maker execution | **PASS** (7.17 bps vs 7.47) | does not need larger effects; it lowers the bar 3.3× into the measured range | **YES, conditional** |
| C — better data | fail (15.8 bps vs 7.47) | standalone requires >91% of the typical move; no evidence any regime supplies that | **NO, except as Path B's instrument** |

**One path passes: B.** It passes on the only non-degenerate reading of the
gate, it is the only path whose required edge lands inside the range of effects
this program has actually measured, and it is the only one that remains
statistically measurable.

It passes **conditionally**, and the condition is not a formality. The 1.23 bps
adverse-selection estimate comes from data that cannot distinguish a touch from
a fill, and if the true figure is 5.54 bps or higher the entire advantage
disappears. That condition is cheap to settle — weeks of recorded L2 and trade
prints, before any strategy code exists.

If recorded fills put adverse selection at or above 5.54 bps per leg, then no
path passes, and the protocol's instruction stands: **stop the trading research
program rather than inventing a fourth strategy family.**
