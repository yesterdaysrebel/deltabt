"""Hand-built price paths with the answer worked out by hand from docs/careful_sim_spec.md; all three
implementations must give it. Coin 'TESTUSD' is not in Delta's table -> default terms (20x to $50k).

    PYTHONPATH=. python scripts/careful_scenarios.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_sim_a as A  # noqa: E402
import careful_sim_b as B  # noqa: E402
import careful_sim_c as Cc  # noqa: E402

N = 2881
DAY = pd.Timestamp("2025-03-03")


def path(points):
    """points: {minute: (high, low, close)}; other minutes no trade (close carried)."""
    H, L, Cl = np.full(N, -np.inf), np.full(N, np.inf), np.full(N, np.nan)
    for k, (h, l, c) in points.items():
        H[k], L[k], Cl[k] = h, l, c
    return H, L, pd.Series(Cl).ffill().to_numpy()


def hand_notional(margin):            # default terms: N (5% + 0.12%) = margin below $50k; above: r N^2 + b N = margin
    a = 0.0512
    n = margin / a
    if n > 50000:
        r = 1e-07
        b = a - r * 50000
        n = (-b + (b * b + 4 * r * margin) ** 0.5) / (2 * r)
    return n


def check(name, points, want, variant="run4", lenient=False, start=D.START):
    H, L, Cl = path(points)
    days = [(DAY, [(1, "TESTUSD", H, L, Cl)])]
    D.START, keep = start, D.START
    outs = [M.run(days, variant, lenient)[0] for M in (A, B, Cc)]
    D.START = keep
    for got, nm in zip(outs, "ABC"):
        assert len(got) == len(want), (name, nm, got)
        for g, w in zip(got, want):
            assert g[2:6] == w[:4], (name, nm, g, w)
            assert abs(g[6] - w[4]) < 0.01, (name, nm, g[6], w[4])
    print(f"  PASS {name}: " + "; ".join(f"{w[0]} {w[3]} {w[4]:+,.2f}" for w in want))


def main():
    S0 = 3184.0
    m, x = 0.49 * S0, 0.25 * S0
    n = hand_notional(m)                                            # ~ $30,473 position at ~19.5x
    e = 1.05
    pnl = lambda px: max(n * (-(px / e - 1) - 0.0002 - 0.0005 - 0.0005 * px / e), -(m + x))  # noqa: E731
    T = e * (1 - (500 / n + 0.0012))
    K = e * (1 - (200 / n + 0.0012))
    S = e * 1.0225
    base = {2: (1.0, 1.0, 1.0)}
    print(f"hand: position ${n:,.2f}; target {T:.6f}; lock {K:.6f}; stop {S:.6f}")
    # 1 fill at minute 10 (high 1.051 >= 1.05 x 1.0005), target at minute 20
    check("target", {**base, 10: (1.051, 1.04, 1.045), 20: (1.03, T * 0.999, 1.03)},
          [("sell", 10, 20, "TARGET", n * (-(T / e - 1) - 0.0002 - 0.0005 - 0.0005 * T / e))])
    # 2 no fill: high 1.0504 < 1.05 x 1.0005
    check("no fill", {**base, 10: (1.0504, 1.0, 1.0)}, [])
    # 3 stop, STRICT: high reaches the stop, close above it -> fill at the close x 1.001
    check("stop strict", {**base, 10: (1.051, 1.049, 1.05), 15: (S + 0.01, 1.05, S + 0.005)},
          [("sell", 10, 15, "STOP", pnl((S + 0.005) * 1.001))])
    check("stop lenient", {**base, 10: (1.051, 1.049, 1.05), 15: (S + 0.01, 1.05, S + 0.005)},
          [("sell", 10, 15, "STOP", pnl(S * 1.001))], lenient=True)
    # 4 liquidation: the minute closes past entry x (1 + 2.5% + 25%/~19.5x... = extra/N)
    Q = e * (1 + 0.025 + x / n)
    check("liquidation", {**base, 10: (1.051, 1.049, 1.05), 15: (Q * 1.01, 1.05, Q * 1.005)},
          [("sell", 10, 15, "LIQ", -(m + x))])
    # 5 lock: low -1% at minute 20 closing above the lock price -> out at the close x 1.001 (LOCKED)
    check("lock at market", {**base, 10: (1.051, 1.049, 1.05), 20: (1.05, e * 0.99, K + 0.002)},
          [("sell", 10, 20, "LOCKED", pnl((K + 0.002) * 1.001))])
    # 6 lock moved (close under it), then the trail: low falls to 1.031, trail = 1.031 x 1.008; minute 40 rises
    #   through it -> STRICT fill at the worse of it and the close
    lowp = 1.031
    tr = lowp * 1.008
    check("lock then trail", {**base, 10: (1.051, 1.049, 1.05), 20: (1.04, e * 0.99, 1.0390), 30: (1.036, lowp, 1.032),
                               40: (tr + 0.001, 1.032, tr - 0.0005)},
          [("sell", 10, 40, "LOCKED", pnl(tr * 1.001))])
    # 7 24 h exit: filled at minute 10, nothing else, out at minute 1450 at the close x 1.001
    check("24h", {**base, 10: (1.051, 1.049, 1.05), 1450: (1.049, 1.048, 1.0485)},
          [("sell", 10, 1450, "24h", pnl(1.0485 * 1.001))])
    # 8 re-sell: stopped at 15 (high 1.08, close 1.075); top 1.10 at 30; low 1.04 at 60 <= 0.95 x 1.10 = 1.045
    #   -> re-sell at min(1.045, close 1.042) x 0.999; nothing more -> TIME at minute 15 + 1440
    b1 = S0 + pnl(1.075 * 1.001)
    p = 1.042 * 0.999
    n2 = hand_notional(0.49 * b1)
    tpx = 1.02
    re_pnl = n2 * (-(tpx * 1.001 / p - 1) - 0.0005 - 0.0005 * tpx * 1.001 / p)
    check("re-sell", {**base, 10: (1.051, 1.049, 1.05), 15: (1.08, 1.05, 1.075), 30: (1.10, 1.07, 1.09),
                      60: (1.06, 1.04, 1.042), 1455: (1.021, 1.019, tpx)},
          [("sell", 10, 15, "STOP", pnl(1.075 * 1.001)), ("resell", 60, 1455, "TIME", re_pnl)], variant="run4+resell")
    # 9 re-sell not taken when the stop was the month's 2nd (here: start a fresh month counter -> 1st, so taken);
    #   and not taken with no re-sell variant
    check("no re-sell in run4", {**base, 10: (1.051, 1.049, 1.05), 15: (1.08, 1.05, 1.075), 60: (1.06, 1.04, 1.042)},
          [("sell", 10, 15, "STOP", pnl(1.075 * 1.001))])
    # 10 stop and target in one minute -> stop
    check("stop before target", {**base, 10: (1.051, 1.049, 1.05), 15: (S + 0.001, T * 0.99, S)},
          [("sell", 10, 15, "STOP", pnl(S * 1.001))])
    print("\nALL SCENARIOS PASS in A, B and C.")


if __name__ == "__main__":
    main()
