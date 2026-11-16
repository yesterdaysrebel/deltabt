"""Re-check of the option-selling test (owner 2026-11-16: "Approved" -- re-run it and read the paper account).

Suspected defect in scripts/option_selling_lab.py: the sale is at 12:00 UTC the day before expiry, but the
settlement uses the perp 1h bar that OPENS at expiry-day 23:00 of the previous day (p.get(d - HOUR)), i.e. the price
at 00:00 -- 12 h after the sale, 12 h before the 12:00 expiry. This script re-fetches the 1% legs and settles
each one three ways:
  S00   perp close at 00:00 of the expiry day (the lab's, to show the fetch reproduces it)
  S12   perp close at 12:00 of the expiry day (the 1h bar 11:00-12:00)
  DLT   Delta's own settlement_price of the expired option (/v2/products/{symbol}; payout per unit)
Sale at HAIRCUT x the option's MARK close of the 1h bar 11:00-12:00 the day before (1.0 / 0.9 / 0.8 shown).
Fees as the lab and the paper account: min(0.01% notional, 3.5% of premium) on the sale and on the settlement.
Two implementations of the P&L: vectorised pandas (A) and live.options_paper.leg_pnl per leg (B) must agree.

    PYTHONPATH=. python scripts/option_recheck.py fetch     # resumable, public GET only
    PYTHONPATH=. python scripts/option_recheck.py report
"""
from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live.options_paper import leg_pnl, strike_order  # noqa: E402

OUT = Path("out/options/recheck")
API = "https://api.india.delta.exchange"
TS = lambda s: int(pd.Timestamp(s, tz="UTC").timestamp())  # noqa: E731
DAY, HOUR = 86400, 3600
FIRST, BUILD_END, TEST_END, LAST = TS("2025-01-15"), TS("2026-01-01"), TS("2026-10-28"), TS("2026-11-15")
UND = {"BTC": dict(perp="BTCUSD", cv=0.001, grid=200), "ETH": dict(perp="ETHUSD", cv=0.01, grid=10)}
FEE_N, FEE_P = 0.0001, 0.035
_lock, _last = threading.Lock(), [0.0]


def _get(path, params=None, tries=5):
    for k in range(tries):
        with _lock:
            w = _last[0] + 0.17 - time.time()
            if w > 0:
                time.sleep(w)
            _last[0] = time.time()
        try:
            r = requests.get(API + path, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (k + 1))
                continue
            if r.status_code == 404:
                return {}
            return r.json()
        except Exception:  # noqa: BLE001
            time.sleep(2 * (k + 1))
    return None


def perp_1h(sym):
    p = OUT / f"{sym}_1h.parquet"
    if p.exists():
        return pd.read_parquet(p)
    rows, end = [], LAST + DAY
    while end > FIRST - 3 * DAY:
        start = max(FIRST - 3 * DAY, end - 3999 * HOUR)
        rows += (_get("/v2/history/candles", {"symbol": sym, "resolution": "1h", "start": start, "end": end}) or {}).get("result", [])
        end = start - HOUR
    d = pd.DataFrame(rows).drop_duplicates("time").astype({"time": "int64", "close": float}).sort_values("time")
    d = d[["time", "open", "high", "low", "close"]]
    d.to_parquet(p, index=False)
    return d


def tag(t):
    return pd.to_datetime(t, unit="s", utc=True).strftime("%d%m%y")


def _job(a):
    und, day, kind, spot = a
    entry = day - DAY + 12 * HOUR
    for k in strike_order(spot, kind, UND[und]["grid"]):
        sym = f"{kind}-{und}-{k}-{tag(day)}"
        j = _get("/v2/history/candles", {"symbol": "MARK:" + sym, "resolution": "1h", "start": entry - 2 * HOUR, "end": entry})
        if j is None:
            return dict(und=und, day=day, kind=kind, error=True)
        mark = next((float(x["close"]) for x in j.get("result", []) or [] if int(x["time"]) == entry - HOUR and x.get("close") is not None), None)
        if mark is None:
            continue
        pr = _get(f"/v2/products/{sym}")
        if pr is None:
            return dict(und=und, day=day, kind=kind, error=True)
        res = pr.get("result") or {}
        sp = res.get("settlement_price")
        return dict(und=und, day=day, kind=kind, strike=k, symbol=sym, mark=mark, spot=spot, state=res.get("state"),
                    settlement=None if sp in (None, "") else float(sp))
    return dict(und=und, day=day, kind=kind, strike=None)


def cmd_fetch():
    OUT.mkdir(parents=True, exist_ok=True)
    store = OUT / "legs.jsonl"
    done = set()
    if store.exists():
        for line in store.read_text().splitlines():
            x = json.loads(line)
            if not x.get("error"):
                done.add((x["und"], x["day"], x["kind"]))
    jobs = []
    for und, u in UND.items():
        p = perp_1h(u["perp"]).set_index("time").close
        for day in range(FIRST, LAST + DAY, DAY):
            t = day - DAY + 11 * HOUR
            if t in p.index:
                jobs += [(und, day, kind, float(p.loc[t])) for kind in ("P", "C") if (und, day, kind) not in done]
    print(f"{len(done)} cached, {len(jobs)} to fetch", flush=True)
    with ThreadPoolExecutor(4) as ex, store.open("a") as f:
        for i, r in enumerate(ex.map(_job, jobs), 1):
            f.write(json.dumps(r) + "\n")
            if i % 200 == 0:
                f.flush()
                print(f"  {i}/{len(jobs)}", flush=True)


def legs():
    x = pd.DataFrame([json.loads(l) for l in (OUT / "legs.jsonl").read_text().splitlines()])
    if "error" in x:
        x = x[x.error.isna() | (x.error == False)]  # noqa: E712
    x = x.drop_duplicates(["und", "day", "kind"], keep="last")
    x = x[x.strike.notna()].copy()
    out = []
    for und, u in UND.items():
        p = perp_1h(u["perp"]).set_index("time").close
        s = x[x.und == und].copy()
        s["p00"] = s.day.map(lambda d: p.get(d - HOUR, np.nan))          # bar 23:00-00:00 -> price at 00:00 (lab)
        s["p12"] = s.day.map(lambda d: p.get(d + 11 * HOUR, np.nan))     # bar 11:00-12:00 -> price at 12:00
        s["cv"] = u["cv"]
        out.append(s)
    t = pd.concat(out, ignore_index=True)
    for c in ("p00", "p12"):
        t["v_" + c] = np.where(t.kind == "P", np.maximum(t.strike - t[c], 0), np.maximum(t[c] - t.strike, 0))
    t["v_dlt"] = t.settlement
    return t


def pnl_a(t, hc, col):
    """Implementation A: vectorised."""
    notional = t.spot * t.cv
    prem = t.mark * hc * t.cv
    paid = t[col] * t.cv
    return prem - paid - np.minimum(FEE_N * notional, FEE_P * prem) - np.minimum(FEE_N * notional, FEE_P * paid)


def pnl_b(t, hc, col):
    """Implementation B: the paper account's own tested function, leg by leg."""
    return pd.Series([leg_pnl(r.mark * hc, getattr(r, col), r.spot, r.cv, 1)["pnl"] if pd.notna(getattr(r, col)) else np.nan
                      for r in t.itertuples()], index=t.index)


def strangles(t, hc, col):
    t = t.assign(pnl=pnl_a(t, hc, col), notional=t.spot * t.cv)
    s = t.pivot_table(index=["und", "day"], columns="kind", values=["pnl", "notional"], aggfunc="first").dropna()
    return pd.DataFrame({"pnl": s[("pnl", "P")] + s[("pnl", "C")], "notional": s[("notional", "P")]}).reset_index().assign(
        bps=lambda d: 1e4 * d.pnl / d.notional)


def line(s, label):
    if not len(s):
        return f"   {label:30s} none"
    by_day = s.groupby("day").bps.mean()
    se = by_day.std(ddof=1) / np.sqrt(len(by_day)) if len(by_day) > 1 else np.nan
    w, lo = s[s.bps > 0].bps, s[s.bps <= 0].bps
    return (f"   {label:30s} n {len(s):4d} | win {100 * (s.bps > 0).mean():3.0f}% | mean {s.bps.mean():+7.2f} bps (t {by_day.mean() / se:+5.2f}) | "
            f"avg win {w.mean():+6.1f} avg loss {lo.mean():+7.1f} | worst {s.bps.min():+7.0f} bps")


def cmd_report():
    t = legs()
    rows = []
    for col in ("v_p00", "v_p12", "v_dlt"):
        ok = t[col].notna()
        a, b = pnl_a(t[ok], 0.9, col), pnl_b(t[ok], 0.9, col)
        assert np.allclose(a, b, atol=1e-9), (col, float((a - b).abs().max()))
    rows.append("CHECK PASSED: implementations A (vectorised) and B (live.options_paper.leg_pnl) agree on every leg, all three settlements.")
    d = t[t[["v_p12", "v_dlt"]].notna().all(axis=1)]
    rows.append(f"legs with a strike: {len(t)}; with Delta's settlement_price: {t.v_dlt.notna().sum()} "
                f"(state expired {(t.state == 'expired').sum()})")
    diff = (d.v_dlt - d.v_p12).abs() * d.cv
    rows.append(f"settlement payout Delta vs perp-12:00 stand-in: median |diff| ${diff.median():.4f}/leg, "
                f"mean ${diff.mean():.4f}, max ${diff.max():.2f}; same in/out-of-the-money on {100 * ((d.v_dlt > 0) == (d.v_p12 > 0)).mean():.1f}% of legs")
    periods = [("BUILD 2025", FIRST, BUILD_END), ("TEST 2026 (to 10-27)", BUILD_END, TEST_END),
               ("PAPER period 10-28..11-15", TEST_END, LAST + DAY), ("ALL", FIRST, LAST + DAY)]
    names = {"v_p00": "S00 lab's 00:00 price", "v_p12": "S12 perp at 12:00", "v_dlt": "DLT Delta settlement"}
    for hc in (0.9, 1.0, 0.8):
        rows.append(f"\n=== sold at {hc:.1f} x mark ===")
        for col in ("v_p00", "v_p12", "v_dlt"):
            s = strangles(t, hc, col)
            rows.append(f" {names[col]}")
            for nm, a, b in periods:
                rows.append(line(s[(s.day >= a) & (s.day < b)], nm))
            if col == "v_dlt" and hc == 0.9:
                for und in UND:
                    for nm, a, b in periods[:2]:
                        u = s[(s.und == und) & (s.day >= a) & (s.day < b)]
                        rows.append(line(u, f"{und} {nm}"))
    txt = "\n".join(rows)
    print(txt)
    (OUT / "report.txt").write_text(txt + "\n")


if __name__ == "__main__":
    {"fetch": cmd_fetch, "report": cmd_report}[sys.argv[1]]()
