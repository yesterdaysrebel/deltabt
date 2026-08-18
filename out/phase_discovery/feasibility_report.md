We have completed the discovery phase and strategic diagnosis.

Do not search for a trading strategy. Do not propose a fourth hypothesis family. Do not modify the signal research program.

The only authorized next experiment is:

Determine whether passive maker execution on Delta can actually reduce the economic cost enough to justify reopening strategy research.

This is an execution/measurement experiment, not a market-prediction experiment.

Frozen context

The current research has:

19 recorded market experiments
0 positive economic verdicts
largest measured directional effect: 3.74 bps
current taker round-trip cost: 15.80 bps
proposed maker-both-legs cost: 4.72 bps
maker + measured adverse-selection allowance: 7.17 bps
maker saving versus taker: 11.08 bps
pre-declared kill threshold: 5.54 bps adverse selection per leg
H-NULL-1 passed and is frozen
cluster inference is the ratified production primary
TEST must remain untouched
no strategy research is authorized until this gate passes

The existing OHLC-only estimate of adverse selection (~1.23 bps/leg) is not trusted as a fill model because OHLC cannot distinguish a price touch from an actual fill.

Therefore we need real execution data.

OBJECTIVE

Build the smallest possible empirical experiment that answers exactly two questions:

Q1 — Can a realistic resting limit order actually fill?

Estimate:

fill probability
time-to-fill
partial-fill probability
queue position / approximate queue ahead
fill conditional on market movement
fill conditional on order-book state
Q2 — What is the economic adverse selection of those actual fills?

Measure, per filled order:

mid-price at order submission
limit price
mid-price at fill
subsequent return after:
1m
5m
15m
markout relative to the fill price
signed markout
adverse-selection cost in bps

Do not substitute "price touched the limit" for "order filled."

DATA COLLECTION

Use the existing Delta websocket infrastructure if possible.

Record, at minimum:

L2 order book
timestamp
bid/ask levels
depth around the proposed limit
best bid
best ask
spread
visible quantity at the limit
visible quantity ahead of the hypothetical order
Trade prints
timestamp
price
quantity
aggressor/buyer-seller side if available
Simulated/resting orders

Do not place real money orders.

Create a paper-order simulator that records:

order timestamp
symbol
side
limit price
size
estimated queue ahead
book state at submission
subsequent trades
subsequent book depletion
estimated fill quantity
fill timestamp
cancellation timestamp

The simulator must be explicit about its assumptions.

If actual queue position cannot be reconstructed reliably from public data, say so.

Do not manufacture precision.

SAMPLE TARGETS

Use the previously frozen targets:

approximately 600 resting orders for fill-rate precision
approximately 400 actual/credible fills for adverse-selection precision

Do not stop early because the estimate looks favorable.

Do not extend the experiment merely because the result looks unfavorable.

The sample-size rule is frozen before collection.

PRIMARY METRIC

The primary economic quantity is:

adverse selection per filled maker leg, in bps

Define it precisely before collecting results.

Use signed markout so that:

a passive BUY followed by a price decline = adverse
a passive SELL followed by a price rise = adverse

Report:

point estimate
confidence interval
number of fills
number of orders
fill rate
partial-fill rate

Use the dependence-aware inference infrastructure from H-NULL-1 where applicable.

Do not use an iid SE merely because it is convenient.

THE HARD KILL GATE

The decision boundary is frozen:

5.54 bps adverse selection per maker leg

This number must not be moved after seeing the data.

PASS

If the credible estimate is below 5.54 bps and the fill model is sufficiently supported by the observed data, Path B remains viable.

FAIL

If adverse selection is ≥5.54 bps per leg, Path B is dead.

INCONCLUSIVE

If the data cannot establish the quantity reliably, report INCONCLUSIVE.

Do not turn INCONCLUSIVE into PASS by choosing a favorable assumption.

Do not turn INCONCLUSIVE into FAIL merely because the result is inconvenient.

CRITICAL GOVERNANCE RULE

This experiment must NOT become a strategy experiment.

Therefore:

Forbidden
EMA
WPR
ADX
Supertrend
market structure
momentum
mean reversion
order-flow alpha
signal discovery
feature engineering for prediction
parameter sweeps
strategy backtests
LONG vs SHORT optimization
entry optimization
exit optimization
stop optimization
TP optimization
VALID
TEST
new hypothesis families
H-MAKER-2
"let's try another execution assumption" after seeing the result

The only question is:

Can passive execution produce a sufficiently low and measurable trading cost?

ANTI-LOOP RULE

This is the most important requirement.

Before implementation, write a frozen preregistration containing:

data schema
collection period
sample targets
fill definition
queue reconstruction assumptions
adverse-selection definition
markout horizons
inference method
missing-data treatment
5.54 bps kill threshold
PASS / FAIL / INCONCLUSIVE rules

Hash the preregistration.

After data collection begins:

DO NOT CHANGE THE RULES BASED ON INTERMEDIATE RESULTS.

If the simulator discovers that queue position cannot be estimated reliably, stop and report that fact rather than inventing a new estimator.

IMPORTANT DISTINCTION

Separate these three quantities:

1. Touch rate

Price reached the limit.

2. Simulated fill rate

The reconstructed book/trade sequence implies that the order would have filled.

3. Actual fill rate

A real order was actually filled.

Do not call #1 a fill rate.

If #2 cannot be established robustly from the available market data, explicitly report the limitation.

REQUIRED OUTPUT

Produce:

out/hmaker1/
    preregistration.md
    preregistration.sha256
    collection_report.md
    fill_model.md
    adverse_selection.md
    statistical_report.md
    final_verdict.md

Also add one append-only registry record.

Do not modify historical registry entries.

FINAL VERDICT FORMAT

The final report must end with exactly one of:

PATH B — PASS

Passive execution is sufficiently measurable and economically viable.

or

PATH B — FAIL

Passive execution does not overcome the 5.54 bps adverse-selection threshold.

or

PATH B — INCONCLUSIVE

The available execution data cannot establish the economics reliably.

If PASS:

Do not immediately start strategy research.

Instead report exactly what evidence has now been established and wait for operator authorization to reopen market research.

If FAIL:

Stop the Delta directional-trading research program.

Do not propose another strategy family.

If INCONCLUSIVE:

Identify the precise measurement limitation, but do not create another research cycle to rescue the hypothesis.

THE GOVERNING PRINCIPLE

We have already demonstrated that repeatedly searching for a larger predictive edge produces a research loop.

The next experiment therefore attacks the binding economic constraint, not the prediction problem.

If maker execution cannot solve that constraint, we stop.

No fourth family.

No "one last indicator."

No H-MAKER-2 unless the operator explicitly authorizes a new experiment after reviewing the frozen result.

Build the measurement. Freeze the rules. Collect the data. Answer the one question. Stop.