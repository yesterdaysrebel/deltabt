"""How much of Spike Sell's loss is the stop-losses, and what they are made of (owner 2026-11-15: "huge stops is the
real pain point"). Every trade the rule takes (out/careful/trades_every_*.csv, fixed $3,184 size).

    PYTHONPATH=. python scripts/careful_stop_pain.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402

days = {(d.date(), s): (H, L, Cl) for d, ps in D.load() for r, s, H, L, Cl in ps}
for fill in ("strict", "lenient"):
    t = pd.read_csv(f"out/careful/trades_every_{fill}.csv", parse_dates=["day"])
    st = t[t.exit.isin(["STOP", "LIQ"])]
    win = t[~t.exit.isin(["STOP", "LIQ"])]
    planned = st.position_usd * (0.0225 * 1.001 + 0.001 + 0.0012 - 0.0005)   # stop distance + exit slip + fees (approx)
    print(f"{fill.upper()}: {len(t)} trades, total ${t.pnl.sum():+,.0f}")
    print(f"  stop-losses {len(st)} ({len(st) / len(t):.0%}): total ${st.pnl.sum():+,.0f}, average ${st.pnl.mean():+,.0f}, "
          f"worst ${st.pnl.min():+,.0f}")
    print(f"  everything else {len(win)}: total ${win.pnl.sum():+,.0f}, average ${win.pnl.mean():+,.0f} "
          f"(targets {int((t.exit == 'TARGET').sum())} avg ${t[t.exit == 'TARGET'].pnl.mean():+,.0f}, "
          f"locked {int((t.exit == 'LOCKED').sum())} avg ${t[t.exit == 'LOCKED'].pnl.mean():+,.0f})")
    gap = st.exit_price / st.stop - 1
    print(f"  stop fill vs the stop price: median {gap.median():+.2%}, mean {gap.mean():+.2%}, over +0.5% in {(gap > .005).mean():.0%}")
    # wicks: did the price come back under the stop soon after?
    back = []
    for r in st.itertuples():
        H, L, Cl = days[(r.day.date(), r.coin)]
        j = int(r.minutes_held) + int(pd.Timestamp(r.fill_utc).hour * 60 + pd.Timestamp(r.fill_utc).minute)
        nxt = Cl[j + 1:j + 31]
        back.append((nxt < r.stop).any() if len(nxt) else False)
        r_ = nxt
    back = np.array(back)
    print(f"  stopped then back under the stop within 30 min: {back.mean():.0%} of stops; "
          f"minutes from fill to stop: median {st.minutes_held.median():.0f}, under 5 min {(st.minutes_held < 5).mean():.0%}")
    print(f"  without the stops' slippage past the stop (fill AT the stop) the stops would total about "
          f"${-planned.sum():+,.0f} vs ${st.pnl.sum():+,.0f}\n")
