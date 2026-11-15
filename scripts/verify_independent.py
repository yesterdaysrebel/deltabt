"""An INDEPENDENT re-implementation of the owner's version, written from the rule text alone (owner 2026-11-15: "before
that make sure numbers are accurate"). It shares only the data loaders and Delta's product table with
scripts/clean_compare.py -- no trade logic -- and compares every trade with clean_compare.short.

RULE (owner's version): spike entry: a resting limit sell 5% above the 00:03 close, valid until 24:00 UTC, fills when a
1m high trades 0.05% through it; 50% of the balance as isolated margin at 20x (Delta per-coin limits); take-profit
where the trade nets +$500 after costs; stop +2.25% from the fill; when a 1m low first reaches -1% from the fill the
stop moves (at that minute's close) to the price netting +$200 -- if the close is already above that price the stop
fires at once at the close; out 24 h after the fill. Costs: entry maker 0.02% + 0.05% slippage; exit taker 0.05%; the
exit slippage is the +0.1% on every market exit (a triggered stop fills at the worse of its price and the minute's
close, +0.1% = STRICT; the 24 h exit and an immediate lock fire at the close +0.1%); the take-profit fills at its price. Liquidation at
entry x (1 + IM - MM); a fill at/after it loses the margin. Stop and target in one minute = stop.

    PYTHONPATH=. python scripts/verify_independent.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402  (data loaders and the reference only)

PRODUCTS = json.load(open("out/delta_margin/product_limits_2026-11-15.json"))
IM_DEFAULT, MM_DEFAULT, CAP_DEFAULT = 0.05, 0.025, 50_000.0
MAKER_FEE, TAKER_FEE, HALF_SPREAD = 0.0002, 0.0005, 0.0005


def position_and_liquidation(symbol: str, margin_usd: float, price: float) -> tuple[float, float]:
    """Delta: order margin = notional x (IM + 0.12% fee reserve); IM rises by `ims` per $ above max_leverage_notional;
    position capped by the contract limit. Liquidation distance = IM - MM of the resulting notional."""
    # a coin missing from today's product table (delisted since) gets Delta's usual terms: 20x up to $50,000 of
    # position, then 0.001% more initial margin per $ (fixed 2026-11-15: it had no cap here, the reference had one)
    p = PRODUCTS.get(symbol) or dict(im=5.0, mm=2.5, ims=1e-05, mms=5e-06, max_lev_notional=CAP_DEFAULT, limit=10 ** 12, cv=1.0)
    im0, mm0, ims, mms, cap = p["im"] / 100, p["mm"] / 100, p["ims"] / 100, p["mms"] / 100, p["max_lev_notional"]
    # solve notional x (im0 + ims x max(0, notional - cap) + 0.0012) = margin by bisection
    lo_n, hi_n = 0.0, margin_usd / (im0 + 0.0012)
    for _ in range(80):
        mid = (lo_n + hi_n) / 2
        need = mid * (im0 + ims * max(0.0, mid - cap) + 0.0012)
        if need <= margin_usd:
            lo_n = mid
        else:
            hi_n = mid
    notional = min(lo_n, p["limit"] * p["cv"] * price)
    over = max(0.0, notional - cap)
    return notional, (im0 + ims * over) - (mm0 + mms * over)


def trade(symbol, highs, lows, closes, k_decide, margin_usd, extra_usd=0.0, target_usd=500.0, lock_usd=200.0, trail=None):
    ref = closes[k_decide - 1]
    limit = ref * 1.05
    fill_min = None
    for k in range(k_decide, min(len(closes), 1440)):
        if highs[k] >= limit * 1.0005:
            fill_min = k
            break
    if fill_min is None:
        return None
    entry = limit
    notional, liq_dist = position_and_liquidation(symbol, margin_usd, entry)
    liq_dist += extra_usd / notional            # option A: extra isolated margin from the fill pushes liquidation out
    lost_if_liquidated = margin_usd + extra_usd
    in_cost = MAKER_FEE + HALF_SPREAD

    def net(exit_price):
        gross = notional * (entry - exit_price) / entry
        fees = notional * in_cost + notional * (exit_price / entry) * TAKER_FEE
        return max(gross - fees, -lost_if_liquidated)

    round_trip = in_cost + TAKER_FEE
    target_price = entry * (1 - (target_usd / notional + round_trip))
    stop_price = entry * 1.0225
    liq_price = entry * (1 + liq_dist)
    lock_price = entry * (1 - (lock_usd / notional + round_trip))
    locked = False
    lowest = entry
    last = min(len(closes) - 1, fill_min + 1440)
    for k in range(fill_min + 1, last + 1):
        if highs[k] >= stop_price:
            fill = max(stop_price, closes[k]) * 1.001
            if fill >= liq_price:
                return "LIQ", -lost_if_liquidated, fill_min, k
            return ("LOCKED" if locked else "STOP"), net(fill), fill_min, k
        if lows[k] <= target_price * 0.999:
            gross = notional * (entry - target_price) / entry
            fees = notional * in_cost + notional * (target_price / entry) * TAKER_FEE
            return "TARGET", gross - fees, fill_min, k
        lowest = min(lowest, lows[k])
        if not locked and lows[k] <= entry * 0.99:
            locked = True
            stop_price = lock_price
            if closes[k] >= lock_price:
                return "LOCKED", net(closes[k] * 1.001), fill_min, k
        if trail is not None and locked:
            trailing_level = lowest * (1 + trail)
            if trailing_level < stop_price:
                stop_price = trailing_level
                if closes[k] >= stop_price:
                    return "LOCKED", net(closes[k] * 1.001), fill_min, k
    return "24h", net(closes[last] * 1.001), fill_min, last


def main() -> None:
    C.checks()
    hist, *_ = C.paths_history()
    real = C.paths_real()
    margin = 0.5 * C.START
    extra = 0.25 * C.START if "--extra" in sys.argv else 0.0
    print(f"extra margin after the fill: ${extra:,.2f}\n")
    for label, paths in (("HISTORY (1,022 picks)", hist), ("REAL PICKS (20)", real)):
        same = diff = 0
        worst = []
        a_tot = b_tot = 0.0
        kinds_a, kinds_b = {}, {}
        for day, sym, hi, lo, cl, k0 in paths:
            a = C.short(sym, hi, lo, cl, k0, margin, 500, "spike", 200, 0.0225, None, None, extra)
            b = trade(sym, hi, lo, cl, k0, margin, extra)
            if a is None and b is None:
                continue
            if (a is None) != (b is None):
                diff += 1
                worst.append((day.date(), sym, a, b))
                continue
            a_tot += a[1]
            b_tot += b[1]
            kinds_a[a[0]] = kinds_a.get(a[0], 0) + 1
            kinds_b[b[0]] = kinds_b.get(b[0], 0) + 1
            if a[0] == b[0] and a[2] == b[2] and a[3] == b[3] and abs(a[1] - b[1]) < 0.05:
                same += 1
            else:
                diff += 1
                worst.append((day.date(), sym, a, b))
        print(f"{label}: trades {same + diff}, IDENTICAL {same}, different {diff}")
        print(f"   reference  outcomes {dict(sorted(kinds_a.items()))} total ${a_tot:+,.2f}")
        print(f"   independent outcomes {dict(sorted(kinds_b.items()))} total ${b_tot:+,.2f}")
        for w in worst[:10]:
            print("   DIFF", w)
        print()


def main_final() -> None:
    """The FINAL rules (49% margin, 25% extra, trail 0.8%) per trade on a fixed $3,184, vs scripts/run4_trailing.short."""
    import run4_trailing as T
    C.checks()
    hist, *_ = C.paths_history()
    real = C.paths_real()
    margin, extra = 0.49 * C.START, 0.25 * C.START
    for label, paths in (("HISTORY (1,022 picks)", hist), ("REAL PICKS (20)", real)):
        same = diff = 0
        ta = tb = 0.0
        bad = []
        for day, sym, hi, lo, cl, k0 in paths:
            a = T.short(sym, hi, lo, cl, k0, margin, 500.0, 200.0, extra, False, 0.008, None)
            b = trade(sym, hi, lo, cl, k0, margin, extra, 500.0, 200.0, 0.008)
            if a is None and b is None:
                continue
            if (a is None) != (b is None) or a[0] != b[0] or a[3] != b[3] or abs(a[1] - b[1]) >= 0.05:
                diff += 1
                bad.append((day.date(), sym, a, b))
                continue
            same += 1
            ta += a[1]
            tb += b[1]
        print(f"FINAL RULES {label}: trades {same + diff}, IDENTICAL {same}, different {diff}; totals ${ta:+,.2f} vs ${tb:+,.2f}")
        for x in bad[:8]:
            print("   DIFF", x)


if __name__ == "__main__":
    main_final() if "--final" in sys.argv else main()
