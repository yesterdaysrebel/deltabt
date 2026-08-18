# OPERATOR SUMMARY — is another research cycle worth funding?

Plain numbers. A ₹1,00,000 position is used throughout so the costs are
concrete. All figures measured on TRAIN; TEST untouched. No strategy was tested.

---

## What we know

Nineteen experiments. **Zero** produced a positive economic verdict.

The largest directional effect ever measured, across every family and every
horizon, was **3.7 basis points** — and even that could not be distinguished
from random noise.

On a ₹1,00,000 trade:

    what one round trip costs today      ₹158
    how far price typically moves in 1h  ₹336
    the biggest edge we ever measured    ₹37

**We are paying ₹158 to chase a ₹336 move, armed with a ₹37 edge that probably
isn't real.**

---

## What is killing profitability

One thing, and it is not the strategies.

**Fees and slippage eat 47% of a typical hourly move.**

| hold for | price typically moves | cost | cost eats |
|---|---:|---:|---:|
| 5 minutes | ₹100 | ₹158 | **158%** |
| 15 minutes | ₹173 | ₹158 | **92%** |
| 1 hour | ₹336 | ₹158 | **47%** |
| 1 day | ₹1,979 | ₹165 | 8% |

At five minutes, a trader who is **right about direction every single time**
still loses money. That is not a strategy problem. It is arithmetic.

Funding, incidentally, is not the problem — it costs about ₹7 per day on a
₹1,00,000 long. It was the obvious suspect and it is innocent.

---

## What would have to change

We looked at exactly three levers.

### A. Hold positions longer — **doesn't work**

Longer holds look great economically: cost drops from 47% of the move to 8% at
one day, 5% at three days.

But the bar does not move. You still need to earn **₹165** per trade — the round
trip costs the same whether you hold an hour or a week. Meanwhile the price
swings get much wider and you get far fewer trades to learn from.

The result: at one day we could only detect an edge of **₹1,170 or larger**,
when we only need ₹165 to break even. **We would be trading blind** — unable to
tell a working strategy from a lucky one. Proving a 1-day strategy works would
need 2.9 years of history; we have 1.5. At three days, 20 years.

Better economics, but you go blind faster than you get richer.

### B. Use limit orders instead of market orders — **the one that works**

Post orders and wait to be filled, rather than crossing the spread.

    cost today, market orders both ways   ₹158
    cost with limit orders both ways       ₹47
    cost after allowing for the fact that
    limit orders fill at bad moments       ₹72

**The bar falls from ₹158 to ₹72 — a 2.2× reduction.** For the first time in
nineteen experiments, the edge we need (₹72) is within reach of the edges we
have actually measured (up to ₹37). Still a stretch, but no longer absurd.

**The catch, stated honestly:** we cannot yet prove limit orders would fill. Our
data only shows whether price *touched* our price, not whether we were at the
front of the queue. Our data says a limit order gets touched 88% of the time,
which is obviously not a fill rate — it just means price wobbles.

### C. Buy better market data (order book, trade prints) — **doesn't work on its own**

Better data does not make trading cheaper. It only changes what you can predict.

Order-flow signals work over seconds to minutes — exactly where costs are
worst. You would need an edge of **158% of the typical 5-minute move.** Not
difficult; impossible.

There is also no history to buy. It must be recorded going forward, so we would
wait roughly **12 months** before the first experiment could even run.

**But it is the missing piece for B.** Order-book depth and trade prints are
precisely what is needed to know whether a limit order actually fills. As a
signal source, C is dead. As the measuring instrument for B, it is the cheapest
useful thing on this list.

---

## Best next path

**Path B — limit-order execution.** It is the only one that passes the
pre-declared feasibility gate.

It is also the only one that attacks the actual problem. A and C both try to
find a bigger edge. **B makes the required edge smaller**, and the required edge
has been the binding constraint the entire time.

---

## Why

- It cuts the bar 2.2×, from ₹158 to ₹72 per ₹1,00,000 traded.
- It is the only path whose requirement lands inside the range of effects we
  have measured rather than 4–80× above it.
- It stays statistically measurable — we could still tell a real edge from luck,
  which Path A cannot.
- **It is cheap to check.** Weeks, not months, and no strategy code.

The next step is not a strategy. It is to record the order book and trade prints
for a few weeks and measure two numbers: how often a resting order actually
fills, and how much it costs us that fills arrive at bad moments.

Roughly 600 orders settles the fill rate; roughly 400 fills settles the rest.

---

## What would make us stop

**One number, declared now, before anything is built.**

Using limit orders saves ₹111 per ₹1,00,000 round trip. The saving is cancelled
if being filled at bad moments costs **₹55 per side or more**.

Our current rough estimate is ₹12 per side — comfortably survivable. But that
estimate comes from data that cannot tell a touch from a fill, so it is close to
a best case.

> **If recorded fills show a cost of ₹55 per side or worse, Path B is dead.**

And if Path B is dead, all three paths are dead. In that case the honest move is
to stop this Delta directional-trading research program rather than start a
fourth strategy family — which is what the protocol already instructs, and what
we would recommend.

---

## One-line answer

Stop trying to find a bigger edge. **The edge was never the problem — the ₹158
toll was.** Spend a few weeks proving we can pay ₹72 instead, and if we cannot,
close the program.
