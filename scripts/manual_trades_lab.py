"""Round trips from the owner's Delta order-history export (data/manual/, gitignored, never committed).

    PYTHONPATH=. .venv/bin/python scripts/manual_trades_lab.py            # builds data/manual/round_trips.csv, prints the cut

A fill is any row whose "Filled/Remaining" filled part is > 0 -- CANCELLED orders carry partial fills
too (e.g. a reduce-only order filled 150 of 280 then cancelled), and dropping them breaks the chain. A liquidation fill is a closed limit order carrying
a machine-generated Stop Price (>= 5 decimals; the owner's stop prices are round). Realised P&L on
the export is gross of fees; fees are a separate column. Times are IST as exported.
"""
import sys
import numpy as np, pandas as pd

SRC = "data/manual/delta_order_history_2026-07-15_09-08.csv"


def ndec(x):
    if pd.isna(x):
        return 0
    s = f"{x:.8f}".rstrip("0"); return len(s.split(".")[1]) if "." in s else 0


def load():
    d = pd.read_csv(SRC); d.columns = [c.strip() for c in d.columns]
    d["t"] = pd.to_datetime(d["Time"].str.replace(" IST Asia/Kolkata", "", regex=False), utc=True).dt.tz_convert("Asia/Kolkata")
    for c in ["Qty", "Exec.Price", "Order Price", "Stop Price", "Order Value", "Trading Fees", "Cashflow", "Realised P&L"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    fr = d["Filled/Remaining"].str.split("/", expand=True); d["filled"] = pd.to_numeric(fr[0], errors="coerce").fillna(0.0)
    d["liqfill"] = (d.filled > 0) & (d["Order Type"] == "limit_order") & (d["Stop Price"].map(ndec) >= 5)
    cs = (d["Order Value"] / (d.filled * d["Exec.Price"])).groupby(d.Contract).median(); d["csize"] = d.Contract.map(cs)
    d["notional"] = d.filled * d["Exec.Price"] * d.csize                      # Order Value is blank on partially filled cancelled rows
    return d.sort_values("t").reset_index(drop=True)


def trips(d):
    f = d[d.filled > 0].copy(); f["sq"] = np.where(f.Side == "buy", 1, -1) * f.filled
    so = d[(d["Order Type"] == "market_order") & d["Stop Price"].notna()]
    out = []
    for sym, g in f.groupby("Contract", sort=False):
        pos, cur = 0.0, None
        for i, r in g.sort_values("t").iterrows():
            if abs(pos) < 1e-9:
                cur = dict(symbol=sym, side="long" if r.sq > 0 else "short", t_in=r.t, first_pnl=r["Realised P&L"], liq=False, pnl=0.0, fees=0.0,
                           notional_in=0.0, qty_in=0.0, px_num=0.0, max_pos=0.0, adds=0, n_fills=0, px_out_num=0.0, qty_out=0.0, exit_mkt=0, exit_lim=0, csize=r.csize)
            cur["n_fills"] += 1; cur["fees"] += r["Trading Fees"]; cur["pnl"] += 0.0 if pd.isna(r["Realised P&L"]) else r["Realised P&L"]
            if (r.sq > 0) == (cur["side"] == "long"):
                cur["notional_in"] += r.notional; cur["qty_in"] += r.filled; cur["px_num"] += r.filled * r["Exec.Price"]
                if abs(pos) > 1e-9: cur["adds"] += 1
            else:
                cur["liq"] |= bool(r.liqfill); cur["px_out_num"] += r.filled * r["Exec.Price"]; cur["qty_out"] += r.filled
                cur["exit_mkt" if r["Order Type"] == "market_order" else "exit_lim"] += 1
            pos += r.sq; cur["max_pos"] = max(cur["max_pos"], abs(pos))
            if abs(pos) < 1e-9:
                cur["t_out"] = r.t; cur["open"] = False; out.append(cur); cur = None
        if cur is not None:
            cur["t_out"] = pd.NaT; cur["open"] = True; cur["residual"] = pos; out.append(cur)
    T = pd.DataFrame(out); T["px_in"] = T.px_num / T.qty_in; T["px_out"] = T.px_out_num / T.qty_out.replace(0, np.nan)
    T["net"] = T.pnl - T.fees; T["open"] = T.open.astype(bool); T["liq"] = T.liq.astype(bool)
    T["hold_min"] = (T.t_out - T.t_in).dt.total_seconds() / 60
    T["adverse_pct"] = np.where(T.side == "long", (T.px_in - T.px_out) / T.px_in, (T.px_out - T.px_in) / T.px_in) * 100
    T["ret_pct"] = 100 * T.pnl / T.notional_in
    T["pnl_recomputed"] = np.where(T.side == "long", T.px_out - T.px_in, T.px_in - T.px_out) * T.qty_in * T.csize

    def stops_for(r):
        w = so[(so.Contract == r.symbol) & (so.t >= r.t_in - pd.Timedelta("1min")) & (so.t <= (r.t_out if pd.notna(r.t_out) else so.t.max()))]
        if r.side == "long": sl = w[(w.Side == "sell") & (w["Stop Price"] < r.px_in)]; tp = w[(w.Side == "sell") & (w["Stop Price"] >= r.px_in)]
        else: sl = w[(w.Side == "buy") & (w["Stop Price"] > r.px_in)]; tp = w[(w.Side == "buy") & (w["Stop Price"] <= r.px_in)]
        return pd.Series(dict(n_sl=len(sl), n_tp=len(tp), sl_pct=np.nan if sl.empty else 100 * abs(sl["Stop Price"].iloc[0] - r.px_in) / r.px_in,
                              tp_pct=np.nan if tp.empty else 100 * abs(tp["Stop Price"].iloc[0] - r.px_in) / r.px_in))
    T = pd.concat([T, T.apply(stops_for, axis=1)], axis=1).sort_values("t_in").reset_index(drop=True)
    T["hour"] = T.t_in.dt.hour; T["dow"] = T.t_in.dt.day_name().str[:3]; T["date"] = T.t_in.dt.date
    return T.drop(columns=["px_num", "px_out_num"])


if __name__ == "__main__":
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 300)
    d = load(); T = trips(d); T.to_csv("data/manual/round_trips.csv", index=False)
    f = d[d.filled > 0]
    print("fills", len(f), " of which on cancelled orders:", int((f.Status == "cancelled").sum()), " sum filled notional", round(f.notional.sum()))
    print("trips", len(T), "closed", int((~T.open).sum()), "open-at-end", int(T.open.sum()), "| opening fills with nonzero P&L (carried over):", int((T.first_pnl.fillna(0) != 0).sum()))
    if T.open.any(): print(T[T.open][["symbol", "side", "t_in", "notional_in", "pnl", "fees", "n_fills", "residual"]].to_string(index=False))
    C = T[~T.open].copy()
    err = (C.pnl - C.pnl_recomputed); print("hand check: P&L recomputed from avg prices vs export: median abs diff $%.2f, 90%% $%.2f, max $%.2f" % (err.abs().median(), err.abs().quantile(.9), err.abs().max()))
    print("\n=== closed trips: clean vs liquidated ===")
    print(C.groupby("liq").agg(n=("net", "size"), gross=("pnl", "sum"), fees=("fees", "sum"), net=("net", "sum"), mean_net=("net", "mean"), win=("net", lambda x: (x > 0).mean()),
                               med_hold_min=("hold_min", "median"), med_notional=("notional_in", "median"), med_adverse=("adverse_pct", "median"),
                               has_sl=("n_sl", lambda x: (x > 0).mean()), has_tp=("n_tp", lambda x: (x > 0).mean())).round(2).to_string())
    cl = C[~C.liq]; print("clean: wins", int((cl.net > 0).sum()), "losses", int((cl.net <= 0).sum()), "avg win $%.0f avg loss $%.0f, median ret%% win %.2f loss %.2f" % (cl[cl.net > 0].net.mean(), cl[cl.net <= 0].net.mean(), cl[cl.net > 0].ret_pct.median(), cl[cl.net <= 0].ret_pct.median()))
    print("SL distance %% where set:", C.sl_pct.describe()[["count", "25%", "50%", "75%"]].round(2).to_dict(), " TP:", C.tp_pct.describe()[["count", "25%", "50%", "75%"]].round(2).to_dict())
    print("adverse %% at liquidation:", C[C.liq].adverse_pct.describe()[["min", "25%", "50%", "75%", "max"]].round(2).to_dict(), "| liquidated trips with a SL order:", int((C.liq & (C.n_sl > 0)).sum()), "of", int(C.liq.sum()))
    for k in ["hour", "dow"]:
        print(f"\n=== by {k} ==="); print(C.groupby(k).agg(n=("net", "size"), net=("net", "sum"), liq=("liq", "sum"), win=("net", lambda x: (x > 0).mean())).round(2).T.to_string())
    print("\n=== by week ==="); print(C.assign(wk=C.t_in.dt.tz_localize(None).dt.to_period("W").astype(str)).groupby("wk").agg(n=("net", "size"), net=("net", "sum"), liq=("liq", "sum"), med_notional=("notional_in", "median")).round(1).to_string())
    b = pd.cut(C.hold_min, [0, 2, 5, 10, 30, 60, 240, 1e9], labels=["<2m", "2-5m", "5-10m", "10-30m", "30-60m", "1-4h", ">4h"])
    print("\n=== hold ==="); print(C.groupby(b, observed=True).agg(n=("net", "size"), net=("net", "sum"), liq=("liq", "sum"), win=("net", lambda x: (x > 0).mean())).round(2).T.to_string())
    print("\n=== adds ==="); print(C.groupby(C.adds.clip(upper=3)).agg(n=("net", "size"), net=("net", "sum"), liq=("liq", "sum"), liq_rate=("liq", "mean")).round(2).T.to_string())
    print("\n=== by coin ==="); print(C.groupby("symbol").agg(n=("net", "size"), net=("net", "sum"), liq=("liq", "sum"), med_notional=("notional_in", "median")).round(0).sort_values("net").to_string())
    C = C.reset_index(drop=True); C["prev_net"] = C.net.shift(1); C["prev_liq"] = C.liq.shift(1); C["gap_min"] = (C.t_in - C.t_out.shift(1)).dt.total_seconds() / 60; C["size_ratio"] = C.notional_in / C.notional_in.shift(1)
    L, N = C[C.liq], C[~C.liq]
    print("\n=== what preceded a liquidation (liq vs clean) ===")
    print("prev trade lost: %.0f%% vs %.0f%% | prev was liquidated: %.0f%% vs %.0f%% | median gap since prev exit: %.1f vs %.1f min | notional/prev: %.2f vs %.2f" % (
        100 * (L.prev_net < 0).mean(), 100 * (N.prev_net < 0).mean(), 100 * (L.prev_liq == True).mean(), 100 * (N.prev_liq == True).mean(), L.gap_min.median(), N.gap_min.median(), L.size_ratio.median(), N.size_ratio.median()))
    print("trade right after a liquidation: n", int((C.prev_liq == True).sum()), "net $%.0f, liquidated again %d" % (C[C.prev_liq == True].net.sum(), C[C.prev_liq == True].liq.sum()))
    dd = C.groupby("date").net.sum(); print("\nactive days", len(dd), "| trades/day median %.0f max %d | daily net mean $%.0f worst $%.0f (%s) best $%.0f (%s) | losing days %d/%d" % (C.groupby("date").size().median(), C.groupby("date").size().max(), dd.mean(), dd.min(), dd.idxmin(), dd.max(), dd.idxmax(), (dd < 0).sum(), len(dd)))
    eq = dd.cumsum(); print("cum net:", {str(k): round(v) for k, v in eq.iloc[::max(1, len(eq) // 8)].items()}, "end", round(eq.iloc[-1]))
