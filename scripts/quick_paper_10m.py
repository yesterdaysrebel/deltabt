"""Owner 2026-11-17: "run same test on paper trades" -- QUICK at 10% margin x 5x (position 0.5x the balance: $1,448 at
$2,895.60), both picks, extra margin after the fill (liquidation ~27% above), today's SL / target / 23:59 exit, on the
paper bot's own QUICK ledger (out/fade_quick_full/quick_paper.json 2026-10-29..11-12 and quick_paper_1113_1117.json
11-13..11-17, read-only from the prod fade_positions table, account QUICK) and its saved 1m candles
(traded and MARK; Delta liquidates on the mark price).

  1  PAPER AS RECORDED: each closed paper trade's own entry / exit prices, sized at $1,448.
  2  RE-SIMULATED from the candles with the ledger's own entry, stop and target (scripts/quick_margin50.py sim1 and
     sim2, which must agree; LENIENT = the paper bot's fills, STRICT = 0.05% worse entry, stop at the worse of stop and
     close +0.1%, target passed by 0.1%), INCLUDING the picks the paper bot skipped only because its $250 paper
     balance was below one contract. A trade whose MARK high reaches +27% before its exit is liquidated (-29.5%).
     The re-simulated exits must match the ledger's on the trades it closed.
Before fees, then after (taker in; maker at the target, taker otherwise).

    PYTHONPATH=. python scripts/quick_paper_10m.py      # -> out/careful/quick_paper_10m.txt
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import quick_margin50 as Q  # noqa: E402
from spike_verify import MAKER, TAKER  # noqa: E402

BAL = 2895.60
POS = 0.5 * BAL
LIQ_UP, LIQ_LOSS = 0.27, 0.295
DIR = Path("out/fade_quick_full")


def bars(sym, t0, mark=False):
    f = DIR / f"{'MARK_' if mark else ''}{sym}_{t0}.json"
    if not f.exists():
        return None
    H, L, C = (np.full(1441, np.nan) for _ in range(3))
    for b in json.load(open(f)):
        m = (int(b["time"]) - t0) // 60
        if 0 <= m < 1441:
            H[m], L[m], C[m] = float(b["high"]), float(b["low"]), float(b["close"])
    return H, L, C


def main():
    led = json.load(open(DIR / "quick_paper.json")) + json.load(open(DIR / "quick_paper_1113_1117.json"))   # + prod 11-13..11-17
    out = [__doc__.split("\n\n")[0], ""]
    w = out.append
    rec = [x for x in led if x.get("exit_reason")]
    r1 = np.array([(float(x["entry_price"]) - float(x["exit_price"])) / float(x["entry_price"]) for x in rec])
    f1 = np.array([TAKER + (MAKER if x["exit_reason"] == "TARGET" else TAKER) for x in rec])
    u = r1 * POS
    w(f"1  PAPER AS RECORDED: {len(rec)} closed trades, {int((u > 0).sum())} wins avg ${u[u > 0].mean():+.0f}, {int((u <= 0).sum())} losses avg "
      f"${u[u <= 0].mean():+.0f}; per trade ${u.mean():+.1f} before fees, ${((r1 - f1) * POS).mean():+.1f} after; total ${((r1 - f1) * POS).sum():+,.0f} after fees")
    w("")
    for fill in ("LENIENT", "STRICT"):
        strict = fill == "STRICT"
        rows, same, comp = [], 0, 0
        for x in led:
            t0 = int(pd.Timestamp(x["day"]).timestamp())
            tb, mb = bars(x["symbol"], t0), bars(x["symbol"], t0, mark=True)
            if tb is None or x.get("stop_price") is None:
                w(f"   {x['day'][:10]} {x['symbol']}: no candles or no stop recorded -- left out")
                continue
            H, L, Cl = tb
            e0 = float(x["entry_price"]) if x.get("entry_price") else Cl[0]
            e = e0 * (0.9995 if strict else 1.0)
            Q.TP = 1 - float(x["target_price"]) / e0
            sp = float(x["stop_price"])
            a = Q.sim1(H, L, Cl, e, sp, False, None, strict)
            b = Q.sim2(H, L, Cl, e, sp, False, None, strict)
            assert a[0] == b[0] and a[2] == b[2] and abs(a[1] - b[1]) < 1e-9, (x["day"], x["symbol"], a, b)
            j, ret, how = a
            mh = mb[0][1:j + 1] if mb is not None else H[1:j + 1]
            if np.nanmax(mh) >= e * (1 + LIQ_UP):
                ret, how = -LIQ_LOSS, "LIQUIDATED"
            if x.get("exit_reason"):
                comp += 1
                same += {"TARGET": "TARGET", "STOP": "STOP"}.get(x["exit_reason"], "CLOCK") == how
            fee = TAKER + (0.0 if j <= 15 else (MAKER if how == "TARGET" else TAKER))
            peak = np.nanmax(mh) / e - 1
            rows.append((x["day"][:10], x["symbol"], "paper" if x.get("exit_reason") else "SKIPPED by paper", how, ret, fee, sp / e0 - 1, peak))
        d = pd.DataFrame(rows, columns=["day", "sym", "src", "how", "ret", "fee", "stop", "peak"])
        w(f"2  RE-SIMULATED, {fill}: exits match the paper ledger on {same} of {comp} closed trades")
        for x in d.itertuples():
            w(f"   {x.day} {x.sym:12} {x.src:17} {x.how:10} {100 * x.ret:+6.2f}%  ${x.ret * POS:+5.0f}  (after fees ${(x.ret - x.fee) * POS:+5.0f})  "
              f"stop {100 * x.stop:4.1f}% above; mark high before exit {100 * x.peak:+5.1f}%")
        u = d.ret * POS
        uf = (d.ret - d.fee) * POS
        day = uf.groupby(d.day).sum()
        w(f"   -> {len(d)} trades ({int((d.src != 'paper').sum())} the paper bot skipped): win {100 * (u > 0).mean():.0f}%, avg win ${u[u > 0].mean():+.0f}, "
          f"avg loss ${u[u <= 0].mean():+.0f}; per trade ${u.mean():+.1f} before fees, ${uf.mean():+.1f} after; total after fees ${uf.sum():+,.0f}; "
          f"liquidated {int((d.how == 'LIQUIDATED').sum())}; worst day ${day.min():+.0f}, best day ${day.max():+.0f}")
        w("")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/quick_paper_10m.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
