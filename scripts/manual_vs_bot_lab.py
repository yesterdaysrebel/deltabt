"""Owner's hand trades (data/manual/round_trips.csv, gitignored) against the bot's %R signals and
the 1m path, on the coins with cached DEV candles. Idea 5 of the 2026-10-13 list.

    PYTHONPATH=. .venv/bin/python scripts/manual_vs_bot_lab.py

Three reads: (1) did a same-direction bot signal fire within 30 min before a hand entry, and did a
hand entry follow a bot signal; (2) what did the bot's own trades on these coins make on the same
days; (3) the worst adverse excursion of each hand trade on 1m LTP, so a hypothetical 1% stop can
be costed against the liquidations.
"""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "scripts")
import labkit
from deltabt import rulecore
from deltabt.harness import _resampled

SYMS = ["AKEUSD", "BANKUSD", "BEATUSD", "ETHUSD", "SOLUSD", "BLESSUSD", "XRPUSD", "DOGEUSD"]
BEFORE, AFTER = 30 * 60, 5 * 60
STOP_PCT = 1.0


def main():
    pd.set_option("display.width", 250)
    T = pd.read_csv("data/manual/round_trips.csv", parse_dates=["t_in", "t_out"])
    T = T[T.symbol.isin(SYMS)].copy(); T["ein"] = T.t_in.map(lambda x: int(x.timestamp())); T["eout"] = T.t_out.map(lambda x: int(x.timestamp()))
    lo, hi = T.ein.min() - 86400, T.eout.max() + 86400
    data = labkit.load_window("data", SYMS); bks = labkit.books(data)
    print("hand trips on these coins:", len(T), "of 369 |", {s: int((T.symbol == s).sum()) for s in SYMS})
    # (1) signals
    rows = []; cache = {}; wpr_key = None
    for s in SYMS:
        b = bks[s]; bt = b.bars.time.to_numpy("int64"); sig_t = bt + 300                          # the bot enters at the close of the signal bar
        L = sig_t[np.asarray(b.signals.long_entry, bool)]; S = sig_t[np.asarray(b.signals.short_entry, bool)]
        P, _, _ = _resampled(data[s], 5, cache); C = _resampled(data[s], 1, cache)[0]; sa = rulecore.compute(P, C, labkit.SPEC)
        if wpr_key is None:
            wpr_key = next((k for k in vars(sa.primary) if "wpr" in k.lower() or "williams" in k.lower()), None); print("primary indicator fields:", list(vars(sa.primary))[:16], "-> %R field:", wpr_key)
        wpr = np.asarray(getattr(sa.primary, wpr_key), float) if wpr_key else None
        for i, r in T[T.symbol == s].iterrows():
            arr = L if r.side == "long" else S
            k = np.searchsorted(arr, r.ein + AFTER) - 1; near = (k >= 0) and (r.ein - arr[k] <= BEFORE)
            kb = np.searchsorted(bt, r.ein, side="right") - 1                                        # bar holding the hand entry; use the PRIOR closed bar's %R
            setup = (sa.long_setup if r.side == "long" else sa.short_setup)
            rows.append(dict(idx=i, bot_signal_near=bool(near), wpr_prev_bar=float(wpr[kb - 1]) if (wpr is not None and kb >= 1) else np.nan,
                             setup_prev_bar=bool(setup[kb - 1]) if kb >= 1 else False))
        # the other way: bot signals in the owner's window with a hand entry within 30 min after
        for arr, side in ((L, "long"), (S, "short")):
            arr = arr[(arr >= lo) & (arr <= hi)]; h = T[(T.symbol == s) & (T.side == side)].ein.to_numpy()
            took = [bool(((h >= a) & (h <= a + BEFORE)).any()) for a in arr]
            rows.append(dict(sig_symbol=s, sig_side=side, sig_n=len(arr), sig_taken=int(sum(took))))
    R = pd.DataFrame([x for x in rows if "idx" in x]).set_index("idx"); T = T.join(R)
    G = pd.DataFrame([x for x in rows if "sig_symbol" in x])
    # base rate: the same test at random minutes inside the owner's active hours on the same coins
    rng = np.random.default_rng(0); base = []
    for s in SYMS:
        b = bks[s]; bt = b.bars.time.to_numpy("int64") + 300; h = T[T.symbol == s]
        if h.empty: continue
        for side in ("long", "short"):
            arr = bt[np.asarray(b.signals.long_entry if side == "long" else b.signals.short_entry, bool)]
            for e in rng.choice(h.ein.to_numpy(), size=20 * len(h)) + rng.integers(-6 * 3600, 6 * 3600, size=20 * len(h)):
                k = np.searchsorted(arr, e + AFTER) - 1; base.append((k >= 0) and (e - arr[k] <= BEFORE))
    nbars = sum(len(bks[s].bars) for s in SYMS)
    print("\n(1) raw signal bars (5m, before the engine's one-position/cooldown rules): %d of %d bars (%.1f%%)" % (G.sig_n.sum(), nbars, 100 * G.sig_n.sum() / nbars))
    print("    hand entries with a same-direction raw signal in the 30 min before: %d of %d (%.0f%%)   base rate at random nearby minutes: %.0f%%" % (T.bot_signal_near.sum(), len(T), 100 * T.bot_signal_near.mean(), 100 * np.mean(base)))
    print("    raw signals followed by a hand entry within 30 min: %d of %d" % (G.sig_taken.sum(), G.sig_n.sum()))
    print("    hand entries where the bot's %%R SETUP was true on the prior closed 5m bar: %.0f%%" % (100 * T.setup_prev_bar.mean()))
    if wpr_key:
        print("    %%R (prior closed 5m bar) at hand entries: longs median %.0f (IQR %.0f..%.0f), shorts median %.0f (IQR %.0f..%.0f)" % tuple(
            np.nanpercentile(T[T.side == "long"].wpr_prev_bar, [50, 25, 75]).tolist() + np.nanpercentile(T[T.side == "short"].wpr_prev_bar, [50, 25, 75]).tolist()))
    print("    net by signal match:"); print(T.groupby(["bot_signal_near", "liq"]).agg(n=("net", "size"), net=("net", "sum"), win=("net", lambda x: (x > 0).mean())).round(2).to_string())
    # (2) the bot's own trades on these coins, same window and same days
    bt_tr, _ = labkit.run(bks, data); w = bt_tr[(bt_tr.entry_time >= lo) & (bt_tr.entry_time <= hi)].copy()
    hand_days = set(T.t_in.dt.tz_convert("Asia/Kolkata").dt.date); w["day_ist"] = pd.to_datetime(w.entry_time, unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    w["owner_active"] = w.day_ist.isin(hand_days)
    print("\n(2) bot (hold-to-3R, %%R t3, 5m) on these 8 coins 2026-07-14..09-09: %d trades, mean %+.3fR, win %.0f%%, total %+.1fR" % (len(w), w.r_multiple.mean(), 100 * (w.r_multiple > 0).mean(), w.r_multiple.sum()))
    print(w.groupby("owner_active").r_multiple.agg(n="size", mean="mean", total="sum").round(3).to_string())
    # overlap against the bot's ACTUAL trades (after its gates); the engine stores side as +1/-1
    w["side"] = np.where(w.stop_price < w.entry_price, "long", "short"); T["bot_trade_near"] = False
    for s in SYMS:
        for side in ("long", "short"):
            arr = np.sort(w[(w.symbol == s) & (w.side == side)].entry_time.to_numpy("int64")); m = (T.symbol == s) & (T.side == side)
            for i in T[m].index:
                k = np.searchsorted(arr, T.at[i, "ein"] + AFTER) - 1; T.at[i, "bot_trade_near"] = bool((k >= 0) and (T.at[i, "ein"] - arr[k] <= BEFORE))
    h_by = {(s, sd): T[(T.symbol == s) & (T.side == sd)].ein.to_numpy() for s in SYMS for sd in ("long", "short")}
    w["hand_near"] = [bool(((h_by[(r.symbol, r.side)] >= r.entry_time - AFTER) & (h_by[(r.symbol, r.side)] <= r.entry_time + BEFORE)).any()) for r in w.itertuples()]
    print("    hand entries within 30 min after a bot TRADE entry, same coin and side: %d of %d (%.0f%%) | bot trades with a hand entry within 30 min: %d of %d" % (T.bot_trade_near.sum(), len(T), 100 * T.bot_trade_near.mean(), w.hand_near.sum(), len(w)))
    print("    bot trades the owner also took vs not:"); print(w.groupby("hand_near").r_multiple.agg(n="size", mean="mean", win=lambda x: (x > 0).mean(), total="sum").round(3).to_string())
    print(w.groupby("symbol").r_multiple.agg(n="size", mean="mean", total="sum").round(2).to_string())
    # (3) adverse excursion on 1m LTP
    mae = []
    for i, r in T.iterrows():
        l = data[r.symbol]["ltp"]; lt = l.time.to_numpy("int64"); a, b = np.searchsorted(lt, r.ein), np.searchsorted(lt, r.eout, side="right")
        if b <= a: mae.append(np.nan); continue
        seg = l.iloc[a:b]; mae.append(100 * ((r.px_in - seg.low.min()) / r.px_in if r.side == "long" else (seg.high.max() - r.px_in) / r.px_in))
    T["mae_pct"] = mae; C = T[~T.liq]
    print("\n(3) worst adverse excursion (1m LTP) during the trade:")
    for nm, g in (("clean winners", C[C.net > 0]), ("clean losers", C[C.net <= 0]), ("liquidated", T[T.liq])):
        q = np.nanpercentile(g.mae_pct, [25, 50, 75, 90]); print("    %-14s n %3d  MAE%% 25/50/75/90: %.2f / %.2f / %.2f / %.2f  share > %.0f%%: %.0f%%" % (nm, len(g), *q, STOP_PCT, 100 * np.nanmean(g.mae_pct > STOP_PCT)))
    hit = T.mae_pct > STOP_PCT; hyp = np.where(hit, -STOP_PCT / 100 * T.notional_in - T.fees, T.net)
    print("    what-if: every trade carried a %.0f%% stop (fills at the stop, no re-entry): actual net $%.0f -> $%.0f; liquidated trips alone $%.0f -> $%.0f; clean trips $%.0f -> $%.0f" % (
        STOP_PCT, T.net.sum(), hyp.sum(), T[T.liq].net.sum(), hyp[T.liq.to_numpy()].sum(), C.net.sum(), hyp[~T.liq.to_numpy()].sum()))
    T.to_csv("data/manual/round_trips_vs_bot.csv", index=False)


if __name__ == "__main__":
    main()
