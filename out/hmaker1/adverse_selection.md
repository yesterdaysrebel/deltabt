# H-MAKER-1 — ADVERSE SELECTION  (Q2)

> What is the economic adverse selection of those actual fills?

## Definition (frozen before collection)

    signed_markout(h) = side * (mid(fill+h) - mid(fill)) / mid(fill) * 10_000
    adverse_selection(h) = -signed_markout(h)

A passive BUY followed by a decline, or a passive SELL followed by a rise,
gives **positive** adverse selection — the fill hurt us. Markout is against
the **mid**, not our fill price: measuring against our own price would
credit us the half-spread we earned by resting, which the 4.72 bps fee
arithmetic already counts.

## Conservative queue bound

| horizon | adverse (bps) | 95% CI | fills | clusters | MDE | cluster t |
|---|---:|---|---:|---:|---:|---:|
| +1m **(PRIMARY)** | +0.221 | [-0.231, +0.673] | 190 | 73 | 0.646 | +0.96 |
| +5m | +0.646 | [-0.307, +1.599] | 187 | 70 | 1.361 | +1.33 |
| +15m | -1.138 | [-2.871, +0.596] | 175 | 65 | 2.477 | -1.29 |

## Optimistic queue bound

| horizon | adverse (bps) | 95% CI | fills | clusters | MDE | cluster t |
|---|---:|---|---:|---:|---:|---:|
| +1m **(PRIMARY)** | +0.221 | [-0.231, +0.673] | 190 | 73 | 0.646 | +0.96 |
| +5m | +0.646 | [-0.307, +1.599] | 187 | 70 | 1.361 | +1.33 |
| +15m | -1.138 | [-2.871, +0.596] | 175 | 65 | 2.477 | -1.29 |

## Against the frozen kill threshold of 5.54 bps

| bound | adverse @ +1m | 95% CI upper | vs threshold |
|---|---:|---:|---|
| conservative | +0.221 | +0.673 | **below** |
| optimistic | +0.221 | +0.673 | **below** |

## Per symbol (conservative, primary horizon)

| symbol | fills | adverse @ +1m | 95% CI |
|---|---:|---:|---|
| BTCUSD | 51 | +0.761 | [+0.146, +1.375] |
| ETHUSD | 65 | +0.095 | [-0.773, +0.963] |
| SOLUSD | 53 | +0.295 | [-0.572, +1.161] |
| XRPUSD | 21 | -0.884 | [-2.111, +0.342] |
