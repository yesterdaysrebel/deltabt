# H-NULL-1 — Estimator audit (§3)

Audit of `hema3.paired_statistic`, the estimator behind H-EMA-3's +15.5σ claim.
Every number below is measured on a **driftless random walk with provably zero
directional information**, not on market data.

## 1. What it measures

At each signal bar, both directions are evaluated and the signal is scored
against their average:

    out_x(k) = 1 if direction x reaches +k·R_x before −1·R_x, else 0
    stat(k)  = out_d(k) − ( out_L(k) + out_S(k) ) / 2
    excess_gross_R = (1 + k) · mean(stat)

Each leg uses its **own** R: `R_L = entry − stop_long`, `R_S = stop_short − entry`.

## 2. The assumption it rests on

> For a martingale, `P(hit +kR before −1R) = 1/(1+k)` for **any** R, so the
> estimator is scale-free and stop-width asymmetry cannot bias it.

Both the author and an independent adversarial reviewer accepted this. **It is
false as an implementation-level claim**, and that is the entire failure.

## 3. The measured violation

Driftless random walk, k = 0.5, so theory says 0.6667 at every width:

| stop width | P(hit) | deviation from null |
|---|---|---|
| 0.10% | 0.6464 | **−0.0203** |
| 0.20% | 0.6527 | −0.0140 |
| 0.40% | 0.6621 | −0.0045 |
| 0.80% | 0.6658 | −0.0009 |

P(hit) is **not** scale-free. It depends on R, and the dependence is largest
exactly where R is smallest.

## 4. The false positive, reproduced on zero-signal data

| stop geometry | direction rule | excess R | t | verdict |
|---|---|---|---|---|
| symmetric 0.4% / 0.4% | random | −0.0071 | −1.75 | ok |
| symmetric 0.4% / 0.4% | wider-stop side | −0.0019 | −0.46 | ok |
| asymmetric 0.4% / 0.1% | random | −0.0075 | −2.01 | ok |
| **asymmetric 0.4% / 0.1%** | **wider-stop side** | **+0.0188** | **+5.02** | **FALSE POSITIVE** |
| asymmetric 3.2× (as measured on real data) | wider-stop side | +0.0117 | +3.07 | FALSE POSITIVE |

Two conditions are jointly required, and neither alone suffices:

1. the two legs have **different R**, and
2. the direction rule is **correlated with which leg has the larger R**.

Random direction is safe even under 4× asymmetry. Symmetric stops are safe even
under a maximally-correlated direction rule. H-EMA-3 had both: 3.2× median
asymmetry, and an EMA signal 88.2% collinear with the wider-stop side.

## 5. Attribution — two earlier explanations of mine, both falsified

I previously recorded, in the `H-EMA-3-CORRECTION` registry entry, that the
cause was the same-bar-resolves-to-STOP convention together with MARK-triggered
stops. Isolating each convention refutes that:

| convention | excess R | t | delta vs frozen |
|---|---|---|---|
| frozen: same-bar → STOP, MARK trigger | +0.0188 | +5.02 | — |
| same-bar → TARGET, MARK | +0.0188 | +5.02 | **0.0000** |
| same-bar bets EXCLUDED, MARK | +0.0188 | +5.02 | **0.0000** |
| frozen same-bar, LTP (no MARK) | +0.0168 | +4.48 | −0.0020 |

The same-bar rule contributes **nothing** — with a stop at −1R and a target at
+0.5R, a bar spanning 1.5R is essentially never observed. MARK widening explains
about **11%** of the bias. Neither is the cause.

My second hypothesis, plain bar-range discretisation, is also refuted — the sign
**flips** with bar range:

| intrabar range / R | P(hit) | deviation |
|---|---|---|
| 0.054 | 0.6350 | −0.0317 |
| 0.189 | 0.6497 | −0.0170 |
| 0.540 | 0.7218 | **+0.0552** |

and shrinking the bar range toward zero does **not** remove the bias
(−0.0351 at a 0.10% stop).

## 6. What the evidence actually supports

Two competing discretisation effects with opposite signs:

- **Path step size relative to R.** At a 0.10% stop the per-bar volatility is
  ~0.4 R, so the walk can jump clean past a barrier. This penalises the tighter
  leg and survives as bar range → 0.
- **Intrabar range relative to R.** The barrier test reads bar highs and lows,
  which reach the **nearer** barrier (+0.5R) more readily than the further one
  (−1R). This favours the tighter leg and grows with bar range.

Their balance — and therefore the **sign** of the bias — depends on geometry.
That is the durable finding: the null of `1/(1+k)` is a property of a continuous
idealisation, not of any bar-level implementation, and its violation cannot be
signed or bounded a priori.

## 7. Consequences for the framework

1. **No estimator may compare two legs with different R.** Symmetry in stop
   geometry must be structural, not argued.
2. **A theoretical null is a hypothesis about the implementation, and must be
   measured on zero-signal data before use** — §11 of the H-NULL-1 protocol.
3. **The wider-stop rule becomes a permanent regression test.** Any framework
   that scores it as PROMISING is broken.
4. Two rounds of my own attribution were wrong before this measurement. The
   lesson is not "be more careful" but "isolate each convention empirically and
   report the delta", which is what §7 of the protocol now requires.

## 8. Status of this audit

Steps 1 and 3 of the H-NULL-1 execution order are complete. The pre-registration,
manifest freeze, null library (N1–N6), geometry and execution matrices, planted-
edge generator and TRAIN/VALID passes remain.
