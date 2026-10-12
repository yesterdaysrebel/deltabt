"""Idea 6: trade a coin only after a win on it (2026-10-13, docs/ideas_1_to_4_prereg.md).

    PYTHONPATH=. .venv/bin/python scripts/win_follow_lab.py
"""
import gc, sys
import numpy as np, pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "scripts")
import labkit as K

PROD = ["BEATUSD", "AKEUSD", "BANKUSD"]


def label(t):
    """real/virtual at entry: real iff the coin's last CLOSED trade (before this entry) was a win."""
    t = t.sort_values("entry_time").reset_index(drop=True); real = np.ones(len(t), bool); prev = np.full(len(t), np.nan)
    for s, idx in t.groupby("symbol").indices.items():
        last_win, last_exit = None, -1
        for i in idx:
            if last_win is not None and last_exit <= t.entry_time.iat[i]:
                real[i] = last_win; prev[i] = last_win
            last_win, last_exit = bool(t.r_multiple.iat[i] > 0), int(t.exit_time.iat[i])
    t["real"] = real; t["prev_win"] = prev
    return t


def report(win, t, judged):
    t = label(t); R, V = t[t.real], t[~t.real]
    print(f"\n  {win}")
    print(K.fmt("all trades", t)); print(K.fmt("REAL (after a win / first)", R, f"  share {100 * len(R) / len(t):.0f}%")); print(K.fmt("virtual (after a loss)", V))
    p = K.day_boot_p(R, V)
    aw, al = t[t.prev_win == 1], t[t.prev_win == 0]
    print(f"    after a win {aw.r_multiple.mean():+.3f}R win {100 * (aw.r_multiple > 0).mean():.1f}% (n {len(aw)}) | after a loss "
          f"{al.r_multiple.mean():+.3f}R win {100 * (al.r_multiple > 0).mean():.1f}% (n {len(al)}) | real beats virtual p {p:.3f}")
    return p, R.r_multiple.mean()


if __name__ == "__main__":
    out = {}
    for win, root in K.WINDOWS.items():
        data = K.load_window(root, K.SCREEN); bks = K.books(data)
        print(f"\n==== {win}: {len(data)} coins ====")
        th, _ = K.run(bks, data, "hold-to-3R"); tt, _ = K.run(bks, data, "trail")
        print("  hold-to-3R (judged)"); out[win] = report(win, th, True)
        print("  trail (reported)"); report(win, tt, False)
        if win.startswith("DEV"):
            pr = {s: bks[s] for s in PROD}; tp, _ = K.run(pr, data, "hold-to-3R"); print("  prod 3 coins, hold-to-3R (reported)"); report(win, tp, False)
        del data, bks; gc.collect()
    (p1, _), (p2, m2) = out.values()
    W1, W2, W3 = p1 <= 0.05, p2 <= 0.05, m2 > 0
    print(f"\n  W1 DEV p {p1:.3f}: {'PASS' if W1 else 'fail'}   W2 OLD p {p2:.3f}: {'PASS' if W2 else 'fail'}   W3 OLD real net {m2:+.3f}R > 0: {'PASS' if W3 else 'fail'}")
    print(f"\nIDEA 6: {'PASS' if W1 and W2 and W3 else 'FAIL'}")
