"""The owner's hand trades on Delta, rebuilt from Delta's own exports (2026-11-16, owner: "you pull it").

Inputs (gitignored, out/manual/ -- THE REPOSITORY IS PUBLIC, the per-trade record never leaves that folder):
    FillHistoryDelta_*.csv                     every fill
    Delta-TransactionLog-OrderHistory*.csv     orders with Delta's own Realised P&L (a shorter window)
    Annual Account Summary*.xlsx               Delta's per-coin totals in INR

    python scripts/hand_trades_review.py trips     # fills -> round trips, reconciled against Delta's numbers
    python scripts/hand_trades_review.py candles   # Delta public 1m candles around every trip
    python scripts/hand_trades_review.py report    # winners vs liquidations, and the what-if sweeps

A ROUND TRIP is one coin's position from flat to flat. A fill that crosses through zero is split.
Gross P&L = sell notional - buy notional (linear USD perps). Fees are the "Fees paid" column (GST included).
"""
import glob
import html
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

DIR = Path("out/manual")
IST = "Asia/Kolkata"
FX = 85.0                                                             # Delta India's fixed INR per USD (CHECK 2 confirms it)


def load_fills():
    f = pd.read_csv(glob.glob(str(DIR / "FillHistoryDelta_*.csv"))[0])
    f["t"] = pd.to_datetime(f["Time"].str.slice(0, 32), utc=True)
    f = f[~f.Contract.str.match(r"^[CP]-")].copy()                    # perps only; the handful of option fills are listed apart
    f = f.iloc[::-1].reset_index(drop=True)                           # the file is newest first
    f = f.sort_values("t", kind="stable").reset_index(drop=True)
    f["sgn"] = np.where(f.Side.eq("buy"), 1.0, -1.0)
    return f


def build_trips(f):
    rows, partial = [], []
    for sym, g in f.groupby("Contract", sort=False):
        pos, cur = 0.0, None

        def start(t):
            return dict(sym=sym, t0=t, side=0, ent_q=0.0, ent_n=0.0, ex_q=0.0, ex_n=0.0, gross=0.0, fees=0.0, ent_fee=0.0, nf=0,
                        ent_orders=set(), ex_orders=set(), liq_q=0.0, liq_n=0.0, maxq=0.0, t_ent_last=t, ent_taker_n=0.0,
                        ex_mkt_n=0.0, ent_times=[])

        def add(cur, q, r, share):
            n = r["Value Notional"] * share
            cur["gross"] += -r.sgn * n
            cur["fees"] += r["Fees paid"] * share
            cur["nf"] += 1
            if r.sgn == cur["side"]:
                cur["ent_q"] += q
                cur["ent_n"] += n
                cur["ent_fee"] += r["Fees paid"] * share
                cur["ent_orders"].add(r["Order ID"])
                cur["t_ent_last"] = r.t
                cur["ent_times"].append((r.t, q, r["Exec.Price"]))
                if r["Fee rate"] >= 0.0005:
                    cur["ent_taker_n"] += n
            else:
                cur["ex_q"] += q
                cur["ex_n"] += n
                cur["ex_orders"].add(r["Order ID"])
                if r["Fill Type"] == "liquidation":
                    cur["liq_q"] += q
                    cur["liq_n"] += n
                if r["Order Type"] == "market_order":
                    cur["ex_mkt_n"] += n

        for r in g.itertuples(index=False):
            rr = pd.Series(dict(zip(g.columns, r)))
            q = rr["Filled Qty"]
            while q > 1e-9:
                if cur is None:
                    cur = start(rr.t)
                    cur["side"] = rr.sgn
                if rr.sgn == cur["side"]:
                    add(cur, q, rr, q / rr["Filled Qty"])
                    pos += rr.sgn * q
                    cur["maxq"] = max(cur["maxq"], abs(pos))
                    q = 0
                else:
                    use = min(q, abs(pos))
                    add(cur, use, rr, use / rr["Filled Qty"])
                    pos += rr.sgn * use
                    q -= use
                    if abs(pos) < 1e-9:
                        cur["t1"] = rr.t
                        rows.append(cur)
                        cur, pos = None, 0.0
        if cur is not None:
            partial.append((sym, pos, cur["t0"]))
    t = pd.DataFrame(rows)
    cv = f.groupby("Contract").apply(lambda g: (g["Value Notional"] / (g["Filled Qty"] * g["Exec.Price"])).median())
    t["cv"] = t.sym.map(cv)
    t["avg_in"] = t.ent_n / (t.ent_q * t.cv)
    t["avg_out"] = t.ex_n / (t.ex_q * t.cv)
    # Delta's annual summary charges, per coin, more "Fee" than the fills' "Fees paid" -- only on coins with
    # liquidations (1.66 USD in total on the others). That extra is the liquidation charge; it is spread over the
    # coin's liquidated trips in proportion to the notional liquidated.
    s = annual_summary().set_index("sym")
    extra = (-s.fee / FX).reindex(t.sym.unique()).fillna(0) - t.groupby("sym").fees.sum()
    liq_tot = t.groupby("sym").liq_n.sum()
    t["liq_charge"] = (t.liq_n / t.sym.map(liq_tot).replace(0, np.nan) * t.sym.map(extra)).fillna(0).clip(lower=0)
    t["net"] = t.gross - t.fees - t.liq_charge
    t["liq"] = t.liq_q > 0
    t["n_adds"] = t.ent_orders.map(len)
    t["n_exits"] = t.ex_orders.map(len)
    t["hold_min"] = (t.t1 - t.t0).dt.total_seconds() / 60
    t["move"] = t.side * (t.avg_out / t.avg_in - 1)                    # the trip's price move in its own favour
    t["peak_n"] = t.maxq * t.cv * t.avg_in
    t["ent_taker"] = t.ent_taker_n / t.ent_n
    t["ex_mkt"] = t.ex_mkt_n / t.ex_n
    t["ent_fills"] = t.ent_times
    t = t.drop(columns=["ent_orders", "ex_orders", "ent_times", "ent_taker_n", "ex_mkt_n"]).sort_values("t0").reset_index(drop=True)
    t["day"] = t.t0.dt.tz_convert(IST).dt.date
    t["n_of_day"] = t.groupby("day").cumcount() + 1
    t["hour_ist"] = t.t0.dt.tz_convert(IST).dt.hour
    return t, partial


def annual_summary():
    z = zipfile.ZipFile(glob.glob(str(DIR / "Annual Account Summary*.xlsx"))[0])
    ss = [html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", s, re.S)))
          for s in re.findall(r"<si>(.*?)</si>", z.read("xl/sharedStrings.xml").decode(), re.S)]
    out = []
    for row in re.findall(r"<row[^>]*>(.*?)</row>", z.read("xl/worksheets/sheet1.xml").decode(), re.S):
        c = {}
        for m in re.finditer(r'<c r="([A-Z]+)\d+"([^>]*)>(.*?)</c>', row, re.S):
            v = re.search(r"<v>(.*?)</v>", m.group(3), re.S)
            if v:
                c[m.group(1)] = ss[int(v.group(1))] if 't="s"' in m.group(2) else v.group(1)
        if set("ABCDEFG") <= set(c) and c["A"].strip() and c["B"] != "Gross Profit":
            out.append((c["A"], *(float(c[k]) for k in "BCDEFG")))
    return pd.DataFrame(out, columns=["sym", "gp", "gl", "turnover", "fee", "funding", "net"])


def cmd_trips():
    f = load_fills()
    t, partial = build_trips(f)
    out = [f"fills (perps): {len(f)}  {f.t.min():%Y-%m-%d} .. {f.t.max():%Y-%m-%d}   notional ${f['Value Notional'].sum():,.0f}   fees ${f['Fees paid'].sum():,.2f}",
           f"round trips: {len(t)}   positions still open at the end of the file: {partial}",
           f"gross ${t.gross.sum():+,.2f}   trading fees ${t.fees.sum():,.2f}   liquidation charges ${t.liq_charge.sum():,.2f}   net ${t.net.sum():+,.2f}", ""]

    # CHECK 1: Delta's own Realised P&L per order (order history, a shorter window) against the same orders' trips.
    o = pd.read_csv(glob.glob(str(DIR / "Delta-TransactionLog-OrderHistory*.csv"))[0])
    o["t"] = pd.to_datetime(o["Time"].str.slice(0, 32), utc=True)
    oc = o[o.Status.eq("closed") & ~o.Contract.str.match(r"^[CP]-")]
    lo, hi = oc.t.min(), oc.t.max()
    inwin = t[(t.t0 >= lo - pd.Timedelta("1min")) & (t.t1 <= hi + pd.Timedelta("1min"))]
    out.append(f"CHECK 1  order-history window {lo:%Y-%m-%d %H:%M} .. {hi:%Y-%m-%d %H:%M} UTC")
    out.append(f"   Delta: realised ${oc['Realised P&L'].sum():+,.2f}  fees ${oc['Trading Fees'].sum():,.2f}   |   mine: gross ${inwin.gross.sum():+,.2f}  fees ${inwin.fees.sum():,.2f}  ({len(inwin)} trips)")
    a = oc.groupby("Contract")["Realised P&L"].sum()
    b = inwin.groupby("sym").gross.sum()
    d = pd.concat([a, b], axis=1, keys=["delta", "mine"]).fillna(0)
    d["diff"] = d.mine - d.delta
    out.append("   per coin, largest differences:  " + "  ".join(f"{k} {v:+.2f}" for k, v in d["diff"].abs().sort_values(ascending=False).head(5).items()))

    # CHECK 2: the annual summary (INR, from 2026-04-01) against the trips, per coin. The ratio should be one FX rate.
    s = annual_summary()
    s = s[~s.sym.isin(["BTC", "ETH"])].set_index("sym")
    m = t.groupby("sym").agg(gross=("gross", "sum"), fees=("fees", "sum"))
    j = s.join(m, how="outer").fillna(0)
    j["inr_gross"] = j.gp - j.gl
    big = j[j.turnover > 50000]
    fx = (big.inr_gross / big.gross).median()
    out.append(f"\nCHECK 2  annual summary (INR): gross {j.inr_gross.sum():+,.0f}  fee {j.fee.sum():+,.0f}  funding {j.funding.sum():+,.0f}  net {j.net.sum():+,.0f}")
    out.append(f"   median INR per USD implied by the big coins: {fx:.2f};  fee ratio {(-big.fee / big.fees).median():.2f}")
    out.append(f"   mine at that rate: gross {t.gross.sum() * fx:+,.0f}  trading fees {-t.fees.sum() * fx:+,.0f}  + liquidation charges {-t.liq_charge.sum() * fx:+,.0f}"
               f" = {-(t.fees.sum() + t.liq_charge.sum()) * fx:+,.0f}   net {t.net.sum() * fx:+,.0f} (Delta's net also holds funding {j.funding.sum():+,.0f})")
    j["mine_inr"] = j.gross * fx
    j["gap"] = j.mine_inr - j.inr_gross
    out.append("   coins where the two differ by more than 3,000 INR (trades before the fill file starts, or open positions):")
    for k, r in j[j.gap.abs() > 3000].sort_values("gap").iterrows():
        out.append(f"      {k:12s} Delta {r.inr_gross:+12,.0f}   mine {r.mine_inr:+12,.0f}   gap {r.gap:+10,.0f}")
    txt = "\n".join(out)
    print(txt)
    t.drop(columns=["ent_fills"]).to_csv(DIR / "trips.csv", index=False)
    t.to_pickle(DIR / "trips.pkl")
    (DIR / "trips_check.txt").write_text(txt + "\n")


API = "https://api.india.delta.exchange/v2/history/candles"
PRE, POST = 24 * 3600, 72 * 3600                                      # candles kept before an entry / after a liquidation


def _get(params, tries=6):
    import time

    import requests
    for k in range(tries):
        try:
            r = requests.get(API, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (k + 1))
                continue
            return r.json().get("result", []) or []
        except Exception:  # noqa: BLE001
            time.sleep(2 * (k + 1))
    raise RuntimeError(f"gave up {params}")


def cmd_candles():
    """Delta's public 1m TRADE candles for [entry - 24 h, exit + 1 h] (liquidated trips: exit + 72 h). Resumable."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    t = pd.read_pickle(DIR / "trips.pkl")
    (DIR / "candles").mkdir(exist_ok=True)

    def one(sym):
        p = DIR / "candles" / f"{sym}.parquet"
        if p.exists():
            return f"{sym} cached"
        g = t[t.sym == sym]
        iv = sorted((int(a.timestamp()) - PRE, int(b.timestamp()) + (POST if q else 3600)) for a, b, q in zip(g.t0, g.t1, g.liq))
        merged = [list(iv[0])]
        for a, b in iv[1:]:
            if a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        rows, n = [], 0
        for a, b in merged:
            x = a
            while x < b:
                e = min(b, x + 1999 * 60)
                rows += _get({"symbol": sym, "resolution": "1m", "start": x, "end": e})
                n += 1
                x = e
                time.sleep(0.35)
        d = pd.DataFrame(rows)
        if d.empty:
            return f"{sym} EMPTY ({n} requests)"
        d = d.drop_duplicates("time").astype({"time": "int64"}).sort_values("time")
        d[["time", "open", "high", "low", "close", "volume"]].to_parquet(p, index=False)
        return f"{sym} {len(d)} rows, {n} requests"

    with ThreadPoolExecutor(3) as ex:
        for msg in ex.map(one, sorted(t.sym.unique())):
            print(msg, flush=True)


TAKER, MAKER = 0.0005 * 1.18, 0.0002 * 1.18                           # this account's rates, GST included (the fills confirm them)
STOPS = (0.005, 0.01, 0.015, 0.02, 0.03, 0.05, 0.10, 0.20)


def candles(sym):
    p = DIR / "candles" / f"{sym}.parquet"
    if not p.exists():
        return None
    d = pd.read_parquet(p)
    return d.time.to_numpy(), d.high.to_numpy(), d.low.to_numpy(), d.close.to_numpy()


def covered(tr, c):
    """Candles exist for the entry minute and the exit minute (and, for a liquidation, 72 h after it)."""
    if c is None:
        return False
    T = c[0]
    a, b = int(tr.t0.timestamp()) // 60 * 60, int(tr.t1.timestamp()) // 60 * 60
    i, j = np.searchsorted(T, a), np.searchsorted(T, b)
    ok = i < len(T) and T[i] == a and j < len(T) and T[j] == b and (j - i) == (b - a) // 60
    if ok and tr.liq:
        k = np.searchsorted(T, b + POST - 3600)
        ok = k < len(T) and (k - j) >= (POST - 3600) // 60 - 5
    return bool(ok)


def whatif(tr, c, X, g, fill):
    """Net P&L of one trip had a stop been resting X (a fraction of the average entry price) against it.

    The position is one block: all entry fills at their average price. Up to the real exit the owner's own exit
    stands unless the stop is touched first. A trip Delta liquidated before the stop was touched is HELD (that is
    what lower leverage means): out at the stop, or at a resting target g in its favour (g = 0: at the entry
    price), or after 72 h at the close.
    STRICT: the entry and exit minutes count for the stop, a stop fills at the worse of stop and that minute's
    close and 0.1% worse again, a target must be passed by 0.1%, the stop wins a shared minute.
    LENIENT: only the minutes strictly between entry and exit count, the stop fills at its price, a target fills
    on touch and wins a shared minute.  -> (net, how)
    """
    T, H, L, C = c
    s, e, n = tr.side, tr.avg_in, tr.ent_n
    sp = e * (1 - s * X)
    a = int((tr.t0 if fill == "STRICT" else tr.t_ent_last).timestamp()) // 60 * 60
    b = int(tr.t1.timestamp()) // 60 * 60
    i0 = np.searchsorted(T, a) + (0 if fill == "STRICT" else 1)
    i1 = np.searchsorted(T, b) + (1 if fill == "STRICT" else 0)

    def stop_net(i):
        if fill == "STRICT":
            x = (min(sp, C[i]) * 0.999) if s > 0 else (max(sp, C[i]) * 1.001)
        else:
            x = sp
        return s * (x / e - 1) * n - tr.ent_fee - TAKER * n * x / e

    adverse = (L[i0:i1] <= sp) if s > 0 else (H[i0:i1] >= sp)
    hit = np.flatnonzero(adverse)
    if hit.size:
        return stop_net(i0 + int(hit[0])), "STOP"
    if not tr.liq:
        return tr.net, "AS TRADED"
    j0, j1 = np.searchsorted(T, b), np.searchsorted(T, b + POST)
    tg = e * (1 + s * g)
    need = tg * (1 + s * 0.001) if fill == "STRICT" else tg
    adv = np.flatnonzero((L[j0:j1] <= sp) if s > 0 else (H[j0:j1] >= sp))
    fav = np.flatnonzero((H[j0 + 1:j1] >= need) if s > 0 else (L[j0 + 1:j1] <= need)) + 1     # not in the liquidation minute
    ia = int(adv[0]) if adv.size else 10 ** 9
    ib = int(fav[0]) if fav.size else 10 ** 9
    if ia < 10 ** 9 and (ia < ib or (ia == ib and fill == "STRICT")):
        return stop_net(j0 + ia), "STOP LATER"
    if ib < 10 ** 9:
        return g * n - tr.ent_fee - MAKER * n * (1 + s * g), "TARGET"
    x = C[j1 - 1] * ((1 - s * 0.001) if fill == "STRICT" else 1.0)
    return s * (x / e - 1) * n - tr.ent_fee - TAKER * n * x / e, "72h"


def pre_move(tr, c, minutes):
    """Price change over the `minutes` before the first entry fill, signed so + means the trade went WITH the move."""
    T, _, _, C = c
    a = int(tr.t0.timestamp()) // 60 * 60
    i, j = np.searchsorted(T, a - 60), np.searchsorted(T, a - 60 - minutes * 60)
    if i >= len(T) or T[i] != a - 60 or T[j] != a - 60 - minutes * 60:
        return np.nan
    return tr.side * (C[i] / C[j] - 1)


def excursions(tr, c):
    """Worst and best price against/for the trip between its entry and exit minutes, as fractions of the entry."""
    T, H, L, _ = c
    i0 = np.searchsorted(T, int(tr.t0.timestamp()) // 60 * 60)
    i1 = np.searchsorted(T, int(tr.t1.timestamp()) // 60 * 60) + 1
    lo, hi = L[i0:i1].min() / tr.avg_in - 1, H[i0:i1].max() / tr.avg_in - 1
    return (lo, hi) if tr.side > 0 else (-hi, -lo)


def cls(t):
    return np.where(t.liq, "LIQUIDATED", np.where(t.net > 0, "WIN", "LOSS"))


def cmd_report():
    t = pd.read_pickle(DIR / "trips.pkl")
    t["cls"] = cls(t)
    C = {s: candles(s) for s in t.sym.unique()}
    t["has_c"] = [covered(r, C[r.sym]) for r in t.itertuples()]
    out = []
    w = out.append
    n_days = t.day.nunique()
    w(f"THE OWNER'S HAND TRADES, {t.t0.min():%Y-%m-%d} .. {t.t1.max():%Y-%m-%d}: {len(t)} round trips on {n_days} days, {t.sym.nunique()} coins.")
    w(f"gross {t.gross.sum():+,.0f}   trading fees {-t.fees.sum():+,.0f}   liquidation charges {-t.liq_charge.sum():+,.0f}   NET {t.net.sum():+,.0f} USD"
      f"   (Delta's annual summary: {t.net.sum() * FX:+,.0f} INR before funding)")
    w("")
    w(f"{'':12} {'trips':>5} {'net USD':>10} {'avg':>8} {'median':>8} {'median size':>11} {'median hold':>11} {'median move':>11}")
    for k in ("WIN", "LOSS", "LIQUIDATED"):
        g = t[t.cls == k]
        w(f"{k:12} {len(g):5d} {g.net.sum():+10,.0f} {g.net.mean():+8.0f} {g.net.median():+8.0f} {g.peak_n.median():11,.0f} {g.hold_min.median():9.1f} m {100 * g.move.median():+10.2f}%")
    cl = t[~t.liq]
    w(f"not liquidated: {len(cl)} trips, net {cl.net.sum():+,.0f}, {100 * (cl.net > 0).mean():.0f}% win, avg win {cl.net[cl.net > 0].mean():+.0f}, avg loss {cl.net[cl.net <= 0].mean():+.0f}")
    lq = t[t.liq]
    w(f"liquidated: {len(lq)} = {100 * len(lq) / len(t):.0f}% of trips; the price had moved {-100 * lq.move.median():.2f}% against (middle half "
      f"{-100 * lq.move.quantile(.75):.2f}..{-100 * lq.move.quantile(.25):.2f}%) -> about {1 / (-lq.move.median() + 0.005):.0f}x leverage")
    r = t.gross / t.ent_n
    w(f"price move per trip before any fee, % of the position: mean {100 * r.mean():+.3f}%  (t {r.mean() / (r.std(ddof=1) / np.sqrt(len(r))):+.2f}), "
      f"size-weighted {100 * t.gross.sum() / t.ent_n.sum():+.3f}%;  fees + liquidation charges {100 * (t.fees.sum() + t.liq_charge.sum()) / t.ent_n.sum():.3f}%")
    top = t.nlargest(10, "net")
    w(f"the 10 biggest wins: {top.net.sum():+,.0f} USD, median hold {top.hold_min.median():.0f} min, median move {100 * top.move.median():+.1f}%;  "
      f"trips held over 4 h: {int((t.hold_min > 240).sum())}, net {t.net[t.hold_min > 240].sum():+,.0f}")

    def table(title, key, order=None):
        w(f"\n-- by {title}")
        w(f"{'':22} {'trips':>5} {'win':>5} {'liquidated':>10} {'net USD':>10} {'avg':>7} {'net if not liq.':>15}")
        for k, g in t.groupby(key, observed=True, sort=order is None):
            w(f"{str(k):22} {len(g):5d} {100 * (g.cls == 'WIN').mean():4.0f}% {100 * g.liq.mean():9.0f}% {g.net.sum():+10,.0f} {g.net.mean():+7.0f} {g.net[~g.liq].sum():+15,.0f}")

    t = t.sort_values("t0").reset_index(drop=True)
    t["prev"] = np.where(t.day == t.day.shift(), t.cls.shift(), "first of the day")
    t["gap"] = (t.t0 - t.t1.shift()).dt.total_seconds() / 60
    table("month", t.t0.dt.tz_convert(IST).dt.strftime("%Y-%m"))
    table("trade number of the day", pd.cut(t.n_of_day, [0, 1, 3, 5, 10, 20, 999], labels=["1st", "2-3", "4-5", "6-10", "11-20", "21+"]))
    table("what the previous trade of the day was", "prev")
    table("minutes since the previous trade ended", pd.cut(t.gap, [-1e9, 2, 10, 60, 1e9], labels=["under 2", "2-10", "10-60", "over 60"]))
    table("direction", t.side.map({1.0: "long", -1.0: "short"}))
    table("hour (IST)", pd.cut(t.hour_ist, [-1, 5, 9, 13, 17, 23], labels=["00-05", "06-09", "10-13", "14-17", "18-23"]))
    table("position size", pd.qcut(t.peak_n, 4).map(lambda i: f"${i.left:,.0f}-{i.right:,.0f}"))
    table("hold time", pd.cut(t.hold_min, [0, 2, 5, 15, 60, 240, 1e9], labels=["under 2 min", "2-5", "5-15", "15-60", "1-4 h", "over 4 h"]))
    table("entry order", np.where(t.ent_taker > 0.5, "taker (crossed the spread)", "maker (resting limit)"))
    big = t.sym.map(t.sym.value_counts()) >= 20
    table("coin (20+ trips)", t.sym.where(big, "all others"))

    d = t.groupby("day").agg(n=("net", "size"), net=("net", "sum"), liq=("liq", "sum"))
    w(f"\ndays: {len(d)}, {int((d.net > 0).sum())} positive ({d.net[d.net > 0].sum():+,.0f}), {int((d.net <= 0).sum())} negative ({d.net[d.net <= 0].sum():+,.0f}); "
      f"{int((d.liq == 0).sum())} days without a liquidation net {d.net[d.liq == 0].sum():+,.0f}, {int((d.liq > 0).sum())} days with one net {d.net[d.liq > 0].sum():+,.0f}")

    w("\n-- rules that only DROP trades (no candles needed; assumes the kept trades would have been the same)")
    first_liq = t[t.liq].groupby("day").t1.min()
    keep = t.t0 <= t.day.map(first_liq).fillna(pd.Timestamp("2100-01-01", tz="UTC"))
    w(f"stop for the day after the first liquidation: {int(keep.sum())} trips kept, net {t.net[keep].sum():+,.0f}   (as traded {t.net.sum():+,.0f})")
    for k in (3, 5, 10):
        w(f"at most {k:2d} trades a day: {int((t.n_of_day <= k).sum())} trips kept, net {t.net[t.n_of_day <= k].sum():+,.0f}")
    cum = t.groupby("day").net.cumsum() - t.net
    for lim in (200, 500, 1000):
        k = cum > -lim
        w(f"stop for the day once it is down ${lim}: {int(k.sum())} trips kept, net {t.net[k].sum():+,.0f}")

    v = t[t.has_c].copy()
    w(f"\n== WITH DELTA'S 1m CANDLES: {len(v)} of {len(t)} trips have complete candles (the rest: coin no longer served, or gaps). "
      f"Those {len(v)}: net {v.net.sum():+,.0f}, {int(v.liq.sum())} liquidated.")
    for m in (15, 60, 240, 1440):
        v[f"pre{m}"] = [pre_move(r, C[r.sym], m) for r in v.itertuples()]
    ex = [excursions(r, C[r.sym]) for r in v.itertuples()]
    v["mae"], v["mfe"] = [a for a, _ in ex], [b for _, b in ex]
    w("\n-- how far the coin had already moved before the entry (+ = the trade went WITH that move, - = against it)")
    w(f"{'':12} " + " ".join(f"{'last ' + (str(m) + ' min' if m < 60 else str(m // 60) + ' h'):>14}" for m in (15, 60, 240, 1440)) + "   (median)")
    for k in ("WIN", "LOSS", "LIQUIDATED"):
        g = v[v.cls == k]
        w(f"{k:12} " + " ".join(f"{100 * g[f'pre{m}'].median():+13.2f}%" for m in (15, 60, 240, 1440)))
    for m in (60, 1440):
        w(f"\n   by the move over the last {m // 60} h, signed by the trade's direction")
        b = pd.cut(100 * v[f"pre{m}"], [-1e9, -10, -3, -1, 0, 1, 3, 10, 1e9] if m == 1440 else [-1e9, -3, -1, -0.3, 0, 0.3, 1, 3, 1e9])
        for k, g in v.groupby(b, observed=True):
            w(f"   {str(k):18} {len(g):4d} trips  win {100 * (g.cls == 'WIN').mean():3.0f}%  liquidated {100 * g.liq.mean():3.0f}%  net {g.net.sum():+9,.0f}  avg {g.net.mean():+6.0f}")
    win = v[v.cls == "WIN"]
    w("\n-- how far the WINNERS went against the owner before they won (worst price between entry and exit, 1m candles)")
    w("   " + "   ".join(f"over {100 * x:.1f}%: {100 * (win.mae <= -x).mean():.0f}% of wins, {win.net[win.mae <= -x].sum():+,.0f} USD" for x in (0.005, 0.01, 0.015, 0.02)))

    w("\n== WHAT IF every trade had carried a stop (same entries, same sizes, the owner's own exits otherwise)?")
    w("   A liquidated trade that the stop does not reach first is HELD instead (lower leverage, same size): out at the stop,")
    w("   or at a resting target (at the entry price / 1% in its favour), or after 72 h. STRICT / LENIENT fills as in whatif().")
    res = {}
    for g in (0.0, 0.01):
        w(f"\n   held liquidations exit at {'the entry price (scratch)' if g == 0 else '+1% (the median win)'}:")
        w(f"   {'stop':>6} | {'STRICT net':>10} {'LENIENT net':>11} | {'stopped':>7} {'of them wins':>12} | liquidations: {'stopped':>7} {'later stop':>10} {'target':>6} {'72h':>4}   (lenient counts)")
        for X in STOPS:
            col = {}
            for fill in ("STRICT", "LENIENT"):
                rr = [whatif(r, C[r.sym], X, g, fill) for r in v.itertuples()]
                col[fill] = (np.array([a for a, _ in rr]), np.array([b for _, b in rr]))
                res[(X, g, fill)] = col[fill][0]
            net_s, net_l = col["STRICT"][0].sum(), col["LENIENT"][0].sum()
            how = col["LENIENT"][1]
            lq = v.liq.to_numpy()
            w(f"   {X:6.1%} | {net_s:+10,.0f} {net_l:+11,.0f} | {int((how == 'STOP').sum()):7d} {int(((how == 'STOP') & (v.cls == 'WIN').to_numpy()).sum()):12d} | "
              f"{'':13} {int(((how == 'STOP') & lq).sum()):7d} {int((how == 'STOP LATER').sum()):10d} {int((how == 'TARGET').sum()):6d} {int((how == '72h').sum()):4d}")
    w(f"   as traded, same {len(v)} trips: {v.net.sum():+,.0f}")
    pd.to_pickle((v, res), DIR / "report_detail.pkl")
    txt = "\n".join(out)
    print(txt)
    (DIR / "report.txt").write_text(txt + "\n")


if __name__ == "__main__":
    {"trips": cmd_trips, "candles": cmd_candles, "report": cmd_report}[sys.argv[1]]()
